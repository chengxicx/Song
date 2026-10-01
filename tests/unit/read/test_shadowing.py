"""
Tests for the shadowing (read-aloud) scoring feature.

The real faster-whisper dependency is never loaded: transcribe_clip is
monkeypatched (or given a fake model), and the tests cover the token
diff and the /read/shadowing/transcribe route.
"""

import io
import json
import os
import time
from unittest.mock import patch

import pytest

from lute.read import shadowing


@pytest.fixture(autouse=True)
def _no_real_model_load():
    "Never load a real faster-whisper model: the task loads it up front."
    with patch.object(shadowing, "_load_model", return_value=object()):
        yield


def _wait_for_task(task_id, timeout=10.0):
    "Poll a task until it reaches a terminal state."
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = shadowing.task_status(task_id)
        if status["state"] in ("finished", "error"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} did not finish in time: {status}")


# ---------------------------------------------------------------------
# Duck-typed Language for the diff tests: the spoken tokens are fixed
# per instance, so tests control exactly what whisper "heard".
# ---------------------------------------------------------------------


class _FakeParsedToken:
    def __init__(self, token, is_word=True):
        self.token = token
        self.is_word = is_word


class _FakeParser:
    "Duck-typed parser: fixed readings + plain lowercase."

    def __init__(self, readings=None):
        self.readings = readings or {}

    def get_reading(self, text):
        return self.readings.get(text)

    def get_lowercase(self, text):
        return text.lower()


class _FakeLanguage:
    "Duck-typed Language.  Entries may be strings or (token, is_word)."

    def __init__(self, spoken_tokens, parser_type="spacedel", readings=None):
        self.parser_type = parser_type
        self.spoken_tokens = spoken_tokens
        self._parser = _FakeParser(readings)

    @property
    def parser(self):
        return self._parser

    def get_parsed_tokens(self, _text):
        toks = []
        for t in self.spoken_tokens:
            if isinstance(t, tuple):
                toks.append(_FakeParsedToken(t[0], t[1]))
            else:
                toks.append(_FakeParsedToken(t))
        return toks


# ---------------------------------------------------------------------
# Token diff
# ---------------------------------------------------------------------


def test_perfect_match():
    lang = _FakeLanguage(["The", "calm", "cat"])
    res = shadowing.compare_tokens(["The", "calm", "cat"], "The calm cat.", lang)
    assert res["statuses"] == [2, 2, 2]
    assert res["score"] == 100
    assert res["extras"] == []


def test_missed_word():
    "A skipped word stays a miss; the score rounds to the nearest percent."
    lang = _FakeLanguage(["the", "cat"])
    res = shadowing.compare_tokens(["The", "calm", "cat"], "the cat", lang)
    assert res["statuses"] == [2, 0, 2]
    assert res["score"] == 67


def test_nothing_spoken_marks_all_missed():
    lang = _FakeLanguage([])
    res = shadowing.compare_tokens(["the", "cat"], "", lang)
    assert res["statuses"] == [0, 0]
    assert res["score"] == 0


def test_misread_word_is_fuzzy():
    "A near-match token (ratio >= 0.6) counts as a misread attempt."
    lang = _FakeLanguage(["the", "cam", "cat"])
    res = shadowing.compare_tokens(["The", "calm", "Cat"], "the cam cat", lang)
    assert res["statuses"] == [2, 1, 2]
    assert res["spoken_for_fuzzy"] == {1: "cam"}
    assert res["score"] == 83


def test_replace_below_ratio_is_miss():
    "A token that is too far from anything spoken is just a miss."
    lang = _FakeLanguage(["the", "dog", "cat"])
    res = shadowing.compare_tokens(["The", "calm", "cat"], "the dog cat", lang)
    assert res["statuses"] == [2, 0, 2]
    assert res["spoken_for_fuzzy"] == {}


def test_extra_spoken_words():
    lang = _FakeLanguage(["the", "very", "calm", "cat"])
    res = shadowing.compare_tokens(["The", "calm", "cat"], "the very calm cat", lang)
    assert res["statuses"] == [2, 2, 2]
    assert res["extras"] == ["very"]


