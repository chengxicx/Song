"""
Tests for the Bilibili video book feature.
"""

import io
import json
from unittest.mock import patch
import requests
from lute.db import db
from lute.book.service import (
    bilibili_video_id,
    bilibili_embed_url,
    bilibili_page,
    Service as BookService,
)
from lute.read.routes import _subtitle_words_html
from lute.models.repositories import BookRepository


SAMPLE_SRT = """1
00:00:01,000 --> 00:00:04,200
Hello world.

2
00:00:05,000 --> 00:00:08,500
This is a test subtitle.

3
00:00:10,000 --> 00:00:13,000
Goodbye!
"""


# ---------------------------------------------------------------------
# bilibili_video_id / bilibili_embed_url
# ---------------------------------------------------------------------


def test_bilibili_video_id_bv_url():
    assert bilibili_video_id("https://www.bilibili.com/video/BV1xx411c7mD") == (
        "BV1xx411c7mD",
        None,
    )


def test_bilibili_video_id_av_url():
    assert bilibili_video_id("https://www.bilibili.com/video/av123456") == (
        None,
        "123456",
    )


def test_bilibili_video_id_invalid():
    assert bilibili_video_id("https://example.com/not-bilibili") == (None, None)
    assert bilibili_video_id("") == (None, None)
    assert bilibili_video_id(None) == (None, None)


def test_bilibili_embed_url_bv():
    assert bilibili_embed_url("https://www.bilibili.com/video/BV1xx411c7mD") == (
        "https://player.bilibili.com/player.html"
        "?bvid=BV1xx411c7mD&page=1&high_quality=1&danmaku=0"
    )


def test_bilibili_embed_url_av():
    assert bilibili_embed_url("https://www.bilibili.com/video/av123456") == (
        "https://player.bilibili.com/player.html"
        "?aid=123456&page=1&high_quality=1&danmaku=0"
    )


def test_bilibili_embed_url_invalid():
    assert bilibili_embed_url("https://example.com/not-bilibili") is None


def test_bilibili_page_defaults_to_one_when_absent():
    assert bilibili_page("https://www.bilibili.com/video/BV1xx411c7mD") == 1
    assert bilibili_page("") == 1
    assert bilibili_page(None) == 1


def test_bilibili_page_parses_p_parameter():
    assert bilibili_page("https://www.bilibili.com/video/BV1xx411c7mD?p=3") == 3
    assert bilibili_page("https://www.bilibili.com/video/BV1xx411c7mD?p=1") == 1
    assert bilibili_page("https://www.bilibili.com/video/BV1xx411c7mD?foo=1&p=4") == 4


def test_bilibili_page_ignores_invalid_p():
    assert bilibili_page("https://www.bilibili.com/video/BV1xx411c7mD?p=abc") == 1
    assert bilibili_page("https://www.bilibili.com/video/BV1xx411c7mD?p=0") == 1


def test_bilibili_embed_url_uses_selected_page():
    assert bilibili_embed_url("https://www.bilibili.com/video/BV1xx411c7mD?p=3") == (
        "https://player.bilibili.com/player.html"
        "?bvid=BV1xx411c7mD&page=3&high_quality=1&danmaku=0"
    )


# ---------------------------------------------------------------------
# bilibili_title
# ---------------------------------------------------------------------


def _fake_response(payload):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    return _Resp()


def test_bilibili_title_uses_view_api_bv(app, app_context):
    "The real title is fetched from the view API for a BV id."
    payload = {
        "code": 0,
        "data": {"title": "A Real Bilibili Title"},
    }
    with patch("lute.book.service.requests.get", return_value=_fake_response(payload)):
        svc = BookService()
        title = svc.bilibili_title("https://www.bilibili.com/video/BV1xx411c7mD")
    assert title == "A Real Bilibili Title"


def test_bilibili_title_uses_view_api_av(app, app_context):
    "The view API is called with the av id for legacy videos."
    payload = {
        "code": 0,
        "data": {"title": "Legacy Title"},
    }
    with patch("lute.book.service.requests.get", return_value=_fake_response(payload)) as m:
        svc = BookService()
        title = svc.bilibili_title("https://www.bilibili.com/video/av123456")
    assert title == "Legacy Title"
    assert "aid=123456" in m.call_args.args[0]


def test_bilibili_title_fallbacks_to_id_on_error(app, app_context):
    "If the API fails, the title falls back to the video id."
    with patch(
        "lute.book.service.requests.get",
        side_effect=requests.exceptions.RequestException("boom"),
    ):
        svc = BookService()
        title = svc.bilibili_title("https://www.bilibili.com/video/BV1xx411c7mD")
    assert title == "Bilibili video (BV1xx411c7mD)"


# ---------------------------------------------------------------------
# _subtitle_words_html aligns with cues for bilibili books
# ---------------------------------------------------------------------


