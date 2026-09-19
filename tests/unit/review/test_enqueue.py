"""
Review queue tests: enqueue from specs.
"""

from datetime import datetime

import pytest

from lute.db import db
from lute.models.review import ReviewCard, ReviewSpec
from lute.review import enqueue

from tests.utils import add_terms, make_book


def _make_spec(
    name, criteria="", card_types=("recognition", "recall", "cloze"), active=True
):
    "Add a spec."
    spec = ReviewSpec()
    spec.name = name
    spec.criteria = criteria
    spec.set_card_types(list(card_types))
    spec.active = active
    db.session.add(spec)
    db.session.commit()
    return spec


def _card_types():
    "All queued (term_id, card_type) pairs."
    return set(db.session.query(ReviewCard.term_id, ReviewCard.card_type).all())


def _read_book(spanish, content):
    "Make a book whose text is read (so its sentences are searchable)."
    b = make_book("Book", content, spanish)
    b.texts[0].read_date = datetime.now()
    db.session.add(b)
    db.session.commit()
    return b


def test_default_card_types_are_recognition_and_cloze(empty_db):
    "Recall (typing) is opt-in."
    spec = ReviewSpec()
    assert spec.card_types_enabled == ["recognition", "cloze"]


def test_sync_dry_run_writes_nothing(empty_db, spanish):
    "Default sync is a dry run."
    add_terms(spanish, ["perro", "gato"])
    _make_spec("all")
    result = enqueue.run_sync()
    assert result.committed is False
    assert result.cards_added == 4
    assert len(_card_types()) == 0


def test_sync_adds_cards_for_matching_terms(empty_db, spanish):
    "Criteria filter the candidates; inactive specs do nothing."
    terms = add_terms(spanish, ["perro", "gato"])
    terms[1].status = 2
    db.session.add(terms[1])
    db.session.commit()

    _make_spec("learning", "status > 1")
    _make_spec("inactive", active=False)

    result = enqueue.run_sync(commit=True)
    assert result.cards_added == 2
    assert _card_types() == {(terms[1].id, "recognition"), (terms[1].id, "recall")}


def test_resync_is_idempotent(empty_db, spanish):
    "Re-syncing never duplicates cards or resets them."
    add_terms(spanish, ["perro"])
    _make_spec("all")
    enqueue.run_sync(commit=True)
    cards = db.session.query(ReviewCard).all()
    assert len(cards) == 2

    result = enqueue.run_sync(commit=True)
    assert result.cards_added == 0
    assert db.session.query(ReviewCard).count() == 2


def test_cloze_requires_a_read_sentence(empty_db, spanish):
    "Cloze cards only exist for terms seen in read sentences."
    terms = add_terms(spanish, ["gato", "perro"])
    _read_book(spanish, "Tengo un gato. El gato es negro.")

    _make_spec("cloze-only", card_types=("cloze",))
    result = enqueue.run_sync(commit=True)

    assert result.cards_added == 1
    assert result.skipped_no_sentence == 1
    assert _card_types() == {(terms[0].id, "cloze")}


def test_blank_criteria_matches_all_learning_terms(empty_db, spanish):
    "Blank criteria admit everything; other statuses stay out."
    terms = add_terms(spanish, ["perro"])
    terms[0].status = 99  # Well known.
    db.session.add(terms[0])
    db.session.commit()
    _make_spec("all")

    result = enqueue.run_sync(commit=True)
    assert result.cards_added == 0


def test_invalid_criteria_is_reported_per_spec(empty_db, spanish):
    "A broken spec doesn't block the others."
    add_terms(spanish, ["perro"])
    _make_spec("broken", "status >")
    _make_spec("good", "status > 0")

    result = enqueue.run_sync(commit=True)
    assert "broken" in result.errors
    assert result.cards_added == 2


def test_format_report_mentions_skips(empty_db, spanish):
    "The report shows skipped cloze cards and dry-run state."
    add_terms(spanish, ["perro"])
    _make_spec("cloze-only", card_types=("cloze",))
    result = enqueue.run_sync()
    report = enqueue.format_report(result)
    assert "DRY RUN" in report
    assert "no read sentence" in report
