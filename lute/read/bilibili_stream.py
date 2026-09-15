"""Backend for playing Bilibili videos as a direct DASH stream.

Bilibili's official iframe player refuses to initialise when embedded on
a non-whitelisted domain (it shows its splash screen but never loads the
video and never talks over postMessage).  To work around that we bypass
the player entirely and play the raw DASH stream (fragmented MP4 video +
audio) ourselves:

  * ``stream_info`` calls Bilibili's public ``view`` and ``playurl``
    APIs (no login required) to obtain the fragmented-MP4 segment URLs
    and the codec / init-range metadata.
  * ``build_mpd`` turns that into an on-demand DASH manifest whose
    BaseURLs point at our own proxy endpoints.  Every video rendition
    Bilibili offers becomes its own Representation, so the player can
    switch quality without refetching the manifest; they are ordered
    lowest-bitrate first because the stream is relayed through a narrow
    egress and the low end is what should be used by default.
  * ``proxy_stream`` relays the byte ranges dash.js requests on to
    Bilibili's CDN, adding the headers the CDN requires (Referer, UA),
    so the browser never talks to Bilibili directly and the stream is
    not blocked by CORS / anti-leeching.

Stream info is cached briefly because the playurl segment URLs carry an
expiry (deadline) and we don't want to hit the API on every segment.
"""

import time
from xml.sax.saxutils import escape as _xml_escape

import requests

from lute.utils.outbound_proxy import bilibili_proxies

# Sentinel for the availability of the bilibili page / stream.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_REFERER = "https://www.bilibili.com"

# Bilibili offers the same quality in more than one codec (AVC/H.264 is
# codecid 7, HEVC is 12) and lists them all under the same quality id.
# Only AVC is safe to hand to a browser as DASH-in-MP4: an HEVC rendition
# that wins a bandwidth-based pick fails to decode, which shows up as a
# black player and no error message at all.  Preference, not a filter --
# if a video is HEVC-only we still play it rather than refusing.
_VIDEO_CODEC_PREFERENCE = ("avc1", "avc3")
_AUDIO_CODEC_PREFERENCE = ("mp4a",)

# name -> (expiry_ts, info)
_stream_cache = {}
_STREAM_TTL = 30 * 60  # playurl URLs last ~2h; refresh well before that.



class BilibiliStreamError(Exception):
    """A Bilibili stream could not be obtained or relayed.

    This covers two very different situations, both of which used to
    escape as an unhandled ``requests`` exception (and therefore as a
    500 error page, which the DASH player cannot parse):

      * the video genuinely has no usable stream (deleted, region-locked,
        page number out of range), and
      * Bilibili's risk control refused the request outright.  It answers
        HTTP 412 / ``{"code": -412, "message": "request was banned"}``
        for IP ranges it distrusts, notably datacenter and overseas
        addresses, no matter which headers or cookies are sent.  A server
        on such an address can never proxy the stream -- see the
        ``_risk_control_hint`` below.
    """


def _risk_control_hint(err):
    "Extra guidance for the status codes Bilibili uses to ban a client."
    code = getattr(getattr(err, "response", None), "status_code", None)
    if code in (403, 412, 429):
        return (
            " -- Bilibili is refusing this server's IP (risk control). "
            "It cannot be worked around with headers or cookies; the host "
            "needs an egress IP that Bilibili accepts, or the video has to "
            "be played with the official embed instead."
        )
    return ""


