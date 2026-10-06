"""
Review (FSRS) statistics for the stats page.

The queue tables already exist (lute/models/review.py); this module
closes the loop by deriving the three numbers the queue itself cannot
show:

* on-time review rate -- was the card graded before it went overdue?
* retention           -- was the card recalled (anything but Again)?
* forgetting curve    -- retention vs days since the previous review,
                         with the FSRS-6 prediction overlaid.

All three depend on the pre-review scheduling state, which is only
available from the review log's own JSON snapshot (``RlData`` ->
``"before"``): fsrs cannot be run backwards, so the due date, stability
and last-review time that were in effect at grading time are not
recoverable from the card row afterwards.  Gradings written before undo
existed have no snapshot; they still count towards volume and retention
and are reported as having no timing data.

fsrs itself is deliberately NOT imported -- it is an optional
dependency and the stats page has to render without it.  The predicted
curve uses the FSRS-6 default decay, which is what ``Scheduler()``
uses when constructed with no explicit parameters, and
``lute.review.scheduler`` does exactly that.
"""

import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import inspect, text

# fsrs 6.x (venv/lib/python3.12/site-packages/fsrs/scheduler.py):
#   DECAY  = -parameters[20]            (default 0.1542)
#   FACTOR = 0.9 ** (1 / DECAY) - 1     (approximately 0.9806)
#   R(t)   = (1 + FACTOR * t / S) ** DECAY
# At t == S this is exactly 0.9, i.e. the desired retention.
FSRS_DECAY = -0.1542
FSRS_FACTOR = 0.9 ** (1.0 / FSRS_DECAY) - 1.0

# fsrs.Rating.Again: the only rating that counts as a failed recall.
_AGAIN = 1

# Rating buckets, in the order they are plotted.
_RATING_KEYS = ("again", "hard", "good", "easy")

# A card counts as "on time" when it is graded within a day of falling
# due.  Learning cards come back minutes later, so a zero-tolerance
# rule would flag every one of them as late, and a card due at 23:50
# graded at 00:10 is not what "overdue" means to a human.
ON_TIME_GRACE_DAYS = 1.0

# The forgetting curve's x buckets, in days since the previous review.
# Geometric-ish spacing: the interesting action is in the first month,
# and the tail has few reviews.
_CURVE_BUCKETS = (
    (0.0, 1.0, "≤1d"),
    (1.0, 3.0, "1-3d"),
    (3.0, 7.0, "3-7d"),
    (7.0, 14.0, "7-14d"),
    (14.0, 30.0, "14-30d"),
    (30.0, 90.0, "30-90d"),
    (90.0, 180.0, "90-180d"),
    (180.0, 365.0, "180-365d"),
    (365.0, None, "365d+"),
)

# reviewcards.RcState -> label.  The 0 is Lute's own "not graded yet"
# marker; 1-3 mirror fsrs.State (Learning, Review, Relearning).
_STATE_LABELS = {
    0: "New",
    1: "Learning",
    2: "Review",
    3: "Relearning",
}

_CARD_TYPE_LABELS = {
    "recognition": "Recognition",
    "cloze": "Cloze",
}

_VALID_PERIODS = ("today", "7days", "monthly")

_DAILY_FIELDS = (
    "total",
    "on_time",
    "late",
    "recalled",
    "again",
    "hard",
    "good",
    "easy",
)


def get_review_stats(session, lang_id=None, period="7days"):
    """
    Review queue statistics for the stats page.

    ``lang_id`` filters to one language (None = all languages).
    ``period`` scopes the summary cards and the volume chart; the
    forgetting curve and the per-state breakdown always use every
    review, because a curve drawn from a week of data is noise.

    Never raises for a database that predates the review feature: the
    tables are probed first and an empty payload is returned instead.
    """
    if period not in _VALID_PERIODS:
        period = "7days"
    if not _has_review_tables(session):
        return _empty_payload(period, lang_id)

    samples = [_sample(row) for row in _fetch_logs(session, lang_id)]
    series = _apply_period(_daily_series(samples), period)

    return {
        "has_data": bool(samples),
        "period": period,
        "lang_id": lang_id,
        "summary": _summary(series, samples, _card_counts(session, lang_id)),
        "daily": series,
        "ratings": _rating_breakdown(series),
        "forgetting": _forgetting_curve(samples),
        "by_state": _retention_groups(samples, _state_group),
        "by_card_type": _retention_groups(samples, _card_type_group),
        "on_time_grace_days": ON_TIME_GRACE_DAYS,
    }


