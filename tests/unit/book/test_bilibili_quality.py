"""
Video quality selection for Bilibili books.

The stream is relayed through a narrow egress (an SSH reverse tunnel to
an accepted IP -- see lute/utils/outbound_proxy.py), so the cheapest
rendition Bilibili offers is the right default and the reader raises it
deliberately from the player's settings menu when they want to.  That
makes two things load-bearing:

  * the default is the LOWEST bitrate, not the highest (it used to be
    the highest, which is the opposite of what a relay wants);
  * every rendition reaches the player in one manifest, so switching
    quality does not need a re-request, a reload, or a lost position.

The manifest carries several renditions for the first time, which also
made its BaseURLs contain "&q=" -- and a bare ampersand is not legal
XML.  An unescaped one makes the whole manifest unparseable, and the
player then simply never starts, so these tests parse it for real rather
than eyeballing the string.
"""

import os
import re
import xml.etree.ElementTree as ET
from unittest.mock import patch

from bs4 import BeautifulSoup

import lute
from lute.read import bilibili_stream

_STATIC_DIR = os.path.join(os.path.dirname(lute.__file__), "static")
_TEMPLATES_DIR = os.path.join(os.path.dirname(lute.__file__), "templates")

_TEMPLATE = os.path.join(_TEMPLATES_DIR, "read", "bilibili_player.html")
_CSS = os.path.join(_STATIC_DIR, "css", "player-styles.css")
_JS = os.path.join(_STATIC_DIR, "js", "bilibili-player.js")

_MPD_NS = "{urn:mpeg:dash:schema:mpd:2011}"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------
# Rendition fixtures, shaped like Bilibili's real playurl payload.
# ---------------------------------------------------------------------


def _seg(init="0-799", index="800-1399"):
    return {"Initialization": init, "indexRange": index}


AVC_480 = {
    "id": 32,
    "codecid": 7,
    "bandwidth": 61143,
    "codecs": "avc1.64001F",
    "width": 852,
    "height": 480,
    "baseUrl": "https://cdn.example/480.m4s",
    "SegmentBase": _seg(),
}
AVC_360 = {
    "id": 16,
    "codecid": 7,
    "bandwidth": 42049,
    "codecs": "avc1.64001E",
    "width": 640,
    "height": 360,
    "baseUrl": "https://cdn.example/360.m4s",
    "SegmentBase": _seg(),
}
# Deliberately faster than the AVC rendition: a real payload does offer
# HEVC at a higher bandwidth, and a bandwidth-only pick would choose it.
HEVC_480 = {
    "id": 32,
    "codecid": 12,
    "bandwidth": 99000,
    "codecs": "hev1.1.6.L120.90",
    "width": 852,
    "height": 480,
    "baseUrl": "https://cdn.example/480hevc.m4s",
    "SegmentBase": _seg(),
}
AUDIO_192 = {
    "id": 30280,
    "bandwidth": 90957,
    "codecs": "mp4a.40.2",
    "baseUrl": "https://cdn.example/a192.m4s",
    "SegmentBase": _seg(),
}
AUDIO_132 = {
    "id": 30232,
    "bandwidth": 90957,
    "codecs": "mp4a.40.2",
    "baseUrl": "https://cdn.example/a132.m4s",
    "SegmentBase": _seg(),
}
AUDIO_64 = {
    "id": 30216,
    "bandwidth": 67348,
    "codecs": "mp4a.40.2",
    "baseUrl": "https://cdn.example/a64.m4s",
    "SegmentBase": _seg(),
}

_VIEW = {"duration": 7200, "pages": [{"cid": 101, "duration": 193}]}


def _stream_info(bvid, video, audio, page=1):
    """stream_info with the API layer stubbed out."""
    play = {"dash": {"video": video, "audio": audio, "duration": 193}}
    with patch.object(bilibili_stream, "_fetch_view", return_value=_VIEW), patch.object(
        bilibili_stream, "_fetch_playurl", return_value=play
    ):
        return bilibili_stream.stream_info(bvid, page)


# ---------------------------------------------------------------------
# Default = cheapest rendition
# ---------------------------------------------------------------------


