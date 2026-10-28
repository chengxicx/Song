"""
Smoke test the full review flow: auto-admit -> session -> grade.
"""

from datetime import datetime
import re

import pytest

from lute.db import db  # noqa: E402
from lute.models.review import ReviewCard  # noqa: E402
from lute.review import service  # noqa: E402

from tests.utils import add_terms, make_book  # noqa: E402


def review_settings_form_data(**overrides):
    """
    A complete review-settings POST payload, for tests to override.

    Every numeric field on ReviewSettingsForm is InputRequired, and the
    route writes nothing unless the whole form validates -- so a POST
    that omits one field saves nothing and still comes back 200 with the
    page re-rendered, which reads as a round-trip that worked.  Start
    from the full set so adding a field to the form cannot quietly turn
    an existing test into one that asserts nothing.
    """
    data = {
        "review_desired_retention": "0.9",
        "review_max_new_per_day": "20",
        "review_max_shadowing_per_day": "10",
    }
    data.update(overrides)
    return data


def test_full_review_flow(empty_db, spanish):
    "Open a session, run it, grade a cloze card by typing."
    pytest.importorskip("fsrs")
    terms = add_terms(spanish, ["gato"])
    terms[0].translation = "cat"
    db.session.add(terms[0])
    db.session.commit()

    b = make_book("Book", "Tengo un gato. El gato es negro.", spanish)
    b.texts[0].read_date = datetime.now()
    db.session.add(b)
    db.session.commit()

    # No sync step: the session start admits the cards itself.
    payload = service.start_session(db.session)
    assert len(payload["cards"]) == 2
    assert db.session.query(ReviewCard).count() == 2

    cloze = next(c for c in payload["cards"] if c["card_type"] == "cloze")
    assert "[...]" in cloze["sentence_blank"]

    graded = service.grade(db.session, cloze["id"], 3, typed_answer="gato")
    assert graded["correct"] is True

    card = db.session.query(ReviewCard).filter_by(card_type="cloze").one()
    assert card.reps == 1
    assert card.state != ReviewCard.STATE_NEW


def test_review_routes(empty_db, spanish, client):
    "The no-fsrs HTTP endpoints wire up: index auto-admits, session page."
    terms = add_terms(spanish, ["perro"])
    db.session.add(terms[0])
    db.session.commit()

    # Index page renders and auto-admits the learning term's card.
    resp = client.get("/review/index")
    assert resp.status_code == 200
    assert b"Review" in resp.data
    assert db.session.query(ReviewCard).filter_by(card_type="recognition").count() == 1

    # Session page renders; cards load client-side via /review/start.
    resp = client.get("/review/session")
    assert resp.status_code == 200
    assert b"review_session_container" in resp.data


def test_review_grade_routes(empty_db, spanish, client):
    "Start + grade via the endpoints."
    pytest.importorskip("fsrs")

    terms = add_terms(spanish, ["perro"])
    db.session.add(terms[0])
    db.session.commit()

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

    def boom(_retention):
        raise sched_mod.SchedulerUnavailableError("The fsrs package is not installed.")

    monkeypatch.setattr(sched_mod, "load_scheduler", boom)

    resp = client.post("/review/start")
    assert resp.status_code == 400
    assert resp.json["needs_fsrs"] is True


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
    # Both card types default to on.
    assert b'name="card_recognition"' in resp.data
    assert b'name="card_cloze"' in resp.data

    resp = client.post(
        "/review/settings",
        data=review_settings_form_data(
            review_desired_retention="0.85",
            review_max_new_per_day="5",
            card_cloze="y",  # recognition unchecked
        ),
        follow_redirects=True,
    )
    assert resp.status_code == 200

    repo = UserSettingRepository(db.session)
    assert float(repo.get_value("review_desired_retention")) == 0.85
    assert int(repo.get_value("review_max_new_per_day")) == 5

    from lute.review import enqueue

    assert enqueue.enabled_card_types(db.session) == ["cloze"]

    # The saved switches render back as checkbox state.
    resp = client.get("/review/settings")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")

    def input_tag(name):
        return re.search(rf'<input[^>]*name="{name}"[^>]*>', html).group(0)

    assert "checked" in input_tag("card_cloze")
    assert "checked" not in input_tag("card_recognition")

    # Out-of-range values are rejected by the form, not stored.  The
    # rest of the payload is valid, so the rejection can only be the
    # retention -- an incomplete POST would fail validation for a reason
    # that has nothing to do with what this asserts.
    resp = client.post(
        "/review/settings",
        data=review_settings_form_data(
            review_desired_retention="2.0", review_max_new_per_day="5"
        ),
    )
    assert resp.status_code == 200
    assert float(repo.get_value("review_desired_retention")) == 0.85


def test_review_index_links_to_settings(empty_db, spanish, client):
    "The settings page is reachable from the dashboard."
    resp = client.get("/review/index")
    assert resp.status_code == 200
    assert b'href="/review/settings"' in resp.data


def test_review_index_links_to_the_stats_section(empty_db, spanish, client):
    "The dashboard points at the review statistics on the stats page."
    resp = client.get("/review/index")
    assert resp.status_code == 200
    assert b'href="/stats#review"' in resp.data


def test_stats_page_renders_the_review_section(empty_db, spanish, client):
    "The review block renders on /stats and carries its anchor and canvases."
    resp = client.get("/stats/")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert 'id="review"' in html
    assert 'id="review-cards"' in html
    for canvas in (
        "reviewVolumeChart",
        "reviewRatingChart",
        "reviewCurveChart",
        "reviewGroupChart",
    ):
        assert canvas in html, canvas
    assert "/stats/review_data" in html


def test_review_data_endpoint_with_no_reviews(empty_db, spanish, client):
    "The endpoint answers with the full shape before anything is graded."
    resp = client.get("/stats/review_data?period=7days&lang_id=all")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["has_data"] is False
    assert set(data["summary"]) >= {"reviews", "on_time_rate", "retention"}


def test_review_data_endpoint_reports_a_real_grade(empty_db, spanish, client):
    "A card graded through the API shows up in the stats endpoint."
    pytest.importorskip("fsrs")

    terms = add_terms(spanish, ["perro"])
    db.session.add(terms[0])
    db.session.commit()

    card_id = client.post("/review/start").json["cards"][0]["id"]
    client.post("/review/grade", json={"card_id": card_id, "rating": 3})

    data = client.get("/stats/review_data?period=today&lang_id=all").get_json()
    assert data["has_data"] is True
    assert data["summary"]["reviews"] == 1
    assert data["summary"]["retention"] == 1.0
    assert data["summary"]["on_time"] == 1
    assert data["summary"]["no_timing"] == 0
    assert data["ratings"]["good"] == 1


def test_review_speak_cards_setting_round_trips(empty_db, spanish, client):
    "The card-pronunciation switch saves, and reaches the session page."
    from lute.models.repositories import UserSettingRepository

    resp = client.get("/review/settings")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "Speak each card" in html
    box = re.search(r'<input[^>]*name="review_speak_cards"[^>]*>', html).group(0)
    assert "checked" in box, "cards should be spoken by default"

    # Unticking it is simply an absent field in the POST.
    resp = client.post(
        "/review/settings",
        data=review_settings_form_data(),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    stored = UserSettingRepository(db.session).get_value("review_speak_cards")
    assert str(stored) in ("0", "False"), stored

    # lute-review.js reads it out of the page's user settings.
    resp = client.get("/review/session")
    assert resp.status_code == 200
    assert b'"review_speak_cards": false' in resp.data
