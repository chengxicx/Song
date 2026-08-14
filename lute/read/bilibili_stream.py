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
    BaseURLs point at our own proxy endpoints.
  * ``proxy_stream`` relays the byte ranges dash.js requests on to
    Bilibili's CDN, adding the headers the CDN requires (Referer, UA),
    so the browser never talks to Bilibili directly and the stream is
    not blocked by CORS / anti-leeching.

Stream info is cached briefly because the playurl segment URLs carry an
expiry (deadline) and we don't want to hit the API on every segment.
"""

import time
import requests

# Sentinel for the availability of the bilibili page / stream.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_REFERER = "https://www.bilibili.com"

# name -> (expiry_ts, info)
_stream_cache = {}
_STREAM_TTL = 30 * 60  # playurl URLs last ~2h; refresh well before that.


def _api_headers():
    return {"User-Agent": _UA, "Referer": _REFERER}


def _fetch_view(bvid):
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    r = requests.get(url, timeout=10, headers=_api_headers())
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise ValueError(data.get("message") or "Bilibili view API error")
    return data["data"]


def _fetch_playurl(bvid, cid):
    url = (
        "https://api.bilibili.com/x/player/playurl"
        f"?bvid={bvid}&cid={cid}&fnval=16&fourk=1&qn=64&platform=pc&high_quality=1"
    )
    r = requests.get(url, timeout=10, headers=_api_headers())
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise ValueError(data.get("message") or "Bilibili playurl API error")
    return data["data"]


def _pick(streams):
    """Return the highest-bandwidth stream from a DASH list."""
    best = None
    for s in streams or []:
        bw = s.get("bandwidth") or 0
        if best is None or bw > best.get("bandwidth", 0):
            best = s
    return best


def stream_info(bvid, page=1):
    """
    Return the DASH stream metadata for a Bilibili video.

    Returns a dict with keys: duration, video (baseUrl, mimeType, codecs,
    bandwidth, width, height, init, index), audio (baseUrl, mimeType,
    codecs, bandwidth, init, index).  Raises ValueError if the video is
    unavailable or has no DASH streams.
    """
    key = (bvid, page)
    now = time.time()
    cached = _stream_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]

    view = _fetch_view(bvid)
    pages = view.get("pages") or []
    if not pages or page < 1 or page > len(pages):
        raise ValueError("Video page not found")
    cid = pages[page - 1]["cid"]
    # The view API's top-level "duration" sums all pages of a multi-part
    # video (the whole collection).  Use the selected page's own
    # duration so the progress bar reflects just this one episode.
    duration = pages[page - 1].get("duration") or view.get("duration") or 0

    play = _fetch_playurl(bvid, cid)
    dash = play.get("dash") or {}
    video = _pick(dash.get("video"))
    audio = _pick(dash.get("audio"))
    if not video or not audio:
        raise ValueError("No playable stream for this video")

    def _seg(s):
        sb = s.get("SegmentBase") or s.get("segment_base") or {}
        return {
            "baseUrl": s.get("baseUrl") or s.get("base_url"),
            "mimeType": s.get("mimeType") or s.get("mime_type") or "video/mp4",
            "codecs": s.get("codecs") or "",
            "bandwidth": int(s.get("bandwidth") or s.get("bandwidth") or 0),
            "width": int(s.get("width") or 0),
            "height": int(s.get("height") or 0),
            "init": sb.get("initialization") or sb.get("Initialization"),
            "index": sb.get("index_range") or sb.get("indexRange"),
        }

    info = {
        "bvid": bvid,
        "cid": cid,
        "duration": duration,
        "video": _seg(video),
        "audio": _seg(audio),
    }
    info.setdefault("video", {})["init"] = info["video"].get("init")
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
      <Representation id="video" mimeType="{video_mime}" codecs="{video_codecs}"
                      bandwidth="{video_bw}" width="{video_w}" height="{video_h}">
        <BaseURL>{video_proxy}</BaseURL>
        <SegmentBase indexRange="{video_index}">
          <Initialization range="{video_init}"/>
        </SegmentBase>
      </Representation>
    </AdaptationSet>
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


def build_mpd(info, video_proxy, audio_proxy):
    """Build an on-demand DASH manifest from stream_info."""
    v = info["video"]
    a = info["audio"]
    return _MPD_TEMPLATE.format(
        duration=info["duration"],
        video_mime=v.get("mimeType") or "video/mp4",
        video_codecs=v.get("codecs") or "avc1.64001F",
        video_bw=v.get("bandwidth") or 0,
        video_w=v.get("width") or 0,
        video_h=v.get("height") or 0,
        video_proxy=video_proxy,
        video_index=v.get("index") or "",
        video_init=v.get("init") or "",
        audio_mime=a.get("mimeType") or "audio/mp4",
        audio_codecs=a.get("codecs") or "mp4a.40.2",
        audio_bw=a.get("bandwidth") or 0,
        audio_proxy=audio_proxy,
        audio_index=a.get("index") or "",
        audio_init=a.get("init") or "",
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
    r = requests.get(
        base_url,
        headers=headers,
        stream=True,
        timeout=30,
    )
    resp_headers = {
        "Content-Type": r.headers.get("Content-Type", "application/octet-stream"),
        "Accept-Ranges": r.headers.get("Accept-Ranges", "bytes"),
    }
    if r.headers.get("Content-Range"):
        resp_headers["Content-Range"] = r.headers["Content-Range"]
    if r.headers.get("Content-Length"):
        resp_headers["Content-Length"] = r.headers["Content-Length"]
    return r.status_code, resp_headers, r.iter_content(chunk_size=8192)