"""
Tests for the Bilibili video book feature.
"""

import io
import json
from unittest.mock import Mock, patch
import pytest
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
    with patch(
        "lute.book.service.requests.get", return_value=_fake_response(payload)
    ) as m:
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
    with patch.object(
        BookService, "bilibili_title", return_value="Route Bilibili Book"
    ):
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
    resp = client.post(
        f"/book/edit/{dbbook.id}", data=form_data, follow_redirects=False
    )
    assert resp.status_code == 302

    repo = BookRepository(db.session)
    book = repo.find(dbbook.id)
    assert book.book_type == "bilibili"
    assert len(book.cues) == 3


# ---------------------------------------------------------------------
# stream_info uses the selected page's duration, not the whole collection
# ---------------------------------------------------------------------


def test_stream_info_uses_selected_page_duration():
    "The reported duration is the page's own duration, not the collection total."
    from lute.read import bilibili_stream

    view = {
        "code": 0,
        "data": {
            # Top-level duration is the whole collection's total.
            "duration": 7200,
            "pages": [
                {"cid": 101, "duration": 120},
                {"cid": 202, "duration": 300},
                {"cid": 303, "duration": 180},
            ],
        },
    }
    play = {
        "code": 0,
        "data": {
            "dash": {
                "video": [
                    {
                        "bandwidth": 1000,
                        "baseUrl": "v.mp4",
                        "SegmentBase": {"Initialization": "init", "indexRange": "0-99"},
                    }
                ],
                "audio": [
                    {
                        "bandwidth": 500,
                        "baseUrl": "a.m4a",
                        "SegmentBase": {"Initialization": "init", "indexRange": "0-99"},
                    }
                ],
            }
        },
    }
    with patch.object(
        bilibili_stream, "_fetch_view", return_value=view["data"]
    ), patch.object(bilibili_stream, "_fetch_playurl", return_value=play["data"]):
        info = bilibili_stream.stream_info("BV1xx411c7mD", page=2)
    assert info["duration"] == 300
    assert info["cid"] == 202


# ---------------------------------------------------------------------
# Upstream failures must surface as BilibiliStreamError.
#
# They used to escape as raw requests exceptions, and since the routes
# only caught ValueError the request died as an HTML 500 page -- which
# the DASH player cannot parse, so the player just sat there.  Bilibili
# bans datacenter / overseas IPs with HTTP 412 (or
# {"code": -412, "message": "request was banned"}), so this is the
# normal failure mode of a server that cannot reach Bilibili, not an
# exotic one.
# ---------------------------------------------------------------------


def _http_error(status):
    "A requests HTTPError carrying a response with the given status."
    resp = requests.Response()
    resp.status_code = status
    resp.url = "https://api.bilibili.com/x/web-interface/view"
    return requests.exceptions.HTTPError(f"{status} Client Error", response=resp)


def _json_response(payload):
    "A stand-in for a requests response whose body is JSON."
    m = Mock()
    m.raise_for_status.return_value = None
    m.status_code = 200
    m.json.return_value = payload
    return m


def test_http_error_412_becomes_stream_error_with_guidance():
    "A risk-control ban is reported as such, not as a bare 412."
    from lute.read import bilibili_stream

    with patch.object(bilibili_stream.requests, "get", side_effect=_http_error(412)):
        with pytest.raises(bilibili_stream.BilibiliStreamError) as exc:
            bilibili_stream.stream_info("BV1risk412", 1)
    msg = str(exc.value)
    assert "412" in msg
    assert "risk control" in msg
    assert "official embed" in msg


def test_blocked_body_with_banned_code_becomes_stream_error():
    "The real blocked response shape (code -412) is not mistaken for success."
    from lute.read import bilibili_stream

    banned = _json_response({"code": -412, "message": "request was banned"})
    with patch.object(bilibili_stream.requests, "get", return_value=banned):
        with pytest.raises(bilibili_stream.BilibiliStreamError) as exc:
            bilibili_stream.stream_info("BV1banned1", 1)
    assert "request was banned" in str(exc.value)


def test_non_json_body_becomes_stream_error():
    "A risk-control HTML page must not surface as a JSON decode error."
    from lute.read import bilibili_stream

    m = Mock()
    m.raise_for_status.return_value = None
    m.status_code = 200
    m.json.side_effect = ValueError("Expecting value: line 1 column 1")
    with patch.object(bilibili_stream.requests, "get", return_value=m):
        with pytest.raises(bilibili_stream.BilibiliStreamError) as exc:
            bilibili_stream.stream_info("BV1htmlpage", 1)
    assert "non-JSON" in str(exc.value)