def test_japanese_matches_by_reading():
    "良い spoken as いい is the correct reading of the kanji, not a miss."
    lang = _FakeLanguage(
        ["今日", "は", "いい", "天気", "です"],
        parser_type="japanese",
        readings={"良い": "いい", "天気": "テンキ"},
    )
    res = shadowing.compare_tokens(
        ["今日", "は", "良い", "天気", "です"], "今日はいい天気です", lang
    )
    assert res["statuses"] == [2, 2, 2, 2, 2]
    assert res["score"] == 100


def test_japanese_reading_katakana_normalized():
    "IPADIC-style katakana readings compare equal to hiragana speech."
    lang = _FakeLanguage(["てんき"], parser_type="japanese", readings={"天気": "テンキ"})
    res = shadowing.compare_tokens(["天気"], "てんき", lang)
    assert res["statuses"] == [2]


def test_spoken_punctuation_ignored():
    lang = _FakeLanguage([("。", False), ("cat", True)])
    res = shadowing.compare_tokens(["cat"], "。cat", lang)
    assert res["statuses"] == [2]


def test_zws_stripped_from_tokens():
    lang = _FakeLanguage(["ca\u200Bt"])
    res = shadowing.compare_tokens(["cat"], "cat", lang)
    assert res["statuses"] == [2]


def test_empty_original_tokens_are_skipped():
    "Rendering artifacts (empty word spans) are skipped, not scored."
    lang = _FakeLanguage(["cat"])
    res = shadowing.compare_tokens(["cat", "", ""], "cat", lang)
    assert res["statuses"] == [2, 3, 3]
    assert res["total"] == 1
    assert res["score"] == 100


def test_punctuation_original_tokens_are_neutral():
    "Punctuation on the original side is never scored nor marked."
    lang = _FakeLanguage(["你好", "世界"])
    res = shadowing.compare_tokens(["你好", "。", "世界"], "你好世界", lang)
    assert res["statuses"] == [2, 3, 2]
    assert res["total"] == 2
    assert res["score"] == 100


def test_spoken_punctuation_dropped_even_if_marked_as_word():
    "A parser tagging punctuation is_word=True cannot make it scoreable."
    lang = _FakeLanguage([("。", True), ("你好", True)])
    res = shadowing.compare_tokens(["你好"], "你好。", lang)
    assert res["statuses"] == [2]
    assert res["spoken_count"] == 1
    assert res["extras"] == []


def test_all_punctuation_originals_score_100():
    lang = _FakeLanguage(["你好"])
    res = shadowing.compare_tokens(["。", "、"], "你好", lang)
    assert res["statuses"] == [3, 3]
    assert res["total"] == 0
    assert res["score"] == 100


def test_empty_original_tokens_returns_neutral_result():
    lang = _FakeLanguage(["cat"])
    res = shadowing.compare_tokens([], "cat", lang)
    assert res["statuses"] == []
    assert res["spoken_count"] == 1


def test_compare_tokens_accepts_preparsed_spoken_tokens():
    "The caller's already-parsed tokens are reused instead of re-parsing."
    lang = _FakeLanguage(["the", "cat"])
    res = shadowing.compare_tokens(
        ["The", "cat"], "the cat", lang, spoken_tokens=["the", "cat"]
    )
    assert res["statuses"] == [2, 2]
    assert res["score"] == 100


# ---------------------------------------------------------------------
# Furigana annotation
# ---------------------------------------------------------------------


def test_annotate_tokens_japanese_includes_readings():
    lang = _FakeLanguage(
        [],
        parser_type="japanese",
        readings={"天気": "てんき", "良い": "いい"},
    )
    annotated = shadowing.annotate_tokens(["天気", "は", "良い"], lang)
    assert annotated == [
        {"text": "天気", "reading": "てんき"},
        {"text": "は", "reading": None},
        {"text": "良い", "reading": "いい"},
    ]


def test_annotate_tokens_non_japanese_has_no_readings():
    lang = _FakeLanguage([], readings={"Where": "どこ"})
    annotated = shadowing.annotate_tokens(["Where", "are"], lang)
    assert annotated == [
        {"text": "Where", "reading": None},
        {"text": "are", "reading": None},
    ]


