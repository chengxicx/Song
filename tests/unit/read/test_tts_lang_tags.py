"""
TTS language tag / voice resolution tests.
"""

from types import SimpleNamespace

import pytest

from lute.tts.routes import (
    DEFAULT_VOICE,
    _translate_via_mymemory,
    get_lang_code,
    get_lang_code_for,
    primary_subtag,
    trans_cache,
    voice_for_tag,
)


def test_get_lang_code_returns_bcp47_tags():
    "Lute language names map to BCP-47 tags."
    cases = {
        "Japanese": "ja-JP",
        "English": "en-US",
        "Mandarin Chinese": "zh-CN",
        "Traditional Chinese": "zh-TW",
        "Simplified Chinese": "zh-CN",
        "Cantonese Chinese": "zh-HK",
        "Cantonese": "zh-HK",
        "Portuguese": "pt-BR",
        "Latin": "la",
    }
    for name, expected in cases.items():
        assert get_lang_code(name) == expected, name


def test_get_lang_code_fallback():
    "Unknown or missing names fall back to en-US."
    assert get_lang_code("Klingon") == "en-US"
    assert get_lang_code(None) == "en-US"
    assert get_lang_code("") == "en-US"


def test_get_lang_code_for_prefers_custom_setting():
    "A language's custom tts_lang overrides the name lookup."
    lang = SimpleNamespace(name="Cantonese Chinese", tts_lang="yue")
    assert get_lang_code_for(lang) == "yue"

    lang = SimpleNamespace(name="Cantonese Chinese", tts_lang="  zh-HK  ")
    assert get_lang_code_for(lang) == "zh-HK"


def test_get_lang_code_for_falls_back_to_name_lookup():
    "Empty or missing custom tts_lang falls back to the name mapping."
    lang = SimpleNamespace(name="Cantonese Chinese", tts_lang=None)
    assert get_lang_code_for(lang) == "zh-HK"

    lang = SimpleNamespace(name="Cantonese Chinese", tts_lang="")
    assert get_lang_code_for(lang) == "zh-HK"

    lang = SimpleNamespace(name="Klingon", tts_lang=None)
    assert get_lang_code_for(lang) == "en-US"

    assert get_lang_code_for(None) == "en-US"


def test_primary_subtag():
    "Primary language subtag is the first subtag, lowercased."
    assert primary_subtag("pt-BR") == "pt"
    assert primary_subtag("zh-TW") == "zh"
    assert primary_subtag("yue") == "yue"
    assert primary_subtag(None) == ""


def test_voice_for_tag_exact_match():
    "Tags with an exact voice entry use it."
    assert voice_for_tag("zh-HK") == "zh-HK-HiuMaanNeural"
    assert voice_for_tag("zh-TW") == "zh-TW-HsiaoChenNeural"
    assert voice_for_tag("zh-CN") == "zh-CN-XiaoxiaoNeural"
    # "yue" is kept as an alias for previously cached /tts/yue/ URLs.
    assert voice_for_tag("yue") == "zh-HK-HiuMaanNeural"


def test_voice_for_tag_falls_back_to_primary_subtag():
    "Full tags without an exact entry fall back to the primary subtag."
    assert voice_for_tag("ja-JP") == "ja-JP-NanamiNeural"
    assert voice_for_tag("pt-BR") == "pt-BR-FranciscaNeural"
    assert voice_for_tag("zh-TW") == "zh-TW-HsiaoChenNeural"


def test_voice_for_tag_unknown_falls_back_to_default():
    "Unknown tags get the default voice."
    assert voice_for_tag("la") == DEFAULT_VOICE
    assert voice_for_tag("xx-YY") == DEFAULT_VOICE
    assert voice_for_tag(None) == DEFAULT_VOICE


# ------------------------------------------------------------------
# MyMemory translation guards.  MyMemory returns its error messages
# (e.g. "PLEASE SELECT TWO DISTINCT LANGUAGES" for zh|zh) as
# responseData.translatedText; these must never surface as
# translations.
# ------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture(name="mymemory")
def fixture_mymemory(monkeypatch):
    "Mock requests.get for the MyMemory call, recording requested urls."
    urls_requested = []

    def _fake_get(url, timeout=None):
        urls_requested.append(url)
        return _FakeResponse(_fake_get.payload)

    _fake_get.payload = {}
    _fake_get.urls = urls_requested
    monkeypatch.setattr("lute.tts.routes.requests.get", _fake_get)
    return _fake_get


