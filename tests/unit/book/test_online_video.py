"""
Tests for the "Online video" and online-URL audio import feature.

Covers the new import types on /book/import_webpage: an online video
(media + subtitles via upload or URL) and the mp3 form's online URL
inputs.  Also covers the 20 MB rule (small media downloaded locally,
larger media streamed from the remote URL) and online subtitle parsing.
"""

import io
import json
from unittest.mock import patch

from lute.db import db
from lute.book import service as book_service
from lute.book.service import (
    BookImportException,
    parse_subtitle_content_any,
    parse_subtitle_from_url,
    MEDIA_LOCAL_MAX_BYTES,
)
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
# Import page renders the new options / fields
# ---------------------------------------------------------------------


def test_import_page_shows_online_video_and_mp3_url_fields(app, client):
    "The import page has an Online video option and mp3 online URL inputs."
    resp = client.get("/book/import_webpage")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)

    assert 'value="video"' in content
    assert "Online video" in content
    assert 'id="video_url"' in content
    assert 'id="video_srt_url"' in content
    assert 'id="video-file"' not in content  # placeholder, not matched
    assert 'id="mp3_url"' in content
    assert 'id="mp3_srt_url"' in content


# ---------------------------------------------------------------------
# parse_subtitle_content_any: srt / vtt / txt handling
# ---------------------------------------------------------------------


def test_parse_txt_with_timestamps_returns_cues():
    txt = "1\n00:00:01,000 --> 00:00:04,200\nHello world.\n"
    text, cues_json = parse_subtitle_content_any("sub.txt", txt)
    assert text.strip() == "Hello world."
    assert len(__import__("json").loads(cues_json)) == 1


def test_parse_txt_plain_transcript_has_no_cues():
    txt = "Once upon a time there was a fox."
    text, cues_json = parse_subtitle_content_any("transcript.txt", txt)
    assert text.strip() == txt
    assert cues_json == "[]"


def test_parse_srt_url_downloads_and_parses():
    "parse_subtitle_from_url downloads the subtitle and parses it."
    class _Resp:
        content = None

        def raise_for_status(self):
            pass

    resp = _Resp()
    resp.content = SAMPLE_SRT.encode("utf-8-sig")

    with patch.object(book_service.requests, "get", return_value=resp):
        text, cues_json = parse_subtitle_from_url(
            "https://example.com/sub.srt"
        )

    assert "Hello world." in text
    assert len(__import__("json").loads(cues_json)) == 3


def test_parse_subtitle_url_download_failure_raises():
    class _Err(Exception):
        pass

    def _boom(*args, **kwargs):
        raise book_service.requests.exceptions.ConnectionError("nope")

    with patch.object(book_service.requests, "get", side_effect=_boom):
        try:
            parse_subtitle_from_url("https://example.com/x.srt")
            raised = False
        except BookImportException:
            raised = True
    assert raised


# ---------------------------------------------------------------------
# 20 MB rule
# ---------------------------------------------------------------------


def _fake_length_urls(sizes):
    "Return callables that report distinct Content-Lengths per URL."

    def length(url, **kwargs):
        return sizes.get(url, 100)

    return length


def test_media_under_20mb_is_downloaded_locally(app, app_context, client, english):
    "A small online video URL is downloaded and stored locally."
    with patch(
        "lute.book.routes.parse_subtitle_from_url", return_value=("Hello text.", "[]")
    ), patch(
        "lute.book.routes._url_content_length",
        side_effect=_fake_length_urls({"https://v.example.com/a.mp4": 10 * 1024}),
    ), patch(
        "lute.book.routes.download_url_to_file", return_value="local.mp4"
    ) as dl:
        resp = client.post(
            "/book/import_webpage",
            data={
                "import_type": "video",
                "video_url": "https://v.example.com/a.mp4",
                "video_srt_url": "https://v.example.com/sub.srt",
                "video_tag": "my-video-tag",
                "language_id": str(english.id),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/read/" in resp.headers["Location"]
        dl.assert_called_once()

    repo = BookRepository(db.session)
    book = repo.find_by_title("a.mp4", english.id)
    assert book is not None
    assert book.book_type == "video"
    assert book.audio_filename == "local.mp4"
    assert book.media_url is None


def test_media_over_20mb_is_streamed_from_remote(app, app_context, client, english):
    "A large online video URL is streamed, not downloaded."
    with patch(
        "lute.book.routes.parse_subtitle_from_url", return_value=("Hello text.", "[]")
    ), patch(
        "lute.book.routes._url_content_length",
        return_value=MEDIA_LOCAL_MAX_BYTES + 1,
    ) as length:
        resp = client.post(
            "/book/import_webpage",
            data={
                "import_type": "video",
                "video_url": "https://v.example.com/big.mp4",
                "video_srt_url": "https://v.example.com/sub.srt",
                "video_tag": "my-video-tag",
                "language_id": str(english.id),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/read/" in resp.headers["Location"]
        length.assert_called_once()

    repo = BookRepository(db.session)
    book = repo.find_by_title("big.mp4", english.id)
    assert book is not None
    assert book.media_url == "https://v.example.com/big.mp4"
    assert book.audio_filename is None


def test_mp3_online_url_creates_mp3_book(app, app_context, client, english):
    "The mp3 form accepts an online audio URL and an online subtitle URL."
    with patch(
        "lute.book.routes.parse_subtitle_from_url", return_value=("A.B.", "[]")
    ), patch(
        "lute.book.routes._url_content_length",
        return_value=MEDIA_LOCAL_MAX_BYTES + 1,
    ):
        resp = client.post(
            "/book/import_webpage",
            data={
                "import_type": "mp3",
                "mp3_url": "https://a.example.com/song.mp3",
                "mp3_srt_url": "https://a.example.com/sub.srt",
                "mp3_tag": "my-audio-tag",
                "language_id": str(english.id),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/read/" in resp.headers["Location"]

    repo = BookRepository(db.session)
    book = repo.find_by_title("song.mp3", english.id)
    assert book is not None
    assert book.book_type == "mp3"
    assert book.media_url == "https://a.example.com/song.mp3"


def _make_video_book(app, app_context, english, media_url="https://v.example.com/clip.mp4"):
    from lute.book.model import Book
    from lute.book.service import Service as BookService

    b = Book()
    b.title = "Video Book"
    b.language_id = english.id
    b.text = "Hello world.\nGoodbye!"
    b.book_type = "video"
    b.media_url = media_url
    b.source_uri = media_url
    b.srt_data = json.dumps(
        [
            {"start": 1.0, "end": 4.2, "text": "Hello world."},
            {"start": 5.0, "end": 8.5, "text": "Goodbye!"},
        ]
    )
    b.book_tags = ["video"]
    return BookService().import_book(b, db.session)


def test_read_page_renders_video_backend(app, app_context, client, english):
    "Reading a video book renders the HTML5 video player with the media URL."
    dbbook = _make_video_book(app, app_context, english)
    resp = client.get(f"/read/{dbbook.id}/page/1")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)

    # Unified player uses the "video" backend and an HTML5 <video> element.
    assert '"video"' in content  # LUTE_YT_DATA.backend
    assert 'id="yt-video-player"' in content
    assert "https://v.example.com/clip.mp4" in content