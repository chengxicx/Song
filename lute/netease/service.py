"""
NetEase Cloud Music (music.163.com) API helpers.

Powers the "NetEase Cloud Music" book import type: song metadata,
audio URL, and LRC lyrics.  Also implements QR-code login: logging in
stores the account's MUSIC_U cookie as a per-user setting, so songs
the account can play (incl. VIP / paid songs) can be imported.

The song endpoints are plain /api/ GETs needing no signature; the QR
login endpoints require POST (GETs return a 参数错误 400).
"""

import io
import re

import requests

from lute.db import db
from lute.models.repositories import UserSettingRepository
from lute.book.service import BookImportException
from lute.settings.current import refresh_global_settings

BASE_URL = "https://music.163.com"

# The setting key holding the MUSIC_U cookie value ('' = logged out).
# Stored as a dynamic user setting, so in multi-user mode each user
# logs in to their own account.
COOKIE_SETTING_KEY = "netease_cookie"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
}

# Songs are downloaded and stored locally (the CDN URLs expire, so
# remote streaming is not an option).  320 kbps mp3s of typical songs
# are 5-10 MB; the cap is generous for long "songs", above which the
# import tells the user to fall back to the MP3 import type.
NETEASE_MAX_AUDIO_BYTES = 100 * 1024 * 1024  # 100 MB


def netease_song_id(url):
    """
    Extract the song id from a music.163.com song URL, or return None.

    Handles the /#/song?id=... and /song/{id}/ forms (incl. the
    y.music.163.com / m.music.163.com mobile variants).  Other page
    types (playlists, albums, ...) are rejected so their ids are not
    mistaken for songs.
    """
    if not url or "music.163.com" not in url:
        return None
    m = re.search(r"/song/(\d+)", url)
    if m:
        return m.group(1)
    if re.search(r"/song\?", url):
        m = re.search(r"[?&]id=(\d+)", url)
        if m:
            return m.group(1)
    return None


def netease_song_title(song_id):
    """
    The song's display title: "Song name - Artist1/Artist2" (just the
    name when the song has no credited artist).
    """
    data = _api_get("/api/song/detail/", {"id": song_id, "ids": f"[{song_id}]"})
    songs = data.get("songs") or []
    if not songs:
        raise BookImportException(
            f"NetEase song {song_id} not found (deleted, or not a song link)."
        )
    song = songs[0]
    artists = "/".join(
        a.get("name", "") for a in song.get("artists") or [] if a.get("name")
    )
    name = (song.get("name") or "").strip() or f"NetEase song {song_id}"
    return f"{name} - {artists}" if artists else name


def netease_audio_url(song_id):
    """
    The song's playable mp3 URL (320 kbps when available).

    Raises when the account (anonymous, or the logged-in one) can't
    play the full song.
    """
    data = _api_get(
        "/api/song/enhance/player/url", {"ids": f"[{song_id}]", "br": "320000"}
    )
    info = (data.get("data") or [{}])[0]
    if info.get("freeTrialInfo"):
        raise BookImportException(
            "NetEase only returned a preview snippet: the logged-in account "
            "doesn't have full play access to this song."
        )
    url = info.get("url")
    if not url:
        raise BookImportException(
            "No playable audio for this song (copyright-restricted, VIP-only, "
            "or delisted).  Log in via Settings with an account that can "
            "play it, or import the audio manually as an MP3 book."
        )
    return url


def netease_lyric_content(song_id):
    "The song's LRC lyrics text, raising when the song has none."
    data = _api_get(
        "/api/song/lyric", {"id": song_id, "lv": "1", "kv": "1", "tv": "-1"}
    )
    lrc = ((data.get("lrc") or {}).get("lyric") or "").strip()
    if not lrc:
        raise BookImportException(
            "This song has no lyrics on NetEase (purely instrumental?)."
        )
    return lrc


# ---------------------------------------------------------------------
# QR-code login
# ---------------------------------------------------------------------


