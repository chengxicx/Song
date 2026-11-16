"""
Data for the 3D insight charts.

Four shapes, each chosen because the third dimension carries something a
2D chart would have to spend a family of curves on:

* ``retention_surface`` -- measured recall rate over (stability, elapsed
  days), with the FSRS prediction alongside it.  In 2D this is one line
  per stability bucket.
* ``lexicon_galaxy`` -- every studied word as a point in (when it entered
  study, status, token count).  Rotation is what makes clusters legible;
  a flat scatter of thousands of points is a rectangle of ink.
* ``reading_terrain`` -- daily words read as a height field, weeks by
  weekday.
* ``level_towers`` -- one column per exam level, banded by status.

Anything with too little data returns an empty-but-valid payload rather
than raising: the page renders "nothing to show" instead of a 500.
"""

import math
from bisect import bisect_right
from datetime import date, datetime, timedelta

from sqlalchemy import text

from lute.stats import (
    arabic_data,
    cefr_data,
    dele_data,
    french_data,
    german_data,
    hsk_data,
    jlpt_data,
    russian_data,
    thai_data,
    topik_data,
)
from lute.stats.review_stats import FSRS_DECAY, FSRS_FACTOR, retention_samples

# Right-open buckets, in days.  Shared by both axes of the retention
# surface so a cell is comparable along either direction.
STABILITY_EDGES = [0, 1, 2, 3, 5, 7, 10, 14, 21, 30, 60, 90, 180, 365, 100000]
ELAPSED_EDGES = [0, 1, 2, 3, 5, 7, 14, 30, 60, 90, 180, 365, 100000]

# Below this a cell is noise, and drawing it would invent a signal.
MIN_CELL_SAMPLES = 5

GALAXY_LIMIT = 5000
TERRAIN_DAYS = 371

# Language name (lowercased) -> (vocab module, level function, scheme).
# The names are the built-in language definitions; anything missing here
# simply has no level report and the tower chart says so.
_LEVEL_SOURCES = {
    "japanese": (jlpt_data, "jlpt_level", None),
    "korean": (topik_data, "topik_level", None),
    "english": (cefr_data, "cefr_level", None),
    "french": (french_data, "french_level", None),
    "german": (german_data, "german_level", None),
    "spanish": (dele_data, "dele_level", None),
    "italian": (cefr_data, "cefr_level", None),
    "portuguese": (cefr_data, "cefr_level", None),
    "russian": (russian_data, "russian_level", None),
    "thai": (thai_data, "thai_level", None),
    "arabic": (arabic_data, "arabic_level", None),
    "chinese": (hsk_data, "hsk2_level", "2"),
    "mandarin chinese": (hsk_data, "hsk2_level", "2"),
}

_STATUS_BANDS = ["0", "1", "2", "3", "4", "5", "98", "99"]


def language_name(session, lang_id):
    "The display name of a language, or '' if it is gone."
    row = session.execute(
        text("select LgName from languages where LgID = :lid"), {"lid": lang_id}
    ).first()
    return (row[0] or "") if row else ""


def _bucket(edges, value):
    "Index of the right-open bucket that holds value."
    idx = bisect_right(edges, value) - 1
    return max(0, min(idx, len(edges) - 2))


def _bucket_label(edges, idx):
    "Human label for a bucket, e.g. '7-9d' or '>=365d'."
    low, high = edges[idx], edges[idx + 1]
    if high >= 100000:
        return f">={int(low)}d"
    return f"{int(low)}-{int(high) - 1}d"


def _bucket_center(edges, idx):
    "A representative day count for a bucket, for the theory surface."
    low, high = edges[idx], min(edges[idx + 1], 100000)
    if low <= 0:
        return 0.5
    if high >= 100000:
        return low * 2.0
    return math.sqrt(low * high)


def _fsrs_retention(elapsed_days, stability_days):
    "FSRS-6 retrievability; the same curve the review page draws."
    if stability_days <= 0:
        return 0.0
    return (1.0 + FSRS_FACTOR * elapsed_days / stability_days) ** FSRS_DECAY


def retention_surface(session, lang_id):
    """
    Measured recall rate over (stability, elapsed), plus the theory grid.

    ``cells`` only holds buckets with enough samples to mean something;
    ``theory`` covers the whole grid so the two can be drawn together.
    """
    samples = retention_samples(session, lang_id)
    counts = {}
    for stability, elapsed, recalled in samples:
        key = (_bucket(STABILITY_EDGES, stability), _bucket(ELAPSED_EDGES, elapsed))
        cell = counts.setdefault(key, [0, 0])
        cell[0] += 1
        if recalled:
            cell[1] += 1

    cells = []
    for (xi, yi), (n, hits) in sorted(counts.items()):
        if n < MIN_CELL_SAMPLES:
            continue
        cells.append({"x": xi, "y": yi, "z": round(hits / n, 4), "n": n})

    theory = []
    for xi in range(len(STABILITY_EDGES) - 1):
        s = _bucket_center(STABILITY_EDGES, xi)
        for yi in range(len(ELAPSED_EDGES) - 1):
            e = _bucket_center(ELAPSED_EDGES, yi)
            theory.append([xi, yi, round(_fsrs_retention(e, s), 4)])

    return {
        "x_labels": [
            _bucket_label(STABILITY_EDGES, i) for i in range(len(STABILITY_EDGES) - 1)
        ],
        "y_labels": [
            _bucket_label(ELAPSED_EDGES, i) for i in range(len(ELAPSED_EDGES) - 1)
        ],
        "cells": cells,
        "theory": theory,
        "samples": len(samples),
        "min_samples": MIN_CELL_SAMPLES,
        "empty": len(cells) == 0,
    }


