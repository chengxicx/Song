"""
Smoke test the full review flow: spec -> sync -> session -> grade.
"""

from datetime import datetime

import pytest

pytest.importorskip("fsrs")

from lute.db import db  # noqa: E402
from lute.models.review import ReviewCard  # noqa: E402
from lute.review import enqueue, service  # noqa: E402

from tests.utils import add_terms, make_book  # noqa: E402


def test_full_review_flow(empty_db, spanish):
    "Sync a spec, run a session, grade a cloze card by typing."
    terms = add_terms(spanish, ["gato"])
    terms[0].translation = "cat"
    db.session.add(terms[0])
    db.session.commit()

    b = make_book("Book", "Tengo un gato. El gato es negro.", spanish)
    b.texts[0].read_date = datetime.now()
    db.session.add(b)
    db.session.commit()

    from lute.models.review import ReviewSpec

    spec = ReviewSpec()
    spec.name = "ja-ish"
    spec.criteria = 'status > 0 and language == "Spanish"'
    spec.set_card_types(["recognition", "cloze"])
    spec.active = True
    db.session.add(spec)
    db.session.commit()

    result = enqueue.run_sync(commit=True)
    assert result.cards_added == 2
    assert db.session.query(ReviewCard).count() == 2

    payload = service.start_session(db.session)
    assert len(payload["cards"]) == 2

    cloze = next(c for c in payload["cards"] if c["card_type"] == "cloze")
    assert "[...]" in cloze["sentence_blank"]

    graded = service.grade(db.session, cloze["id"], 3, typed_answer="gato")
    assert graded["correct"] is True

    card = db.session.query(ReviewCard).filter_by(card_type="cloze").one()
    assert card.reps == 1
    assert card.state != ReviewCard.STATE_NEW


def test_review_routes(empty_db, spanish, client):
    "The HTTP endpoints wire up: index page, sync, start, grade."
    terms = add_terms(spanish, ["perro"])
    db.session.add(terms[0])
    db.session.commit()

    from lute.models.review import ReviewSpec

    spec = ReviewSpec()
    spec.name = "dogs"
    spec.criteria = 'language == "Spanish"'
    spec.set_card_types(["recognition"])
    spec.active = True
    db.session.add(spec)
    db.session.commit()

    # Index page renders.
    resp = client.get("/review/index")
    assert resp.status_code == 200
    assert b"Review" in resp.data

    # Sync via the endpoint.
    resp = client.post("/review/sync")
    assert resp.status_code == 200
    assert resp.json["cards_added"] == 1

    # Start + grade via the endpoints.
    resp = client.post("/review/start")
    assert resp.status_code == 200
    cards = resp.json["cards"]
    assert len(cards) == 1
    card_id = cards[0]["id"]

    resp = client.post("/review/grade", json={"card_id": card_id, "rating": 3})
    assert resp.status_code == 200
    assert resp.json["correct"] is True
    assert db.session.query(ReviewCard).filter_by(id=card_id).one().reps == 1


def test_start_without_fsrs_returns_400(empty_db, spanish, client, monkeypatch):
    "A missing scheduler is a 400 with needs_fsrs, not a 500."
    from lute.review import scheduler as sched_mod

    terms = add_terms(spanish, ["perro"])
    db.session.add(terms[0])
    db.session.commit()

    from lute.models.review import ReviewSpec

    spec = ReviewSpec()
    spec.name = "dogs"
    spec.criteria = ""
    spec.set_card_types(["recognition"])
    spec.active = True
    db.session.add(spec)
    db.session.commit()

    def boom(_retention):
        raise sched_mod.SchedulerUnavailableError("The fsrs package is not installed.")

    monkeypatch.setattr(sched_mod, "load_scheduler", boom)

    resp = client.post("/review/start")
    assert resp.status_code == 400
    assert resp.json["needs_fsrs"] is True


def test_spec_form_flow(empty_db, spanish, client):
    "The spec form pages render and a post saves the spec."
    resp = client.get("/review/spec/new")
    assert resp.status_code == 200
    assert b"review_spec_form" in resp.data

    resp = client.post(
        "/review/spec/new",
        data={
            "name": "ja words",
            "criteria": 'status > 1 and language == "Spanish"',
            "card_recognition": "y",
            "card_cloze": "y",
            "active": "y",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    from lute.models.review import ReviewSpec

    spec = db.session.query(ReviewSpec).one()
    assert spec.name == "ja words"
    assert spec.criteria == 'status > 1 and language == "Spanish"'
    assert spec.card_types_enabled == ["recognition", "cloze"]

    # Edit page renders the saved spec.
    resp = client.get(f"/review/spec/edit/{spec.id}")
    assert resp.status_code == 200

    # Bad criteria surface a form error, not a 500.
    resp = client.post(
        "/review/spec/new",
        data={"name": "bad", "criteria": "status >", "active": "y"},
    )
    assert resp.status_code == 200
    assert b"Criteria syntax error" in resp.data

    # Delete removes the spec; queued cards stay (additive sync).
    resp = client.get(f"/review/spec/delete/{spec.id}", follow_redirects=True)
    assert resp.status_code == 200
    assert db.session.query(ReviewSpec).count() == 0
