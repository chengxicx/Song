"""
Auto-generate audiobook subtitles with faster-whisper.

Mirrors the grammar-engine optional-dependency pattern
(lute/read/render/grammar_analysis.py): the heavy dependency is pip
installed on demand, detected with importlib.util.find_spec, and only
imported lazily when a transcription actually runs.

A transcription runs on a daemon thread; the import page polls
task_status(task_id) until the book is created (or an error surfaces).
"""

import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager

from lute.book.model import Book
from lute.book.service import Service as BookService
from lute.db import db
from lute.multiuser import context as mu_context

logger = logging.getLogger(__name__)

# Concrete pip requirements, mirroring the "whisper" extra in
# pyproject.toml (kept in sync by hand).
_WHISPER_INSTALL_SPECS = ["faster-whisper>=1.0,<2", "av>=11,<15"]
_PIP_TIMEOUT_SECONDS = 900

ALLOWED_MODEL_SIZES = ["base", "small", "medium"]
DEFAULT_MODEL_SIZE = "small"

# Model repos published by the faster-whisper project, keyed by the
# size names shown in the UI.
_MODEL_REPOS = {
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
}

MAX_CONCURRENT_TRANSCRIPTIONS = 1

# Remote audio larger than the local-storage cutoff is still downloaded
# to a temp file for transcription (transcription needs a local file),
# but only up to this hard cap.
WHISPER_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024

# Task states surfaced to the import page's poller.
#   queued -> loading_model -> transcribing -> finished
# any state -> error
_TASKS = {}
_TASKS_LOCK = threading.Lock()

# Loaded WhisperModel instances, keyed by model size.  Only the most
# recent size is kept: small/int8 is ~500 MB resident, and consecutive
# imports at the same size (multi-part podcasts) then skip the load.
_MODEL_CACHE = {}
_MODEL_CACHE_LOCK = threading.Lock()

# A loaded model is ~500 MB that the process never gives back on its own.
# Left alone it sits there forever, which is what pushes the 4 GB server
# into swap between takes, so drop it after this long without a
# transcription.  The weights stay in the HuggingFace cache on disk: the
# next transcription reloads them locally in seconds, no re-download.
MODEL_IDLE_TIMEOUT_SECONDS = 15 * 60
_IDLE_CHECK_INTERVAL_SECONDS = 60

# Monotonic time of the last transcription start/finish, and how many are
# running.  Both guarded by _MODEL_CACHE_LOCK.
_MODEL_LAST_USED = 0.0
_MODEL_USES = 0
_IDLE_REAPER_STARTED = False


def whisper_status():
    """
    UI summary of the whisper dependency:
      {"installed": bool, "missing": [package names]}
    """
    missing = ["faster_whisper"] if importlib.util.find_spec("faster_whisper") is None else []
    return {"installed": not missing, "missing": missing}


def install_whisper():
    """
    pip-install faster-whisper.

    Returns (ok, message).  Same contract as install_grammar_engine.
    """
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                # Wheels only: building PyAV from source needs ffmpeg
                # headers, Cython and a C toolchain, which a typical
                # server does not have (prod: av-14.4.0.tar.gz build
                # died).  Every supported Python has prebuilt wheels
                # within the pinned ranges, so let pip pick one.
                "--only-binary=:all:",
                *_WHISPER_INSTALL_SPECS,
            ],
            capture_output=True,
            text=True,
            timeout=_PIP_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "pip install of faster-whisper timed out"
    except OSError as e:
        return False, f"Could not run pip: {e}"
    if proc.returncode != 0:
        output = (proc.stdout or "") + (proc.stderr or "")
        return (
            False,
            "pip install of faster-whisper failed (wheels-only; a C build "
            "of PyAV is not attempted):\n"
            + output.strip()[-2000:],
        )
    return True, (
        "Installed faster-whisper.  "
        "If the import page still says it is missing, restart the app."
    )


