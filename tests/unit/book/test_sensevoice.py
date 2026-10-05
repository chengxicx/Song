"""
Tests for the SenseVoice engine module.

The real sherpa-onnx dependency is never imported: the language
mapping and file/bookkeeping helpers are pure, and anything touching
the recognizer is stubbed at the module boundary.
"""

import json
import os
import sys
import types
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


# ---------------------------------------------------------------------
# Streaming audiobook transcription (sherpa_onnx stubbed at sys.modules)
# ---------------------------------------------------------------------


def _write_wav(path, secs):
    "Write a mono 16 kHz pcm_s16le wav of `secs` seconds of silence."
    import av
    import numpy as np

    container = av.open(path, "w")
    stream = container.add_stream("pcm_s16le", rate=16000, layout="mono")
    for _ in range(secs):
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


class _Seg:
    "Stand-in for sherpa_onnx's SpeechSegment (sample-index start)."

    def __init__(self, start, samples):
        self.start = start
        self.samples = samples


class _FakeVad:
    """
    Deterministic VoiceActivityDetector: every cycle emits `speech_secs`
    as a segment and drops the following `silence_secs` as silence, so
    consecutive cues are separated by a pause like the real thing.
    """

    def __init__(self, _config=None, buffer_size_in_seconds=120, silence_secs=1.0):
        import numpy as np

        self._np = np
        self._speech = int(2.0 * 16000)
        self._cycle = self._speech + int(silence_secs * 16000)
        self._queue = []
        self._buf = []
        self._buf_len = 0
        self._pos = 0  # absolute fed samples

    def accept_waveform(self, samples):
        np = self._np
        self._buf.append(samples)
        self._buf_len += len(samples)
        self._pos += len(samples)
        while self._buf_len >= self._cycle:
            data = np.concatenate(self._buf)
            start = self._pos - self._buf_len
            self._queue.append(_Seg(start, data[: self._speech].copy()))
            rest = data[self._cycle :]
            self._buf = [rest]
            self._buf_len = rest.size

    def flush(self):
        np = self._np
        if self._buf_len:
            data = np.concatenate(self._buf)
            n = min(self._speech, self._buf_len)
            self._queue.append(_Seg(self._pos - self._buf_len, data[:n].copy()))
            self._buf, self._buf_len = [], 0

    def empty(self):
        return not self._queue

    @property
    def front(self):
        return self._queue[0]

    def pop(self):
        self._queue.pop(0)


class _CountingRecognizer:
    "Answers 'seg N' from the Nth stream, so cues are tellable apart."

    def __init__(self):
        self._n = 0

    def create_stream(self):
        self._n += 1
        return _FakeStream(f"seg {self._n}")

    def decode_stream(self, _stream):
        pass


def _install_fake_sherpa(monkeypatch, silence_secs=1.0):
    "Swap in a fake sherpa_onnx module providing only the VAD surface."
    fake = types.ModuleType("sherpa_onnx")

    class _Inner:
        pass  # cfg.silero_vad.* attribute bag

    class _FakeVadModelConfig:
        def __init__(self):
            self.silero_vad = _Inner()
            self.sample_rate = 16000

    fake.VadModelConfig = _FakeVadModelConfig
    fake.VoiceActivityDetector = lambda cfg, buffer_size_in_seconds=120: _FakeVad(
        silence_secs=silence_secs
    )
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)


