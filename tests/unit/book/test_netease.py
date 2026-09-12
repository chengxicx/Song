"""
Tests for the NetEase Cloud Music import feature.

Covers song-URL parsing, LRC lyric parsing, the /book/import_webpage
"netease" import path, and the QR-code login routes on /netease.
"""

import json
from unittest.mock import patch

import pytest

from lute.db import db
from lute.book.service import (
    BookImportException,
    _parse_lrc_cues,
    parse_subtitle_content,
)
from lute.models.repositories import BookRepository, UserSettingRepository
from lute.netease import service as netease_service
from lute.netease.service import netease_song_id


SAMPLE_LRC = """[ti:Test Song]
[ar:Artist]
[offset:+500]
[00:00.000]Credits
[00:10.140]First line
[00:12.500]Second line
[00:15.000]
[00:20.000][01:30.000]Chorus
[01:35.000]Last"""


class _FakeResp:
    "Minimal requests.Response stand-in."

    def __init__(self, data, cookies=None, headers=None):
        self._data = data
        self.cookies = cookies or {}
        self.headers = headers or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


# ---------------------------------------------------------------------
# netease_song_id
# ---------------------------------------------------------------------


def test_netease_song_id_hash_song_url():
    assert netease_song_id("https://music.163.com/#/song?id=3360424346") == "3360424346"


def test_netease_song_id_plain_song_url_with_params():
    assert netease_song_id("https://music.163.com/song?id=42&userid=7") == "42"


def test_netease_song_id_mobile_url():
    assert netease_song_id("https://y.music.163.com/m/song?id=99/") == "99"


def test_netease_song_id_invalid():
    assert netease_song_id("https://music.163.com/#/playlist?id=123") is None
    assert netease_song_id("https://example.com/song?id=1") is None
    assert netease_song_id("") is None
    assert netease_song_id(None) is None


# ---------------------------------------------------------------------
# LRC parsing
# ---------------------------------------------------------------------


def test_parse_lrc_basic_cues_and_end_chaining():
    cues = _parse_lrc_cues(SAMPLE_LRC)
    assert [c["text"] for c in cues] == [
        "Credits",
        "First line",
        "Second line",
        "Chorus",
        "Chorus",
        "Last",
    ]
    # offset:+500 shifts times 0.5s earlier.
    assert cues[0] == {"start": 0.0, "end": 9.64, "text": "Credits"}
    assert cues[1]["start"] == pytest.approx(9.64)
    assert cues[1]["end"] == pytest.approx(12.0)
    # Last cue gets a fixed 5s tail.
    assert cues[-1]["end"] == pytest.approx(cues[-1]["start"] + 5.0)


def test_parse_lrc_metadata_and_empty_lines_skipped():
    cues = _parse_lrc_cues(SAMPLE_LRC)
    texts = [c["text"] for c in cues]
    assert "Test Song" not in texts
    assert "Artist" not in texts
    # The empty [00:15.000] line produced no cue, but still bounds the
    # previous cue's end time.
    assert cues[2] == {"start": 12.0, "end": 14.5, "text": "Second line"}


def test_parse_lrc_word_level_timestamps_stripped():
    cues = _parse_lrc_cues("[00:01.000]<00:01.00>Hello <00:01.50>world")
    assert len(cues) == 1
    assert cues[0]["text"] == "Hello world"


def test_parse_lrc_via_parse_subtitle_content():
    text, cues_json = parse_subtitle_content(SAMPLE_LRC, ext=".lrc")
    assert text.startswith("Credits")
    cues = json.loads(cues_json)
    assert len(cues) == 6
    assert all(set(c) == {"start", "end", "text"} for c in cues)


def test_parse_lrc_duplicate_timestamps():
    "Each timestamp plays the line once (LRC repeat semantics)."
    cues = _parse_lrc_cues("[00:05.000][00:05.000]Same line")
    assert len(cues) == 2
    assert cues[0]["start"] == 5.0
    assert cues[1]["start"] == 5.0


# ---------------------------------------------------------------------
# NetEase API helpers
# ---------------------------------------------------------------------