def test_default_rendition_is_the_lowest_bitrate():
    "The relay is narrow, so the cheap end plays unless asked otherwise."
    info = _stream_info("BV1quality01", [AVC_480, AVC_360], [AUDIO_192])
    assert info["video"]["height"] == 360
    assert info["video"]["bandwidth"] == 42049
    assert "360" in info["video"]["baseUrl"]


def test_every_rendition_is_kept_for_manual_switching():
    info = _stream_info("BV1quality02", [AVC_480, AVC_360], [AUDIO_192])
    assert [v["height"] for v in info["videos"]] == [360, 480], (
        "renditions must be listed lowest-bitrate first: the player takes"
        " the first as its default and indexes into this order"
    )
    assert info["videos"][0] == info["video"]


def test_avc_is_preferred_over_a_faster_hevc_rendition():
    """
    HEVC wins a bandwidth-only comparison, and a browser that cannot
    decode it shows a black player with no error to explain itself.
    """
    info = _stream_info("BV1quality03", [HEVC_480, AVC_480, AVC_360], [AUDIO_192])
    assert [v["height"] for v in info["videos"]] == [360, 480]
    assert not any(
        "hev" in v["codecs"] for v in info["videos"]
    ), "an undecodable rendition must not be offered in the menu"


def test_hevc_only_video_still_plays():
    "Preference, not a filter: with nothing else on offer, take it."
    info = _stream_info("BV1quality04", [HEVC_480], [AUDIO_192])
    assert info["video"]["codecs"].startswith("hev")


def test_renditions_without_a_url_are_dropped():
    "Nothing to relay means nothing to offer, however cheap it looks."
    no_url = {"bandwidth": 1, "codecs": "avc1.64001E", "height": 144}
    info = _stream_info("BV1quality05", [no_url, AVC_480], [AUDIO_192])
    assert [v["height"] for v in info["videos"]] == [480]


def test_identical_renditions_are_collapsed():
    "Bilibili does list the same audio track twice."
    info = _stream_info("BV1quality06", [AVC_480], [AUDIO_192, AUDIO_132])
    assert len(info["videos"]) == 1
    assert info["audio"]["baseUrl"].endswith("a192.m4s")


def test_audio_stays_the_best_available_track():
    """
    Audio is a small fraction of the bytes next to the video, and
    intelligibility is the point of a listening book, so it is not the
    first thing to trade away for egress.
    """
    info = _stream_info("BV1quality07", [AVC_480], [AUDIO_64, AUDIO_192])
    assert info["audio"]["bandwidth"] == 90957


# ---------------------------------------------------------------------
# The manifest carries all of them
# ---------------------------------------------------------------------


def _manifest(bvid, video=None, audio=None):
    video = video if video is not None else [AVC_480, AVC_360]
    audio = audio if audio is not None else [AUDIO_192]
    info = _stream_info(bvid, video, audio)
    proxies = [
        f"/read/bilibili/stream/proxy/{bvid}/video?page=1&q={i}"
        for i in range(len(info["videos"]))
    ]
    proxies_audio = f"/read/bilibili/stream/proxy/{bvid}/audio?page=1"
    return info, bilibili_stream.build_mpd(info, proxies, proxies_audio)


def test_manifest_is_well_formed_xml():
    """
    A bare '&' in a BaseURL query string makes the manifest unparseable,
    and dash.js then never starts playback -- with nothing in the UI to
    explain why.
    """
    _, mpd = _manifest("BV1quality08")
    ET.fromstring(mpd)  # raises ParseError if the URLs are not escaped


def test_manifest_escapes_the_quality_parameter():
    _, mpd = _manifest("BV1quality09")
    assert "&amp;q=" in mpd, "the quality parameter must be XML-escaped"
    assert not re.search(r"[^;]&q=", mpd), "a bare '&' must not appear"