def _make_bilibili_book(app, app_context, english):
    from lute.book.model import Book

    b = Book()
    b.title = "Route Bilibili Book"
    b.language_id = english.id
    b.text = "Hello world.\nThis is a test subtitle.\nGoodbye!"
    b.book_type = "bilibili"
    b.srt_data = json.dumps(
        [
            {"start": 1.0, "end": 4.2, "text": "Hello world."},
            {"start": 5.0, "end": 8.5, "text": "This is a test subtitle."},
            {"start": 10.0, "end": 13.0, "text": "Goodbye!"},
        ]
    )
    b.source_uri = "https://www.bilibili.com/video/BV1xx411c7mD"
    b.book_tags = ["bilibili"]
    svc = BookService()
    return svc.import_book(b, db.session)


def test_subtitle_words_html_for_bilibili(app, app_context, english):
    "Bilibili books get tokenized word HTML per cue."
    dbbook = _make_bilibili_book(app, app_context, english)
    words = _subtitle_words_html(dbbook)
    assert len(words) == 3, "one HTML chunk per cue"
    assert "Hello" in words[0] and "world" in words[0]
    assert "Goodbye" in words[2]


# ---------------------------------------------------------------------
# Import page + route
# ---------------------------------------------------------------------


def test_import_webpage_form_renders_bilibili_fields(app, app_context, english, client):
    "The import page has a bilibili type option and form."
    resp = client.get("/book/import_webpage")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)

    assert 'value="bilibili"' in content
    assert "Bilibili video" in content
    assert 'id="bilibili_url"' in content
    assert 'id="bilibili_tag"' in content
    assert 'id="bilibili-language"' in content


def test_import_bilibili_video_route(app, app_context, english, client):
    "POSTing to import_webpage with type=bilibili creates a book."
    with patch.object(BookService, "bilibili_title", return_value="Route Bilibili Book"):
        data = {
            "import_type": "bilibili",
            "bilibili_url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "bilibili_tag": "my-tag",
            "language_id": str(english.id),
            "srt_file": (io.BytesIO(SAMPLE_SRT.encode()), "sub.srt"),
        }
        resp = client.post(
            "/book/import_webpage",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/read/" in resp.headers["Location"]

        repo = BookRepository(db.session)
        book = repo.find_by_title("Route Bilibili Book", english.id)
        assert book is not None, "book created with the given title"

        assert book.book_type == "bilibili"
        assert book.source_uri == "https://www.bilibili.com/video/BV1xx411c7mD"
        assert len(book.cues) == 3
        assert [t.text for t in book.book_tags] == ["my-tag"]


def test_import_bilibili_video_invalid_url(app, app_context, english, client):
    "An invalid bilibili URL is rejected without creating a book."
    data = {
        "import_type": "bilibili",
        "bilibili_url": "https://example.com/not-bilibili",
        "bilibili_tag": "my-tag",
        "language_id": str(english.id),
        "srt_file": (io.BytesIO(SAMPLE_SRT.encode()), "sub.srt"),
    }
    resp = client.post(
        "/book/import_webpage",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/book/import_webpage" in resp.headers["Location"]


def test_read_page_passes_bilibili_data(app, app_context, english, client):
    "Reading a bilibili book renders the player with the embed URL and cues."
    dbbook = _make_bilibili_book(app, app_context, english)

    resp = client.get(f"/read/{dbbook.id}/page/1")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)

    assert "yt-player-container" in content
    assert "bilibili-player.js" in content
    assert "player.bilibili.com/player.html?bvid=BV1xx411c7mD" in content
    assert "Hello world." in content


def test_edit_book_preserves_bilibili_type(app, app_context, english, client):
    "Editing a bilibili book and saving keeps the bilibili data."
    dbbook = _make_bilibili_book(app, app_context, english)

    resp = client.get(f"/book/edit/{dbbook.id}")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)
    assert 'value="bilibili"' in content
    assert "00:00:01,000 --&gt; 00:00:04,200" in content

    form_data = {
        "title": "Route Bilibili Book",
        "text": (
            "1\n"
            "00:00:01,000 --> 00:00:04,200\n"
            "Hello world.\n\n"
            "2\n"
            "00:00:05,000 --> 00:00:08,500\n"
            "This is a test subtitle.\n\n"
            "3\n"
            "00:00:10,000 --> 00:00:13,000\n"
            "Goodbye!"
        ),
        "split_by": "paragraphs",
        "threshold_page_tokens": "250",
        "source_uri": "https://www.bilibili.com/video/BV1xx411c7mD",
        "book_tags": '[{"value": "bilibili"}]',
        "book_type": "bilibili",
    }
    resp = client.post(f"/book/edit/{dbbook.id}", data=form_data, follow_redirects=False)
    assert resp.status_code == 302

    repo = BookRepository(db.session)
    book = repo.find(dbbook.id)
    assert book.book_type == "bilibili"
    assert len(book.cues) == 3