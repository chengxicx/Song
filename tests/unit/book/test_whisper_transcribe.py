"""
Tests for the whisper auto-subtitle import feature.

The real faster-whisper dependency is never imported here: the
transcription function is monkeypatched, and the tests cover the task
state machine, the /book/whisper/* routes, and the temp-file lifecycle.
"""

import io
import json
import os
import threading
import time
from unittest.mock import patch

import pytest

from lute.db import db
from lute.book import whisper_transcribe
from lute.models.repositories import BookRepository

# pylint: disable=protected-access


# ---------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------


def test_whisper_status_not_installed():
    "find_spec miss is reported as not installed."
    with patch("importlib.util.find_spec", return_value=None):
        status = whisper_transcribe.whisper_status()
    assert status == {"installed": False, "missing": ["faster_whisper"]}


def test_whisper_status_installed():
    with patch("importlib.util.find_spec", return_value=object()):
        status = whisper_transcribe.whisper_status()
    assert status == {"installed": True, "missing": []}


def test_lang_code_mapping(app_context):
    """
    tts_lang is reduced to a bare ISO code; when tts_lang is unset the
    language name is looked up in the TTS name table (Japanese/Korean
    ship without a tts_lang and must not fall through to whisper's
    auto-detect, which misfires on short shadowing takes).
    """

    class _Lang:
        tts_lang = "zh-CN"

    assert whisper_transcribe.whisper_lang_code(_Lang()) == "zh"

    class _Ja:
        tts_lang = "ja"

    assert whisper_transcribe.whisper_lang_code(_Ja()) == "ja"

    class _Cantonese:
        tts_lang = "zh-HK"

    # yue only on the large-v3 family; smaller models get the Mandarin
    # rewrite (the yue token exists but is untrained there).
    assert whisper_transcribe.whisper_lang_code(_Cantonese()) == "zh"
    assert whisper_transcribe.whisper_lang_code(_Cantonese(), "small") == "zh"
    assert (
        whisper_transcribe.whisper_lang_code(_Cantonese(), "large-v3-turbo")
        == "yue"
    )

    class _ByName:
        tts_lang = None
        name = "Japanese"

    assert whisper_transcribe.whisper_lang_code(_ByName()) == "ja"

    class _KoreanByName:
        tts_lang = ""
        name = "korean"

    assert whisper_transcribe.whisper_lang_code(_KoreanByName()) == "ko"

    class _CantoneseByName:
        tts_lang = None
        name = "Cantonese Chinese"

    assert whisper_transcribe.whisper_lang_code(_CantoneseByName()) == "zh"
    assert (
        whisper_transcribe.whisper_lang_code(
            _CantoneseByName(), "large-v3-turbo"
        )
        == "yue"
    )

    # Cantonese on a non-yue model is surfaced as a client hint.
    assert whisper_transcribe.whisper_language_note(_Cantonese(), "small")
    assert (
        whisper_transcribe.whisper_language_note(
            _Cantonese(), "large-v3-turbo"
        )
        is None
    )
    assert whisper_transcribe.whisper_language_note(_ByName(), "small") is None

    # Unknown name: auto-detect, never the TTS table's "en" default.
    class _Unknown:
        tts_lang = None
        name = "Sanskrit"

    assert whisper_transcribe.whisper_lang_code(_Unknown()) is None

    class _NoLang:
        tts_lang = None

    assert whisper_transcribe.whisper_lang_code(_NoLang()) is None
    assert whisper_transcribe.whisper_lang_code(None) is None


# ---------------------------------------------------------------------
# Route validation
# ---------------------------------------------------------------------


def test_prepare_requires_whisper_installed(app, client, english):
    with patch.object(whisper_transcribe, "whisper_status", return_value={"installed": False}):
        resp = client.post(
            "/book/whisper/prepare",
            data={"language_id": str(english.id)},
        )
    assert resp.status_code == 400
    assert "not installed" in resp.get_json()["error"]


def test_prepare_requires_language(app, client):
    with patch.object(whisper_transcribe, "whisper_status", return_value={"installed": True}):
        resp = client.post("/book/whisper/prepare", data={})
    assert resp.status_code == 400
    assert "language" in resp.get_json()["error"].lower()