def test_manifest_has_one_representation_per_video_rendition():
    _, mpd = _manifest("BV1quality10")
    root = ET.fromstring(mpd)
    reps = root.findall(f".//{_MPD_NS}Representation")
    assert len(reps) == 3, "two video renditions plus the audio track"
    videos = [r for r in reps if r.get("id").startswith("video-")]
    assert [r.get("height") for r in videos] == ["360", "480"], (
        "representations must stay in lowest-bitrate-first order so the"
        " player's rendition index matches the proxy's q index"
    )
    assert [b.text for b in videos[0].findall(f"{_MPD_NS}BaseURL")] == [
        "/read/bilibili/stream/proxy/BV1quality10/video?page=1&q=0"
    ]


def test_manifest_carries_every_quality_parameter():
    info, mpd = _manifest("BV1quality11")
    for i in range(len(info["videos"])):
        assert f"&amp;q={i}" in mpd, f"rendition {i} has no proxy URL"


def test_manifest_accepts_a_single_proxy_url():
    "A caller that has only one URL must not blow up the whole manifest."
    info = _stream_info("BV1quality12", [AVC_480, AVC_360], [AUDIO_192])
    mpd = bilibili_stream.build_mpd(info, "/one/proxy/url", "/audio/url")
    root = ET.fromstring(mpd)
    urls = [b.text for b in root.findall(f".//{_MPD_NS}BaseURL")]
    assert urls[:2] == ["/one/proxy/url", "/one/proxy/url"]


# ---------------------------------------------------------------------
# Routes: the manifest advertises q, the proxy honours it
# ---------------------------------------------------------------------


