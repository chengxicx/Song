"""
Edge-TTS voice synthesis and Google auto-translation routes.

Provides:
  * GET /tts/<lang>/<path:text>  -- generate (and cache) an mp3 using edge-tts.
  * GET /api/translate/<sl>/<tl>/<path:text>  -- translate text via Google's
    free translate API, cached in memory.
"""

import os
import asyncio
import hashlib
import urllib.parse

import requests
from flask import Blueprint, current_app, send_file, jsonify

try:
    import edge_tts
    _EDGE_TTS_AVAILABLE = True
except ImportError:
    _EDGE_TTS_AVAILABLE = False

bp = Blueprint("tts", __name__)


# Voice mapping: language code -> edge-tts voice name.
VOICE_MAP = {
    "ja": "ja-JP-NanamiNeural",
    "en": "en-US-AvaNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "hi": "hi-IN-MadhurNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "ko": "ko-KR-SunHiNeural",
    "ar": "ar-EG-SalmaNeural",
    "it": "it-IT-ElsaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "tr": "tr-TR-EmelNeural",
    "nl": "nl-NL-ColetteNeural",
    "pl": "pl-PL-AgnieszkaNeural",
    "cs": "cs-CZ-VlastaNeural",
    "th": "th-TH-PremwadeeNeural",
    "id": "id-ID-GadisNeural",
    "vi": "vi-VN-HoaiMyNeural",
    "el": "el-GR-AthinaNeural",
    "he": "he-IL-HilaNeural",
    "sv": "sv-SE-SofieNeural",
    "uk": "uk-UA-PolinaNeural",
    "no": "nb-NO-PernilleNeural",
    "fi": "fi-FI-NooraNeural",
    "da": "da-DK-JeppeNeural",
    "ro": "ro-RO-AlinaNeural",
    "hu": "hu-HU-NoemiNeural",
    "ca": "ca-ES-JoanaNeural",
    "bg": "bg-BG-BorislavNeural",
    "hr": "hr-HR-GabrijelaNeural",
    "zh-CN": "zh-CN-XiaoxiaoNeural",
    "zh-TW": "zh-TW-HsiaoChenNeural",
    "zh-HK": "zh-HK-HiuMaanNeural",
    "yue": "zh-HK-HiuMaanNeural",
}
DEFAULT_VOICE = "en-US-AvaNeural"

# Mapping from the Lute Language "name" to a BCP-47 language tag used by the
# TTS and translate routes.  Used by read/routes.py to pass the source
# language tag to the reading template.
#
# Tags carry the regional/script variety of the TTS voice used for that
# language (e.g. "ja-JP" for the ja-JP voice, "zh-HK" for Cantonese).
# Cantonese is tagged zh-HK rather than yue because the voices it needs
# (edge-tts and browser SpeechSynthesis natural voices) are all keyed
# under the zh-HK locale; a "yue" tag misses them in voice matching.
# Consumers resolve the full tag, falling back to the primary language
# subtag as needed (see voice_for_tag and _translate_via_mymemory).
LANG_NAME_TO_CODE = {
    "japanese": "ja-JP",
    "english": "en-US",
    "spanish": "es-ES",
    "french": "fr-FR",
    "german": "de-DE",
    "chinese": "zh-CN",
    "classical chinese": "zh-CN",
    "simplified chinese": "zh-CN",
    "traditional chinese": "zh-TW",
    "mandarin": "zh-CN",
    "mandarin chinese": "zh-CN",
    "cantonese": "zh-HK",
    "cantonese chinese": "zh-HK",
    "italian": "it-IT",
    "portuguese": "pt-BR",
    "russian": "ru-RU",
    "korean": "ko-KR",
    "arabic": "ar-EG",
    "hindi": "hi-IN",
    "dutch": "nl-NL",
    "polish": "pl-PL",
    "turkish": "tr-TR",
    "vietnamese": "vi-VN",
    "thai": "th-TH",
    "indonesian": "id-ID",
    "czech": "cs-CZ",
    "greek": "el-GR",
    "hebrew": "he-IL",
    "swedish": "sv-SE",
    "ukrainian": "uk-UA",
    "latin": "la",
    "norwegian": "nb-NO",
    "finnish": "fi-FI",
    "danish": "da-DK",
    "romanian": "ro-RO",
    "hungarian": "hu-HU",
    "catalan": "ca-ES",
    "bulgarian": "bg-BG",
    "croatian": "hr-HR",
    "persian": "fa",
    "malay": "ms-MY",
    "tagalog": "tl",
}

DEFAULT_LANG_TAG = "en-US"


def get_lang_code(lang_name):
    """
    Get the BCP-47 language tag for a Lute language name.
    Falls back to 'en-US' if the language is unknown.
    """
    if not lang_name:
        return DEFAULT_LANG_TAG
    return LANG_NAME_TO_CODE.get(lang_name.lower(), DEFAULT_LANG_TAG)


def get_lang_code_for(language):
    """
    Get the BCP-47 language tag for a Lute Language entity.

    The language's custom tts_lang setting (set on the language edit
    screen) takes precedence; otherwise the tag is derived from the
    language name via LANG_NAME_TO_CODE.
    """
    custom = getattr(language, "tts_lang", None)
    if custom and custom.strip():
        return custom.strip()
    return get_lang_code(language.name if language is not None else None)


def primary_subtag(tag):
    """
    Return the primary language subtag of a BCP-47 tag, e.g.
    "pt-BR" -> "pt", "yue" -> "yue".
    """
    return (tag or "").split("-")[0].lower()