def qr_key():
    """
    Start a QR login.

    Returns (unikey, qr_svg), where the QR image encodes
    https://music.163.com/login?codekey={unikey} -- scanning it with
    the NetEase Cloud Music app asks the user to authorize.
    """
    try:
        resp = requests.post(
            f"{BASE_URL}/api/login/qrcode/unikey",
            data={"type": "1"},
            headers=_BROWSER_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        raise BookImportException(
            f"Could not start NetEase QR login ({e}).", cause=e
        ) from e
    # The raw endpoint returns {"code": 200, "unikey": "..."}; some
    # wrappers nest it under "data", so accept both shapes.
    key = data.get("unikey") or (data.get("data") or {}).get("unikey")
    if data.get("code") != 200 or not key:
        raise BookImportException(
            f"Could not start NetEase QR login (code {data.get('code')})."
        )
    return key, _qr_svg(f"{BASE_URL}/login?codekey={key}")


def qr_check(key):
    """
    Poll the QR login state once.

    Returns {"state": "waiting" | "scanned" | "expired" | "confirmed"}
    plus "nickname" on confirmation.  On confirmation the MUSIC_U
    cookie from the response is stored as a user setting.
    """
    try:
        resp = requests.post(
            f"{BASE_URL}/api/login/qrcode/client/login",
            data={"key": key or "", "type": "1"},
            headers=_BROWSER_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        raise BookImportException(
            f"NetEase QR login check failed ({e}).", cause=e
        ) from e
    code = data.get("code")
    if code == 800:
        return {"state": "expired"}
    if code == 801:
        return {"state": "waiting"}
    if code == 802:
        return {"state": "scanned"}
    if code == 803:
        cookie = resp.cookies.get("MUSIC_U")
        if not cookie:
            m = re.search(r"MUSIC_U=([^;]+)", resp.headers.get("Set-Cookie", ""))
            cookie = m.group(1) if m else None
        if not cookie:
            raise BookImportException(
                "NetEase confirmed the login but returned no MUSIC_U cookie."
            )
        save_cookie_value(cookie)
        profile = account_profile() or {}
        return {"state": "confirmed", "nickname": profile.get("nickname", "")}
    raise BookImportException(f"Unexpected NetEase QR login response (code {code}).")


def account_profile():
    """
    {'nickname', 'vip'} for the logged-in account, or None when no
    (working) cookie is stored.
    """
    if not _stored_cookie():
        return None
    try:
        resp = requests.get(
            f"{BASE_URL}/api/nuser/account/get", headers=_headers(), timeout=15
        )
        profile = (resp.json() or {}).get("profile") or {}
    except (requests.exceptions.RequestException, ValueError):
        return None
    if not profile:
        return None
    return {
        "nickname": profile.get("nickname", ""),
        "vip": (profile.get("vipType") or 0) > 0,
    }


def save_cookie(raw):
    """
    Manual-login fallback: store a pasted MUSIC_U value (or a whole
    cookie header, from which MUSIC_U is extracted).  Returns the
    account profile for display.
    """
    raw = (raw or "").strip()
    if not raw:
        raise BookImportException("Paste the MUSIC_U cookie value first.")
    m = re.search(r"MUSIC_U=([^;\s]+)", raw)
    save_cookie_value(m.group(1) if m else raw)
    return account_profile()


def clear_cookie():
    "Log out: clear the stored cookie."
    save_cookie_value("")


def save_cookie_value(value):
    "Persist the MUSIC_U value and refresh the cached settings."
    repo = UserSettingRepository(db.session)
    repo.set_dynamic_value(COOKIE_SETTING_KEY, value)
    db.session.commit()
    refresh_global_settings(db.session)


# ---------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------


def _stored_cookie():
    "The stored MUSIC_U value ('' when logged out)."
    return UserSettingRepository(db.session).get_dynamic_value(COOKIE_SETTING_KEY) or ""


def _headers():
    "Request headers, with the MUSIC_U cookie when logged in."
    h = dict(_BROWSER_HEADERS)
    cookie = _stored_cookie()
    if cookie:
        h["Cookie"] = f"MUSIC_U={cookie}"
    return h


def _api_get(path, params=None):
    "GET a music.163.com /api/ endpoint, raising BookImportException on errors."
    try:
        resp = requests.get(
            f"{BASE_URL}{path}", params=params, headers=_headers(), timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        raise BookImportException(f"Could not reach NetEase ({e}).", cause=e) from e
    code = data.get("code")
    if code != 200:
        raise BookImportException(f"NetEase API error (code {code}).")
    return data


def _qr_svg(content):
    "Render text as an inline-embeddable QR code SVG string."
    import qrcode
    import qrcode.image.svg

    qr = qrcode.QRCode(
        image_factory=qrcode.image.svg.SvgPathImage,
        box_size=12,
        border=2,
    )
    qr.add_data(content)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image().save(buf)
    svg = buf.getvalue().decode("utf-8")
    # Drop the XML declaration and the mm-based width/height so the
    # SVG can be embedded inline and sized via CSS (the viewBox keeps
    # it scalable).
    svg = re.sub(r"<\?xml[^>]*\?>\s*", "", svg)
    svg = re.sub(r'\s(?:width|height)="[^"]*"', "", svg, count=2)
    return svg