def _api_get_json(url, what):
    """GET a Bilibili JSON API and return its ``data``, or raise.

    Every failure is funnelled into BilibiliStreamError so callers never
    have to guess at the exception type.
    """
    try:
        r = requests.get(
            url, timeout=10, headers=_api_headers(), proxies=bilibili_proxies()
        )
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.HTTPError as e:
        raise BilibiliStreamError(
            f"{what} request failed: {e}{_risk_control_hint(e)}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise BilibiliStreamError(f"{what} request failed: {e}") from e
    except ValueError as e:
        # A non-JSON body (Bilibili serves an HTML risk-control page for
        # some blocked requests) reaches here via r.json().
        raise BilibiliStreamError(f"{what} returned a non-JSON response") from e
    if data.get("code") != 0:
        raise BilibiliStreamError(
            data.get("message") or f"{what} error (code {data.get('code')})"
        )
    return data["data"]


def _api_headers():
    return {"User-Agent": _UA, "Referer": _REFERER}


def _fetch_view(bvid):
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    return _api_get_json(url, "Bilibili view API")


def _fetch_playurl(bvid, cid):
    url = (
        "https://api.bilibili.com/x/player/playurl"
        f"?bvid={bvid}&cid={cid}&fnval=16&fourk=1&qn=64&platform=pc&high_quality=1"
    )
    return _api_get_json(url, "Bilibili playurl API")


def _codec_preferred(stream, prefixes):
    "True when the stream's codec string starts with one of the prefixes."
    codecs = (stream.get("codecs") or "").lower()
    return any(codecs.startswith(p) for p in prefixes)


def _usable_renditions(streams, codec_prefixes):
    """Return playable renditions, lowest bandwidth first.

    Preference order is: decodable codec, then bitrate ascending.  A
    lowest-first list matters twice over -- the caller takes the first
    entry as the default (the egress is narrow, so the cheap end should
    be what plays unless the reader asks otherwise) and the DASH manifest
    keeps that order so a rendition index means the same thing to the
    player as it does here.

    Renditions that would look identical to the player (same codec, same
    bitrate and size) are collapsed: Bilibili does list the same audio
    track twice.  Streams with no URL are dropped, since there is nothing
    to relay.
    """
    usable = [
        s
        for s in (streams or [])
        if (s.get("baseUrl") or s.get("base_url"))
    ]
    preferred = [s for s in usable if _codec_preferred(s, codec_prefixes)]
    chosen = preferred or usable  # never end up with nothing to play
    seen = set()
    out = []
    for s in sorted(chosen, key=lambda s: int(s.get("bandwidth") or 0)):
        key = (
            s.get("codecs"),
            int(s.get("bandwidth") or 0),
            s.get("width"),
            s.get("height"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def stream_info(bvid, page=1):
    """
    Return the DASH stream metadata for a Bilibili video.

    Returns a dict with keys: duration, cid, videos, video, audio.

    ``videos`` is every video rendition Bilibili offers for this page,
    lowest bandwidth first, each shaped as (baseUrl, mimeType, codecs,
    bandwidth, width, height, init, index).  ``video`` is simply the
    first of them: the default is the *cheapest* rendition, because the
    stream is relayed through a narrow egress (see
    lute/utils/outbound_proxy.py) and the reader can pick a higher one
    from the player's settings menu when they want it.  ``audio`` is the
    highest-bandwidth track, which is often the *larger* half of the
    bytes (measured on one 45-part video: 102 kbps audio against 42 and
    61 kbps video) -- but intelligibility is the whole point of a
    listening book, so it is not traded away for egress.

    Raises BilibiliStreamError if the video is unavailable or has no DASH
    streams, or if Bilibili cannot be reached (its API bans the server's
    IP outright, so this is the normal failure and callers should degrade
    rather than error out).
    Results are cached for _STREAM_TTL per (bvid, page), so the API leg
    runs at most once per half hour even while segments stream through.
    """
    key = (bvid, page)
    now = time.time()
    cached = _stream_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]

    view = _fetch_view(bvid)
    pages = view.get("pages") or []
    if not pages or page < 1 or page > len(pages):
        raise BilibiliStreamError("Video page not found")
    cid = pages[page - 1]["cid"]
    # The view API's top-level "duration" sums all pages of a multi-part
    # video (the whole collection).  Use the selected page's own
    # duration so the progress bar reflects just this one episode.
    duration = pages[page - 1].get("duration") or view.get("duration") or 0

    play = _fetch_playurl(bvid, cid)
    dash = play.get("dash") or {}
    videos = _usable_renditions(dash.get("video"), _VIDEO_CODEC_PREFERENCE)
    audios = _usable_renditions(dash.get("audio"), _AUDIO_CODEC_PREFERENCE)
    if not videos or not audios:
        raise BilibiliStreamError("No playable stream for this video")

    def _seg(s):
        sb = s.get("SegmentBase") or s.get("segment_base") or {}
        return {
            "baseUrl": s.get("baseUrl") or s.get("base_url"),
            "mimeType": s.get("mimeType") or s.get("mime_type") or "video/mp4",
            "codecs": s.get("codecs") or "",
            "bandwidth": int(s.get("bandwidth") or 0),
            "width": int(s.get("width") or 0),
            "height": int(s.get("height") or 0),
            "init": sb.get("initialization") or sb.get("Initialization"),
            "index": sb.get("index_range") or sb.get("indexRange"),
        }

    info = {
        "bvid": bvid,
        "cid": cid,
        "duration": duration,
        "videos": [_seg(v) for v in videos],
        "video": _seg(videos[0]),
        "audio": _seg(audios[-1]),
    }
    _stream_cache[key] = (now + _STREAM_TTL, info)
    return info


def invalidate_stream(bvid):
    """Drop cached stream info for a video (used when a book is re-imported)."""
    for k in [k for k in _stream_cache if k[0] == bvid]:
        _stream_cache.pop(k, None)


_MPD_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"
     profiles="urn:mpeg:dash:profile:isoff-on-demand:2011"
     type="static" mediaPresentationDuration="PT{duration:.3f}S"
     minBufferTime="PT1.5S">
  <Period duration="PT{duration:.3f}S">
    <AdaptationSet mimeType="{video_mime}" segmentAlignment="true" startWithSAP="1">
{video_reps}    </AdaptationSet>
    <AdaptationSet mimeType="{audio_mime}" segmentAlignment="true" startWithSAP="1">
      <Representation id="audio" mimeType="{audio_mime}" codecs="{audio_codecs}"
                      bandwidth="{audio_bw}">
        <BaseURL>{audio_proxy}</BaseURL>
        <SegmentBase indexRange="{audio_index}">
          <Initialization range="{audio_init}"/>
        </SegmentBase>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""

# One video rendition.  Its BaseURL carries the rendition index (``q``), so
# the proxy knows which CDN stream to relay.  That makes the URL contain an
# ampersand, which XML does not allow bare -- hence escaping at the call
# site: an unescaped one makes the whole manifest unparseable and the
# player simply never starts.
_VIDEO_REP_TEMPLATE = """      <Representation id="video-{i}" mimeType="{mime}" codecs="{codecs}"
                      bandwidth="{bw}" width="{w}" height="{h}">
        <BaseURL>{proxy}</BaseURL>
        <SegmentBase indexRange="{index}">
          <Initialization range="{init}"/>
        </SegmentBase>
      </Representation>
"""


def build_mpd(info, video_proxies, audio_proxy):
    """Build an on-demand DASH manifest from stream_info.

    Every available video rendition becomes a Representation, all served
    through our own proxy, so the player can switch quality inside the
    settings menu without refetching the manifest or losing its place.
    They are emitted lowest-bandwidth first, which is both the default
    and the order the player's rendition indices rely on.

    ``video_proxies`` may be a single URL (used for every rendition) or a
    list parallel to ``info["videos"]``.
    """
    v = info["video"]
    a = info["audio"]
    renditions = info.get("videos") or [v]
    if isinstance(video_proxies, (list, tuple)):
        proxies = list(video_proxies)
    else:
        proxies = [video_proxies]

    reps = []
    for i, r in enumerate(renditions):
        proxy = proxies[i] if i < len(proxies) else proxies[-1]
        reps.append(
            _VIDEO_REP_TEMPLATE.format(
                i=i,
                mime=r.get("mimeType") or "video/mp4",
                codecs=r.get("codecs") or "avc1.64001F",
                bw=r.get("bandwidth") or 0,
                w=r.get("width") or 0,
                h=r.get("height") or 0,
                proxy=_xml_escape(proxy),
                index=_xml_escape(r.get("index") or ""),
                init=_xml_escape(r.get("init") or ""),
            )
        )

    return _MPD_TEMPLATE.format(
        duration=info["duration"],
        video_mime=v.get("mimeType") or "video/mp4",
        video_reps="".join(reps),
        audio_mime=a.get("mimeType") or "audio/mp4",
        audio_codecs=a.get("codecs") or "mp4a.40.2",
        audio_bw=a.get("bandwidth") or 0,
        audio_proxy=_xml_escape(audio_proxy),
        audio_index=_xml_escape(a.get("index") or ""),
        audio_init=_xml_escape(a.get("init") or ""),
    )


def proxy_stream(base_url, range_header):
    """
    Relay a Range request to the Bilibili CDN and return (status, headers,
    iterable).  ``base_url`` is the CDN segment URL; ``range_header`` is the
    incoming ``Range`` header (or None).
    """
    headers = _api_headers()
    if range_header:
        headers["Range"] = range_header
    try:
        r = requests.get(
            base_url,
            headers=headers,
            stream=True,
            timeout=30,
            proxies=bilibili_proxies(),
        )
    except requests.exceptions.RequestException as e:
        # A CDN segment host -- often an obfuscated PCDN node that only
        # serves mainland clients -- can be unreachable from the server.
        raise BilibiliStreamError(f"CDN segment request failed: {e}") from e
    resp_headers = {
        "Content-Type": r.headers.get("Content-Type", "application/octet-stream"),
        "Accept-Ranges": r.headers.get("Accept-Ranges", "bytes"),
    }
    if r.headers.get("Content-Range"):
        resp_headers["Content-Range"] = r.headers["Content-Range"]
    if r.headers.get("Content-Length"):
        resp_headers["Content-Length"] = r.headers["Content-Length"]
    return r.status_code, resp_headers, r.iter_content(chunk_size=8192)