def test_prepare_rejects_bad_extension(app, client, english):
    "Non-audio uploads are rejected before any task starts."
    with patch.object(whisper_transcribe, "whisper_status", return_value={"installed": True}):
        resp = client.post(
            "/book/whisper/prepare",
            data={
                "language_id": str(english.id),
                "mp3_file": (io.BytesIO(b"not audio"), "notes.txt"),
            },
            content_type="multipart/form-data",
        )
    assert resp.status_code == 400
    assert resp.get_json()["error"]


def test_prepare_busy_returns_409(app, client, english):
    with patch.object(whisper_transcribe, "whisper_status", return_value={"installed": True}), patch.object(
        whisper_transcribe, "has_running_task", return_value=True
    ):
        resp = client.post(
            "/book/whisper/prepare",
            data={"language_id": str(english.id), "mp3_url": "https://a.example.com/x.mp3"},
        )
    assert resp.status_code == 409
    assert "already running" in resp.get_json()["error"]


def test_available_endpoint(app, client):
    resp = client.get("/book/whisper/available")
    assert resp.status_code == 200
    assert "installed" in resp.get_json()


# ---------------------------------------------------------------------
# Model pre-download
# ---------------------------------------------------------------------


def test_download_model_requires_whisper_installed(app, client):
    with patch.object(whisper_transcribe, "whisper_status", return_value={"installed": False}):
        resp = client.post("/book/whisper/download_model", data={"whisper_model": "small"})
    assert resp.status_code == 400
    assert "not installed" in resp.get_json()["error"]


def test_download_model_rejects_unknown_size(app, client):
    with patch.object(whisper_transcribe, "whisper_status", return_value={"installed": True}):
        resp = client.post("/book/whisper/download_model", data={"whisper_model": "giant"})
    assert resp.status_code == 400
    assert "Unknown model size" in resp.get_json()["error"]


def test_download_model_busy_returns_409(app, client):
    with patch.object(whisper_transcribe, "whisper_status", return_value={"installed": True}), patch.object(
        whisper_transcribe, "has_running_task", return_value=True
    ):
        resp = client.post("/book/whisper/download_model", data={"whisper_model": "small"})
    assert resp.status_code == 409


def test_download_model_completes(app, app_context, client):
    "Happy path with a fake loader: task finishes and caches nothing."
    with patch.object(whisper_transcribe, "whisper_status", return_value={"installed": True}), patch.object(
        whisper_transcribe, "_load_model", return_value=object()
    ) as fake_load:
        resp = client.post("/book/whisper/download_model", data={"whisper_model": "small"})
        assert resp.status_code == 200
        task_id = resp.get_json()["task_id"]
        status = _wait_for_terminal(task_id)
    assert status["state"] == "finished"
    fake_load.assert_called_once_with("small")


def test_prepare_409_while_model_downloads(app, client, english):
    "A running model download blocks a new transcription (mutex = 1)."
    started = threading.Event()
    release = threading.Event()

    def _slow_load(model_size):
        started.set()
        release.wait(timeout=10)
        return object()

    with patch.object(whisper_transcribe, "whisper_status", return_value={"installed": True}), patch.object(
        whisper_transcribe, "_load_model", side_effect=_slow_load
    ):
        resp = client.post("/book/whisper/download_model", data={"whisper_model": "small"})
        task_id = resp.get_json()["task_id"]
        assert started.wait(timeout=10)

        with patch.object(whisper_transcribe, "whisper_status", return_value={"installed": True}):
            resp2 = client.post(
                "/book/whisper/prepare",
                data={"language_id": str(english.id), "mp3_url": "https://a.example.com/x.mp3"},
            )
        assert resp2.status_code == 409

        release.set()
        status = _wait_for_terminal(task_id)
        assert status["state"] == "finished"


# ---------------------------------------------------------------------
# Model cache management (Settings page).
# ---------------------------------------------------------------------


class _FakeRepo:
    "Duck-typed huggingface_hub CachedRepo."

    def __init__(self, repo_id, size_mb, path):
        self.repo_id = repo_id
        self.size_on_disk = size_mb * 1024 * 1024
        self.repo_path = path