def test_mymemory_skipped_for_same_primary_language(mymemory):
    "zh-HK -> zh-CN share the primary subtag, so MyMemory isn't called."
    mymemory.payload = {
        "responseData": {"translatedText": "PLEASE SELECT TWO DISTINCT LANGUAGES"},
        "responseStatus": 403,
    }
    assert _translate_via_mymemory("zh-HK", "zh-CN", "你好") == ""
    assert mymemory.urls == [], "no network call made"


def test_mymemory_called_for_distinct_primary_languages(mymemory):
    "Distinct primary subtags are passed as a plain langpair."
    mymemory.payload = {
        "responseData": {"translatedText": "bonjour"},
        "responseStatus": 200,
    }
    assert _translate_via_mymemory("en-US", "fr-FR", "hello") == "bonjour"
    assert "langpair=en%7Cfr" in mymemory.urls[0]


def test_mymemory_403_error_not_returned(mymemory):
    "A 403 response with an error message yields ''."
    mymemory.payload = {
        "responseData": {"translatedText": "PLEASE SELECT TWO DISTINCT LANGUAGES"},
        "responseStatus": 403,
    }
    assert _translate_via_mymemory("en", "fr", "hello") == ""


def test_mymemory_error_string_in_200_response_not_returned(mymemory):
    "MyMemory sometimes returns warnings with responseStatus 200."
    mymemory.payload = {
        "responseData": {
            "translatedText": "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY"
        },
        "responseStatus": 200,
    }
    assert _translate_via_mymemory("en", "fr", "hello") == ""


def test_mymemory_valid_translation_returned(mymemory):
    "A good response is returned."
    mymemory.payload = {
        "responseData": {"translatedText": "bonjour"},
        "responseStatus": 200,
    }
    assert _translate_via_mymemory("en", "fr", "hello") == "bonjour"


def test_mymemory_identical_result_not_returned(mymemory):
    "A result identical to the input is treated as no translation."
    mymemory.payload = {
        "responseData": {"translatedText": "hello"},
        "responseStatus": 200,
    }
    assert _translate_via_mymemory("en", "fr", "hello") == ""


# ------------------------------------------------------------------
# /api/translate route: only successful translations are cached.
# ------------------------------------------------------------------


@pytest.fixture(name="clean_trans_cache")
def fixture_clean_trans_cache():
    "Ensure the in-memory translation cache starts and ends empty."
    trans_cache.clear()
    yield
    trans_cache.clear()


def test_translate_route_does_not_cache_empty_result(
    app_context, monkeypatch, clean_trans_cache
):
    "Empty results aren't cached, so later requests retry."
    from lute.tts import routes as tts_routes

    monkeypatch.setattr(tts_routes, "_translate_via_google", lambda sl, tl, t: "")
    monkeypatch.setattr(tts_routes, "_translate_via_mymemory", lambda sl, tl, t: "")

    resp = tts_routes.translate("zh-HK", "zh-CN", "你好")
    assert resp.get_json()["translation"] == ""
    assert "zh-HK_zh-CN_你好" not in trans_cache


def test_translate_route_caches_successful_result(
    app_context, monkeypatch, clean_trans_cache
):
    "Successful results are cached and reused."
    from lute.tts import routes as tts_routes

    calls = {"n": 0}

    def _google(sl, tl, text):  # pylint: disable=unused-argument
        calls["n"] += 1
        return "hello"

    monkeypatch.setattr(tts_routes, "_translate_via_google", _google)

    first = tts_routes.translate("zh-CN", "en", "你好").get_json()
    second = tts_routes.translate("zh-CN", "en", "你好").get_json()
    assert first == {"translation": "hello"}
    assert second == {"translation": "hello"}
    assert calls["n"] == 1, "second call served from cache"

