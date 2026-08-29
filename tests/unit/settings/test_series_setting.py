"""
Tests for the book_series_tags setting round-trip through /book/settings.
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


def test_series_tags_roundtrip(app, app_context, client, english):
    "Saving /book/settings stores selected tags comma-separated."
    _mk_tagged_book("Erin-01", ["Erin", "Video"], english)

    resp = client.get("/book/settings")
    assert resp.status_code == 200
    assert b"Book series tags" in resp.data
    assert b"Erin (1 book)" in resp.data
    assert b"Video (1 book)" in resp.data

    resp = client.post("/book/settings", data={"book_series_tags": ["Erin"]})
    assert resp.status_code == 302
    assert UserSettingRepository(db.session).get_value("book_series_tags") == "Erin"

    # Reloading the form re-checks the saved tags.
    resp = client.get("/book/settings")
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
    repo.set_dynamic_value("book_series_tags", "Erin")
    db.session.commit()

    client.post("/book/settings", data={})
    assert repo.get_value("book_series_tags") == ""


def test_series_tags_not_on_general_settings_page(app, app_context, client):
    "The general settings page no longer hosts the series-tags field."
    resp = client.get("/settings/index")
    assert resp.status_code == 200
    assert b"Book series tags" not in resp.data
