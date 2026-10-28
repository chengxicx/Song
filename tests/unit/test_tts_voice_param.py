"""
Unit tests for the optional ?voice= override on the /tts route.

The voice name is interpolated into the edge-tts call and the cache key, so
client input is gated twice: the edge-tts naming pattern, and agreement of the
voice's locale with the requested language.  These tests pin both gates --
see `_requested_voice_is_acceptable` in lute/tts/routes.py.
"""

from lute.tts.routes import _requested_voice_is_acceptable, voice_for_tag


def test_well_formed_same_language_voice_is_accepted():
    assert _requested_voice_is_acceptable("ja-JP-KeitaNeural", "ja-JP")
    assert _requested_voice_is_acceptable("ja-JP-NanamiNeural", "ja")
    assert _requested_voice_is_acceptable("en-US-AriaNeural", "en-US")


def test_mismatched_language_voice_is_rejected():
    assert not _requested_voice_is_acceptable("ja-JP-KeitaNeural", "en-US")
    assert not _requested_voice_is_acceptable("en-US-AriaNeural", "ja")


def test_malformed_names_are_rejected():
    # Not edge-tts names at all.
    assert not _requested_voice_is_acceptable("", "ja-JP")
    assert not _requested_voice_is_acceptable(None, "ja-JP")
    assert not _requested_voice_is_acceptable("Nanami", "ja-JP")
    assert not _requested_voice_is_acceptable("ja-JP-Nanami", "ja-JP")
    assert not _requested_voice_is_acceptable("ja_JP-NanamiNeural", "ja-JP")
    assert not _requested_voice_is_acceptable("ja-JP-NanamiNeural-extra", "ja-JP")
    # Injection attempts must never pass the pattern.
    assert not _requested_voice_is_acceptable("ja-JP-$(reboot)Neural", "ja-JP")
    assert not _requested_voice_is_acceptable("ja-JP-;lsNeural", "ja-JP")


def test_chinese_regional_variants_share_the_primary_subtag():
    # zh-CN / zh-HK / zh-TW voices all carry the "zh" primary subtag; the
    # gate agrees on the primary subtag, so any may serve a zh-* request.
    assert _requested_voice_is_acceptable("zh-HK-HiuMaanNeural", "zh-CN")
    assert _requested_voice_is_acceptable("zh-CN-XiaoxiaoNeural", "zh-HK")
    # But zh voices still cannot serve non-zh languages.
    assert not _requested_voice_is_acceptable("zh-CN-XiaoxiaoNeural", "ja")


def test_voice_for_tag_default_mapping_unchanged():
    # The ?voice= path must not disturb the language -> default voice table.
    assert voice_for_tag("ja-JP") == "ja-JP-NanamiNeural"
    assert voice_for_tag("unknown-XX") is not None