def test_transcribe_to_cues_streams_and_reports_progress(model_dir_tmp, monkeypatch):
    """
    The audiobook path must stream-decode (never hold the whole file),
    keep VAD timestamps absolute, and report percent against the
    container duration.
    """
    try:
        import numpy  # noqa: F401
        import av  # noqa: F401
    except ImportError:
        pytest.skip("PyAV/numpy not installed")

    model_dir_tmp.mkdir()
    wav = model_dir_tmp / "book.wav"
    _write_wav(str(wav), 7)  # cycles of 2 s speech + 1 s silence

    _install_fake_sherpa(monkeypatch)
    pcts = []
    with patch.object(
        sensevoice, "_recognizer", return_value=_CountingRecognizer()
    ):
        text, cues_json = sensevoice.transcribe_to_cues(
            str(wav), "ja", progress_cb=pcts.append
        )

    cues = json.loads(cues_json)
    # 7 s -> speech at 0-2, 3-5, and a 1 s flush tail at 6-7.
    assert [c["start"] for c in cues] == [0.0, 3.0, 6.0]
    # 1 s pauses are >= SENTENCE_GAP_SECONDS: every cue ends like a
    # sentence, and the text reads as punctuated lines.
    assert [c["text"] for c in cues] == ["seg 1。", "seg 2。", "seg 3。"]
    assert text == "seg 1。\nseg 2。\nseg 3。"
    # Percent climbs monotonically and is capped at 99 before completion.
    assert pcts == sorted(pcts)
    assert pcts[-1] == 99


def test_transcribe_to_cues_short_pause_marks_clause(model_dir_tmp, monkeypatch):
    "A sub-0.7 s pause is a clause boundary (、 for Japanese)."
    try:
        import numpy  # noqa: F401
        import av  # noqa: F401
    except ImportError:
        pytest.skip("PyAV/numpy not installed")

    model_dir_tmp.mkdir()
    wav = model_dir_tmp / "book.wav"
    _write_wav(str(wav), 6)  # 2 s speech + 0.4 s pause, twice

    _install_fake_sherpa(monkeypatch, silence_secs=0.4)
    with patch.object(
        sensevoice, "_recognizer", return_value=_CountingRecognizer()
    ):
        text, cues_json = sensevoice.transcribe_to_cues(str(wav), "ja")

    cues = json.loads(cues_json)
    # Cues at 0-2 and 2.4-4.4 (0.4 s pauses -> clause marks), plus the
    # 4.8-6 flush tail, which gets the final sentence mark.
    assert [c["text"] for c in cues] == ["seg 1、", "seg 2、", "seg 3。"]
    assert text == "seg 1、\nseg 2、\nseg 3。"


def test_punctuate_cues_respects_existing_punctuation_and_languages():
    from lute.book.whisper_transcribe import punctuate_cues

    cues = [
        {"start": 0.0, "end": 2.0, "text": "_already？"},
        {"start": 3.0, "end": 5.0, "text": "hello world"},
        {"start": 5.2, "end": 7.0, "text": "hi"},
    ]
    punctuate_cues(cues, "en")
    # 0.2 s gap -> clause comma; the final cue gets a sentence mark.
    assert [c["text"] for c in cues] == ["_already？", "hello world,", "hi."]


def test_iter_16k_mono_chunks(model_dir_tmp):
    "The decoder yields bounded ~10 s float32 chunks, not the whole file."
    try:
        import numpy  # noqa: F401
        import av  # noqa: F401
    except ImportError:
        pytest.skip("PyAV/numpy not installed")
    import numpy as np

    model_dir_tmp.mkdir()
    wav = model_dir_tmp / "in.wav"
    _write_wav(str(wav), 25)

    chunks = list(sensevoice._iter_16k_mono(str(wav), chunk_secs=10))
    # Encode/decode roundtrips shift sample counts slightly, so the
    # chunk boundaries are "~10 s", not exact (the last chunk is short).
    assert len(chunks) == 3
    assert all(abs(c.size - 160000) < 1600 for c in chunks[:-1])
    assert sum(c.size for c in chunks) == pytest.approx(25 * 16000, abs=1600)
    assert chunks[0].dtype == np.float32

    # The whole-file helper still works for (short) clips.
    sr, samples, duration = sensevoice._decode_16k_mono(str(wav))
    assert sr == 16000
    assert samples.size == 25 * 16000
    assert duration == 25.0