def whisper_lang_code(language):
    """
    Map a Language to a whisper language code ("ja", "zh", ...), or None
    to let whisper auto-detect.

    Resolution order: the language's custom tts_lang (e.g. "zh-HK"),
    then a lookup of the language name in the TTS name table (so
    "Japanese" resolves even without a tts_lang configured).  That name
    fallback matters: languages without a tts_lang used to return None
    here, and whisper's auto-detect on a few seconds of speech
    regularly misfires (shadowing takes were read as the wrong
    language).  An unknown name returns None rather than an "en"
    default -- auto-detect beats guessing English.

    Not reusing get_lang_code_for directly because it falls back to
    DEFAULT_LANG_TAG ("en-US") for unknown names.
    """
    if language is None:
        return None
    # Imported here: lute.tts.routes pulls in the flask app machinery,
    # which must not be loaded at module import time (this module is
    # imported from background threads and task contexts too).
    from lute.tts.routes import LANG_NAME_TO_CODE

    custom = (getattr(language, "tts_lang", None) or "").strip()
    if custom:
        return custom.split("-")[0].lower() or None
    tag = LANG_NAME_TO_CODE.get(
        (getattr(language, "name", "") or "").strip().lower()
    )
    return tag.split("-")[0].lower() if tag else None


def _load_model(model_size):
    "Load (or reuse a cached) WhisperModel.  Blocking; may download on first use."
    global _MODEL_LAST_USED  # pylint: disable=global-statement

    with _MODEL_CACHE_LOCK:
        # Counted as "used" on the way in, not just on the way out: a
        # take that takes longer than the idle timeout must not have the
        # model dropped underneath it.
        _MODEL_LAST_USED = time.monotonic()
        model = _MODEL_CACHE.get(model_size)
        if model is not None:
            # Logged so a slow take can be told apart: a hit means the
            # wait is pure CPU inference, a miss means the model is being
            # re-read (or re-downloaded) and the cache is not surviving.
            logger.info("whisper: reusing cached model '%s'", model_size)
        else:
            logger.info("whisper: cache miss, loading model '%s'", model_size)
            # Delayed: only import the heavy package when actually
            # transcribing.
            from faster_whisper import WhisperModel  # pylint: disable=import-error,import-outside-toplevel

            # CPU + int8: no GPU assumed on a self-hosted box, int8 is the
            # fastest accurate quantization for CPU inference.
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            _MODEL_CACHE.clear()
            _MODEL_CACHE[model_size] = model
            _MODEL_LAST_USED = time.monotonic()

    # Outside the lock: it is held for the whole load above.
    _ensure_idle_reaper_started()
    return model


def _ensure_idle_reaper_started():
    "Start the idle-unload thread once, on the first successful load."
    global _IDLE_REAPER_STARTED  # pylint: disable=global-statement

    with _MODEL_CACHE_LOCK:
        if _IDLE_REAPER_STARTED:
            return
        _IDLE_REAPER_STARTED = True
    threading.Thread(
        target=_idle_reaper_loop, name="whisper-idle-reaper", daemon=True
    ).start()


def _idle_reaper_loop():
    "Drop the cached model once nothing has transcribed for a while."
    while True:
        time.sleep(_IDLE_CHECK_INTERVAL_SECONDS)
        unload_idle_model()


def unload_idle_model():
    """
    Drop the cached model if it has been idle for MODEL_IDLE_TIMEOUT_SECONDS.

    Returns the size that was unloaded, or None if nothing was (still in
    use, or not idle long enough).  Only the in-process instance goes:
    the weights are still in the HuggingFace cache on disk.
    """
    with _MODEL_CACHE_LOCK:
        if not _MODEL_CACHE or _MODEL_USES > 0:
            return None
        if time.monotonic() - _MODEL_LAST_USED < MODEL_IDLE_TIMEOUT_SECONDS:
            return None
        sizes = ", ".join(sorted(_MODEL_CACHE))
        _MODEL_CACHE.clear()

    logger.info(
        "whisper: unloaded model(s) %s after %d min idle; the next "
        "transcription reloads them from the local cache",
        sizes,
        MODEL_IDLE_TIMEOUT_SECONDS // 60,
    )
    return sizes


@contextmanager
def model_in_use():
    """
    Hold the loaded model for the duration of a transcription.

    The idle reaper skips the cache while this is held.  Without it a
    take longer than MODEL_IDLE_TIMEOUT_SECONDS would be evicted midway:
    the caller's own reference keeps that instance alive and the next
    _load_model builds a second one beside it -- two ~500 MB copies on a
    box with room for neither.
    """
    global _MODEL_USES, _MODEL_LAST_USED  # pylint: disable=global-statement

    with _MODEL_CACHE_LOCK:
        _MODEL_USES += 1
        _MODEL_LAST_USED = time.monotonic()
    try:
        yield
    finally:
        with _MODEL_CACHE_LOCK:
            _MODEL_USES -= 1
            _MODEL_LAST_USED = time.monotonic()


