"""
EPUB import route tests.

The preview endpoint must not write to the database; the import
endpoints create one book per chapter, tagged EPUB + book title, and
add the book title to the book_series_tags setting.
"""

import io
import json
import urllib.parse

from lute.db import db
from lute.models.book import Book as DBBook
from lute.models.repositories import UserSettingRepository
from tests.utils import make_epub, make_epub_xhtml


def _epub_bytes(title="Test Book"):
    "A 3-chapter EPUB with a nav TOC."
    return make_epub(
        {
            "OEBPS/ch1.xhtml": make_epub_xhtml(["Chapter one text."]),
            "OEBPS/ch2.xhtml": make_epub_xhtml(["Chapter two text."]),
            "OEBPS/ch3.xhtml": make_epub_xhtml(["Chapter three text."]),
        },
        [("ch1", "ch1.xhtml"), ("ch2", "ch2.xhtml"), ("ch3", "ch3.xhtml")],
        title=title,
        nav_href="nav.xhtml",
        nav_entries=[
            ("ch1.xhtml", "First"),
            ("ch2.xhtml", "Second"),
            ("ch3.xhtml", "Third"),
        ],
    )


def _post(client, url, epub, **fields):
    data = {"epub_file": (io.BytesIO(epub), "test.epub")}
    data.update(fields)
    return client.post(url, data=data, content_type="multipart/form-data")


def _all_books():
    return db.session.query(DBBook).all()


def test_preview_returns_chapters_without_writing_db(app, app_context, english):
    "The preview parses the EPUB but creates no books."
    client = app.test_client()
    resp = _post(client, "/book/import/epub/preview", _epub_bytes())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["title"] == "Test Book"
    assert [c["title"] for c in data["chapters"]] == ["First", "Second", "Third"]
    assert [c["index"] for c in data["chapters"]] == [0, 1, 2]
    assert _all_books() == []


def test_preview_rejects_non_epub_file(app, app_context, english):
    "Wrong extension and corrupt content are reported as errors."
    client = app.test_client()
    resp = client.post(
        "/book/import/epub/preview",
        data={"epub_file": (io.BytesIO(b"x"), "test.txt")},
        content_type="multipart/form-data",
    )
    assert resp.get_json()["success"] is False

    resp = client.post(
        "/book/import/epub/preview",
        data={"epub_file": (io.BytesIO(b"not an epub"), "test.epub")},
        content_type="multipart/form-data",
    )
    assert resp.get_json()["success"] is False


def test_import_creates_one_book_per_chapter_with_tags(app, app_context, english):
    "Each chapter becomes a book tagged EPUB + book title."
    client = app.test_client()
    resp = _post(
        client,
        "/book/import/epub",
        _epub_bytes(),
        language_id=str(english.id),
        epub_title="My Book",
        chapters=json.dumps([0, 1, 2]),
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["imported"] == 3
    assert data["failed"] == 0
    assert data["series_tag"] == "My Book"

    books = sorted(_all_books(), key=lambda b: b.title)
    assert len(books) == 3
    assert [b.title for b in books] == [
        "My Book 01 - First",
        "My Book 02 - Second",
        "My Book 03 - Third",
    ]
    tag_texts = sorted(t.text for t in books[0].book_tags)
    assert tag_texts == ["EPUB", "My Book"]
    # Chapter text was imported and split into pages.
    assert "Chapter one text." in books[0].texts[0].text


def test_import_selected_chapters_only(app, app_context, english):
    "Only the selected chapter indices are imported."
    client = app.test_client()
    resp = _post(
        client,
        "/book/import/epub",
        _epub_bytes(),
        language_id=str(english.id),
        epub_title="My Book",
        chapters=json.dumps([1]),
    )
    data = resp.get_json()
    assert data["success"] is True
    assert data["imported"] == 1
    books = _all_books()
    assert len(books) == 1
    assert books[0].title == "My Book 02 - Second"


def test_import_adds_book_title_to_series_tags(app, app_context, english):
    "The book title is appended to the book_series_tags setting."
    client = app.test_client()
    resp = _post(
        client,
        "/book/import/epub",
        _epub_bytes(),
        language_id=str(english.id),
        epub_title="My Book",
    )
    assert resp.get_json()["success"] is True
    usrepo = UserSettingRepository(db.session)
    assert usrepo.get_dynamic_value("book_series_tags") == "My Book"


def test_series_page_lists_imported_chapters(app, app_context, english):
    "After import, the series overview page lists the chapters."
    client = app.test_client()
    resp = _post(
        client,
        "/book/import/epub",
        _epub_bytes(),
        language_id=str(english.id),
        epub_title="My Book",
    )
    assert resp.get_json()["success"] is True
    quoted = urllib.parse.quote("My Book")
    resp = client.get(f"/book/series/{quoted}")
    assert resp.status_code == 200
    assert "My Book 01 - First".encode() in resp.data
    assert "My Book 03 - Third".encode() in resp.data


def test_direct_import_redirects_to_series_page(app, app_context, english):
    "The form-post import (no preview) imports all chapters."
    client = app.test_client()
    resp = _post(
        client,
        "/book/import_webpage",
        _epub_bytes(),
        import_type="epub",
        language_id=str(english.id),
        epub_title="",
        epub_tag='[{"value":"EPUB"}]',
    )
    assert resp.status_code == 302
    assert "/book/series/" in resp.headers["Location"]
    # Title fell back to the EPUB metadata title.
    assert len(_all_books()) == 3
    assert _all_books()[0].title.startswith("Test Book ")


def test_import_page_renders_with_epub_type(app, app_context, english):
    "The import page includes the EPUB type and preview controls."
    client = app.test_client()
    resp = client.get("/book/import_webpage")
    assert resp.status_code == 200
    assert b'<option value="epub">' in resp.data
    assert b'id="epub-preview"' in resp.data
    assert b'id="epub-preview-panel"' in resp.data


def test_import_requires_language(app, app_context, english):
    client = app.test_client()
    resp = _post(
        client,
        "/book/import/epub",
        _epub_bytes(),
        language_id="",
        epub_title="My Book",
    )
    data = resp.get_json()
    assert data["success"] is False
    assert "language" in data["error"].lower()
    assert _all_books() == []