def lexicon_galaxy(session, lang_id, limit=GALAXY_LIMIT):
    """
    Every studied word as a point.

    x is the day the word entered study relative to the earliest point.
    That is ``WoCreated``, which a trigger resets when a term leaves
    Unknown status, so it means "when it started being studied", not
    "when the row was inserted" -- the page labels it that way.

    Abscissa/z are normalised to day counts and token counts rather than
    dates so the client can plot without parsing anything.
    """
    sql = (
        "select WoID, WoStatus, coalesce(WoTokenCount, 0), "
        "coalesce(julianday(WoCreated), 0) "
        "from words where WoLgID = :lid and WoStatus in (1,2,3,4,5,99) "
        "order by WoID"
    )
    rows = session.execute(text(sql), {"lid": lang_id}).all()
    total = len(rows)
    if total == 0:
        return {
            "days": [],
            "status": [],
            "tokens": [],
            "total": 0,
            "shown": 0,
            "empty": True,
            "status_counts": {},
        }

    # Deterministic thinning: the same run always sends the same points,
    # so the picture does not jitter between refreshes.
    step = 1
    if total > limit:
        step = math.ceil(total / limit)
    kept = rows[::step][:limit]

    base = min(r[3] for r in rows if r[3])
    days = []
    statuses = []
    tokens = []
    status_counts = {}
    for _wo_id, status, token_count, jd in kept:
        days.append(int(round(jd - base)) if jd else 0)
        statuses.append(int(status))
        tokens.append(int(token_count))
        status_counts[str(int(status))] = status_counts.get(str(int(status)), 0) + 1

    return {
        "days": days,
        "status": statuses,
        "tokens": tokens,
        "total": total,
        "shown": len(kept),
        "empty": False,
        "status_counts": status_counts,
        "max_day": max(days) if days else 0,
        "max_tokens": max(tokens) if tokens else 0,
    }


def reading_terrain(session, lang_id, days=TERRAIN_DAYS):
    """
    Daily words read as a week-by-weekday height field.

    Aligned to Monday so the grid has seven real columns instead of a
    ragged diagonal.
    """
    sql = (
        "select strftime('%Y-%m-%d', WrReadDate, 'localtime') as dt, "
        "sum(WrWordCount) as total "
        "from wordsread where WrLgID = :lid and WrReadDate is not null "
        "group by dt order by dt"
    )
    rows = session.execute(text(sql), {"lid": lang_id}).all()
    daily = {}
    for dt, total in rows:
        if not dt:
            continue
        daily[dt] = int(total or 0)

    if not daily:
        return {
            "weeks": [],
            "max": 0,
            "total": 0,
            "empty": True,
            "weekdays": _weekday_labels(),
        }

    last = datetime.strptime(max(daily), "%Y-%m-%d").date()
    first = last - timedelta(days=days - 1)
    first -= timedelta(days=first.weekday())  # back up to Monday

    weeks = []
    cursor = first
    while cursor <= last:
        week = []
        for _ in range(7):
            week.append(daily.get(cursor.isoformat(), 0))
            cursor += timedelta(days=1)
        weeks.append(week)

    values = [v for week in weeks for v in week]
    return {
        "weeks": weeks,
        "max": max(values) if values else 0,
        "total": sum(values),
        "empty": sum(values) == 0,
        "first_date": first.isoformat(),
        "last_date": last.isoformat(),
        "weekdays": _weekday_labels(),
    }


def _weekday_labels():
    "Monday-first, matching the grid alignment."
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def level_towers(session, lang_id, language_name_value=None):
    """
    One column per exam level, banded by Lute status.

    Reuses the vocab packs the stats page already ships; a language with
    no pack (or a damaged one) reports ``supported: False`` instead of an
    empty chart that looks like a bug.
    """
    name = (language_name_value or language_name(session, lang_id)).strip().lower()
    source = _LEVEL_SOURCES.get(name)
    if source is None:
        return {"supported": False, "language": name, "levels": []}

    module, level_fn_name, scheme = source
    try:
        levels = getattr(module, "LEVELS", None) or getattr(module, "LEVELS2")
        level_fn = getattr(module, level_fn_name)
        totals = module.level_totals(scheme) if scheme else module.level_totals()
    except Exception:  # pylint: disable=broad-except
        return {"supported": False, "language": name, "levels": []}

    sql = (
        "select WoText, WoStatus from words "
        "where WoLgID = :lid and WoStatus in (1,2,3,4,5,98,99)"
    )
    rows = session.execute(text(sql), {"lid": lang_id}).all()

    bands = {level: {b: 0 for b in _STATUS_BANDS} for level in levels}
    for word_text, status in rows:
        word = (word_text or "").strip().lower()
        if not word:
            continue
        level = None
        try:
            level = level_fn(word)
        except Exception:  # pylint: disable=broad-except
            level = None
        if level is None or level not in bands:
            continue
        key = str(int(status))
        if key in bands[level]:
            bands[level][key] += 1

    out = []
    for level in levels:
        out.append(
            {
                "level": level,
                "total": int(totals.get(level, 0)),
                "bands": bands[level],
                "studied": sum(
                    bands[level][b] for b in ("1", "2", "3", "4", "5", "99")
                ),
            }
        )

    return {
        "supported": True,
        "language": name,
        "levels": out,
        "bands": _STATUS_BANDS,
        "empty": all(x["studied"] == 0 for x in out),
    }