def test_annotate_tokens_strips_zws_from_text():
    lang = _FakeLanguage([], parser_type="japanese", readings={})
    annotated = shadowing.annotate_tokens(["ca\u200Bt"], lang)
    assert annotated == [{"text": "cat", "reading": None}]


# ---------------------------------------------------------------------
# transcribe_clip (fake model; faster-whisper is never imported)
# ---------------------------------------------------------------------


def test_transcribe_clip_uses_greedy_decoding_without_word_timestamps():
    "Greedy + no word timestamps + no initial_prompt (echoing would inflate scores)."
    captured = {}

    class _Seg:
        text = " Hello world."
        words = None

    class _Info:
        duration = 1.5

    class _FakeModel:
        def transcribe(self, path, **kwargs):
            captured.update(kwargs)
            return iter([_Seg()]), _Info()

    with patch.object(shadowing, "_load_model", return_value=_FakeModel()):
        text, duration = shadowing.transcribe_clip("/tmp/x.wav", "en", "small")

    assert captured["beam_size"] == 1
    # The diff only needs the text; the alignment pass would only add
    # CPU time to an already CPU-bound step.
    assert captured["word_timestamps"] is False
    assert captured["language"] == "en"
    assert "initial_prompt" not in captured
    assert text == "Hello world."
    assert duration == 1.5


# ---------------------------------------------------------------------
# Route validation
# ---------------------------------------------------------------------


def test_route_requires_whisper(app, client, english):
    with patch.object(shadowing, "whisper_status", return_value={"installed": False}):
        resp = client.post("/read/shadowing/transcribe", data={})
    assert resp.status_code == 400
    assert "not installed" in resp.get_json()["error"]


def test_route_requires_audio(app, client, english):
    with patch.object(shadowing, "whisper_status", return_value={"installed": True}):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={"language_id": str(english.id), "tokens": "[]"},
        )
    assert resp.status_code == 400
    assert "no audio" in resp.get_json()["error"]


def test_route_requires_language(app, client):
    with patch.object(shadowing, "whisper_status", return_value={"installed": True}):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={"tokens": "[]", "audio": (io.BytesIO(b"x"), "clip.webm")},
            content_type="multipart/form-data",
        )
    assert resp.status_code == 400
    assert "language" in resp.get_json()["error"]


def test_route_rejects_bad_tokens(app, client, english):
    with patch.object(shadowing, "whisper_status", return_value={"installed": True}):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={
                "language_id": str(english.id),
                "tokens": "not json",
                "audio": (io.BytesIO(b"x"), "clip.webm"),
            },
            content_type="multipart/form-data",
        )
    assert resp.status_code == 400
    assert "tokens" in resp.get_json()["error"]


# ---------------------------------------------------------------------
# Route happy path + temp-file lifecycle
# ---------------------------------------------------------------------


def test_route_scores_recording(app, app_context, client, english):
    "Happy path: POST returns a task id; the task scores and cleans up."
    tempdir = app.env_config.temppath

    def _fake_clip(audio_path, lang_code, model_size="small"):
        assert os.path.exists(audio_path)
        assert os.path.basename(audio_path).startswith("shadowing_")
        assert model_size == "small"
        return "The calm cat.", 6.0

    with patch.object(
        shadowing, "whisper_status", return_value={"installed": True}
    ), patch.object(shadowing, "transcribe_clip", side_effect=_fake_clip):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={
                "language_id": str(english.id),
                "tokens": json.dumps(["The", "calm", "cat"]),
                "audio": (io.BytesIO(b"fake webm bytes"), "clip.webm"),
            },
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        task_id = resp.get_json()["task_id"]
        status = _wait_for_task(task_id)

    assert status["state"] == "finished"
    body = status["result"]
    assert body["transcription"] == "The calm cat."
    # The heard sentence is annotated token-by-token for the panel's
    # furigana + click-to-pronounce rendering (no readings for English).
    assert body["transcription_tokens"] == [
        {"text": "The", "reading": None},
        {"text": "calm", "reading": None},
        {"text": "cat", "reading": None},
    ]
    assert body["statuses"] == [2, 2, 2]
    assert body["score"] == 100
    assert body["duration"] == 6.0
    # 3 tokens over 6 seconds = 30 tokens/minute.
    assert body["tokens_per_minute"] == 30.0
    assert body["token_kind"] == "word"

    # The temp clip was cleaned up.
    leftovers = [f for f in os.listdir(tempdir) if f.startswith("shadowing_")]
    assert leftovers == []


