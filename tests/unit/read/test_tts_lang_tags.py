"""
TTS language tag / voice resolution tests.
"""

from lute.tts.routes import (
    DEFAULT_VOICE,
    get_lang_code,
    primary_subtag,
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