def transcribe_to_cues(
    audio_path, lang_code, model_size=DEFAULT_MODEL_SIZE, progress_cb=None
):
    """
    Transcribe an audio file into subtitle cues.

    Returns (text, cues_json), matching the output shape of
    parse_subtitle_content: text is the cue texts joined by newlines,
    cues_json is a JSON string of [{"start": secs, "end": secs, "text": str}].
    """
    model = _load_model(model_size)

    with model_in_use():
        segments_iter, info = model.transcribe(
            audio_path,
            language=lang_code,
            # vad_filter skips silence/music gaps, which otherwise produce
            # hallucinated text on podcast interludes.
            vad_filter=True,
            # Not conditioning on previous text keeps hallucination loops
            # from compounding across long audio (community-standard for
            # long-form transcription).
            condition_on_previous_text=False,
        )

        duration = info.duration or 0.0
        cues = []
        for seg in segments_iter:  # lazy: transcription happens while iterating
            cues.append(
                {"start": seg.start, "end": seg.end, "text": (seg.text or "").strip()}
            )
            if progress_cb is not None and duration > 0:
                progress_cb(min(99, int(seg.end / duration * 100)))

    text = "\n".join(c["text"] for c in cues if c["text"])
    return text, json.dumps(cues, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Background task machinery.


def _set_state(task_id, state, percent=0, error=None, book_id=None, message=None):
    with _TASKS_LOCK:
        task = _TASKS.get(task_id, {})
        task.update(
            {
                "state": state,
                "percent": percent,
                "error": error,
                "book_id": book_id,
                "message": message,
            }
        )
        _TASKS[task_id] = task


def _safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def start_task(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    app, audio_path, lang_code, model_size, book_params, media_url=None, username=None
):
    """
    Register and launch a background transcription task.

    audio_path is a temp file (already on disk); it is moved into the
    created book (small files) or deleted (large remote-streamed ones)
    by the task itself.  book_params: {language_id, title, tags,
    source_uri}.  username is the requesting user (multi-user mode);
    the task thread re-enters that user's scope so its db access lands
    on the user's own sqlite file.  Returns the task_id.
    """
    task_id = uuid.uuid4().hex
    _set_state(task_id, "queued", message="Queued.")
    with _TASKS_LOCK:
        _TASKS[task_id]["audio_path"] = audio_path
        _TASKS[task_id]["media_url"] = media_url

    thread = threading.Thread(
        target=_run_task,
        args=(
            app,
            task_id,
            audio_path,
            lang_code,
            model_size,
            book_params,
            media_url,
            username,
        ),
        daemon=True,
    )
    thread.start()
    return task_id


def _run_task(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    app, task_id, audio_path, lang_code, model_size, book_params, media_url, username
):
    """
    Thread body: transcribe, then import the book.

    Holds the app context for the whole run (import_book needs
    current_app.env_config and db.session); user_scope re-enters the
    requesting user's identity -- the multiuser ContextVar does not
    cross threads, and without it both the db connection creator and
    the user-scoped env_config paths fail in multi-user mode.  The
    scoped db session is keyed to the app context, so it is removed
    while the context is still alive; the temp file is dropped on
    success (it has been copied into the book) and failure alike.
    Error state is set last, after cleanup, so a poller that sees the
    terminal state also sees the cleaned-up disk.
    """
    try:
        with app.app_context(), mu_context.user_scope(username):
            try:
                _set_state(
                    task_id,
                    "loading_model",
                    message="Loading model (first run downloads ~500 MB).",
                )
                text, cues_json = transcribe_to_cues(
                    audio_path,
                    lang_code,
                    model_size,
                    progress_cb=lambda p: _set_state(task_id, "transcribing", percent=p),
                )
                if not (text and text.strip()):
                    raise RuntimeError("The transcription produced no text.")

                b = Book()
                b.language_id = book_params.get("language_id")
                b.title = book_params.get("title")
                b.source_uri = book_params.get("source_uri")
                b.text = text
                b.srt_data = cues_json
                b.book_type = "mp3"
                b.book_tags = book_params.get("tags") or []
                b.threshold_page_tokens = 250
                b.split_by = "paragraphs"
                if media_url:
                    # Large remote audio: keep streaming from the URL, the
                    # temp download was only needed for transcription.
                    b.media_url = media_url
                else:
                    # Small/local audio: import_book copies the temp file
                    # into the user audio dir under a unique name.
                    b.audio_source_path = audio_path

                book = BookService().import_book(b, db.session)
                _set_state(task_id, "finished", percent=100, book_id=book.id)
            finally:
                db.session.remove()
                # The temp download has been copied into the book
                # (success) or is no longer needed (failure).
                _safe_remove(audio_path)
    except Exception as e:  # pylint: disable=broad-except
        _set_state(task_id, "error", error=str(e))


def task_status(task_id):
    "Poller payload for one task; unknown ids report state=unknown."
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            return {"state": "unknown"}
        return {
            "state": task.get("state"),
            "percent": task.get("percent", 0),
            "error": task.get("error"),
            "book_id": task.get("book_id"),
            "message": task.get("message"),
        }


def has_running_task():
    "True while any transcription or model download is in flight."
    with _TASKS_LOCK:
        return any(
            t.get("state") in ("queued", "loading_model", "transcribing")
            for t in _TASKS.values()
        )


def start_model_download(app, model_size):
    """
    Pre-download the selected model in the background so the first real
    transcription starts fast.  The download happens inside the
    WhisperModel constructor; on success the instance sits in
    _MODEL_CACHE and is reused.  Returns the task_id.
    """
    task_id = uuid.uuid4().hex
    _set_state(
        task_id,
        "loading_model",
        message=f"Downloading model '{model_size}' (hundreds of MB, one time only).",
    )
    thread = threading.Thread(
        target=_run_model_download, args=(task_id, model_size), daemon=True
    )
    thread.start()
    return task_id


def _run_model_download(task_id, model_size):
    try:
        _load_model(model_size)
        _set_state(task_id, "finished", percent=100)
    except Exception as e:  # pylint: disable=broad-except
        _set_state(
            task_id,
            "error",
            error=(
                f"{e}  (If this was a network timeout downloading the model, "
                "set HF_ENDPOINT=https://hf-mirror.com in the server environment "
                "and retry.)"
            ),
        )


def purge_finished_tasks():
    "Drop terminal-state tasks; called before starting a new one."
    with _TASKS_LOCK:
        for task_id in [
            tid
            for tid, t in _TASKS.items()
            if t.get("state") in ("finished", "error")
        ]:
            del _TASKS[task_id]


# ---------------------------------------------------------------------------
# Model cache management (Settings page).


def model_cache_info():
    """
    Per-size model cache status:
      [{"size": "small", "cached": bool, "size_mb": int}, ...]
    Sizes are reported via huggingface_hub's cache scan (the same cache
    faster-whisper's WhisperModel downloads into).
    """
    info = [{"size": s, "cached": False, "size_mb": 0} for s in ALLOWED_MODEL_SIZES]
    try:
        # Delayed: heavy optional dependency chain.
        from huggingface_hub import scan_cache_dir  # pylint: disable=import-error,import-outside-toplevel

        repos = {}
        for repo in scan_cache_dir().repos:
            repos[repo.repo_id] = repo
        for entry in info:
            repo = repos.get(_MODEL_REPOS[entry["size"]])
            if repo is not None:
                entry["cached"] = True
                entry["size_mb"] = int(repo.size_on_disk / (1024 * 1024))
    except Exception:  # pylint: disable=broad-except
        # No cache dir / huggingface_hub missing: nothing is cached.
        pass
    return info


def delete_model(model_size):
    """
    Remove a downloaded model from disk (and drop any loaded instance).
    Returns (ok, message).
    """
    if model_size not in _MODEL_REPOS:
        return False, "Unknown model size."
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.pop(model_size, None)

    repo_id = _MODEL_REPOS[model_size]
    try:
        from huggingface_hub import scan_cache_dir  # pylint: disable=import-error,import-outside-toplevel

        for repo in scan_cache_dir().repos:
            if repo.repo_id == repo_id:
                shutil.rmtree(repo.repo_path, ignore_errors=True)
                return True, f"Deleted model '{model_size}' ({repo_id})."
    except Exception as e:  # pylint: disable=broad-except
        return False, f"Could not scan the model cache: {e}"
    return False, f"Model '{model_size}' was not downloaded."
