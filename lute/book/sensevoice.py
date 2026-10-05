"""
SenseVoice-Small ASR via sherpa-onnx: the first-choice transcription
engine for the languages it was trained on (zh / yue / en / ja / ko),
with faster-whisper staying as the engine for everything else (Spanish,
Thai, ...) and as the fallback when this one is unavailable.

Why it beats whisper small for those languages: SenseVoice-Small was
actually trained on Cantonese (whisper base/small/medium rewrite
Cantonese as Mandarin -- empirically, on prod, with edge-tts samples),
and on a CPU it transcribes the same clip several times faster
(~300 ms vs seconds for a few seconds of speech).

Mirrors the whisper_transcribe optional-dependency pattern: sherpa-onnx
is pip-installed separately, and the model files auto-download on first
use into a dedicated cache dir ($LUTE_SENSEVOICE_DIR or
~/.cache/lute/sensevoice).  Nothing here imports sherpa_onnx at module
import time -- every entry point imports it lazily.
"""

import importlib.util
import logging
import os
import shutil
import tarfile
import threading
import time
import urllib.request
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_RELEASE_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
_MODEL_TARBALL = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2"
_TARBALL_URL = f"{_RELEASE_BASE}/{_MODEL_TARBALL}"
_VAD_URL = f"{_RELEASE_BASE}/silero_vad.onnx"

# Files kept from the model tarball (the tarball nests them one level down).
_TARBALL_FILES = ("model.int8.onnx", "tokens.txt")

# Language codes SenseVoice was trained on.  Everything else stays on
# whisper.
SUPPORTED_CODES = ("zh", "yue", "en", "ja", "ko")

# Resident-model housekeeping, same idea as whisper_transcribe's idle
# reaper: a loaded recognizer is a few hundred MB the process never
# gives back on its own.
MODEL_IDLE_TIMEOUT_SECONDS = 15 * 60

# Cached OfflineRecognizer: one instance, keyed by language code.
_RECOG = None
_RECOG_LANG = None
_RECOG_LAST_USED = 0.0
_RECOG_LOCK = threading.Lock()
_RECOG_USES = 0
_REAPER_STARTED = False


def model_dir():
    "Directory holding the model files."
    return os.environ.get("LUTE_SENSEVOICE_DIR") or os.path.expanduser(
        "~/.cache/lute/sensevoice"
    )


def model_paths():
    "Absolute paths of the three model files."
    d = model_dir()
    return {
        "model": os.path.join(d, "model.int8.onnx"),
        "tokens": os.path.join(d, "tokens.txt"),
        "vad": os.path.join(d, "silero_vad.onnx"),
    }


def installed():
    "True when the sherpa-onnx package is importable."
    return importlib.util.find_spec("sherpa_onnx") is not None


def model_cached():
    "True when every model file is on disk."
    return all(os.path.exists(p) for p in model_paths().values())


def model_size_mb():
    "Total size of the downloaded model files, in MB (0 when absent)."
    if not model_cached():
        return 0
    total = sum(os.path.getsize(p) for p in model_paths().values())
    return int(total / (1024 * 1024))


def available():
    "True when transcriptions can actually run on this engine."
    return installed() and model_cached()


def status():
    "UI summary for the Settings page."
    return {
        "installed": installed(),
        "cached": model_cached(),
        "size_mb": model_size_mb(),
    }


def lang_code_for(language):
    """
    Map a Language to a SenseVoice language code, or None when this
    engine does not support it.

    Cantonese -> "yue": SenseVoice was trained on it (whisper small was
    not).  Everything else goes by the same resolution whisper uses --
    custom tts_lang first, then the language name in the TTS name
    table -- and is accepted only when the primary subtag is one of the
    trained languages.
    """
    if language is None:
        return None
    from lute.book.whisper_transcribe import resolve_language_tag

    tag = resolve_language_tag(language)
    if not tag:
        return None
    if tag.lower().startswith("zh-hk"):
        return "yue"
    code = tag.split("-")[0].lower()
    return code if code in SUPPORTED_CODES else None


