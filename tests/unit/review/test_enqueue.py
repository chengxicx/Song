"""
Review queue tests: auto-admission of learning terms.
"""

from datetime import datetime

from lute.db import db
from lute.models.review import ReviewCard
from lute.review import enqueue

from tests.utils import add_terms, make_book


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


def test_all_learning_statuses_are_admitted(empty_db, spanish):
    "Statuses 1-5 get cards; unknown (0) and well-known (99) don't."
    terms = add_terms(spanish, ["uno", "dos", "tres", "cuatro", "cinco"])
    for term, status in zip(terms, [0, 1, 3, 5, 99]):
        term.status = status
    db.session.add_all(terms)
    db.session.commit()

    counts = enqueue.auto_admit(db.session)

    assert counts["recognition"] == 3
    assert _card_types() == {(t.id, "recognition") for t in terms[1:4]}


def test_re_admission_is_idempotent(empty_db, spanish):
    "Re-admitting never duplicates cards or resets them."
    add_terms(spanish, ["perro"])
    enqueue.auto_admit(db.session)
    assert db.session.query(ReviewCard).count() == 1

    counts = enqueue.auto_admit(db.session)
    assert counts == {"recognition": 0, "cloze": 0}
    assert db.session.query(ReviewCard).count() == 1


def test_cloze_requires_a_read_sentence(empty_db, spanish):
    "Cloze cards only exist for terms seen in read sentences."
    terms = add_terms(spanish, ["gato", "perro"])
    _read_book(spanish, "Tengo un gato. El gato es negro.")

    counts = enqueue.auto_admit(db.session)

    assert counts["cloze"] == 1
    assert _card_types() == {
        (terms[0].id, "recognition"),
        (terms[0].id, "cloze"),
        (terms[1].id, "recognition"),
    }


def test_cloze_added_once_term_is_read(empty_db, spanish):
    "A term with no read sentence gets its cloze card after reading."
    terms = add_terms(spanish, ["gato"])
    enqueue.auto_admit(db.session)
    assert (terms[0].id, "cloze") not in _card_types()

    _read_book(spanish, "Tengo un gato. El gato es negro.")
    counts = enqueue.auto_admit(db.session)
    assert counts["cloze"] == 1
    assert (terms[0].id, "cloze") in _card_types()


def test_disabled_card_types_are_not_admitted(empty_db, spanish):
    "A card type switched off in the settings gets no new cards."
    terms = add_terms(spanish, ["gato"])
    _read_book(spanish, "Tengo un gato.")
    enqueue.set_enabled_card_types(db.session, ["recognition"])

    counts = enqueue.auto_admit(db.session)

    assert counts == {"recognition": 1}
    assert _card_types() == {(terms[0].id, "recognition")}


def test_switching_types_off_keeps_existing_cards(empty_db, spanish):
    "Admission only ever adds: existing cards are never removed."
    terms = add_terms(spanish, ["gato"])
    _read_book(spanish, "Tengo un gato.")
    enqueue.auto_admit(db.session)
    card = db.session.query(ReviewCard).filter_by(card_type="recognition").one()
    card.reps = 2
    card.due = datetime(2027, 1, 2, 3, 4, 5)
    db.session.add(card)
    db.session.commit()

    enqueue.set_enabled_card_types(db.session, [])
    counts = enqueue.auto_admit(db.session)

    assert counts == {}
    assert db.session.query(ReviewCard).count() == 2
    db.session.refresh(card)
    assert card.reps == 2


def test_enabled_card_types_defaults(empty_db, spanish):
    "Missing or garbage settings fall back to both types."
    assert enqueue.enabled_card_types(db.session) == ["recognition", "cloze"]

    from lute.models.repositories import UserSettingRepository

    repo = UserSettingRepository(db.session)
    repo.set_dynamic_value("review_card_types", "not json")
    assert enqueue.enabled_card_types(db.session) == ["recognition", "cloze"]

    enqueue.set_enabled_card_types(db.session, ["cloze"])
    assert enqueue.enabled_card_types(db.session) == ["cloze"]