class _FakeCache:
    def __init__(self, repos):
        self.repos = repos


def test_model_cache_info_reports_cached_sizes():
    fake = _FakeCache([_FakeRepo("Systran/faster-whisper-small", 460, "/tmp/x")])
    with patch("huggingface_hub.scan_cache_dir", return_value=fake):
        info = whisper_transcribe.model_cache_info()
    by_size = {e["size"]: e for e in info}
    assert by_size["small"]["cached"] is True
    assert by_size["small"]["size_mb"] == 460
    assert by_size["base"]["cached"] is False


def test_model_cache_info_without_cache_dir():
    with patch("huggingface_hub.scan_cache_dir", side_effect=OSError("no cache")):
        info = whisper_transcribe.model_cache_info()
    assert all(e["cached"] is False for e in info)


def test_delete_model_removes_directory(tmp_path):
    target = tmp_path / "models--Systran--faster-whisper-small"
    target.mkdir()
    fake = _FakeCache([_FakeRepo("Systran/faster-whisper-small", 460, str(target))])
    with patch("huggingface_hub.scan_cache_dir", return_value=fake):
        ok, message = whisper_transcribe.delete_model("small")
    assert ok is True
    assert not target.exists()
    assert "Deleted" in message


def test_delete_model_not_downloaded():
    with patch("huggingface_hub.scan_cache_dir", return_value=_FakeCache([])):
        ok, message = whisper_transcribe.delete_model("medium")
    assert ok is False
    assert "not downloaded" in message


def test_delete_model_unknown_size():
    ok, message = whisper_transcribe.delete_model("giant")
    assert ok is False
    assert "Unknown model size" in message


# ---------------------------------------------------------------------
# Idle unload of the in-process model
# ---------------------------------------------------------------------


@pytest.fixture(name="model_cache_state")
def fixture_model_cache_state():
    "Save and restore the module's model-cache globals around a test."
    saved = (
        dict(whisper_transcribe._MODEL_CACHE),
        whisper_transcribe._MODEL_LAST_USED,
        whisper_transcribe._MODEL_USES,
        whisper_transcribe._IDLE_REAPER_STARTED,
    )
    yield
    whisper_transcribe._MODEL_CACHE.clear()
    whisper_transcribe._MODEL_CACHE.update(saved[0])
    (
        whisper_transcribe._MODEL_LAST_USED,
        whisper_transcribe._MODEL_USES,
        whisper_transcribe._IDLE_REAPER_STARTED,
    ) = saved[1], saved[2], saved[3]


def _cache_a_model(size="small", idle_seconds=0):
    "Put a stand-in model in the cache, last used idle_seconds ago."
    whisper_transcribe._MODEL_CACHE.clear()
    whisper_transcribe._MODEL_CACHE[size] = object()
    whisper_transcribe._MODEL_USES = 0
    whisper_transcribe._MODEL_LAST_USED = time.monotonic() - idle_seconds


def test_unload_idle_model_drops_a_stale_model(model_cache_state):
    """
    Past the timeout the instance goes: one transcription must not pin
    ~500 MB in the process for the rest of its life.
    """
    timeout = whisper_transcribe.MODEL_IDLE_TIMEOUT_SECONDS
    _cache_a_model(idle_seconds=timeout + 1)
    assert whisper_transcribe.unload_idle_model() == "small"
    assert whisper_transcribe._MODEL_CACHE == {}


def test_unload_idle_model_keeps_a_recently_used_model(model_cache_state):
    "Between takes the model stays: a reload costs seconds on every clip."
    timeout = whisper_transcribe.MODEL_IDLE_TIMEOUT_SECONDS
    _cache_a_model(idle_seconds=timeout - 60)
    assert whisper_transcribe.unload_idle_model() is None
    assert "small" in whisper_transcribe._MODEL_CACHE


