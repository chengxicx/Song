"""
Undo tests.

The undo path itself needs no scheduler: it only puts a saved card
state back and drops the log row.  So these run everywhere, including
on a Python too old for the fsrs package.  test_service.py has the
end-to-end version (grade then undo) behind importorskip("fsrs").
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from lute.db import db
from lute.models.repositories import UserSettingRepository
from lute.models.review import ReviewCard, ReviewLog
from lute.review import scheduler, service

from tests.utils import add_terms


def _utcnow():
    "Naive UTC now (how the db stores datetimes)."
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_card(term, **overrides):
    "A queued card for a term, without going through enqueue."
    now = _utcnow()
    card = ReviewCard(
        term_id=term.id,
        card_type="recognition",
        due=now,
        state=ReviewCard.STATE_NEW,
        reps=0,
        lapses=0,
        created=now,
    )
    for key, value in overrides.items():
        setattr(card, key, value)
    db.session.add(card)
    db.session.commit()
    return card


def _add_log(card, before, rating=3, reps_before=0):
    "Append a review log row the way service.grade does."
    db.session.add(
        ReviewLog(
            card_id=card.id,
            review_time=_utcnow(),
            rating=rating,
            reps_before=reps_before,
            data=json.dumps({"before": before, "log": None}),
        )
    )
    db.session.commit()


def _graded_card(term):
    "A card that looks like it was just graded, plus its log."
    card = _make_card(term)
    before = scheduler.card_state(card)
    card.reps = 1
    card.state = 2
    card.stability = 12.5
    card.difficulty = 6.25
    card.last_review = _utcnow()
    card.due = _utcnow() + timedelta(days=3)
    db.session.add(card)
    db.session.commit()
    _add_log(card, before, rating=3, reps_before=0)
    return card, before


def test_card_state_round_trips(empty_db, spanish):
    "card_state/restore_card must preserve every scheduling field."
    term = add_terms(spanish, ["gato"])[0]
    card = _make_card(term)
    card.reps = 4
    card.lapses = 1
    card.state = 2
    card.stability = 3.5
    card.difficulty = 7.25
    card.last_review = datetime(2026, 1, 2, 3, 4, 5, 123456)
    card.due = datetime(2026, 2, 3, 4, 5, 6, 654321)
    db.session.add(card)
    db.session.commit()

    saved = scheduler.card_state(card)

    blank = {
        "due": None,
        "state": 0,
        "stability": None,
        "difficulty": None,
        "last_review": None,
        "reps": 0,
        "lapses": 0,
    }
    scheduler.restore_card(card, blank)
    assert card.reps == 0
    assert card.lapses == 0
    assert card.state == 0
    assert card.stability is None
    assert card.due is None
    assert card.last_review is None

    scheduler.restore_card(card, saved)
    assert card.reps == 4
    assert card.lapses == 1
    assert card.state == 2
    assert card.stability == 3.5
    assert card.difficulty == 7.25
    assert card.last_review == datetime(2026, 1, 2, 3, 4, 5, 123456)
    assert card.due == datetime(2026, 2, 3, 4, 5, 6, 654321)


def test_card_state_survives_json(empty_db, spanish):
    "The snapshot is stored as JSON, so it must be JSON-able as-is."
    term = add_terms(spanish, ["gato"])[0]
    card = _make_card(term)
    card.reps = 2
    card.due = datetime(2026, 5, 6, 7, 8, 9)
    db.session.add(card)
    db.session.commit()

    state = scheduler.card_state(card)
    assert json.loads(json.dumps(state)) == state
    # A missing or malformed datetime must not explode the restore.
    scheduler.restore_card(card, dict(state, due="not a date"))
    assert card.due is None


def test_undo_with_nothing_recorded(empty_db, spanish):
    "An empty log is not an error state, it is just nothing to undo."
    assert service.undo_info(db.session) is None
    with pytest.raises(ValueError):
        service.undo_last(db.session)


def test_undo_info_describes_the_last_grade(empty_db, spanish):
    "The UI labels the button from this, so it must name the card."
    term = add_terms(spanish, ["gato"])[0]
    card, _before = _graded_card(term)

    info = service.undo_info(db.session)
    assert info["card_id"] == card.id
    assert info["card_type"] == "recognition"
    assert info["term_text"] == "gato"
    assert info["rating"] == 3


def test_undo_restores_the_card_exactly(empty_db, spanish):
    "Undo puts back every field and drops the log row."
    term = add_terms(spanish, ["gato"])[0]
    card, before = _graded_card(term)
    due_before = card.due

    result = service.undo_last(db.session)

    assert result["card_id"] == card.id
    assert result["term_text"] == "gato"
    assert result["rating"] == 3
    assert result["undo"] is None

    db.session.refresh(card)
    assert scheduler.card_state(card) == before
    assert card.reps == 0
    assert card.state == ReviewCard.STATE_NEW
    assert card.stability is None
    assert card.difficulty is None
    assert card.last_review is None
    assert card.due != due_before
    assert db.session.query(ReviewLog).count() == 0


def test_undo_gives_back_todays_new_card_allowance(empty_db, spanish):
    "A new card that is undone stops counting against the daily cap."
    repo = UserSettingRepository(db.session)
    repo.set_value("review_max_new_per_day", 1)
    db.session.commit()

    term = add_terms(spanish, ["gato"])[0]
    _card, _before = _graded_card(term)
    assert service.counts(db.session)["new_allowed_today"] == 0

    service.undo_last(db.session)
    assert service.counts(db.session)["new_allowed_today"] == 1


def test_undo_refuses_a_log_without_a_snapshot(empty_db, spanish):
    "Gradings from before undo existed must be refused, not half-undone."
    term = add_terms(spanish, ["gato"])[0]
    card = _make_card(term, reps=1)
    db.session.add(
        ReviewLog(
            card_id=card.id,
            review_time=_utcnow(),
            rating=3,
            reps_before=0,
            data=json.dumps({"some": "old fsrs log"}),
        )
    )
    db.session.commit()

    assert service.undo_info(db.session) is None
    with pytest.raises(ValueError):
        service.undo_last(db.session)
    # Nothing was touched.
    assert db.session.query(ReviewLog).count() == 1
    db.session.refresh(card)
    assert card.reps == 1


def test_undo_tolerates_a_broken_log_payload(empty_db, spanish):
    "A corrupt RlData value is treated as un-undoable, not a crash."
    term = add_terms(spanish, ["gato"])[0]
    card = _make_card(term, reps=1)
    for payload in ("", "not json", "[1, 2, 3]"):
        db.session.add(
            ReviewLog(
                card_id=card.id,
                review_time=_utcnow(),
                rating=3,
                reps_before=0,
                data=payload,
            )
        )
        db.session.commit()
        assert service.undo_info(db.session) is None
        with pytest.raises(ValueError):
            service.undo_last(db.session)
        db.session.query(ReviewLog).delete()
        db.session.commit()


def test_undo_only_reverses_the_most_recent_grade(empty_db, spanish):
    "Undoing twice steps back one grading at a time."
    terms = add_terms(spanish, ["gato", "perro"])
    first, first_before = _graded_card(terms[0])
    second, second_before = _graded_card(terms[1])

    service.undo_last(db.session)
    db.session.refresh(second)
    db.session.refresh(first)
    assert scheduler.card_state(second) == second_before
    # The older grading is untouched.
    assert first.reps == 1
    assert db.session.query(ReviewLog).count() == 1
    assert service.undo_info(db.session)["card_id"] == first.id

    service.undo_last(db.session)
    db.session.refresh(first)
    assert scheduler.card_state(first) == first_before
    assert service.undo_info(db.session) is None