def _has_review_tables(session):
    "True when both queue tables exist (a db restored from an old backup may lack them)."
    try:
        insp = inspect(session.get_bind())
        return insp.has_table("reviewlogs") and insp.has_table("reviewcards")
    except Exception:  # pylint: disable=broad-except
        return False


def _empty_payload(period, lang_id):
    "The payload for a db with no review tables at all."
    return {
        "has_data": False,
        "period": period,
        "lang_id": lang_id,
        "summary": _summary([], [], {"total": 0, "due_now": 0, "new": 0, "lapses": 0}),
        "daily": [],
        "ratings": _rating_breakdown([]),
        "forgetting": _forgetting_curve([]),
        "by_state": [],
        "by_card_type": [],
        "on_time_grace_days": ON_TIME_GRACE_DAYS,
    }


def _fetch_logs(session, lang_id):
    "Every review log with its card's type/state, oldest first."
    sql = (
        "select RlReviewTime, RlRating, RlRepsBefore, RlData, "
        "RcCardType, RcState "
        "from reviewlogs "
        "inner join reviewcards on reviewcards.RcID = reviewlogs.RlRcID "
        "inner join words on words.WoID = reviewcards.RcWoID "
    )
    params = {}
    if lang_id is not None:
        sql += "where words.WoLgID = :lid "
        params["lid"] = lang_id
    sql += "order by RlReviewTime"
    return session.execute(text(sql), params).all()


def _sample(row):
    """
    One review log as a flat dict of the numbers the reports need.

    Rows are (review_time, rating, reps_before, data, card_type, state).
    """
    review_time = _as_datetime(row[0])
    has_snapshot = bool(row[3])
    before = _before_snapshot(row[3])
    delay = _delay_days(before, review_time)
    elapsed = _elapsed_days(before, review_time)
    rating = int(row[1] or 0)
    return {
        "review_time": review_time,
        "local_date": _local_date(review_time),
        "rating": rating,
        "recalled": rating != _AGAIN,
        "card_type": row[4] or "recognition",
        # The state *before* this review: what the card was when the
        # user answered it, which is what "retention by maturity"
        # should group on.  Unknown without a snapshot.
        "state": int(row[5] or 0) if has_snapshot else None,
        "reps_before": int(row[2] or 0),
        "delay_days": delay,
        "elapsed_days": elapsed,
        "predicted": _predicted_retention(elapsed, before.get("stability")),
    }


def _before_snapshot(raw):
    "The 'before' dict out of a log's JSON payload; {} when absent or broken."
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    before = payload.get("before")
    return before if isinstance(before, dict) else {}


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


def _parse_iso(value):
    "An ISO string from the log snapshot -> naive datetime, or None."
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=None)
    except ValueError:
        return None


def _delay_days(before, review_time):
    """
    Days between the card falling due and being graded.

    Negative when graded early.  None when the log has no snapshot, or
    the card had no due date (which should not happen).
    """
    due = _parse_iso(before.get("due"))
    if due is None or review_time is None:
        return None
    return (review_time - due).total_seconds() / 86400.0


def _elapsed_days(before, review_time):
    "Days since the previous review, or None for a first review."
    last = _parse_iso(before.get("last_review"))
    if last is None or review_time is None:
        return None
    return max(0.0, (review_time - last).total_seconds() / 86400.0)


def _predicted_retention(elapsed_days, stability):
    "FSRS-6 retrievability at the moment of the review, or None."
    if elapsed_days is None or stability is None:
        return None
    try:
        s = float(stability)
    except (TypeError, ValueError):
        return None
    if s <= 0:
        return None
    return (1.0 + FSRS_FACTOR * elapsed_days / s) ** FSRS_DECAY


