"""
Smoke test the full review flow: spec -> sync -> session -> grade.
"""

from datetime import datetime
import os
import re

import pytest

from lute.db import db  # noqa: E402
from lute.models.review import ReviewCard  # noqa: E402
from lute.review import enqueue, service  # noqa: E402

from tests.utils import add_terms, make_book  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_BUILDER_JS = os.path.normpath(
    os.path.join(
        _HERE, "..", "..", "..", "lute", "static", "js", "lute-review-criteria.js"
    )
)


def test_full_review_flow(empty_db, spanish):
    "Sync a spec, run a session, grade a cloze card by typing."
    pytest.importorskip("fsrs")
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
    "The no-fsrs HTTP endpoints wire up: index, session page, sync."
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

    # Session page renders; cards load client-side via /review/start.
    resp = client.get("/review/session")
    assert resp.status_code == 200
    assert b"review_session_container" in resp.data

    # Sync via the endpoint.
    resp = client.post("/review/sync")
    assert resp.status_code == 200
    assert resp.json["cards_added"] == 1


def test_review_grade_routes(empty_db, spanish, client):
    "Start + grade via the endpoints."
    pytest.importorskip("fsrs")

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

    client.post("/review/sync")

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

    # The dropdown builder renders, with its metadata and the default
    # criteria pre-loaded as rows.
    assert b'id="criteria_builder"' in resp.data
    assert b'id="criteria_preset"' in resp.data
    assert b'id="criteria_meta"' in resp.data
    assert b'"field": "status"' in resp.data

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


def test_undo_with_nothing_to_undo_is_a_400(empty_db, spanish, client):
    "The undo endpoint reports 'nothing to undo' instead of 500ing."
    resp = client.post("/review/undo")
    assert resp.status_code == 400
    assert "nothing to undo" in resp.json["error"].lower()


def test_review_settings_page_round_trips(empty_db, spanish, client):
    "The scheduling settings page renders the stored values and saves them."
    from lute.models.repositories import UserSettingRepository

    resp = client.get("/review/settings")
    assert resp.status_code == 200
    assert b"review_settings_form" in resp.data
    # The seeded defaults are shown, not blank fields.
    assert b'value="0.9"' in resp.data
    assert b'value="20"' in resp.data

    resp = client.post(
        "/review/settings",
        data={"review_desired_retention": "0.85", "review_max_new_per_day": "5"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    repo = UserSettingRepository(db.session)
    assert float(repo.get_value("review_desired_retention")) == 0.85
    assert int(repo.get_value("review_max_new_per_day")) == 5

    # Out-of-range values are rejected by the form, not stored.
    resp = client.post(
        "/review/settings",
        data={"review_desired_retention": "2.0", "review_max_new_per_day": "5"},
    )
    assert resp.status_code == 200
    assert float(repo.get_value("review_desired_retention")) == 0.85


def test_review_index_links_to_settings(empty_db, spanish, client):
    "The settings page is reachable from the dashboard."
    resp = client.get("/review/index")
    assert resp.status_code == 200
    assert b'href="/review/settings"' in resp.data


def test_duplicate_spec_name_is_a_form_error(empty_db, spanish, client):
    "A duplicate name is a form error, not an IntegrityError 500."
    from lute.models.review import ReviewSpec

    spec = ReviewSpec()
    spec.name = "taken"
    spec.criteria = ""
    spec.set_card_types(["recognition"])
    spec.active = True
    db.session.add(spec)
    db.session.commit()

    resp = client.post(
        "/review/spec/new",
        data={"name": "taken", "criteria": "", "card_recognition": "y", "active": "y"},
    )
    assert resp.status_code == 200
    assert b"already exists" in resp.data
    assert db.session.query(ReviewSpec).count() == 1


def test_editing_a_spec_keeps_its_own_name(empty_db, spanish, client):
    "Re-saving a spec must not trip its own uniqueness check."
    from lute.models.review import ReviewSpec

    spec = ReviewSpec()
    spec.name = "mine"
    spec.criteria = ""
    spec.set_card_types(["recognition"])
    spec.active = True
    db.session.add(spec)
    db.session.commit()

    resp = client.post(
        f"/review/spec/edit/{spec.id}",
        data={"name": "mine", "criteria": "", "card_recognition": "y", "active": "y"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert db.session.query(ReviewSpec).one().name == "mine"


def test_criteria_the_builder_cannot_show_are_saved_verbatim(empty_db, spanish, client):
    "Mixed and/or falls back to the raw textarea and is stored unchanged."
    from lute.models.review import ReviewSpec

    mixed = 'status >= 2 and tags:["a"] or language == "Spanish"'
    resp = client.post(
        "/review/spec/new",
        data={
            "name": "mixed",
            "criteria": mixed,
            "card_recognition": "y",
            "active": "y",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert db.session.query(ReviewSpec).one().criteria == mixed

    # And the edit page renders it in raw mode (builder_rows is null,
    # which is what the template keys the 'open the textarea' on).
    spec = db.session.query(ReviewSpec).one()
    resp = client.get(f"/review/spec/edit/{spec.id}")
    assert resp.status_code == 200
    assert b'id="criteria_initial">null</script>' in resp.data


def test_spec_form_provides_every_element_the_builder_needs(empty_db, spanish, client):
    """
    The spec form and the builder JS are a contract.

    Every id the JS looks up must exist in the rendered page.  Without
    this check, renaming or dropping one leaves the builder dead in the
    real app while every JS unit test still passes -- those run against
    their own stub DOM and cannot see a template change at all.
    """
    with open(_BUILDER_JS, encoding="utf-8") as f:
        js = f.read()

    wanted = set(re.findall(r'getElementById\("([^"]+)"\)', js))
    assert wanted, "the builder no longer looks up any element by id"

    html = client.get("/review/spec/new").data.decode("utf-8")
    missing = sorted(i for i in wanted if f'id="{i}"' not in html)
    assert not missing, f"the spec form is missing builder elements: {missing}"

    # The page must load the builder and its stylesheet too; base.html
    # includes both, and moving either would kill the builder silently.
    assert "lute-review-criteria.js" in html
    assert "review.css" in html