def test_netease_song_title_formats_artists():
    data = {
        "code": 200,
        "songs": [
            {"name": "坏掉了", "artists": [{"name": "SUN18"}, {"name": "Other"}]}
        ],
    }
    with patch.object(
        netease_service, "_api_get", return_value=data
    ) as api_get:
        assert netease_service.netease_song_title("1") == "坏掉了 - SUN18/Other"
    assert api_get.call_args[0][0] == "/api/song/detail/"


def test_netease_song_title_without_artist():
    data = {"code": 200, "songs": [{"name": "Song", "artists": []}]}
    with patch.object(netease_service, "_api_get", return_value=data):
        assert netease_service.netease_song_title("1") == "Song"


def test_netease_song_title_not_found_raises():
    with patch.object(netease_service, "_api_get", return_value={"code": 200, "songs": []}):
        with pytest.raises(BookImportException):
            netease_service.netease_song_title("1")


def test_netease_audio_url_returns_url():
    data = {"code": 200, "data": [{"url": "http://cdn.example.com/a.mp3", "br": 320000}]}
    with patch.object(netease_service, "_api_get", return_value=data) as api_get:
        assert netease_service.netease_audio_url("1") == "http://cdn.example.com/a.mp3"
    assert api_get.call_args[0][0] == "/api/song/enhance/player/url"


def test_netease_audio_url_unavailable_raises():
    data = {"code": 200, "data": [{"url": None, "fee": 1}]}
    with patch.object(netease_service, "_api_get", return_value=data):
        with pytest.raises(BookImportException):
            netease_service.netease_audio_url("1")


def test_netease_audio_url_trial_only_raises():
    data = {
        "code": 200,
        "data": [{"url": "http://cdn.example.com/trial.mp3", "freeTrialInfo": {"start": 0, "end": 30000}}],
    }
    with patch.object(netease_service, "_api_get", return_value=data):
        with pytest.raises(BookImportException, match="preview snippet"):
            netease_service.netease_audio_url("1")


def test_netease_lyric_content_missing_raises():
    with patch.object(netease_service, "_api_get", return_value={"code": 200, "lrc": {"lyric": ""}}):
        with pytest.raises(BookImportException):
            netease_service.netease_lyric_content("1")


def test_api_requests_carry_stored_cookie(app_context):
    "The MUSIC_U cookie, when stored, is sent on all API calls."
    netease_service.save_cookie_value("TESTCOOKIE")
    data = {"code": 200, "data": [{"url": "http://cdn.example.com/a.mp3"}]}
    with patch.object(
        netease_service.requests, "get", return_value=_FakeResp(data)
    ) as get:
        netease_service.netease_audio_url("1")
        headers = get.call_args[1]["headers"]
        assert "MUSIC_U=TESTCOOKIE" in headers["Cookie"]
        # PC-client identity: without it VIP audio URLs are referer-locked.
        assert "os=pc" in headers["Cookie"]
        assert "music.163.com" in get.call_args[0][0]

    netease_service.clear_cookie()
    with patch.object(
        netease_service.requests, "get", return_value=_FakeResp(data)
    ) as get:
        netease_service.netease_audio_url("1")
        headers = get.call_args[1]["headers"]
        assert "MUSIC_U" not in headers["Cookie"]
        assert "os=pc" in headers["Cookie"]


def test_save_cookie_extracts_music_u_from_header(app_context):
    netease_service.save_cookie("MUSIC_U=ABC123; __csrf=xyz; other=1")
    repo = UserSettingRepository(db.session)
    assert repo.get_dynamic_value(netease_service.COOKIE_SETTING_KEY) == "ABC123"
    netease_service.clear_cookie()
    assert repo.get_dynamic_value(netease_service.COOKIE_SETTING_KEY) == ""


# ---------------------------------------------------------------------
# QR login routes
# ---------------------------------------------------------------------


def test_qr_login_start_returns_key_and_svg(client):
    post_data = {"code": 200, "data": {"unikey": "UNIKEY1"}}
    with patch.object(
        netease_service.requests.Session, "get", return_value=_FakeResp({})
    ), patch.object(
        netease_service.requests.Session, "post", return_value=_FakeResp(post_data)
    ):
        resp = client.get("/netease/login/qr")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["key"] == "UNIKEY1"
    # The QR SVG is an inline-embeddable path (sized via CSS, no xml decl).
    assert data["qr_svg"].startswith("<svg")
    assert "xmlns" in data["qr_svg"]
    assert "<path" in data["qr_svg"]
    # The session cookies are echoed for the check calls.
    assert data["cookies"] == {}