def _local_date(dt):
    "A naive-UTC datetime's date on the local clock (what the rest of the page uses)."
    if dt is None:
        return None
    return datetime.fromtimestamp(dt.replace(tzinfo=timezone.utc).timestamp()).date()


def _empty_bucket(key):
    "One daily bucket, all counters zeroed."
    b = {"date": key}
    b.update({f: 0 for f in _DAILY_FIELDS})
    return b


def _daily_series(samples):
    "One bucket per local date, oldest first."
    buckets = {}
    for s in samples:
        if s["local_date"] is None:
            continue
        key = s["local_date"].isoformat()
        b = buckets.setdefault(key, _empty_bucket(key))
        b["total"] += 1
        if s["delay_days"] is None:
            pass  # volume only; the log predates the undo snapshot
        elif s["delay_days"] <= ON_TIME_GRACE_DAYS:
            b["on_time"] += 1
        else:
            b["late"] += 1
        if s["recalled"]:
            b["recalled"] += 1
        b[_rating_key(s["rating"])] += 1
    return [buckets[k] for k in sorted(buckets)]


def _rating_key(rating):
    "A 1-4 rating as its bucket name."
    index = rating - 1
    if 0 <= index < len(_RATING_KEYS):
        return _RATING_KEYS[index]
    return "again"


def _apply_period(daily, period):
    "Slice/aggregate the all-time daily series to the requested period."
    today = date.today()
    if period == "today":
        return [b for b in daily if b["date"] == today.isoformat()]
    if period == "7days":
        cutoff = (today - timedelta(days=6)).isoformat()
        return [b for b in daily if cutoff <= b["date"] <= today.isoformat()]
    if period == "monthly":
        # The last 12 *calendar* months, not the last 12 months that
        # happen to hold data: a gap in the middle should not pull a
        # two-year-old review back into the window.
        cutoff = _add_months(_month_start(today), -11)
        recent = [b for b in daily if b["date"] >= cutoff.isoformat()]
        return _monthly_aggregate(recent)
    return daily


def _month_start(d):
    "The first day of d's month."
    return d.replace(day=1)


