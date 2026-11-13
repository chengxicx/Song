"""
Background new-book import tests.

The new-book form used to run BookService.import_book inside its POST;
it now starts an in-process task (lute/book/import_task.py), redirects
to a progress page, and that page polls the status endpoint.
"""

import io
import os
import threading
import time
from unittest.mock import patch

import pytest
from werkzeug.datastructures import FileStorage

from lute.book import import_task
from lute.book.model import Book
from lute.db import db
from lute.models.book import Book as DBBook
from tests.utils import make_pdf_bytes


@pytest.fixture(autouse=True)
def _reset_import_tasks():
    "Task ids are unique per process; do not let them cross tests."
    import_task.purge_finished_tasks()
    yield
    import_task.purge_finished_tasks()


def _wait_for_terminal(task_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = import_task.task_status(task_id)
        if status["state"] in ("finished", "error"):
            return status
        time.sleep(0.02)
    raise AssertionError(f"task did not finish: {import_task.task_status(task_id)}")


def _book(language_id, title="Background book", text="One sentence."):
    b = Book()
    b.title = title
    b.language_id = language_id
    b.text = text
    b.split_by = "paragraphs"
    b.threshold_page_tokens = 250
    return b


def _post_new(client, english, **overrides):
    data = {
        "language_id": str(english.id),
        "title": "Background book",
        "text": "One sentence.\n\nTwo sentence.",
        "split_by": "paragraphs",
        "threshold_page_tokens": "250",
        "book_tags": "",
        "source_uri": "",
    }
    data.update(overrides)
    return client.post("/book/new", data=data, content_type="multipart/form-data")


def _task_id_from(resp):
    location = resp.headers["Location"]
    assert "/book/import/progress/" in location
    return location.rsplit("/", 1)[1]


def test_post_starts_a_background_import_and_redirects_to_progress(
    app, client, english
):
    "The POST answers immediately; the worker creates the book."
    resp = _post_new(client, english)
    assert resp.status_code == 302
    task_id = _task_id_from(resp)

    status = _wait_for_terminal(task_id)
    assert status["state"] == "finished"
    db.session.commit()  # the worker wrote on another connection
    dbbook = db.session.query(DBBook).filter(DBBook.title == "Background book").one()
    assert status["book_id"] == dbbook.id
    assert [t.text for t in dbbook.texts] == ["One sentence.\n\nTwo sentence."]


def test_text_file_upload_survives_the_request_for_the_worker(
    app, client, english, monkeypatch
):
    "The upload stream is copied to a temp file that the task then removes."
    saved = []
    real_save = import_task._save_upload  # pylint: disable=protected-access

    def _tracking_save(temppath, upload):
        path = real_save(temppath, upload)  # pylint: disable=protected-access
        saved.append(path)
        return path

    monkeypatch.setattr(import_task, "_save_upload", _tracking_save)

    resp = _post_new(
        client,
        english,
        title="Uploaded book",
        text="",
        textfile=(io.BytesIO(b"First page.\n\nSecond page."), "notes.txt"),
    )
    task_id = _task_id_from(resp)
    status = _wait_for_terminal(task_id)
    assert status["state"] == "finished"
    db.session.commit()
    dbbook = db.session.query(DBBook).filter(DBBook.title == "Uploaded book").one()
    assert [t.text for t in dbbook.texts] == ["First page.\n\nSecond page."]
    assert saved, "the upload was staged"
    assert not any(os.path.exists(p) for p in saved), "temp files removed"


def test_pdf_file_import_runs_without_a_request_context(app, app_context, english):
    "The PDF note's flash() must not break the worker thread."
    upload = FileStorage(
        stream=io.BytesIO(make_pdf_bytes(["Hello world."])),
        filename="notes.pdf",
    )
    task_id, duplicate = import_task.start_import(
        app, _book(english.id, title="PDF upload"), None, text_upload=upload
    )
    assert duplicate is False
    status = _wait_for_terminal(task_id)
    assert status["state"] == "finished"
    db.session.commit()
    dbbook = db.session.query(DBBook).filter(DBBook.title == "PDF upload").one()
    assert dbbook.texts[0].text.strip() == "Hello world."


def test_same_submission_while_running_reuses_the_task(app, app_context, english):
    "A double-clicked form follows the first task instead of importing twice."
    started = threading.Event()
    release = threading.Event()

    def _blocked_run(_app, task_id, _book, _username):
        started.set()
        release.wait(timeout=10)
        import_task._set_state(  # pylint: disable=protected-access
            task_id, "finished", percent=100, book_id=1
        )

    with patch.object(import_task, "_run_import", side_effect=_blocked_run):
        first_id, first_dup = import_task.start_import(
            app, _book(english.id, title="Double click"), None
        )
        assert started.wait(timeout=10), "the first task started"
        second_id, second_dup = import_task.start_import(
            app, _book(english.id, title="Double click"), None
        )

    assert first_dup is False
    assert second_dup is True
    assert second_id == first_id

    release.set()
    assert _wait_for_terminal(first_id)["state"] == "finished"


def test_failed_import_can_be_retried_with_the_same_form(app, app_context, english):
    "An error task must not swallow the retry of an identical submission."
    first_id, _ = import_task.start_import(app, _book(99999, title="Broken"), None)
    first = _wait_for_terminal(first_id)
    assert first["state"] == "error"
    assert "No language" in first["error"]

    second_id, duplicate = import_task.start_import(
        app, _book(99999, title="Broken"), None
    )
    assert duplicate is False
    assert second_id != first_id
    assert _wait_for_terminal(second_id)["state"] == "error"


def test_failed_import_creates_no_book_and_drops_staged_uploads(
    app, app_context, english, monkeypatch
):
    "A failed import rolls back the session and cleans its temp files."
    saved = []
    real_save = import_task._save_upload  # pylint: disable=protected-access

    def _tracking_save(temppath, upload):
        path = real_save(temppath, upload)  # pylint: disable=protected-access
        saved.append(path)
        return path

    monkeypatch.setattr(import_task, "_save_upload", _tracking_save)
    upload = FileStorage(stream=io.BytesIO(b"unused text"), filename="broken.txt")
    task_id, _ = import_task.start_import(
        app, _book(99999, title="Broken upload"), None, text_upload=upload
    )
    status = _wait_for_terminal(task_id)
    assert status["state"] == "error"
    db.session.commit()
    assert db.session.query(DBBook).filter(DBBook.title == "Broken upload").all() == []
    assert saved and not any(os.path.exists(p) for p in saved)


def test_failed_import_removes_the_audio_copy(app, app_context, english, monkeypatch):
    """
    import_book copies the audio before its commit; a later failure must
    not leave that orphaned file in the user audio dir.
    """
    audio_name = "orphan.mp3"

    def _copy_audio_then_fail(self, book, session):  # pylint: disable=unused-argument
        book.audio_filename = audio_name
        audio_path = os.path.join(app.env_config.useraudiopath, audio_name)
        open(audio_path, "wb").close()  # pylint: disable=consider-using-with
        raise RuntimeError("boom after the copy")

    monkeypatch.setattr(import_task.BookService, "import_book", _copy_audio_then_fail)
    task_id, _ = import_task.start_import(app, _book(english.id, title="Audio"), None)
    status = _wait_for_terminal(task_id)
    assert status["state"] == "error"
    assert not os.path.exists(os.path.join(app.env_config.useraudiopath, audio_name))


def test_wiping_the_test_db_clears_finished_import_tasks(app, client, english):
    """
    A db wipe must also drop the in-process import queue.

    Acceptance tests wipe the db before every scenario, and many scenarios
    submit the same book.  A finished task stays reusable for 30 s to
    protect against double clicks, so a stale entry would hand the next
    scenario the previous task's book id -- a book the wipe deleted.  The
    progress page then bounces off /read/<missing> to the home page and
    the test times out waiting for the reader URL.
    """

    def _finished(_app, task_id, _book, _username):
        import_task._set_state(  # pylint: disable=protected-access
            task_id, "finished", percent=100, book_id=1
        )

    with patch.object(import_task, "_run_import", side_effect=_finished):
        first_id, first_dup = import_task.start_import(
            app, _book(english.id, title="Wiped book"), None
        )
        assert first_dup is False
        assert _wait_for_terminal(first_id)["state"] == "finished"

        reused_id, reused_dup = import_task.start_import(
            app, _book(english.id, title="Wiped book"), None
        )
        assert reused_dup is True and reused_id == first_id

        resp = client.get("/dev_api/wipe_db")
        assert resp.status_code == 302
        assert import_task.task_status(first_id) == {"state": "unknown"}

        after_wipe_id, after_wipe_dup = import_task.start_import(
            app, _book(english.id, title="Wiped book"), None
        )
        assert after_wipe_dup is False and after_wipe_id != first_id
        assert _wait_for_terminal(after_wipe_id)["state"] == "finished"


def test_failed_upload_names_the_original_file_not_the_staged_copy(
    app, app_context, english
):
    """
    Error text must name the file the user picked.

    The worker imports from a temp copy called bookimport_<uuid>.txt; the
    extractor builds its messages (and picks its parser) from the name it
    is given, so passing the path produced "bookimport_9f3c... is not
    utf-8 encoding" for a file the user had called non_utf_8.txt.
    """
    upload = FileStorage(stream=io.BytesIO(b"\xff\xfe\xfd"), filename="non_utf_8.txt")
    task_id, _ = import_task.start_import(
        app, _book(english.id, title="Bad encoding", text=""), None, text_upload=upload
    )
    status = _wait_for_terminal(task_id)
    assert status["state"] == "error"
    assert "non_utf_8.txt is not utf-8 encoding" in status["error"]
    assert "bookimport_" not in status["error"]


def test_status_endpoint_answers_unknown_for_an_unknown_task(client):
    resp = client.get("/book/import/status/does-not-exist")
    assert resp.get_json() == {"state": "unknown"}


def test_progress_page_renders_the_task_id(app, client):
    resp = client.get("/book/import/progress/abc123")
    assert resp.status_code == 200
    assert b'var taskId = "abc123"' in resp.data
