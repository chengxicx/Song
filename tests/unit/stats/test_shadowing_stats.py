"""
Shadowing statistics tests.

Attempts are inserted directly (as the background task's persist call
would), keeping the tests independent of the ASR engines.
"""

import json
from datetime import datetime, timedelta, timezone

from lute.db import db
from lute.models.shadowing import ShadowAttempt
from lute.stats.shadowing_stats import get_shadowing_stats

from tests.utils import add_terms


def _utcnow():
    "Naive UTC now, how the db stores datetimes."
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _attempt(spanish, score=80, created=None, duration=10.0, statuses=None):
    "One attempt row."
    row = ShadowAttempt(
        language_id=spanish.id,
        source="read",
        sentence="Tengo un gato.",
        score=score,
        duration=duration,
        tokens_per_min=60.0,
        engine="sensevoice",
        tokens=json.dumps(statuses or [{"text": "gato", "status": 2}]),
        created=created or _utcnow(),
    )
    db.session.add(row)
    db.session.commit()
    return row


def test_empty_when_the_table_is_missing(empty_db, spanish, monkeypatch):
    "A db without the table renders an empty payload instead of raising."
    from lute.stats import shadowing_stats

    monkeypatch.setattr(shadowing_stats, "_has_table", lambda session: False)

    data = get_shadowing_stats(db.session)
    assert data["has_data"] is False
    assert data["summary"]["attempts"] == 0


def test_summary_and_daily_series(empty_db, spanish):
    "The summary aggregates the period; the daily series averages per day."
    today = _utcnow()
    _attempt(spanish, score=80, created=today - timedelta(hours=1))
    _attempt(spanish, score=60, created=today - timedelta(hours=2))
    _attempt(spanish, score=100, created=today - timedelta(days=10))

    data = get_shadowing_stats(db.session, period="7days")

    assert data["has_data"] is True
    assert data["summary"]["attempts"] == 2
    assert data["summary"]["avg_score"] == 70.0
    assert data["summary"]["attempts_all_time"] == 3
    assert data["summary"]["avg_score_all_time"] == 80.0
    # 10s + 10s of takes.
    assert data["summary"]["minutes"] == 0.3
    today_rows = [d for d in data["daily"] if d["attempts"]]
    assert len(today_rows) == 1
    assert today_rows[0]["avg_score"] == 70.0


def test_language_filter(empty_db, spanish, english):
    "lang_id scopes every report to one language."
    _attempt(spanish, score=80)
    _attempt(english, score=20)

    only_es = get_shadowing_stats(db.session, lang_id=spanish.id, period="7days")
    assert only_es["summary"]["attempts_all_time"] == 1
    assert only_es["summary"]["avg_score"] == 80.0


def test_top_missed_words(empty_db, spanish):
    "Misses and misreads rank; one stumble per take; matches never rank."
    today = _utcnow()
    for _ in range(3):
        _attempt(
            spanish,
            statuses=[
                {"text": "gato", "status": 0},
                {"text": "perro", "status": 1},
                {"text": "casa", "status": 2},
            ],
        )
    _attempt(spanish, created=today - timedelta(days=3))

    data = get_shadowing_stats(db.session, period="7days")
    missed = {g["text"]: g["misses"] for g in data["top_missed"]}
    assert missed == {"gato": 3, "perro": 3}
    assert data["top_missed"][0]["language"] == "Spanish"


def test_top_missed_is_all_time(empty_db, spanish):
    "The stumble ranking ignores the period filter: old misses still rank."
    _attempt(
        spanish,
        statuses=[{"text": "perro", "status": 0}],
        created=_utcnow() - timedelta(days=60),
    )
    data = get_shadowing_stats(db.session, period="7days")
    assert data["summary"]["attempts"] == 0
    assert [g["text"] for g in data["top_missed"]] == ["perro"]


def test_broken_tokens_json_is_ignored(empty_db, spanish):
    "A row with unparseable tokens contributes nothing to the ranking."
    row = ShadowAttempt(
        language_id=spanish.id,
        source="read",
        score=50,
        tokens="{not json",
        created=_utcnow(),
    )
    db.session.add(row)
    db.session.commit()

    data = get_shadowing_stats(db.session, period="7days")
    assert data["top_missed"] == []
    assert data["summary"]["attempts"] == 1
