"""
Test /useraudio/stream supports HTTP Range requests.

The HTML5 audio player needs 206 partial content to seek; without it
seekable() is empty and prev/next-cue jump breaks.
"""

import os

import pytest

from lute.db import db
from lute.models.book import Book
from lute.models.language import Language


@pytest.fixture(name="book_with_audio")
def fixture_book_with_audio(app, app_context):
    "Create a book with a fake audio file in the useraudiopath."
    english = db.session.query(Language).filter(Language.name == "English").first()
    if english is None:
        english = Language()
        english.name = "English"
        english.lang_code = "eng"
        english.parser_type = "spacedel"
        db.session.add(english)
        db.session.commit()
    book = Book("Test audio book", english)
    db.session.add(book)
    db.session.commit()

    audio_path = app.env_config.useraudiopath
    os.makedirs(audio_path, exist_ok=True)
    fname = os.path.join(audio_path, "testbook_audio.mp3")
    with open(fname, "wb") as f:
        f.write(b"A" * 10000)  # 10 KB of fake audio bytes
    book.audio_filename = "testbook_audio.mp3"
    db.session.add(book)
    db.session.commit()
    return book.id


def test_stream_no_range_returns_full(app, book_with_audio):
    "Without a Range header, return 200 and the whole file."
    client = app.test_client()
    resp = client.get(f"/useraudio/stream/{book_with_audio}")
    assert resp.status_code == 200
    assert resp.headers.get("Accept-Ranges") == "bytes"
    assert resp.data == b"A" * 10000


def test_stream_range_returns_partial(app, book_with_audio):
    "With a Range header, return 206 and only the requested bytes."
    client = app.test_client()
    resp = client.get(
        f"/useraudio/stream/{book_with_audio}",
        headers={"Range": "bytes=100-199"},
    )
    assert resp.status_code == 206
    assert resp.headers.get("Content-Range") == "bytes 100-199/10000"
    assert resp.headers.get("Content-Length") == "100"
    assert resp.data == b"A" * 100


def test_stream_range_open_ended(app, book_with_audio):
    "Range with no end returns from start to EOF."
    client = app.test_client()
    resp = client.get(
        f"/useraudio/stream/{book_with_audio}",
        headers={"Range": "bytes=9900-"},
    )
    assert resp.status_code == 206
    assert resp.headers.get("Content-Range") == "bytes 9900-9999/10000"
    assert resp.data == b"A" * 100


def test_stream_range_suffix(app, book_with_audio):
    "Suffix range 'bytes=-500' returns the last 500 bytes."
    client = app.test_client()
    resp = client.get(
        f"/useraudio/stream/{book_with_audio}",
        headers={"Range": "bytes=-500"},
    )
    assert resp.status_code == 206
    assert resp.headers.get("Content-Range") == "bytes 9500-9999/10000"
    assert resp.data == b"A" * 500


def test_stream_range_out_of_bounds(app, book_with_audio):
    "Range beyond file size returns 416."
    client = app.test_client()
    resp = client.get(
        f"/useraudio/stream/{book_with_audio}",
        headers={"Range": "bytes=20000-30000"},
    )
    assert resp.status_code == 416
    assert resp.headers.get("Content-Range") == "bytes */10000"
