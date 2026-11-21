"""
Song term images in PicWords.

A Term has one user-chosen image; the picture deck must show it instead
of the dataset image, and the card must be able to replace or clear it.
"""

from PIL import Image
import pytest

pytest.importorskip("lute_picwords")

from lute.db import db  # noqa: E402
from lute.models.term import Term  # noqa: E402

from lute_picwords import service, vocab  # noqa: E402


class _FakeTerm:
    "The bits of a loaded business Term that _word_payload reads."

    def __init__(self, filename="", translation="猫"):
        self.translation = translation
        self.language_id = 7
        self.current_image = filename

    def get_current_image(self):
        return self.current_image


def _dataset_word():
    "A dataset word that has at least one picture."
    dataset = vocab.load_dataset("ja.json")
    for word in dataset["words"]:
        if word.get("image"):
            return word
    raise AssertionError("dataset has no illustrated word")


def _use_tmp_userimages(app, monkeypatch, tmp_path):
    root = tmp_path / "userimages"
    root.mkdir()
    monkeypatch.setattr(app.env_config.base_config, "userimagespath", str(root))
    return root


def _save_jpeg(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (20, 120, 200)).save(path, "JPEG")


def test_page_boot_exposes_the_image_endpoint(client):
    response = client.get("/picwords/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'id="pw-image-modal"' in page
    assert "/picwords/api/image" in page


def test_word_payload_prefers_the_term_image():
    word = _dataset_word()
    dataset = vocab.load_dataset("ja.json")

    without = service._word_payload(  # pylint: disable=protected-access
        word, dataset, _FakeTerm("")
    )
    assert without["image_source"] == vocab.clean(
        (vocab.images_for(word)[0] or {}).get("source") or ""
    )
    assert without["user_image"] == ""
    assert without["image_url"] == ""

    chosen = service._word_payload(  # pylint: disable=protected-access
        word, dataset, _FakeTerm("chosen.jpg")
    )
    assert chosen["image_source"] == "user"
    assert chosen["user_image"] == "chosen.jpg"
    assert chosen["image_url"] == "/userimages/7/chosen.jpg"
    assert chosen["image_thumb_url"] == "/userimages/7/thumb/chosen.jpg"
    # The dataset image id stays available as the fallback after clearing.
    assert chosen["image"] == word["image"]


def test_set_term_image_creates_unstudied_term_then_clears(empty_db, japanese):
    word = _dataset_word()
    result = service.set_term_image(db.session, japanese.id, word["text"], "chosen.jpg")
    assert result["filename"] == "chosen.jpg"
    assert result["image_source"] == "user"
    assert result["user_image"] == "chosen.jpg"

    term = (
        db.session.query(Term)
        .filter(Term.language_id == japanese.id, Term._text == word["text"])
        .one()
    )
    assert term.status == 0
    assert term.get_current_image() == "chosen.jpg"
    assert term.translation == word["meaning"]
    assert term.romanization == word["reading"]

    practice = service.build_session(
        db.session, japanese.id, word["level"], texts=[word["text"]]
    )
    card = practice["steps"][0]["word"]
    assert card["image_source"] == "user"
    assert card["user_image"] == "chosen.jpg"
    assert card["image_thumb_url"] == f"/userimages/{japanese.id}/thumb/chosen.jpg"

    kept = service.set_term_image(db.session, japanese.id, word["text"], "other.jpg")
    assert kept["filename"] == "other.jpg"
    assert term.status == 0
    assert term.get_current_image() == "other.jpg"

    cleared = service.set_term_image(db.session, japanese.id, word["text"], "")
    assert cleared["filename"] == ""
    assert cleared["user_image"] == ""
    assert term.get_current_image() is None
    assert cleared["image_source"] == vocab.clean(
        (vocab.images_for(word)[0] or {}).get("source") or ""
    )


def test_clearing_without_a_term_does_not_create_one(empty_db, japanese):
    word = _dataset_word()
    result = service.set_term_image(db.session, japanese.id, word["text"], "")
    assert result["filename"] == ""
    assert (
        db.session.query(Term)
        .filter(Term.language_id == japanese.id, Term._text == word["text"])
        .first()
        is None
    )


def test_set_term_image_keeps_existing_status(empty_db, japanese):
    word = _dataset_word()
    term = Term()
    term.language = japanese
    term.text = word["text"]
    term.status = 4
    db.session.add(term)
    db.session.commit()

    service.set_term_image(db.session, japanese.id, word["text"], "chosen.jpg")
    assert term.status == 4
    assert term.get_current_image() == "chosen.jpg"


def test_image_api_sets_and_clears(
    client, empty_db, japanese, app, monkeypatch, tmp_path
):
    word = _dataset_word()
    root = _use_tmp_userimages(app, monkeypatch, tmp_path)
    _save_jpeg(root / str(japanese.id) / "chosen.jpg")

    response = client.post(
        "/picwords/api/image",
        json={
            "lang_id": japanese.id,
            "text": word["text"],
            "filename": "chosen.jpg",
        },
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data["filename"] == "chosen.jpg"
    assert data["image_source"] == "user"
    assert data["image_thumb_url"] == f"/userimages/{japanese.id}/thumb/chosen.jpg"

    cleared = client.post(
        "/picwords/api/image",
        json={"lang_id": japanese.id, "text": word["text"], "filename": ""},
    )
    assert cleared.status_code == 200
    assert cleared.get_json()["filename"] == ""
    assert cleared.get_json()["image_source"] != "user"


@pytest.mark.parametrize(
    "filename",
    [
        "../chosen.jpg",
        "not-an-image.txt",
        "missing.jpg",
    ],
)
def test_image_api_rejects_bad_files(
    client, empty_db, japanese, app, monkeypatch, tmp_path, filename
):
    word = _dataset_word()
    root = _use_tmp_userimages(app, monkeypatch, tmp_path)
    _save_jpeg(root / str(japanese.id) / "chosen.jpg")

    response = client.post(
        "/picwords/api/image",
        json={"lang_id": japanese.id, "text": word["text"], "filename": filename},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert (
        db.session.query(Term)
        .filter(Term.language_id == japanese.id, Term._text == word["text"])
        .first()
        is None
    )
