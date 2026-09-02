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


def test_stream_is_inline_not_attachment(app, book_with_audio):
    "The stream is for the <audio> element, not a download."
    client = app.test_client()
    resp = client.get(f"/useraudio/stream/{book_with_audio}")
    assert resp.status_code == 200
    assert "attachment" not in (resp.headers.get("Content-Disposition") or "")


def test_stream_cache_headers_and_etag(app, book_with_audio):
    "Responses are browser-cacheable (private) and carry an ETag."
    client = app.test_client()
    resp = client.get(f"/useraudio/stream/{book_with_audio}")
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "private, max-age=86400"
    etag = resp.headers.get("ETag")
    assert etag is not None and etag.startswith('"')


def test_stream_if_none_match_returns_304(app, book_with_audio):
    "A matching If-None-Match gets a 304 instead of the bytes."
    client = app.test_client()
    resp = client.get(f"/useraudio/stream/{book_with_audio}")
    etag = resp.headers.get("ETag")
    revalidated = client.get(
        f"/useraudio/stream/{book_with_audio}",
        headers={"If-None-Match": etag},
    )
    assert revalidated.status_code == 304
    assert revalidated.headers.get("ETag") == etag
    assert revalidated.data == b""


def test_stream_if_range_matching_serves_partial(app, book_with_audio):
    "Range + matching If-Range serves 206 from the cached copy."
    client = app.test_client()
    etag = client.get(f"/useraudio/stream/{book_with_audio}").headers.get("ETag")
    resp = client.get(
        f"/useraudio/stream/{book_with_audio}",
        headers={"Range": "bytes=0-99", "If-Range": etag},
    )
    assert resp.status_code == 206
    assert resp.data == b"A" * 100


def test_stream_if_range_mismatched_returns_full(app, book_with_audio):
    "Range + stale If-Range ignores the Range and serves the whole file."
    client = app.test_client()
    resp = client.get(
        f"/useraudio/stream/{book_with_audio}",
        headers={"Range": "bytes=0-99", "If-Range": '"stale-etag"'},
    )
    assert resp.status_code == 200
    assert resp.data == b"A" * 10000


def test_stream_etag_changes_when_file_changes(app, book_with_audio):
    "Replacing the audio file changes the ETag (no stale cache)."
    client = app.test_client()
    etag_before = client.get(f"/useraudio/stream/{book_with_audio}").headers.get("ETag")
    audio_path = app.env_config.useraudiopath
    fname = os.path.join(audio_path, "testbook_audio.mp3")
    os.utime(fname, (0, 0))  # change mtime without changing size
    etag_after = client.get(f"/useraudio/stream/{book_with_audio}").headers.get("ETag")
    assert etag_before != etag_after


def test_stream_missing_book_returns_404(app, app_context):
    "Unknown book id returns 404 instead of a 500."
    client = app.test_client()
    resp = client.get("/useraudio/stream/999999")
    assert resp.status_code == 404


def test_stream_book_without_audio_returns_404(app, app_context):
    "A book with no audio_filename returns 404 instead of a 500."
    from lute.db import db as _db
    from lute.models.book import Book as _Book
    from lute.models.language import Language as _Language

    english = _db.session.query(_Language).filter(_Language.name == "English").first()
    if english is None:
        english = _Language()
        english.name = "English"
        english.lang_code = "eng"
        english.parser_type = "spacedel"
        _db.session.add(english)
        _db.session.commit()
    book = _Book("No-audio book", english)
    _db.session.add(book)
    _db.session.commit()
    client = app.test_client()
    resp = client.get(f"/useraudio/stream/{book.id}")
    assert resp.status_code == 404