def ensure_model_downloaded():
    """
    Download the model files unless they are already on disk.

    Blocking; the tarball is a few hundred MB, one time only.  Raises
    on network or extraction errors so callers can surface the failure.
    """
    if model_cached():
        return
    d = model_dir()
    os.makedirs(d, exist_ok=True)
    paths = model_paths()

    tarpath = os.path.join(d, _MODEL_TARBALL)
    _download(_TARBALL_URL, tarpath)
    try:
        with tarfile.open(tarpath, "r:bz2") as tf:
            wanted = set(_TARBALL_FILES)
            for member in tf.getmembers():
                if os.path.basename(member.name) in wanted:
                    with tf.extractfile(member) as src, open(
                        os.path.join(d, os.path.basename(member.name)), "wb"
                    ) as dst:
                        shutil.copyfileobj(src, dst)
    finally:
        try:
            os.remove(tarpath)
        except OSError:
            pass

    if not os.path.exists(paths["vad"]):
        _download(_VAD_URL, paths["vad"])

    missing = [k for k, p in paths.items() if not os.path.exists(p)]
    if missing:
        raise RuntimeError(f"SenseVoice model download incomplete: {missing}")
    logger.info("sensevoice: model ready in %s (%d MB)", d, model_size_mb())


def _download(url, dest):
    "Stream url to dest (via a temp file), with a UA GitHub insists on."
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "lute-v3"})
    logger.info("sensevoice: downloading %s", url)
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
        while True:
            block = resp.read(1024 * 1024)
            if not block:
                break
            f.write(block)
    os.replace(tmp, dest)


# ---------------------------------------------------------------------------
# Inference.
# ---------------------------------------------------------------------------


def _recognizer(lang_code):
    """
    A (cached) OfflineRecognizer for lang_code.

    Model load costs a second or two; shadowing takes come in bursts, so
    the loaded recognizer is kept and dropped by an idle reaper, exactly
    like the whisper model cache.
    """
    global _RECOG, _RECOG_LANG, _RECOG_LAST_USED  # pylint: disable=global-statement
    with _RECOG_LOCK:
        _RECOG_LAST_USED = time.monotonic()
        if _RECOG is not None and _RECOG_LANG == lang_code:
            return _RECOG

        import sherpa_onnx  # pylint: disable=import-error,import-outside-toplevel

        paths = model_paths()
        rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=paths["model"],
            tokens=paths["tokens"],
            use_itn=True,
            language=lang_code or "",
            # The prod box has 2 cores; the default single inference
            # thread leaves one of them idle through every decode.
            num_threads=2,
        )
        _RECOG, _RECOG_LANG = rec, lang_code
        _RECOG_LAST_USED = time.monotonic()
        _ensure_reaper_started()
        return rec


def _ensure_reaper_started():
    """
    Start the idle-unload thread once.  Caller must hold _RECOG_LOCK
    (this runs from inside _recognizer's locked section; Lock is not
    reentrant, so this must not take it again).
    """
    global _REAPER_STARTED  # pylint: disable=global-statement
    if _REAPER_STARTED:
        return
    _REAPER_STARTED = True
    threading.Thread(
        target=_reaper_loop, name="sensevoice-idle-reaper", daemon=True
    ).start()


def _reaper_loop():
    global _RECOG, _RECOG_LANG  # pylint: disable=global-statement
    while True:
        time.sleep(60)
        with _RECOG_LOCK:
            if _RECOG_USES > 0 or _RECOG is None:
                continue
            if time.monotonic() - _RECOG_LAST_USED < MODEL_IDLE_TIMEOUT_SECONDS:
                continue
            _RECOG = None
            _RECOG_LANG = None
        logger.info(
            "sensevoice: unloaded recognizer after %d min idle",
            MODEL_IDLE_TIMEOUT_SECONDS // 60,
        )


def _iter_16k_mono(audio_path, chunk_secs=10):
    """
    Stream-decode any audio file into 16 kHz mono float32 chunks of
    ~chunk_secs each (the last one short) via PyAV -- the same decoder
    whisper's PyAV wheels already provide, so no new binary dependency.

    A generator on purpose: holding a long file's decoded samples in
    full is ~230 MB per hour, which is what pushed the 4 GB prod box
    into swap during audiobook imports.
    """
    import av  # pylint: disable=import-error,import-outside-toplevel
    import numpy as np  # pylint: disable=import-error,import-outside-toplevel

    resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
    chunk, filled = [], 0
    with av.open(audio_path) as container:
        for packet in container.demux(audio=0):
            for frame in packet.decode():
                for rf in resampler.resample(frame):
                    arr = rf.to_ndarray().reshape(-1)
                    chunk.append(arr)
                    filled += arr.size
                    if filled >= chunk_secs * 16000:
                        yield np.concatenate(chunk)
                        chunk, filled = [], 0
    if chunk:
        yield np.concatenate(chunk)