def test_qr_check_waits_then_confirms_and_stores_cookie(client, app_context):
    waiting = {"code": 801}
    confirmed = {"code": 803}
    confirmed_resp = _FakeResp(
        confirmed, cookies={"MUSIC_U": "NEWCOOKIE"}, headers={}
    )

    with patch.object(
        netease_service.requests.Session, "post", return_value=_FakeResp(waiting)
    ), patch.object(
        netease_service, "_login_session", wraps=netease_service._login_session
    ) as login_session:
        data = client.get(
            "/netease/login/qr/check?key=K&cookies=%7B%22NMTID%22%3A%22abc%22%7D"
        ).get_json()
    assert data == {"ok": True, "state": "waiting"}
    # The echoed cookies are loaded into the polling session.
    login_session.assert_called_once_with({"NMTID": "abc"})

    with patch.object(
        netease_service.requests.Session, "post", return_value=confirmed_resp
    ) as post:
        data = client.get("/netease/login/qr/check?key=K").get_json()
    assert data["ok"] is True
    assert data["state"] == "confirmed"
    # The login POST carries no cookie; the check body sends the unikey.
    assert post.call_args[1]["data"]["key"] == "K"

    repo = UserSettingRepository(db.session)
    assert repo.get_dynamic_value(netease_service.COOKIE_SETTING_KEY) == "NEWCOOKIE"
    netease_service.clear_cookie()


def test_qr_check_verify_state_keeps_polling(client, app_context):
    "Code 8821 (risk control) maps to a recoverable 'verify' state."
    verify_resp = _FakeResp({"code": 8821, "message": "需要行为验证码验证"})
    with patch.object(
        netease_service.requests.Session, "post", return_value=verify_resp
    ):
        data = client.get("/netease/login/qr/check?key=K").get_json()
    assert data == {
        "ok": True,
        "state": "verify",
        "message": "需要行为验证码验证",
    }


def test_qr_check_expired(client):
    with patch.object(
        netease_service.requests.Session,
        "post",
        return_value=_FakeResp({"code": 800}),
    ):
        data = client.get("/netease/login/qr/check?key=K").get_json()
    assert data == {"ok": True, "state": "expired"}


def test_login_status_logged_out_and_in(client, app_context):
    data = client.get("/netease/login/status").get_json()
    assert data == {"logged_in": False}

    netease_service.save_cookie_value("SOMECOOKIE")
    profile = {"code": 200, "profile": {"nickname": "Tester", "vipType": 11}}
    with patch.object(
        netease_service.requests, "get", return_value=_FakeResp(profile)
    ):
        data = client.get("/netease/login/status").get_json()
    assert data == {"logged_in": True, "nickname": "Tester", "vip": True}

    with patch.object(
        netease_service.requests, "get", return_value=_FakeResp({"code": 200})
    ):
        data = client.get("/netease/login/status").get_json()
    assert data == {"logged_in": False}
    netease_service.clear_cookie()


def test_logout_clears_cookie(client, app_context):
    netease_service.save_cookie_value("SOMECOOKIE")
    assert client.post("/netease/logout").get_json() == {"ok": True}
    repo = UserSettingRepository(db.session)
    assert repo.get_dynamic_value(netease_service.COOKIE_SETTING_KEY) == ""


def test_login_cookie_paste_route(client, app_context):
    resp = client.post("/netease/login/cookie", data={"cookie": "MUSIC_U=PASTED"})
    assert resp.get_json()["ok"] is True
    repo = UserSettingRepository(db.session)
    assert repo.get_dynamic_value(netease_service.COOKIE_SETTING_KEY) == "PASTED"
    netease_service.clear_cookie()

    resp = client.post("/netease/login/cookie", data={"cookie": ""})
    data = resp.get_json()
    assert data["ok"] is False
    assert data["message"]


# ---------------------------------------------------------------------
# /book/import_webpage "netease" import
# ---------------------------------------------------------------------


def test_import_page_shows_netease_fields(app, client):
    resp = client.get("/book/import_webpage")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)

    assert 'value="netease"' in content
    assert "NetEase Cloud Music" in content
    assert 'id="netease_url"' in content
    assert 'name="netease_url"' in content