def test_status_endpoint_reports_unknown(app, client):
    resp = client.get("/read/shadowing/status/not-a-task")
    assert resp.status_code == 200
    assert resp.get_json()["state"] == "unknown"


# ---------------------------------------------------------------------
# Furigana readings route
# ---------------------------------------------------------------------


def test_readings_route_returns_parallel_tokens(app, client, english):
    resp = client.post(
        "/read/shadowing/readings",
        json={"language_id": english.id, "tokens": ["The", "calm", "cat"]},
    )
    assert resp.status_code == 200
    assert resp.get_json()["tokens"] == [
        {"text": "The", "reading": None},
        {"text": "calm", "reading": None},
        {"text": "cat", "reading": None},
    ]


def test_readings_route_rejects_bad_tokens(app, client, english):
    resp = client.post(
        "/read/shadowing/readings",
        json={"language_id": english.id, "tokens": "nope"},
    )
    assert resp.status_code == 400
    assert "tokens" in resp.get_json()["error"]


def test_readings_route_requires_language(app, client):
    resp = client.post(
        "/read/shadowing/readings", json={"tokens": ["a"]}
    )
    assert resp.status_code == 400
    assert "language" in resp.get_json()["error"]


def test_route_unknown_model_falls_back_to_default(app, app_context, client, english):
    sizes = []

    def _fake_clip(audio_path, lang_code, model_size="small"):
        sizes.append(model_size)
        return "The calm cat.", 6.0

    with patch.object(
        shadowing, "whisper_status", return_value={"installed": True}
    ), patch.object(shadowing, "transcribe_clip", side_effect=_fake_clip):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={
                "language_id": str(english.id),
                "tokens": json.dumps(["The", "calm", "cat"]),
                "model": "giant",
                "audio": (io.BytesIO(b"x"), "clip.webm"),
            },
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        status = _wait_for_task(resp.get_json()["task_id"])

    assert status["state"] == "finished"
    assert sizes == ["small"]


def test_route_no_speech_fails_the_task(app, app_context, client, english):
    tempdir = app.env_config.temppath

    def _fake_clip(audio_path, lang_code, model_size="small"):
        return "", 2.0

    with patch.object(
        shadowing, "whisper_status", return_value={"installed": True}
    ), patch.object(shadowing, "transcribe_clip", side_effect=_fake_clip):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={
                "language_id": str(english.id),
                "tokens": json.dumps(["The", "calm", "cat"]),
                "audio": (io.BytesIO(b"x"), "clip.webm"),
            },
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        status = _wait_for_task(resp.get_json()["task_id"])

    assert status["state"] == "error"
    assert "no speech" in status["error"]
    leftovers = [f for f in os.listdir(tempdir) if f.startswith("shadowing_")]
    assert leftovers == []


def test_route_transcription_error_fails_the_task_and_cleans_temp(
    app, app_context, client, english
):
    tempdir = app.env_config.temppath

    def _boom(audio_path, lang_code, model_size="small"):
        raise RuntimeError("model exploded")

    with patch.object(
        shadowing, "whisper_status", return_value={"installed": True}
    ), patch.object(shadowing, "transcribe_clip", side_effect=_boom):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={
                "language_id": str(english.id),
                "tokens": json.dumps(["The", "calm", "cat"]),
                "audio": (io.BytesIO(b"x"), "clip.webm"),
            },
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        status = _wait_for_task(resp.get_json()["task_id"])

    assert status["state"] == "error"
    assert "model exploded" in status["error"]
    leftovers = [f for f in os.listdir(tempdir) if f.startswith("shadowing_")]
    assert leftovers == []
