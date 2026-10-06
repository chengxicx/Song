"""
Review shadowing cards: admission, payload, daily cap, score logging.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fsrs")

from unittest.mock import patch  # noqa: E402

from lute.db import db  # noqa: E402
from lute.models.repositories import UserSettingRepository  # noqa: E402
from lute.models.review import ReviewCard, ReviewLog  # noqa: E402
from lute.read import shadowing as read_shadowing  # noqa: E402
from lute.review import enqueue, service  # noqa: E402

from tests.utils import add_terms, make_book  # noqa: E402


def _read_book(spanish, content):
    "Make a read book so its sentences are searchable."
    b = make_book("Book", content, spanish)
    b.texts[0].read_date = datetime.now()
    db.session.add(b)
    db.session.commit()
    return b


def _utcnow():
    "Naive UTC now (how the db stores datetimes)."
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _enabled(types):
    enqueue.set_enabled_card_types(db.session, types)


def test_shadowing_is_opt_in(empty_db, spanish):
    "New installs keep recognition + cloze; shadowing must be enabled."
    terms = add_terms(spanish, ["gato"])
    _read_book(spanish, "Tengo un gato.")

    counts = enqueue.auto_admit(db.session)

    assert counts == {"recognition": 1, "cloze": 1}
    types = {
        ct for (_tid, ct) in db.session.query(ReviewCard.term_id, ReviewCard.card_type)
    }
    assert "shadowing" not in types
    assert terms[0].text == "gato"


def test_shadowing_requires_a_read_sentence(empty_db, spanish):
    "Like cloze, a shadowing card only exists for a term in a read sentence."
    add_terms(spanish, ["gato", "perro"])
    _read_book(spanish, "Tengo un gato. El perro es negro.")
    _enabled(["recognition", "cloze", "shadowing"])

    counts = enqueue.auto_admit(db.session)

    assert counts["shadowing"] == 2
    shadow_terms = [
        card.term.text
        for card in db.session.query(ReviewCard).filter_by(card_type="shadowing")
    ]
    assert sorted(shadow_terms) == ["gato", "perro"]


def test_shadowing_card_payload_has_tokens_and_flags(empty_db, spanish):
    "The card carries the server-side tokens and the engine availability."
    add_terms(spanish, ["gato"])
    _read_book(spanish, "Tengo un gato. El gato es negro.")
    _enabled(["recognition", "cloze", "shadowing"])
    enqueue.auto_admit(db.session)

    with patch.object(read_shadowing, "transcription_available", return_value=True):
        payload = service.start_session(db.session)

    view = next(c for c in payload["cards"] if c["card_type"] == "shadowing")
    assert view["language_id"] == spanish.id
    # The <b></b> wrapper is display markup; the tokens are its words.
    assert "<b>" not in view["sentence_plain"]
    assert "gato" in view["sentence_tokens"]
    assert view["transcribe_available"] is True


def test_shadowing_card_flags_missing_engine(empty_db, spanish):
    "Without an engine the card degrades to read-and-self-grade."
    add_terms(spanish, ["gato"])
    _read_book(spanish, "Tengo un gato.")
    _enabled(["recognition", "cloze", "shadowing"])
    enqueue.auto_admit(db.session)

    with patch.object(read_shadowing, "transcription_available", return_value=False):
        payload = service.start_session(db.session)

    view = next(c for c in payload["cards"] if c["card_type"] == "shadowing")
    assert view["transcribe_available"] is False
    # The card is still served: self-grading it is legitimate.
    assert any(c["card_type"] == "shadowing" for c in payload["cards"])


def test_shadowing_daily_cap(empty_db, spanish):
    "The per-day shadowing cap bounds due and new shadowing cards."
    terms = add_terms(spanish, ["gato", "perro", "pajaro"])
    _read_book(spanish, "El gato. El perro. El pajaro.")
    _enabled(["recognition", "cloze", "shadowing"])
    enqueue.auto_admit(db.session)

    # Recognize the terms once, so the shadowing cards become due cards.
    payload = service.start_session(db.session)
    for view in [c for c in payload["cards"] if c["card_type"] == "recognition"]:
        service.grade(db.session, view["id"], 1)  # Again -> due soon
        card = db.session.get(ReviewCard, view["id"])
        card.due = _utcnow() - timedelta(minutes=1)
        db.session.add(card)
    db.session.commit()

    repo = UserSettingRepository(db.session)
    repo.set_value("review_max_shadowing_per_day", 1)
    db.session.commit()

    payload2 = service.start_session(db.session)
    shadow_served = [c for c in payload2["cards"] if c["card_type"] == "shadowing"]
    assert len(shadow_served) == 1
    # Other card types are untouched by the shadowing cap.
    assert any(c["card_type"] == "recognition" for c in payload2["cards"])
    assert terms[0].text == "gato"


def test_grade_records_the_shadowing_score(empty_db, spanish):
    "The take score rides along on the review log for the stats page."
    add_terms(spanish, ["gato"])
    _read_book(spanish, "Tengo un gato.")
    _enabled(["recognition", "cloze", "shadowing"])
    enqueue.auto_admit(db.session)

    payload = service.start_session(db.session)
    view = next(c for c in payload["cards"] if c["card_type"] == "shadowing")

    service.grade(db.session, view["id"], 3, shadowing_score=87)

    log = db.session.query(ReviewLog).one()
    data = json.loads(log.data)
    assert data["shadowing_score"] == 87
    assert data["before"], "the undo snapshot is still there"


def test_grade_ignores_a_broken_shadowing_score(empty_db, spanish):
    "Out-of-range or non-numeric scores are dropped, not stored."
    add_terms(spanish, ["gato"])
    _read_book(spanish, "Tengo un gato.")
    _enabled(["recognition", "cloze", "shadowing"])
    enqueue.auto_admit(db.session)

    payload = service.start_session(db.session)
    view = next(c for c in payload["cards"] if c["card_type"] == "shadowing")

    service.grade(db.session, view["id"], 3, shadowing_score=4000)
    log = db.session.query(ReviewLog).one()
    assert "shadowing_score" not in json.loads(log.data)
