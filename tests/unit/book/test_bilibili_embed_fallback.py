"""
Guardrails for the Bilibili embed fallback -- the path used when Song
cannot relay the video itself (see ytUseEmbedPlayer in
static/js/bilibili-player.js).

Bilibili's official embed player owns its own play/pause/volume controls,
so in that mode the iframe must be the topmost thing in the video area.
Anything drawn inside .yt-player-video-wrap sits above it: the loading
overlay is inset:0 with z-index:1 while the iframe has no z-index of its
own, so an overlay silently swallows every click -- the video plays but
looks frozen and cannot be controlled, and subtitle sync is already gone
in that mode, so there is nothing left working.

That exact regression shipped once; these tests hold the invariant.
"""

import os
import re

from bs4 import BeautifulSoup

import lute

_STATIC_DIR = os.path.join(os.path.dirname(lute.__file__), "static")
_TEMPLATES_DIR = os.path.join(os.path.dirname(lute.__file__), "templates")

_TEMPLATE = os.path.join(_TEMPLATES_DIR, "read", "bilibili_player.html")
_CSS = os.path.join(_STATIC_DIR, "css", "player-styles.css")
_JS = os.path.join(_STATIC_DIR, "js", "bilibili-player.js")
# Shared player engine (element lookups, ready-timeout) lives here.
_BASE_JS = os.path.join(_STATIC_DIR, "js", "media-player-base.js")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _css_rule(css, selector):
    """The last declaration block for an exactly-matching selector."""
    pattern = re.compile(re.escape(selector) + r"\s*\{([^}]*)\}", re.S)
    matches = pattern.findall(css)
    assert matches, f"No CSS rule for {selector}"
    return matches[-1]


def _zindex(body):
    match = re.search(r"z-index:\s*(-?\d+)", body)
    return int(match.group(1)) if match else 0


def test_embed_notice_is_outside_the_video_wrap():
    """
    The fallback's explanatory notice must be a sibling of the video wrap,
    not an overlay inside it.
    """
    soup = BeautifulSoup(_read(_TEMPLATE), "html.parser")
    notice = soup.find(id="bili-embed-notice")
    assert notice is not None, "bili-embed-notice is missing from the template"
    assert notice.find_parent(class_="yt-player-video-wrap") is None, (
        "bili-embed-notice is inside .yt-player-video-wrap, where it covers"
        " the iframe and swallows clicks meant for Bilibili's own player"
    )


def test_loading_overlay_is_switched_off_in_embed_mode():
    body = _css_rule(_read(_CSS), ".bili-embed-active .yt-player-loading")
    assert "display: none" in re.sub(r"\s+", " ", body), (
        "the loading overlay must be hidden while the embed player is active"
    )
    assert "!important" in body, (
        "the JS sets display inline on this element, so the rule needs"
        " !important to win"
    )


def test_embed_iframe_stacks_above_the_loading_overlay():
    css = _read(_CSS)
    overlay_z = _zindex(_css_rule(css, ".yt-player-loading"))
    frame_z = _zindex(_css_rule(css, ".yt-player-video-wrap .bili-embed-frame"))
    assert frame_z > overlay_z, (
        f"the embed iframe (z-index {frame_z}) must stack above the loading"
        f" overlay (z-index {overlay_z}), otherwise it cannot be clicked"
    )


def test_js_hides_the_overlay_when_the_fallback_engages():
    js = _read(_JS)
    start = js.index("function ytUseEmbedPlayer(")
    body = js[start : js.index("function ytOnError(")]
    assert 'api.els.loading.style.display = "none"' in body, (
        "ytUseEmbedPlayer must take the loading overlay out of the way"
    )
    assert "api.els.embedNotice.hidden = false" in body, (
        "ytUseEmbedPlayer must report the fallback in the sibling notice"
    )
    assert 'getElementById("bili-embed-notice")' in _read(_BASE_JS), (
        "the notice the JS shows must be the element the template ships"
    )


def test_ready_timeout_does_not_fire_in_embed_mode():
    """
    The "player never became ready" timer would re-show the overlay on top
    of a perfectly good embed, because the embed never sets ytPlayerReady.
    """
    js = _read(_BASE_JS)
    assert "!ytPlayerReady && !ytEmbedMode" in js, (
        "the 15s ready-timeout must be skipped in embed mode"
    )
