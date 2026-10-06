"""
Shadowing (read-aloud) statistics for the stats page.

The shadowattempts table (one row per scored take, written by the
background transcription tasks for both the reading page's panel and
shadowing review cards) already knows the score, the duration and the
per-word verdicts; this module turns that into the numbers the practice
itself cannot show:

* volume + score trend  -- attempts and average score per local day,
* spoken time           -- how many minutes went into the takes,
* most-stumbled words   -- the tokens takes most often missed or
                           misread, which are exactly the words a
                           learner wants to see again.

Deliberately model-free: nothing here imports the ASR engines, the
stats page renders without them.
"""

import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import inspect, text

_VALID_PERIODS = ("today", "7days", "monthly")


def get_shadowing_stats(session, lang_id=None, period="7days"):
    """
    Shadowing statistics for the stats page.

    ``lang_id`` filters to one language (None = all languages);
    ``period`` scopes the summary cards and the trend chart.  The
    most-stumbled words always use every attempt: a week's misses are
    too few to rank by.
    """
    if period not in _VALID_PERIODS:
        period = "7days"
    if not _has_table(session):
        return _empty_payload(period, lang_id)

    attempts = _fetch_attempts(session, lang_id)
    samples = [_sample(row) for row in attempts]
    series = _apply_period(_daily_series(samples), period)

    return {
        "has_data": bool(attempts),
        "period": period,
        "lang_id": lang_id,
        "summary": _summary(series, samples, period),
        "daily": series,
        "top_missed": _top_missed(attempts, lang_id),
    }


def _has_table(session):
    "True when the attempts table exists (an old restored db may lack it)."
    try:
        insp = inspect(session.get_bind())
        return insp.has_table("shadowattempts")
    except Exception:  # pylint: disable=broad-except
        return False


def _empty_payload(period, lang_id):
    return {
        "has_data": False,
        "period": period,
        "lang_id": lang_id,
        "summary": _summary([], [], period),
        "daily": [],
        "top_missed": [],
    }


def _fetch_attempts(session, lang_id):
    "Every attempt with its language name, oldest first."
    sql = (
        "select SaCreated, SaScore, SaDuration, SaTokens, LgName "
        "from shadowattempts "
        "inner join languages on languages.LgID = shadowattempts.SaLgID"
    )
    params = {}
    if lang_id is not None:
        sql += " where SaLgID = :lid"
        params["lid"] = lang_id
    sql += " order by SaCreated"
    return session.execute(text(sql), params).all()


def _sample(row):
    "One attempt as a flat dict: (created, score, duration, tokens, lang_name)."
    return {
        "created": _as_datetime(row[0]),
        "local_date": _local_date(_as_datetime(row[0])),
        "score": int(row[1] or 0),
        "duration": float(row[2] or 0.0),
        "lang_name": row[4],
    }


def _as_datetime(value):
    "Coerce a db datetime (or ISO string) to a naive datetime."
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _local_date(dt):
    "A naive-UTC datetime's date on the local clock (what the rest of the page uses)."
    if dt is None:
        return None
    return datetime.fromtimestamp(dt.replace(tzinfo=timezone.utc).timestamp()).date()


def _daily_series(samples):
    "One bucket per local date with attempts and the mean score, oldest first."
    buckets = {}
    for s in samples:
        if s["local_date"] is None:
            continue
        key = s["local_date"].isoformat()
        b = buckets.setdefault(key, {"date": key, "attempts": 0, "score_sum": 0})
        b["attempts"] += 1
        b["score_sum"] += s["score"]
    out = []
    for key in sorted(buckets):
        b = buckets[key]
        n = b["attempts"]
        out.append(
            {
                "date": b["date"],
                "attempts": n,
                "avg_score": round(b["score_sum"] / n, 1) if n else None,
            }
        )
    return out


def _apply_period(daily, period):
    "Slice the all-time daily series to the requested period."
    today = date.today()
    if period == "today":
        return [b for b in daily if b["date"] == today.isoformat()]
    if period == "monthly":
        # The last 12 *calendar* months, rolled up (same rule as the
        # review stats: a mid-gap should not drag old data back in).
        cutoff = _add_months(_month_start(today), -11).isoformat()
        recent = [b for b in daily if b["date"] >= cutoff]
        return _monthly_aggregate(recent)
    cutoff = (today - timedelta(days=6)).isoformat()
    return [b for b in daily if cutoff <= b["date"] <= today.isoformat()]


def _month_start(d):
    return d.replace(day=1)


def _add_months(d, count):
    "Shift a month-start by count months (negative goes back)."
    index = d.month - 1 + count
    return d.replace(year=d.year + index // 12, month=index % 12 + 1, day=1)


def _monthly_aggregate(daily, months=12):
    "Roll the daily series up into calendar months, keeping the last N."
    buckets = {}
    for item in daily:
        key = item["date"][:7]
        b = buckets.setdefault(
            key, {"date": f"{key}-01", "attempts": 0, "score_sum": 0}
        )
        b["attempts"] += item["attempts"]
        b["score_sum"] += item["attempts"] * (item["avg_score"] or 0)
    out = []
    for key in sorted(buckets):
        b = buckets[key]
        n = b["attempts"]
        out.append(
            {
                "date": b["date"],
                "attempts": n,
                "avg_score": round(b["score_sum"] / n, 1) if n else None,
            }
        )
    return out[-months:] if len(out) > months else out


def _summary(series, samples, period):
    """
    Headline numbers for the period, plus all-time context.

    The mean score is weighted per attempt straight off the series, so
    a monthly bucket counts once per take it holds, not once per day.
    """
    attempts = sum(b["attempts"] for b in series)
    weighted = sum((b["avg_score"] or 0) * b["attempts"] for b in series)
    scoped = [s for s in samples if _in_period(s, period)]
    return {
        "attempts": attempts,
        "avg_score": round(weighted / attempts, 1) if attempts else None,
        "best_score": max((s["score"] for s in scoped), default=None),
        "minutes": round(sum(s["duration"] for s in scoped) / 60.0, 1),
        "attempts_all_time": len(samples),
        "avg_score_all_time": round(sum(s["score"] for s in samples) / len(samples), 1)
        if samples
        else None,
    }


def _in_period(sample, period):
    "Whether the sample's local date falls in the requested period."
    d = sample["local_date"]
    if d is None:
        return False
    today = date.today()
    if period == "today":
        return d == today
    if period == "monthly":
        return d >= _add_months(_month_start(today), -11)
    return d >= today - timedelta(days=6)


def _top_missed(attempts, lang_id):
    """
    The tokens takes most often missed or misread, over all time.

    The per-token verdicts ride along in SaTokens as
    [{"text": ..., "status": 0-3}, ...]; statuses 0 (missed) and 1
    (misread) are the stumbles.  Grouped per language, since the same
    letters can be words in several of them.
    """
    counts = {}
    for row in attempts:
        lang_name = row[4]
        try:
            tokens = json.loads(row[3] or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(tokens, list):
            continue
        seen_in_take = set()
        for t in tokens:
            if not isinstance(t, dict) or t.get("status") not in (0, 1):
                continue
            text = (t.get("text") or "").strip()
            if not text or (lang_name, text) in seen_in_take:
                # One stumble per take: re-reading the same word twice
                # in a sentence is one problem, not two.
                continue
            seen_in_take.add((lang_name, text))
            g = counts.setdefault(
                (lang_name, text), {"text": text, "language": lang_name, "misses": 0}
            )
            g["misses"] += 1
    out = sorted(counts.values(), key=lambda g: -g["misses"])[:20]
    return out
