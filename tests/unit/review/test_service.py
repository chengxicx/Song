"""
Review session service tests (need the fsrs package).
"""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fsrs")

from lute.db import db  # noqa: E402
from lute.models.repositories import UserSettingRepository  # noqa: E402
from lute.models.review import ReviewCard, ReviewLog, ReviewSpec  # noqa: E402
from lute.review import enqueue, service  # noqa: E402
from lute.review.scheduler import (  # noqa: E402
    SchedulerUnavailableError,
    load_scheduler,
)

from tests.utils import add_terms, make_book  # noqa: E402


def _make_spec(name="all", criteria="", card_types=("recognition", "cloze")):
    "Add a spec (recall/typing is opt-in, mirroring the model default)."
    spec = ReviewSpec()
    spec.name = name
    spec.criteria = criteria
    spec.set_card_types(list(card_types))
    spec.active = True
    db.session.add(spec)
    db.session.commit()
    return spec


def _read_book(spanish, content):
    "Make a read book so its sentences are searchable."
    b = make_book("Book", content, spanish)
    b.texts[0].read_date = datetime.now()
    db.session.add(b)
    db.session.commit()
    return b


def _queue(
    spanish,
    terms_text="gato",
    book_content="Tengo un gato. El gato es negro.",
    card_types=("recognition", "cloze"),
):
    "Terms + read book + synced queue; returns the terms."
    terms = add_terms(spanish, terms_text.split(","))
    terms[0].translation = "cat"
    db.session.add(terms[0])
    db.session.commit()
    _read_book(spanish, book_content)
    _make_spec(card_types=card_types)
    enqueue.run_sync(commit=True)
    return terms


def _utcnow():
    "Naive UTC now (how the db stores datetimes)."
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_grade_moves_card_and_writes_log(empty_db, spanish):
    "A graded card gets fsrs state, a future due date, and a log row."
    _queue(spanish)
    session_payload = service.start_session(db.session)
    cards = [c for c in session_payload["cards"] if c["card_type"] == "recognition"]
    assert len(cards) == 1
    card_view = cards[0]
    assert card_view["intervals"] is not None
    assert len(card_view["intervals"]) == 4

    result = service.grade(db.session, card_view["id"], 3)

    card = db.session.query(ReviewCard).filter_by(card_type="recognition").first()
    assert card.reps == 1
    assert card.state != ReviewCard.STATE_NEW
    assert card.due > _utcnow() - timedelta(minutes=1)
    log = db.session.query(ReviewLog).one()
    assert log.rating == 3
    assert log.reps_before == 0
    assert log.data is not None
    assert result["correct"] is True


def test_wrong_typed_answer_forces_again(empty_db, spanish):
    "A wrong recall answer is graded Again, with the real answer returned."
    _queue(spanish, card_types=("recognition", "recall", "cloze"))
    payload = service.start_session(db.session)
    card_view = next(c for c in payload["cards"] if c["card_type"] == "recall")

    result = service.grade(db.session, card_view["id"], 3, typed_answer="xxx")

    assert result["correct"] is False
    assert result["answer"] == "gato"
    log = db.session.query(ReviewLog).one()
    assert log.rating == 1
    card = db.session.query(ReviewCard).filter_by(card_type="recall").one()
    assert card.lapses == 1


def test_typed_answer_matches_loosely(empty_db, spanish):
    "Case, surrounding whitespace and zws don't break the check."
    terms = _queue(spanish, card_types=("recognition", "recall", "cloze"))
    terms[0]._text = "ga\u200Bto"  # pylint: disable=protected-access
    db.session.add(terms[0])
    db.session.commit()

    payload = service.start_session(db.session)
    card_view = next(c for c in payload["cards"] if c["card_type"] == "recall")

    result = service.grade(db.session, card_view["id"], 3, typed_answer="  GATO  ")
    assert result["correct"] is True


def test_cloze_front_blanks_the_term(empty_db, spanish):
    "The cloze front has the term blanked out of the sentence."
    _queue(spanish)
    payload = service.start_session(db.session)
    card_view = next(c for c in payload["cards"] if c["card_type"] == "cloze")
    assert "[...]" in card_view["sentence_blank"]
    assert "gato" not in card_view["sentence_blank"].replace("cloze-blank", "")
    assert "<b>gato</b>" in card_view["sentence"]


def test_new_card_daily_cap(empty_db, spanish):
    "review_max_new_per_day caps the new cards in a session."
    _queue(
        spanish,
        terms_text="gato,perro,pajaro",
        book_content="El gato. El perro. El pajaro.",
    )
    repo = UserSettingRepository(db.session)
    repo.set_value("review_max_new_per_day", 2)
    db.session.commit()

    payload = service.start_session(db.session)
    assert len(payload["cards"]) == 2

    # Grade one: today's new quota has 1 left.
    service.grade(db.session, payload["cards"][0]["id"], 3)
    payload2 = service.start_session(db.session)
    new_cards = [c for c in payload2["cards"] if c["reps"] == 0]
    assert len(new_cards) == 1


def test_due_cards_are_not_capped_by_new_quota(empty_db, spanish):
    "A graded learning card comes back when due, regardless of the new cap."
    _queue(spanish)
    payload = service.start_session(db.session)
    service.grade(db.session, payload["cards"][0]["id"], 3)

    # Force the card due again right now.
    card = db.session.query(ReviewCard).filter(ReviewCard.reps > 0).one()
    card.due = _utcnow() - timedelta(minutes=1)
    db.session.add(card)
    db.session.commit()

    repo = UserSettingRepository(db.session)
    repo.set_value("review_max_new_per_day", 0)
    db.session.commit()

    payload2 = service.start_session(db.session)
    assert len(payload2["cards"]) == 1
    assert payload2["cards"][0]["id"] == card.id


def test_counts_report_due_and_new(empty_db, spanish):
    "The index counts distinguish due from new."
    _queue(spanish)
    c = service.counts(db.session)
    assert c["due"] == 0
    assert c["new_remaining"] == 2  # recall is off by default
    assert c["new_allowed_today"] == 20


def test_start_session_needs_fsrs(empty_db, spanish, monkeypatch):
    "Without the fsrs package the session cannot start."

    def boom(_retention):
        raise SchedulerUnavailableError("not installed")

    monkeypatch.setattr(service.scheduler, "load_scheduler", boom)
    _queue(spanish)
    with pytest.raises(SchedulerUnavailableError):
        service.start_session(db.session)


def test_load_scheduler_uses_retention_setting(empty_db, spanish):
    "The desired retention setting reaches the scheduler."
    repo = UserSettingRepository(db.session)
    repo.set_value("review_desired_retention", "0.95")
    db.session.commit()

    val = UserSettingRepository(db.session).get_value("review_desired_retention")
    sched = load_scheduler(val)
    assert abs(sched.desired_retention - 0.95) < 1e-9