def voice_for_tag(tag):
    """
    Resolve a BCP-47 language tag to an edge-tts voice name.

    The exact tag is tried first (e.g. "zh-TW", "yue"), then the
    primary language subtag (e.g. "ja-JP" -> "ja"), falling back to
    DEFAULT_VOICE.
    """
    if not tag:
        return DEFAULT_VOICE
    voice = VOICE_MAP.get(tag)
    if voice is None:
        voice = VOICE_MAP.get(primary_subtag(tag))
    return voice or DEFAULT_VOICE


# In-memory translation cache:  "{sl}_{tl}_{text}" -> translation string.
trans_cache = {}


@bp.route("/tts/<lang>/<path:text>", methods=["GET"])
def tts_speak(lang, text):
    """
    Generate speech for *text* using edge-tts, returning an mp3.

    Audio files are cached on disk in DATAPATH/tts_cache, keyed by the
    MD5 of ``f"{lang}_{text}"`` so repeated requests are served
    instantly.
    """
    voice = voice_for_tag(lang)

    datapath = current_app.config["DATAPATH"]
    cache_dir = os.path.join(datapath, "tts_cache")
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)

    key = f"{lang}_{text}"
    filename = hashlib.md5(key.encode("utf-8")).hexdigest() + ".mp3"
    filepath = os.path.join(cache_dir, filename)

    if not os.path.exists(filepath):
        _generate_audio(text, voice, filepath)

    return send_file(filepath, mimetype="audio/mpeg")


def _generate_audio(text, voice, filepath):
    """
    Use edge-tts to synthesize *text* with *voice*, saving to *filepath*.

    edge-tts is async, so it is run via asyncio.run() within this sync
    Flask route.

    Returns None on success, or an error tuple on failure.
    """
    if not _EDGE_TTS_AVAILABLE:
        return jsonify({"error": "edge-tts not installed"}), 500

    async def _run():
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(filepath)

    asyncio.run(_run())
    return None


@bp.route("/api/translate/<sl>/<tl>/<path:text>", methods=["GET"])
def translate(sl, tl, text):
    """
    Translate *text* from source language *sl* to target language *tl*.

    Tries Google's free translate API first, then falls back to
    MyMemory API if Google fails.

    Results are cached in the in-memory ``trans_cache`` dict.

    Returns JSON ``{"translation": "..."}``.
    """
    cache_key = f"{sl}_{tl}_{text}"
    if cache_key in trans_cache:
        return jsonify({"translation": trans_cache[cache_key]})

    translation = _translate_via_google(sl, tl, text)
    if not translation:
        translation = _translate_via_mymemory(sl, tl, text)

    # Only cache successful translations, so API error messages
    # (e.g. MyMemory's "PLEASE SELECT TWO DISTINCT LANGUAGES")
    # never get served from the cache.
    if translation:
        trans_cache[cache_key] = translation
    return jsonify({"translation": translation})


def _translate_via_google(sl, tl, text):
    """Try Google's free translate API.  Returns '' on failure."""
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl={sl}&tl={tl}&dt=t&q={urllib.parse.quote(text)}"
    )
    try:
        resp = requests.get(url, timeout=8)
        data = resp.json()
        if data and data[0] and data[0][0]:
            result = data[0][0][0] or ""
            if result and result.lower() == text.lower():
                return ""
            return result
    except Exception as e:  # pylint: disable=broad-exception-caught
        current_app.logger.warning("Google translate failed: %s", e)
    return ""


# MyMemory returns its error messages (HTTP 403 etc.) as
# responseData.translatedText with a 200 response body; never let
# these strings through as "translations".
_MYMEMORY_ERROR_PREFIXES = (
    "PLEASE SELECT",
    "MYMEMORY WARNING",
    "QUERY LENGTH LIMIT EXCEEDED",
    "INVALID SOURCE OR TARGET LANGUAGE",
    "INVALID LANGUAGE PAIR",
)


def _is_mymemory_error(result):
    """True if a MyMemory result string is actually an error message."""
    upper = (result or "").strip().upper()
    return any(upper.startswith(p) for p in _MYMEMORY_ERROR_PREFIXES)


def _translate_via_mymemory(sl, tl, text):
    """Try MyMemory free translate API.  Returns '' on failure."""
    # MyMemory expects plain language codes, not full BCP-47 tags.
    sl_primary, tl_primary = primary_subtag(sl), primary_subtag(tl)
    if not sl_primary or not tl_primary:
        return ""
    if sl_primary == tl_primary:
        # Same primary language (e.g. zh-HK -> zh-CN): MyMemory rejects
        # the pair with a 403 error message, and Google above already
        # handles these pairs, so don't bother calling it.
        return ""
    langpair = f"{sl_primary}|{tl_primary}"
    url = (
        "https://api.mymemory.translated.net/get"
        f"?q={urllib.parse.quote(text)}"
        f"&langpair={urllib.parse.quote(langpair)}"
    )
    try:
        resp = requests.get(url, timeout=8)
        data = resp.json()
        status = data.get("responseStatus", 200)
        try:
            status_ok = int(status) == 200
        except (TypeError, ValueError):
            status_ok = False
        if not status_ok:
            return ""
        if data and data.get("responseData") and data["responseData"].get("translatedText"):
            result = data["responseData"]["translatedText"]
            # If result is identical to input, treat as failed translation
            if result and result.lower() == text.lower():
                return ""
            if _is_mymemory_error(result):
                return ""
            return result
    except Exception as e:  # pylint: disable=broad-exception-caught
        current_app.logger.warning("MyMemory translate failed: %s", e)
    return ""
