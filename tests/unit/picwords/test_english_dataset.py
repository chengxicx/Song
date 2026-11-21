"""
English is the Word Atlas base language for the shared image catalog.

The dataset is generated from Word Atlas, so it has one image per word
and CEFR levels rather than JLPT bands.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lute_picwords")

from lute.db import db  # noqa: E402
from lute_picwords import service, vocab  # noqa: E402

ATLAS_ROOT = Path("/Users/cxi/Documents/word-atlas-main")
CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
_ATLAS = pytest.mark.skipif(
    not (ATLAS_ROOT / "public" / "atlas" / "img").is_dir(),
    reason="Word Atlas checkout is not available",
)


def _english_entry():
    for entry in vocab.load_catalog()["languages"]:
        if entry.get("key") == "en":
            return entry
    raise AssertionError("catalog has no English entry")


def test_english_dataset_is_registered_and_complete():
    entry = _english_entry()
    assert entry["dataset"] == "en.json"
    assert entry["levels"] == CEFR_LEVELS
    # English uses Lute's spacedel parser, so matching by parser type
    # would also capture French/German/etc.  Match by language name only.
    assert entry["parser_types"] == []

    dataset = vocab.load_dataset(entry["dataset"])
    assert dataset["total"] == len(dataset["words"]) == 7298
    assert dataset["levels"] == CEFR_LEVELS
    texts = [word["text"] for word in dataset["words"]]
    assert len(texts) == len(set(texts))
    for word in dataset["words"]:
        assert word["reading"]
        assert word["meaning"]
        assert word["image"] == word["id"]
        assert word["images"][0]["file"] == f"shared/{word['id']}.webp"


@_ATLAS
def test_every_english_word_has_a_word_atlas_image():
    entry = _english_entry()
    dataset = vocab.load_dataset(entry["dataset"])
    images = ATLAS_ROOT / "public" / "atlas" / "img"
    missing = [
        word["image"]
        for word in dataset["words"]
        if not (images / f"{word['image']}.webp").is_file()
    ]
    assert missing == []


def test_english_payload_uses_the_shared_word_atlas_image():
    dataset = vocab.load_dataset("en.json")
    word = vocab.find_word(dataset, "library")
    payload = service._word_payload(word, dataset)  # pylint: disable=protected-access
    assert payload["image"] == "library"
    assert payload["image_source"] == "word-atlas"
    assert payload["images"] == ["library"]
    assert payload["meaning"]
    assert payload["level"] == "A1"


def test_english_session_uses_cefr_bands(empty_db, english):
    state = service.state(db.session, english.id)
    assert state["dataset"]["key"] == "en"
    assert state["dataset"]["levels"] == CEFR_LEVELS
    assert state["level"] == "A1"
    assert [band["level"] for band in state["bands"]] == CEFR_LEVELS

    session = service.build_session(db.session, english.id, "A1")
    assert session["steps"]
    words = [step["word"] for step in session["steps"] if "word" in step]
    assert words
    assert all(word["level"] == "A1" for word in words)


def test_english_catalog_does_not_match_other_spacedel_languages():
    class _Language:
        name = "French"
        parser_type = "spacedel"

    assert vocab.catalog_entry_for(_Language()) is None