def test_unload_idle_model_never_drops_a_model_in_use(model_cache_state):
    """
    A take longer than the timeout keeps its model.  Evicting it frees
    nothing (the caller still holds it) and the next load would build a
    second ~500 MB instance beside it.
    """
    timeout = whisper_transcribe.MODEL_IDLE_TIMEOUT_SECONDS
    _cache_a_model(idle_seconds=timeout + 1)
    with whisper_transcribe.model_in_use():
        assert whisper_transcribe.unload_idle_model() is None
    assert "small" in whisper_transcribe._MODEL_CACHE


def test_model_in_use_releases_when_the_take_fails(model_cache_state):
    "A crash mid-transcription must not pin the cache forever."
    timeout = whisper_transcribe.MODEL_IDLE_TIMEOUT_SECONDS
    _cache_a_model(idle_seconds=timeout + 1)
    with pytest.raises(RuntimeError):
        with whisper_transcribe.model_in_use():
            raise RuntimeError("transcription blew up")
    assert whisper_transcribe._MODEL_USES == 0
    # The clock restarts as the take ends, so age it again: the point is
    # that the released cache is droppable, not permanently pinned.
    whisper_transcribe._MODEL_LAST_USED = time.monotonic() - (timeout + 1)
    assert whisper_transcribe.unload_idle_model() == "small"


def test_load_model_refreshes_the_idle_clock(model_cache_state):
    """
    A cache hit counts as use: reading for a while between takes must not
    make the next take pay a reload.
    """
    timeout = whisper_transcribe.MODEL_IDLE_TIMEOUT_SECONDS
    _cache_a_model(idle_seconds=timeout - 1)
    before = whisper_transcribe._MODEL_LAST_USED
    model = whisper_transcribe._load_model("small")
    assert model is whisper_transcribe._MODEL_CACHE["small"]
    assert whisper_transcribe._MODEL_LAST_USED > before


def test_unload_idle_model_with_an_empty_cache(model_cache_state):
    "Nothing cached, nothing to unload."
    whisper_transcribe._MODEL_CACHE.clear()
    whisper_transcribe._MODEL_USES = 0
    whisper_transcribe._MODEL_LAST_USED = 0.0
    assert whisper_transcribe.unload_idle_model() is None


def test_models_endpoint(app, client):
    with patch.object(
        whisper_transcribe, "whisper_status", return_value={"installed": True}
    ), patch.object(whisper_transcribe, "model_cache_info", return_value=[{"size": "base", "cached": False, "size_mb": 0}]):
        resp = client.get("/book/whisper/models")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["installed"] is True
    assert body["models"][0]["size"] == "base"


def test_delete_model_endpoint_busy(app, client):
    with patch.object(whisper_transcribe, "has_running_task", return_value=True):
        resp = client.post("/book/whisper/delete_model", data={"whisper_model": "small"})
    assert resp.status_code == 409