def _decode_16k_mono(audio_path):
    """
    Decode a whole (short) file to (sample_rate, float32 mono samples,
    duration_secs).  For clips only: long files must be streamed via
    _iter_16k_mono rather than landed in RAM in full.
    """
    import numpy as np  # pylint: disable=import-outside-toplevel

    chunks = list(_iter_16k_mono(audio_path, chunk_secs=60))
    if not chunks:
        return 16000, np.zeros(0, dtype=np.float32), 0.0
    samples = np.concatenate(chunks)
    return 16000, samples, samples.size / 16000.0


def _container_duration_secs(audio_path):
    "Duration from container metadata (seconds), or 0.0 when unknown."
    import av  # pylint: disable=import-error,import-outside-toplevel

    try:
        with av.open(audio_path) as container:
            return (container.duration or 0) / av.time_base
    except Exception:  # pylint: disable=broad-except
        return 0.0


def transcribe_clip(audio_path, lang_code):
    """
    Transcribe one short recording (shadowing takes).

    Returns (text, duration_secs).
    """
    sr, samples, duration = _decode_16k_mono(audio_path)
    rec = _recognizer(lang_code)
    with _recog_in_use():
        stream = rec.create_stream()
        stream.accept_waveform(sr, samples)
        rec.decode_stream(stream)
        text = (stream.result.text or "").strip()
    logger.info(
        "sensevoice: transcribed %.1fs clip (language=%r) -> %r",
        duration,
        lang_code,
        text[:80],
    )
    return text, duration


def transcribe_to_cues(audio_path, lang_code, progress_cb=None):
    """
    Transcribe an audio file into subtitle cues (audiobook import).

    The file is stream-decoded and fed to the VAD chunk by chunk; every
    speech segment the VAD yields is recognized and popped before the
    next chunk is decoded, so peak memory is one chunk plus the model
    rather than the whole decoded file.  The VAD still sees one
    continuous stream, so its segmentation is identical to feeding
    everything at once, and cue timestamps are the VAD boundaries --
    same shape as whisper_transcribe.transcribe_to_cues' output.

    Percent progress is computed against the container-reported
    duration; a container without one simply never reports percent.

    Returns (text, cues_json).
    """
    import json

    import sherpa_onnx  # pylint: disable=import-error,import-outside-toplevel

    sr = 16000
    rec = _recognizer(lang_code)

    duration = _container_duration_secs(audio_path)

    paths = model_paths()
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = paths["vad"]
    cfg.silero_vad.min_silence_duration = 0.25
    cfg.silero_vad.min_speech_duration = 0.25
    cfg.silero_vad.max_speech_duration = 20
    cfg.sample_rate = sr
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=120)

    cues = []

    def _take(segment):
        start = segment.start / sr
        seg_samples = segment.samples
        if not len(seg_samples):
            return
        stream = rec.create_stream()
        stream.accept_waveform(sr, seg_samples)
        rec.decode_stream(stream)
        text = (stream.result.text or "").strip()
        end = start + len(seg_samples) / sr
        if text:
            cues.append({"start": round(start, 3), "end": round(end, 3), "text": text})
        if progress_cb is not None and duration > 0:
            progress_cb(min(99, int(end / duration * 100)))

    with _recog_in_use():
        for chunk in _iter_16k_mono(audio_path):
            vad.accept_waveform(chunk)
            while not vad.empty():
                _take(vad.front)
                vad.pop()
        vad.flush()
        while not vad.empty():
            _take(vad.front)
            vad.pop()

    text = "\n".join(c["text"] for c in cues)
    return text, json.dumps(cues, ensure_ascii=False)


@contextmanager
def _recog_in_use():
    "Pin the cached recognizer against the idle reaper during a decode."
    global _RECOG_USES  # pylint: disable=global-statement
    with _RECOG_LOCK:
        _RECOG_USES += 1
        _RECOG_LAST_USED = time.monotonic()
    try:
        yield
    finally:
        with _RECOG_LOCK:
            _RECOG_USES -= 1
            _RECOG_LAST_USED = time.monotonic()


def delete_model_files():
    "Remove the model directory (Settings page delete).  Returns (ok, message)."
    d = model_dir()
    if not os.path.isdir(d):
        return False, "SenseVoice model was not downloaded."
    shutil.rmtree(d, ignore_errors=True)
    return True, "Deleted the SenseVoice model."
