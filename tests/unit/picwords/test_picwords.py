"""
PicWords plugin: dataset integrity and one end-to-end answer.

The plugin is an editable install (plugins/lute-picwords); these tests
make sure its generated dataset is present and that a picture answer
really creates/updates a normal Term and its recognition ReviewCard.
"""

import re

import pytest

pytest.importorskip("lute_picwords")

from lute.db import db  # noqa: E402
from lute.models.review import ReviewCard  # noqa: E402
from lute.models.term import Term  # noqa: E402

from lute_picwords import service, vocab  # noqa: E402


def _catalog_entry(key):
    "The dataset entry for one language, independent of catalog order."
    for entry in vocab.load_catalog()["languages"]:
        if entry.get("key") == key:
            return entry
    raise AssertionError(f"catalog has no {key!r} entry")


def test_dataset_has_words_and_images():
    "The shipped catalog loads, and every image file exists."
    catalog = vocab.load_catalog()
    assert catalog["languages"], "catalog.json has no languages"
    entry = _catalog_entry("ja")
    dataset = vocab.load_dataset(entry["dataset"])

    assert dataset["total"] == len(dataset["words"])
    assert dataset["total"] > 1000
    assert set(dataset["levels"]) == {"N5", "N4", "N3", "N2", "N1"}
    images = set()
    for word in dataset["words"]:
        assert word["text"]
        assert word["reading"]
        assert word["meaning"]
        if word.get("pending_image"):
            continue
        assert vocab.image_file(word["image"]) is not None
        images.add(word["image"])
    # Curated extras may legitimately share an image (起きる/起こす), so
    # the image count is a lower bound, not an exact one.
    assert 1 <= len(images) <= len(dataset["words"])


def test_dataset_has_unique_headwords():
    "One Term per language means one card per headword."
    entry = _catalog_entry("ja")
    dataset = vocab.load_dataset(entry["dataset"])
    texts = [word["text"] for word in dataset["words"]]
    assert len(texts) == len(set(texts))


def test_worker_words_have_chinese_meanings():
    "Worker-added cards must not fall back to English glosses."
    entry = _catalog_entry("ja")
    dataset = vocab.load_dataset(entry["dataset"])
    worker_words = [word for word in dataset["words"] if word.get("worker")]
    assert worker_words
    for word in worker_words:
        assert re.search(r"[\u4e00-\u9fff]", word["meaning"]), word["text"]


def test_answer_creates_term_and_grades_recognition_card(empty_db, japanese):
    "A first answer writes the term, its tags, and an FSRS grade."
    state = service.state(db.session, japanese.id)
    assert state["language_id"] == japanese.id
    assert state["dataset"]["total"] > 1000
    assert state["bands"]
    assert state["recognition_enabled"] is True

    session_payload = service.build_session(db.session, japanese.id, "N5")
    assert session_payload["steps"]
    intro = next(step for step in session_payload["steps"] if step["kind"] == "intro")
    word = intro["word"]

    result = service.apply_result(
        db.session,
        japanese.id,
        word["text"],
        level=word["level"],
        correct=True,
        attempts=1,
        elapsed_ms=1800,
        kind="picture",
    )
    assert result["status"] >= 4
    assert result["was_new"] is True

    term = (
        db.session.query(Term)
        .filter(Term.language_id == japanese.id, Term._text == word["text"])
        .first()
    )
    assert term is not None
    assert term.translation == word["meaning"]
    assert term.romanization == word["reading"]
    assert "picwords" in {tag.text for tag in term.term_tags}

    card = (
        db.session.query(ReviewCard)
        .filter(
            ReviewCard.term_id == term.id,
            ReviewCard.card_type == "recognition",
        )
        .first()
    )
    assert card is not None
    assert card.reps == 1
    assert card.due is not None

    after = service.state(db.session, japanese.id)
    assert after["counts"]["introduced"] >= 1
    assert after["new_today"] >= 1


def test_curated_verb_is_learnable(empty_db, japanese):
    "The hand-curated verb supplement goes through the same write-back."
    dataset = vocab.load_dataset("ja.json")
    word = vocab.find_word(dataset, "食べる")
    assert word is not None
    assert word.get("extra") is True

    result = service.apply_result(
        db.session,
        japanese.id,
        word["text"],
        level=word["level"],
        correct=True,
        attempts=1,
        elapsed_ms=1500,
        kind="picture",
    )
    assert result["status"] >= 4

    term = (
        db.session.query(Term)
        .filter(Term.language_id == japanese.id, Term._text == word["text"])
        .first()
    )
    assert term is not None
    assert term.translation == "吃"