def test_delete_model_endpoint_ok(app, client):
    with patch.object(whisper_transcribe, "has_running_task", return_value=False), patch.object(
        whisper_transcribe, "delete_model", return_value=(True, "Deleted model 'small'.")
    ) as fake_del:
        resp = client.post("/book/whisper/delete_model", data={"whisper_model": "small"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    fake_del.assert_called_once_with("small")


def test_delete_model_endpoint_rejects_unknown(app, client):
    with patch.object(whisper_transcribe, "has_running_task", return_value=False):
        resp = client.post("/book/whisper/delete_model", data={"whisper_model": "giant"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------
# End-to-end task flow (fake transcription)
# ---------------------------------------------------------------------


def _wait_for_terminal(task_id, timeout=10.0):
    "Poll a task until it reaches a terminal state."
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = whisper_transcribe.task_status(task_id)
        if status["state"] in ("finished", "error"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} did not finish in time: {status}")


def test_prepare_transcribes_and_creates_book(app, app_context, client, english):
    "Happy path: audio upload -> transcription -> mp3 book with cues."
    tempdir = app.env_config.temppath

    def _fake_transcribe(audio_path, lang_code, model_size="small", progress_cb=None):
        assert os.path.exists(audio_path)
        assert audio_path.startswith(os.path.join(tempdir, "whisper_"))
        if progress_cb is not None:
            progress_cb(50)
        cues = [{"start": 1.0, "end": 4.2, "text": "Hello world."}]
        return "Hello world.", json.dumps(cues, ensure_ascii=False)

    with patch.object(
        whisper_transcribe, "whisper_status", return_value={"installed": True}
    ), patch.object(whisper_transcribe, "transcribe_to_cues", side_effect=_fake_transcribe):
        resp = client.post(
            "/book/whisper/prepare",
            data={
                "language_id": str(english.id),
                "mp3_file": (io.BytesIO(b"fake audio bytes"), "podcast.mp3"),
                "mp3_tag": '[{"value":"whisper-test"}]',
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        task_id = resp.get_json()["task_id"]

        status = _wait_for_terminal(task_id)
        assert status["state"] == "finished"
        assert status["book_id"] is not None

    repo = BookRepository(db.session)
    book = repo.find_by_title("podcast", english.id)
    assert book is not None
    assert book.book_type == "mp3"
    assert json.loads(book.srt_data) == [{"start": 1.0, "end": 4.2, "text": "Hello world."}]
    assert book.audio_filename is not None
    assert os.path.exists(os.path.join(app.env_config.useraudiopath, book.audio_filename))

    # The temp file was moved into the book's audio dir and cleaned up.
    leftovers = [f for f in os.listdir(tempdir) if f.startswith("whisper_")]
    assert leftovers == []

    # Tags were carried over.
    assert any(t.text == "whisper-test" for t in book.book_tags)

    # Terminal tasks are purgeable so a new import starts clean.
    whisper_transcribe.purge_finished_tasks()
    assert whisper_transcribe.task_status(task_id)["state"] == "unknown"


def test_prepare_error_state_cleans_temp(app, app_context, client, english):
    "A failing transcription reports the error and removes the temp file."

    def _boom(audio_path, lang_code, model_size="small", progress_cb=None):
        raise RuntimeError("model exploded")

    with patch.object(
        whisper_transcribe, "whisper_status", return_value={"installed": True}
    ), patch.object(whisper_transcribe, "transcribe_to_cues", side_effect=_boom):
        resp = client.post(
            "/book/whisper/prepare",
            data={
                "language_id": str(english.id),
                "mp3_file": (io.BytesIO(b"fake audio bytes"), "broken.mp3"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        task_id = resp.get_json()["task_id"]

        status = _wait_for_terminal(task_id)
        assert status["state"] == "error"
        assert "model exploded" in status["error"]

    tempdir = app.env_config.temppath
    leftovers = [f for f in os.listdir(tempdir) if f.startswith("whisper_")]
    assert leftovers == []

    repo = BookRepository(db.session)
    assert repo.find_by_title("broken", english.id) is None


def test_consecutive_transcriptions_allowed_after_finish(app, app_context, client, english):
    "A finished task no longer counts as running (concurrency = 1 while active)."

    def _fake_transcribe(audio_path, lang_code, model_size="small", progress_cb=None):
        return "Hello.", json.dumps([{"start": 0.0, "end": 1.0, "text": "Hello."}])

    with patch.object(
        whisper_transcribe, "whisper_status", return_value={"installed": True}
    ), patch.object(whisper_transcribe, "transcribe_to_cues", side_effect=_fake_transcribe):
        resp = client.post(
            "/book/whisper/prepare",
            data={
                "language_id": str(english.id),
                "mp3_file": (io.BytesIO(b"audio one"), "first.mp3"),
            },
            content_type="multipart/form-data",
        )
        task_id = resp.get_json()["task_id"]
        status = _wait_for_terminal(task_id)
        assert status["state"] == "finished"

        # has_running_task is False once the task finished.
        assert whisper_transcribe.has_running_task() is False

        # And a second prepare is accepted.
        resp2 = client.post(
            "/book/whisper/prepare",
            data={
                "language_id": str(english.id),
                "mp3_file": (io.BytesIO(b"audio two"), "second.mp3"),
            },
            content_type="multipart/form-data",
        )
        assert resp2.status_code == 200
        task_id2 = resp2.get_json()["task_id"]
        status2 = _wait_for_terminal(task_id2)
        assert status2["state"] == "finished"