def _add_months(d, count):
    "Shift a month-start date by count months (negative goes back)."
    index = d.month - 1 + count
    return d.replace(year=d.year + index // 12, month=index % 12 + 1, day=1)


def _monthly_aggregate(daily, months=12):
    "Roll the daily series up into calendar months, keeping the last N."
    buckets = {}
    for item in daily:
        key = item["date"][:7]
        b = buckets.setdefault(key, _empty_bucket(f"{key}-01"))
        for field in _DAILY_FIELDS:
            b[field] += item.get(field, 0)
    ordered = [buckets[k] for k in sorted(buckets)]
    return ordered[-months:] if len(ordered) > months else ordered


def _summary(series, samples, cards):
    "Headline numbers for the period, plus all-time context."
    reviews = sum(b["total"] for b in series)
    on_time = sum(b["on_time"] for b in series)
    late = sum(b["late"] for b in series)
    again = sum(b["again"] for b in series)
    recalled = sum(b["recalled"] for b in series)
    timed = on_time + late
    delays = [s["delay_days"] for s in samples if s["delay_days"] is not None]
    return {
        "reviews": reviews,
        "reviews_all_time": len(samples),
        "on_time": on_time,
        "late": late,
        "no_timing": reviews - timed,
        "on_time_rate": (on_time / timed) if timed else None,
        "recalled": recalled,
        "again": again,
        "retention": (recalled / reviews) if reviews else None,
        "retention_all_time": _retention(samples),
        "avg_delay_days": (sum(delays) / len(delays)) if delays else None,
        "cards_total": cards["total"],
        "cards_due_now": cards["due_now"],
        "cards_new": cards["new"],
        "lapses": cards["lapses"],
    }


def _rating_breakdown(series):
    "Rating distribution over the period, for the rating chart."
    out = {key: 0 for key in _RATING_KEYS}
    for b in series:
        for key in _RATING_KEYS:
            out[key] += b.get(key, 0)
    out["total"] = sum(out[key] for key in _RATING_KEYS)
    return out


def _forgetting_curve(samples):
    """
    Empirical retention vs elapsed days, with the FSRS prediction.

    Only reviews that had a previous review contribute: the first
    grading of a card has no interval to place on the x axis.
    """
    buckets = [
        {
            "label": label,
            "days": _bucket_midpoint(low, high),
            "reviews": 0,
            "recalled": 0,
            "retention": None,
            "predicted": None,
        }
        for low, high, label in _CURVE_BUCKETS
    ]
    pred_sum = [0.0] * len(buckets)
    pred_n = [0] * len(buckets)

    for s in samples:
        elapsed = s["elapsed_days"]
        if elapsed is None or s["reps_before"] <= 0:
            continue
        idx = _bucket_index(elapsed)
        if idx is None:
            continue
        b = buckets[idx]
        b["reviews"] += 1
        if s["recalled"]:
            b["recalled"] += 1
        if s["predicted"] is not None:
            pred_sum[idx] += s["predicted"]
            pred_n[idx] += 1

    out = []
    for idx, b in enumerate(buckets):
        if b["reviews"] == 0:
            continue
        b["retention"] = b["recalled"] / b["reviews"]
        if pred_n[idx]:
            b["predicted"] = pred_sum[idx] / pred_n[idx]
        out.append(b)
    return {"buckets": out, "scope": "all time"}


def _bucket_midpoint(low, high):
    """
    A plottable x for the bucket, in days.

    Always strictly positive: the curve is drawn on a logarithmic x
    axis, which cannot show 0.
    """
    if low <= 0:
        return (high or 1.0) / 2.0
    if high is None:
        return low * 2
    return (low * high) ** 0.5


def _bucket_index(days):
    "Which forgetting-curve bucket a day count falls in."
    for i, (low, high, _label) in enumerate(_CURVE_BUCKETS):
        if high is None:
            if days >= low:
                return i
        elif low <= days < high:
            return i
    return None


def _retention_groups(samples, key_fn):
    "Retention split by a grouping key; groups with no reviews are dropped."
    groups = {}
    for s in samples:
        key = key_fn(s)
        if key is None:
            continue
        g = groups.setdefault(
            key[0], {"key": key[0], "label": key[1], "reviews": 0, "recalled": 0}
        )
        g["reviews"] += 1
        g["recalled"] += 1 if s["recalled"] else 0
    out = list(groups.values())
    for g in out:
        g["retention"] = g["recalled"] / g["reviews"]
    out.sort(key=lambda g: -g["reviews"])
    return out


def _state_group(sample):
    "Grouping key for the pre-review card state."
    state = sample["state"]
    if state is None:
        return None
    return (f"state_{state}", _STATE_LABELS.get(state, f"State {state}"))


def _card_type_group(sample):
    "Grouping key for the card type."
    ct = sample["card_type"]
    return (ct, _CARD_TYPE_LABELS.get(ct, ct.title()))


def _retention(samples):
    "Share of reviews that were not rated Again."
    if not samples:
        return None
    return sum(1 for s in samples if s["recalled"]) / len(samples)


def _card_counts(session, lang_id):
    "Queue size and the current backlog, over the whole queue."
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    sql = (
        "select "
        "count(*), "
        "coalesce(sum(case when RcReps > 0 and RcDue <= :now then 1 else 0 end), 0), "
        "coalesce(sum(case when RcReps = 0 then 1 else 0 end), 0), "
        "coalesce(sum(RcLapses), 0) "
        "from reviewcards "
        "inner join words on words.WoID = reviewcards.RcWoID "
    )
    params = {"now": now}
    if lang_id is not None:
        sql += "where words.WoLgID = :lid"
        params["lid"] = lang_id
    row = session.execute(text(sql), params).first()
    if row is None:
        return {"total": 0, "due_now": 0, "new": 0, "lapses": 0}
    return {
        "total": int(row[0] or 0),
        "due_now": int(row[1] or 0),
        "new": int(row[2] or 0),
        "lapses": int(row[3] or 0),
    }