def test_mpd_route_advertises_a_url_per_rendition(client):
    info = _stream_info("BV1quality13", [AVC_480, AVC_360], [AUDIO_192])
    with patch.object(bilibili_stream, "stream_info", return_value=info):
        resp = client.get("/read/bilibili/stream/mpd/BV1quality13?page=1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "q=0" in body and "q=1" in body


def _proxy_capture(client, url):
    """GET a proxy URL, returning the CDN URL the server would relay."""
    captured = {}

    def fake_proxy(base_url, range_header):
        captured["baseUrl"] = base_url
        return 206, {}, iter([b""])

    with patch.object(bilibili_stream, "proxy_stream", side_effect=fake_proxy):
        resp = client.get(url)
    return resp, captured


def test_proxy_serves_the_cheapest_rendition_by_default(client):
    info = _stream_info("BV1quality14", [AVC_480, AVC_360], [AUDIO_192])
    with patch.object(bilibili_stream, "stream_info", return_value=info):
        resp, captured = _proxy_capture(
            client, "/read/bilibili/stream/proxy/BV1quality14/video?page=1"
        )
    assert resp.status_code == 206
    assert captured["baseUrl"].endswith("360.m4s")


def test_proxy_serves_the_requested_rendition(client):
    info = _stream_info("BV1quality15", [AVC_480, AVC_360], [AUDIO_192])
    with patch.object(bilibili_stream, "stream_info", return_value=info):
        resp, captured = _proxy_capture(
            client, "/read/bilibili/stream/proxy/BV1quality15/video?page=1&q=1"
        )
    assert resp.status_code == 206
    assert captured["baseUrl"].endswith("480.m4s"), "q=1 is the 480p rendition"


def test_proxy_rejects_an_out_of_range_rendition(client):
    "A stale manifest must not be able to name a rendition that does not exist."
    info = _stream_info("BV1quality16", [AVC_480, AVC_360], [AUDIO_192])
    with patch.object(bilibili_stream, "stream_info", return_value=info), patch.object(
        bilibili_stream, "proxy_stream"
    ) as proxy:
        resp = client.get("/read/bilibili/stream/proxy/BV1quality16/video?page=1&q=99")
    assert resp.status_code == 400
    assert resp.is_json
    assert not proxy.called, "nothing should be relayed for a bad index"


def test_proxy_ignores_quality_for_audio(client):
    info = _stream_info("BV1quality17", [AVC_480, AVC_360], [AUDIO_192])
    with patch.object(bilibili_stream, "stream_info", return_value=info):
        resp, captured = _proxy_capture(
            client, "/read/bilibili/stream/proxy/BV1quality17/audio?page=1&q=7"
        )
    assert resp.status_code == 206
    assert captured["baseUrl"].endswith("a192.m4s")


def test_proxy_still_works_with_single_rendition_info(client):
    """
    Info that carries only the chosen rendition has nothing to index
    into, so the proxy must serve it rather than reject the request.
    """
    info = {
        "video": {"baseUrl": "https://cdn.example/only.m4s"},
        "audio": {"baseUrl": "https://cdn.example/a.m4s"},
    }
    with patch.object(bilibili_stream, "stream_info", return_value=info):
        resp, captured = _proxy_capture(
            client, "/read/bilibili/stream/proxy/BV1quality18/video?page=1&q=1"
        )
    assert resp.status_code == 206
    assert captured["baseUrl"].endswith("only.m4s")


# ---------------------------------------------------------------------
# Front-end guardrails
# ---------------------------------------------------------------------


def test_quality_menu_lives_in_the_settings_dropdown():
    soup = BeautifulSoup(_read(_TEMPLATE), "html.parser")
    select = soup.find(id="yt-quality-select")
    assert select is not None, "the template must ship the quality picker"
    assert (
        select.find_parent(id="yt-settings-dropdown") is not None
    ), "the picker belongs in the settings menu, next to Audio only"
    row = soup.find(id="yt-quality-row")
    assert row is not None and row.has_attr(
        "hidden"
    ), "the row starts hidden: it is only shown when there is a choice"


def test_hidden_quality_row_actually_hides():
    """
    .yt-settings-option sets display:flex, which beats the browser's own
    [hidden] rule -- without an explicit override the row would show up
    empty on every video.
    """
    css = _read(_CSS)
    matches = re.findall(r"\.yt-quality-option\[hidden\]\s*\{([^}]*)\}", css)
    assert matches, "no CSS rule hides the quality row when hidden"
    assert "display: none" in re.sub(r"\s+", " ", matches[-1])


def test_quality_control_inherits_its_colours():
    """
    Themes restyle the settings panel (the production theme makes it
    dark), so a hardcoded light background here would be unreadable.
    """
    css = _read(_CSS)
    body = re.findall(r"\.yt-quality-option select\s*\{([^}]*)\}", css)[-1]
    body = re.sub(r"\s+", " ", body)
    assert "color: inherit" in body
    assert "background-color: transparent" in body
    assert "#fff" not in body and "#ffffff" not in body


def test_adaptive_switching_is_disabled_for_video():
    """
    ABR climbs back up as soon as the throughput estimate improves, which
    over the relay means re-fetching at a bitrate the uplink cannot hold.
    """
    js = _read(_JS)
    assert (
        "autoSwitchBitrate: { video: false }" in js
    ), "video ABR must be off or the reader's low-bitrate default is undone"
    assert (
        "updateSettings" in js
    ), "dash.js 4.7 dropped setAutoSwitchQualityFor; settings is the way"


def test_menu_populates_from_the_manifest_and_defaults_to_the_cheapest():
    js = _read(_JS)
    assert (
        'player.on("manifestLoaded"' in js
    ), "the rendition list only exists once the manifest has been parsed"
    options = js[js.index("function ytQualityOptions") :]
    options = options[: options.index("function ytPopulateQualityControls")]
    assert (
        "getVideoBitrates" in options
    ), "the menu must reflect what this video actually offers"
    body = js[js.index("function ytPopulateQualityControls") :]
    body = body[: body.index("function ytApplyVideoQuality")]
    assert (
        "if (!pick) pick = items[0];" in body
    ), "the fallback when nothing is remembered must be the cheapest"


def test_a_single_rendition_shows_no_menu():
    js = _read(_JS)
    body = js[js.index("function ytPopulateQualityControls") :]
    body = body[: body.index("function ytApplyVideoQuality")]
    assert (
        "items.length < 2" in body
    ), "a one-entry menu is not a choice and should not be shown"


def test_quality_choice_survives_a_reload_as_a_height():
    """
    An index only means something inside one manifest; a height carries
    across videos, so that is what gets remembered.
    """
    js = _read(_JS)
    assert 'var QUALITY_STORAGE_KEY = "biliVideoQuality";' in js
    body = js[js.index("function ytOnQualityChange") :]
    body = body[: body.index("function bindSubtitleInteractions")]
    assert "items[i].height" in body, "persist the height, not the index"
