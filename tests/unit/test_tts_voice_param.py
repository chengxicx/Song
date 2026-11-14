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


def test_default_voice_endpoint(app_context, client):
    "GET /tts/voice/<lang> names the backend's default edge-tts voice."
    rv = client.get("/tts/voice/ja-JP")
    assert rv.status_code == 200
    assert rv.get_json() == {"voice": "ja-JP-NanamiNeural"}
    # The answer is deploy-stable: cacheable for a day.
    assert "max-age=86400" in rv.headers["Cache-Control"]


def test_voices_endpoint_lists_same_language_edge_voices(
    app_context, client, monkeypatch
):
    "GET /tts/voices/<lang> lists the language's edge-tts voices."
    from lute.tts import routes as tts_routes

    monkeypatch.setattr(
        tts_routes,
        "_EDGE_VOICES_CACHE",
        [
            {"Locale": "ja-JP", "ShortName": "ja-JP-NanamiNeural", "Gender": "Female"},
            {"Locale": "ja-JP", "ShortName": "ja-JP-KeitaNeural", "Gender": "Male"},
            {"Locale": "en-US", "ShortName": "en-US-AvaNeural", "Gender": "Female"},
        ],
    )
    rv = client.get("/tts/voices/ja-JP")
    assert rv.status_code == 200
    assert rv.get_json()["voices"] == [
        {"id": "ja-JP-NanamiNeural", "gender": "Female"},
        {"id": "ja-JP-KeitaNeural", "gender": "Male"},
    ]
    # A bare-language request falls back to the primary subtag.
    rv2 = client.get("/tts/voices/ja")
    assert [v["id"] for v in rv2.get_json()["voices"]] == [
        "ja-JP-NanamiNeural",
        "ja-JP-KeitaNeural",
    ]
