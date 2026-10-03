"""
Tests for the SenseVoice engine module.

The real sherpa-onnx dependency is never imported: the language
mapping and file/bookkeeping helpers are pure, and anything touching
the recognizer is stubbed at the module boundary.
"""

import os
from unittest.mock import patch

import pytest

from lute.book import sensevoice


@pytest.fixture(name="model_dir_tmp")
def fixture_model_dir_tmp(tmp_path, monkeypatch):
    """
    Point the model dir at a not-yet-existing temp path so tests never
    touch ~/.cache and 'not downloaded' is actually the initial state.
    """
    target = tmp_path / "sensevoice"
    monkeypatch.setattr(sensevoice, "model_dir", lambda: str(target))
    return target


# ---------------------------------------------------------------------
# Language mapping
# ---------------------------------------------------------------------


class _Lang:
    def __init__(self, tts_lang=None, name=None):
        self.tts_lang = tts_lang
        self.name = name


def test_lang_code_cantonese_maps_to_yue(app_context):
    """
    Cantonese -> yue: SenseVoice was trained on it (whisper small was
    not), which is the whole reason this engine exists.
    """
    assert sensevoice.lang_code_for(_Lang(tts_lang="zh-HK")) == "yue"
    assert (
        sensevoice.lang_code_for(_Lang(tts_lang=None, name="Cantonese Chinese"))
        == "yue"
    )


def test_lang_code_supported_languages(app_context):
    assert sensevoice.lang_code_for(_Lang(tts_lang="ja")) == "ja"
    assert sensevoice.lang_code_for(_Lang(tts_lang=None, name="korean")) == "ko"
    assert sensevoice.lang_code_for(_Lang(tts_lang="zh-CN")) == "zh"
    assert sensevoice.lang_code_for(_Lang(tts_lang=None, name="English")) == "en"


def test_lang_code_unsupported_languages_return_none(app_context):
    "Spanish/Thai/etc. stay on whisper; Sanskrit resolves to nothing."
    assert sensevoice.lang_code_for(_Lang(tts_lang="es-ES")) is None
    assert sensevoice.lang_code_for(_Lang(tts_lang=None, name="Thai")) is None
    assert sensevoice.lang_code_for(_Lang(tts_lang=None, name="Sanskrit")) is None
    assert sensevoice.lang_code_for(None) is None


# ---------------------------------------------------------------------
# Model file bookkeeping
# ---------------------------------------------------------------------


def test_model_cached_requires_all_files(model_dir_tmp):
    assert sensevoice.model_cached() is False
    model_dir_tmp.mkdir()
    (model_dir_tmp / "model.int8.onnx").write_bytes(b"x")
    (model_dir_tmp / "tokens.txt").write_bytes(b"x")
    assert sensevoice.model_cached() is False  # vad still missing
    (model_dir_tmp / "silero_vad.onnx").write_bytes(b"x")
    assert sensevoice.model_cached() is True


def test_available_requires_package_and_files(model_dir_tmp):
    assert sensevoice.available() is False
    model_dir_tmp.mkdir()
    (model_dir_tmp / "model.int8.onnx").write_bytes(b"x")
    (model_dir_tmp / "tokens.txt").write_bytes(b"x")
    (model_dir_tmp / "silero_vad.onnx").write_bytes(b"x")
    with patch.object(sensevoice, "installed", return_value=False):
        assert sensevoice.available() is False
    with patch.object(sensevoice, "installed", return_value=True):
        assert sensevoice.available() is True


def test_status_shape(model_dir_tmp):
    st = sensevoice.status()
    assert set(st) == {"installed", "cached", "size_mb"}


def test_delete_model_files(model_dir_tmp):
    "Delete reports False before any download, True afterwards."
    ok, _ = sensevoice.delete_model_files()
    assert ok is False  # nothing downloaded
    model_dir_tmp.mkdir()
    (model_dir_tmp / "tokens.txt").write_bytes(b"x")
    ok, _ = sensevoice.delete_model_files()
    assert ok is True
    # And a second delete finds nothing again.
    ok, _ = sensevoice.delete_model_files()
    assert ok is False


# ---------------------------------------------------------------------
# Transcription wrappers (sherpa_onnx stubbed)
# ---------------------------------------------------------------------


class _FakeStream:
    def __init__(self, text):
        self.result = type("R", (), {"text": text})()

    def accept_waveform(self, _sr, _samples):
        pass


class _FakeRecognizer:
    def __init__(self, text):
        self._text = text

    def create_stream(self):
        return _FakeStream(self._text)

    def decode_stream(self, _stream):
        pass


def test_transcribe_clip_returns_text_and_duration(model_dir_tmp):
    try:
        import numpy  # noqa: F401
        import av  # noqa: F401
    except ImportError:
        pytest.skip("PyAV/numpy not installed")
    import av
    import numpy as np

    model_dir_tmp.mkdir()
    wav = model_dir_tmp / "clip.wav"
    # One second of silence, 16 kHz mono s16.
    samples = np.zeros(16000, dtype=np.int16)
    container = av.open(str(wav), "w")
    stream = container.add_stream("pcm_s16le", rate=16000, layout="mono")
    frame = av.AudioFrame.from_ndarray(
        np.zeros((1, 16000), dtype=np.int16), format="s16", layout="mono"
    )
    frame.sample_rate = 16000
    frame.pts = None
    for packet in stream.encode(frame):
        container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()

    with patch.object(
        sensevoice, "_recognizer", return_value=_FakeRecognizer(" Hello.")
    ):
        text, duration = sensevoice.transcribe_clip(str(wav), "en")
    assert text == "Hello."
    assert 0.9 <= duration <= 1.1
    # os import kept for the file writes above.
    assert os.path.exists(wav)
