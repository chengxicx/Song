"""
Term deletion must clean up the term's review cards and logs.

reviewcards -> words and reviewlogs -> reviewcards carry no ON DELETE
CASCADE clause, so without the before_delete purge in
lute.models.review, deleting any term that has a card fails with an
IntegrityError (observed in production as a 500 from the Delete
button on the term form / reading screen).
"""

from datetime import datetime

from lute.db import db
from lute.models.review import ReviewCard, ReviewLog
from lute.models.term import Term
from lute.review import enqueue
from lute.term.model import Repository

from tests.utils import add_terms


def _make_term_with_card_and_log(spanish):
    "One learning term, auto-admitted, with a logged review."
    terms = add_terms(spanish, ["perro"])
    enqueue.auto_admit(db.session)
    card = db.session.query(ReviewCard).filter_by(term_id=terms[0].id).first()
    assert card is not None, "setup: expected an auto-admitted card"
    db.session.add(
        ReviewLog(
            card_id=card.id,
            review_time=datetime.now(),
            rating=3,
            reps_before=0,
        )
    )
    db.session.commit()
    return terms[0].id


def test_deleting_term_with_card_and_log_succeeds(empty_db, spanish):
    "The Delete-button path: purge review rows, no IntegrityError."
    term_id = _make_term_with_card_and_log(spanish)

    repo = Repository(db.session)
    repo.delete(repo.load(term_id))
    repo.commit()

    assert db.session.query(ReviewCard).count() == 0
    assert db.session.query(ReviewLog).count() == 0
    assert db.session.get(Term, term_id) is None


def test_deleting_term_without_cards_still_works(empty_db, spanish):
    "Plain terms delete as before."
    term_id = add_terms(spanish, ["gato"])[0].id

    repo = Repository(db.session)
    repo.delete(repo.load(term_id))
    repo.commit()

    assert db.session.query(ReviewCard).count() == 0
    assert db.session.query(ReviewLog).count() == 0
    assert db.session.get(Term, term_id) is None
