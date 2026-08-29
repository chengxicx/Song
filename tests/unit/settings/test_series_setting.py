"""
Tests for the book_series_tags setting round-trip through the settings form.
"""

from lute.db import db
from lute.book.model import Book as ServiceBook, Repository as ServiceRepository
from lute.models.repositories import UserSettingRepository


def _mk_tagged_book(title, tags, language):
    svcbook = ServiceBook()
    svcbook.language_id = language.id
    svcbook.title = title
    svcbook.text = f"{title} text."
    svcbook.book_tags = tags
    repo = ServiceRepository(db.session)
    repo.add(svcbook)
    repo.commit()


def _settings_post_data(series_tags=None):
    "Minimal valid payload for the settings form (all fields must validate)."
    data = {
        "backup_dir": "",
        "backup_count": "5",
        "current_theme": "Default.css",
        "custom_styles": "",
        "show_highlights": "1",
        "stats_calc_sample_size": "5",
        "mecab_path": "",
        "japanese_dict": "auto",
        "japanese_reading": "katakana",
        "japanese_sudachi_dict": "core",
        "japanese_sudachi_mode": "C",
        "ankiconnect_url": "http://127.0.0.1:8765",
        "tts_hover_delay": "200",
    }
    if series_tags:
        data["book_series_tags"] = series_tags
    return data


def test_series_tags_roundtrip(app, app_context, client, english):
    "Saving the settings form stores selected tags comma-separated."
    _mk_tagged_book("Erin-01", ["Erin", "Video"], english)

    resp = client.get("/settings/index")
    assert resp.status_code == 200
    assert b"Book series tags" in resp.data
    assert b"Erin (1 book)" in resp.data
    assert b"Video (1 book)" in resp.data

    resp = client.post("/settings/index", data=_settings_post_data(["Erin"]))
    assert resp.status_code == 302
    assert UserSettingRepository(db.session).get_value("book_series_tags") == "Erin"

    # Reloading the form re-checks the saved tags.
    resp = client.get("/settings/index")
    html = resp.data.decode("utf-8")
    import re

    assert re.search(r'<input[^>]*checked[^>]*value="Erin"', html), (
        "Erin checkbox re-checked on load"
    )
    assert not re.search(r'<input[^>]*checked[^>]*value="Video"', html), (
        "Video stays unchecked"
    )


def test_series_tags_empty_save(app, app_context, client, english):
    "Posting no selections clears the setting."
    _mk_tagged_book("Erin-01", ["Erin"], english)
    repo = UserSettingRepository(db.session)
    repo.set_value("book_series_tags", "Erin")
    db.session.commit()

    client.post("/settings/index", data=_settings_post_data())
    assert repo.get_value("book_series_tags") == ""