def test_connection_error_becomes_stream_error():
    "A transport failure is wrapped too."
    from lute.read import bilibili_stream

    boom = requests.exceptions.ConnectionError("dns failure")
    with patch.object(bilibili_stream.requests, "get", side_effect=boom):
        with pytest.raises(bilibili_stream.BilibiliStreamError):
            bilibili_stream.stream_info("BV1dnsfail1", 1)


def test_unreachable_proxy_becomes_stream_error():
    """A dead egress proxy is the expected steady-state failure, not a 500.

    LUTE_BILIBILI_PROXY points at an SSH reverse tunnel, so it is
    unreachable whenever that tunnel is down (the far end slept, the SSH
    session dropped, the proxy was stopped).  requests raises ProxyError,
    which must degrade the page to the embed player rather than crash it.
    """
    from lute.read import bilibili_stream

    boom = requests.exceptions.ProxyError(
        "Cannot connect to proxy", OSError("Connection refused")
    )
    with patch.object(bilibili_stream.requests, "get", side_effect=boom):
        with pytest.raises(bilibili_stream.BilibiliStreamError):
            bilibili_stream.stream_info("BV1noproxy1", 1)


def test_page_out_of_range_raises_stream_error():
    "An out-of-range page is an expected error, not a ValueError."
    from lute.read import bilibili_stream

    view = {"duration": 10, "pages": [{"cid": 101, "duration": 10}]}
    with patch.object(bilibili_stream, "_fetch_view", return_value=view):
        with pytest.raises(bilibili_stream.BilibiliStreamError):
            bilibili_stream.stream_info("BV1pageoor1", page=9)


def test_missing_dash_raises_stream_error():
    "A video with no playable DASH streams is reported, not crashed on."
    from lute.read import bilibili_stream

    view = {"duration": 10, "pages": [{"cid": 101, "duration": 10}]}
    with patch.object(bilibili_stream, "_fetch_view", return_value=view), patch.object(
        bilibili_stream, "_fetch_playurl", return_value={}
    ):
        with pytest.raises(bilibili_stream.BilibiliStreamError) as exc:
            bilibili_stream.stream_info("BV1nodashx1", page=1)
    assert "No playable stream" in str(exc.value)


def test_proxy_stream_connection_error_becomes_stream_error():
    "An unreachable CDN segment host raises the module's own error."
    from lute.read import bilibili_stream

    boom = requests.exceptions.ConnectTimeout("pcdn node timed out")
    with patch.object(bilibili_stream.requests, "get", side_effect=boom):
        with pytest.raises(bilibili_stream.BilibiliStreamError) as exc:
            bilibili_stream.proxy_stream("https://cdn.example/x.m4s", "bytes=0-1")
    assert "CDN segment" in str(exc.value)


# ---------------------------------------------------------------------
# The stream routes answer with JSON and a 502, never an HTML 500 page.
# ---------------------------------------------------------------------


def test_mpd_route_returns_502_json_when_stream_unavailable(client):
    "The manifest endpoint reports upstream failure as JSON."
    from lute.read import bilibili_stream

    err = bilibili_stream.BilibiliStreamError("request was banned")
    with patch.object(bilibili_stream, "stream_info", side_effect=err):
        resp = client.get("/read/bilibili/stream/mpd/BV1xx411c7mD?page=1")
    assert resp.status_code == 502
    assert resp.is_json
    assert "request was banned" in resp.get_json()["error"]


def test_mpd_route_returns_502_when_stream_info_raises_unexpectedly(client):
    "Even an unexpected upstream exception must not become an HTML 500."
    from lute.read import bilibili_stream

    with patch.object(
        bilibili_stream,
        "stream_info",
        side_effect=bilibili_stream.BilibiliStreamError("x"),
    ):
        resp = client.get("/read/bilibili/stream/mpd/BV1xx411c7mD?page=1")
    assert resp.status_code != 500
    assert resp.is_json


def test_proxy_route_returns_502_json_when_segment_unavailable(client):
    "A failing CDN relay is reported as JSON, not as a 500 page."
    from lute.read import bilibili_stream

    info = {
        "video": {"baseUrl": "https://cdn.example/v.m4s"},
        "audio": {"baseUrl": "https://cdn.example/a.m4s"},
    }
    err = bilibili_stream.BilibiliStreamError("CDN segment request failed")
    with patch.object(bilibili_stream, "stream_info", return_value=info), patch.object(
        bilibili_stream, "proxy_stream", side_effect=err
    ):
        resp = client.get("/read/bilibili/stream/proxy/BV1xx411c7mD/video?page=1")
    assert resp.status_code == 502
    assert resp.is_json
    assert "CDN segment" in resp.get_json()["error"]
