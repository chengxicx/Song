"""
Scheduler wrapper tests.

Pure-python parts run anywhere; the fsrs-backed parts are skipped
when the optional fsrs package is missing (needs Python >= 3.10).
"""

from datetime import datetime, timedelta, timezone

import pytest

from lute.review import scheduler


def test_fsrs_status_shape():
    "Status dict always reports installability."
    st = scheduler.fsrs_status()
    assert set(st.keys()) == {"installed", "version", "installable", "pip_spec"}
    assert st["installable"] is True


def test_clamp_retention():
    "Bad or extreme retentions fall back to sane values."
    assert scheduler._clamp_retention(None) == 0.9
    assert scheduler._clamp_retention("garbage") == 0.9
    assert scheduler._clamp_retention("0.95") == 0.95
    assert scheduler._clamp_retention(1.5) == 0.99
    assert scheduler._clamp_retention(0.1) == 0.5


def test_format_interval():
    "Intervals render Anki-style."
    assert scheduler._format_interval(timedelta(seconds=45)) == "45s"
    assert scheduler._format_interval(timedelta(minutes=10)) == "10m"
    assert scheduler._format_interval(timedelta(hours=2)) == "2h"
    assert scheduler._format_interval(timedelta(days=3)) == "3d"
    assert scheduler._format_interval(timedelta(days=45)) == "2mo"
    assert scheduler._format_interval(timedelta(days=730)) == "2y"


def test_naive_aware_roundtrip():
    "Naive datetimes are treated as UTC."
    naive = datetime(2026, 1, 2, 3, 4, 5)
    aware = scheduler._aware(naive)
    assert aware.tzinfo == timezone.utc
    assert scheduler._naive(aware) == naive
    assert scheduler._aware(None) is None


def test_fsrs_missing_error():
    "load_scheduler raises a clear error when fsrs is unusable."
    import sys

    saved = sys.modules.get("fsrs")
    sys.modules["fsrs"] = None  # forces ImportError on import
    try:
        with pytest.raises(scheduler.SchedulerUnavailableError):
            scheduler.load_scheduler(0.9)
    finally:
        if saved is None:
            del sys.modules["fsrs"]
        else:
            sys.modules["fsrs"] = saved


@pytest.mark.skipif(
    scheduler._installed_version() is None, reason="fsrs package not installed"
)
def test_new_card_loads_fresh():
    "Unreviewed cards map to a fresh fsrs card."
    from lute.models.review import ReviewCard

    dbcard = ReviewCard()
    dbcard.reps = 0
    fcard = scheduler.load_card(dbcard)
    assert fcard.due is not None


@pytest.mark.skipif(
    scheduler._installed_version() is None, reason="fsrs package not installed"
)
def test_save_and_load_roundtrip():
    "Graded state survives a save/load roundtrip."
    from lute.models.review import ReviewCard

    sched = scheduler.load_scheduler(0.9)
    now = datetime.now(timezone.utc)

    dbcard = ReviewCard()
    dbcard.reps = 0
    fcard = scheduler.load_card(dbcard)
    new_fcard, _log = sched.review_card(fcard, scheduler.rating_value(3), now)
    scheduler.save_card(dbcard, new_fcard, 3)

    assert dbcard.reps == 1
    assert dbcard.stability is not None
    assert dbcard.last_review is not None

    reloaded = scheduler.load_card(dbcard)
    assert int(reloaded.state) == dbcard.state
    assert reloaded.stability == pytest.approx(dbcard.stability)
    assert scheduler._naive(reloaded.due) == dbcard.due


@pytest.mark.skipif(
    scheduler._installed_version() is None, reason="fsrs package not installed"
)
def test_preview_intervals():
    "Interval previews don't mutate the card."
    from lute.models.review import ReviewCard

    sched = scheduler.load_scheduler(0.9)
    now = datetime.now(timezone.utc)

    dbcard = ReviewCard()
    dbcard.reps = 0
    fcard = scheduler.load_card(dbcard)
    before_state = int(fcard.state)

    intervals = scheduler.preview_intervals(sched, fcard, now)
    assert set(intervals.keys()) == {"again", "good"}
    assert all(isinstance(i, str) for i in intervals.values())
    assert int(fcard.state) == before_state