def test_netease_import_creates_book(app, app_context, client, english):
    with patch(
        "lute.book.routes.netease_song_title", return_value="Bad Song - SUN18"
    ), patch(
        "lute.book.routes.netease_audio_url",
        return_value="http://cdn.example.com/song.mp3",
    ), patch(
        "lute.book.routes.netease_lyric_content", return_value=SAMPLE_LRC
    ), patch(
        "lute.book.routes.download_url_to_file", return_value="stored.mp3"
    ) as dl:
        resp = client.post(
            "/book/import_webpage",
            data={
                "import_type": "netease",
                "netease_url": "https://music.163.com/#/song?id=3360424346",
                "netease_tag": "songs",
                "language_id": str(english.id),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/read/" in resp.headers["Location"]
        assert dl.call_args[1]["max_bytes"] == netease_service.NETEASE_MAX_AUDIO_BYTES

    repo = BookRepository(db.session)
    book = repo.find_by_title("Bad Song - SUN18", english.id)
    assert book is not None
    assert book.book_type == "netease"
    assert book.audio_filename == "stored.mp3"
    assert book.source_uri == "https://music.163.com/#/song?id=3360424346"
    cues = json.loads(book.srt_data)
    assert [c["text"] for c in cues][0] == "Credits"
    # The lyrics are the book text.
    assert "First line" in book.texts[0].text


def test_netease_import_invalid_url_flashes(app, app_context, client, english):
    resp = client.post(
        "/book/import_webpage",
        data={
            "import_type": "netease",
            "netease_url": "https://music.163.com/#/playlist?id=1",
            "language_id": str(english.id),
        },
        follow_redirects=True,
    )
    assert "valid NetEase Cloud Music song URL" in resp.get_data(as_text=True)


def test_netease_import_unavailable_audio_flashes(app, app_context, client, english):
    "A VIP / restricted song without login shows a clear error."
    with patch(
        "lute.book.routes.netease_song_title", return_value="VIP Song"
    ), patch(
        "lute.book.routes.netease_audio_url",
        side_effect=BookImportException("No playable audio for this song"),
    ):
        resp = client.post(
            "/book/import_webpage",
            data={
                "import_type": "netease",
                "netease_url": "https://music.163.com/#/song?id=1",
                "language_id": str(english.id),
            },
            follow_redirects=True,
        )
    content = resp.get_data(as_text=True)
    assert "No playable audio for this song" in content
    repo = BookRepository(db.session)
    assert repo.find_by_title("VIP Song", english.id) is None


def test_netease_import_no_lyrics_flashes(app, app_context, client, english):
    with patch(
        "lute.book.routes.netease_song_title", return_value="Instrumental"
    ), patch(
        "lute.book.routes.netease_audio_url",
        return_value="http://cdn.example.com/song.mp3",
    ), patch(
        "lute.book.routes.netease_lyric_content",
        side_effect=BookImportException("This song has no lyrics on NetEase"),
    ):
        resp = client.post(
            "/book/import_webpage",
            data={
                "import_type": "netease",
                "netease_url": "https://music.163.com/#/song?id=2",
                "language_id": str(english.id),
            },
            follow_redirects=True,
        )
    assert "has no lyrics" in resp.get_data(as_text=True)


def test_netease_read_page_uses_audio_backend(app, app_context, client, english):
    "A netease book renders the unified player's HTML5 audio element."
    from lute.book.model import Book
    from lute.book.service import Service as BookService

    b = Book()
    b.title = "NetEase Book"
    b.language_id = english.id
    b.text = "Hello world.\nGoodbye!"
    b.book_type = "netease"
    b.audio_filename = "song.mp3"
    b.source_uri = "https://music.163.com/#/song?id=1"
    b.srt_data = json.dumps(
        [
            {"start": 1.0, "end": 4.2, "text": "Hello world."},
            {"start": 5.0, "end": 8.5, "text": "Goodbye!"},
        ]
    )
    dbbook = BookService().import_book(b, db.session)

    resp = client.get(f"/read/{dbbook.id}/page/1")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)
    assert '"audio"' in content  # LUTE_YT_DATA.backend
    assert 'id="yt-audio-player"' in content
    assert "/useraudio/stream/" in content
