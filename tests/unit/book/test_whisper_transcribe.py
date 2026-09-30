"""
Tests for the whisper auto-subtitle import feature.

The real faster-whisper dependency is never imported here: the
transcription function is monkeypatched, and the tests cover the task
state machine, the /book/whisper/* routes, and the temp-file lifecycle.
"""

import io
import json
import os
import time
from unittest.mock import patch

from lute.db import db
from lute.book import whisper_transcribe
from lute.models.repositories import BookRepository


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
    "tts_lang is reduced to a bare ISO code; empty becomes None."

    class _Lang:
        tts_lang = "zh-CN"

    assert whisper_transcribe.whisper_lang_code(_Lang()) == "zh"

    class _Ja:
        tts_lang = "ja"

    assert whisper_transcribe.whisper_lang_code(_Ja()) == "ja"

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
