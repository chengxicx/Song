"""
Background new-book import tasks.

The new-book form used to run the whole ``BookService.import_book``
inside its POST: parsing a long EPUB/PDF into pages can take many
seconds, the browser waits on a blank-looking request, and an import
error only surfaces after the wait.  This module follows the in-process
task pattern already used by the whisper transcription queue
(lute/book/whisper_transcribe.py): the POST persists the uploads, a
daemon thread does the import, and the page polls a status endpoint
until the book id comes back.

States: queued -> importing -> finished | error.  The task table lives
in this process only, so a server restart makes in-flight tasks report
``unknown`` (the page tells the user to retry), exactly like whisper.
"""

import hashlib
import logging
import os
import threading
import time
import uuid

from flask import current_app

from lute.book.service import BookImportException
from lute.book.service import Service as BookService
from lute.db import db
from lute.multiuser import context as mu_context

logger = logging.getLogger(__name__)

# task_id -> status dict.  Gunicorn / dev-server workers each own their
# table; the page polls the worker that accepted it, which is fine for
# the short lifetime of an import.
_TASKS = {}
# submission_key -> task_id for a task still worth reusing (running, or
# finished moments ago).  This is what makes a double-clicked form
# follow the first task instead of importing the same book twice.
_ACTIVE_KEYS = {}
_TASKS_LOCK = threading.Lock()

_ACTIVE_STATES = ("queued", "importing")

# A just-finished import still counts as "the same submission" for this
# long: a double-click's second request can land after the first task
# finished, and it must not create a duplicate book.
_DEDUPE_FINISHED_SECONDS = 30

# Terminal tasks are dropped this long after they finish; a page still
# polling one then gets state=unknown, reported as a lost task.
_TASK_TTL_SECONDS = 10 * 60


def _set_state(task_id, state, percent=0, error=None, book_id=None, message=None):
    "Update one task's status; terminal states record when they happened."
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
        if state in ("finished", "error"):
            task["finished_at"] = time.time()
        _TASKS[task_id] = task


def _safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _file_size(path):
    try:
        return os.stat(path).st_size
    except OSError:
        return -1


def _submission_key(book, username, text_path, audio_path):
    """
    Fingerprint of a submitted new-book form.

    Deliberately excludes the temp paths (they differ on every submit)
    and uses the uploaded files' original names and sizes instead, so
    an unchanged form submitted twice hashes the same.
    """
    parts = [
        username or "",
        str(book.language_id or ""),
        book.title or "",
        book.text or "",
        book.text_stream_filename or "",
        str(_file_size(text_path)) if text_path else "",
        book.audio_stream_filename or "",
        str(_file_size(audio_path)) if audio_path else "",
    ]
    return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()


def _purge_locked(now, force=False):
    """
    Drop terminal tasks (and their dedupe keys).

    Their status page turns into ``unknown``, which the UI reports as a
    lost task, matching the whisper queue's behavior.
    """
    for task_id, task in list(_TASKS.items()):
        if task.get("state") not in ("finished", "error"):
            continue
        if not force and now - task.get("finished_at", now) <= _TASK_TTL_SECONDS:
            continue
        del _TASKS[task_id]
        key = task.get("key")
        if key and _ACTIVE_KEYS.get(key) == task_id:
            del _ACTIVE_KEYS[key]


def _reusable_task_id_locked(key):
    "An in-flight (or just-finished) task for this same submission, if any."
    task_id = _ACTIVE_KEYS.get(key)
    if task_id is None:
        return None
    task = _TASKS.get(task_id)
    if task is None:
        return None
    state = task.get("state")
    if state in _ACTIVE_STATES:
        return task_id
    if (
        state == "finished"
        and time.time() - task.get("finished_at", 0) <= _DEDUPE_FINISHED_SECONDS
    ):
        return task_id
    # A failed task must not swallow a retry of the same form.
    return None


def _save_upload(temppath, upload):
    """
    Copy a request-bound upload to a temp file, keeping its extension.

    The stream dies with the request, so the worker cannot read it; the
    extension is kept because that is how the text extractor picks its
    parser.
    """
    ext = os.path.splitext(getattr(upload, "filename", "") or "")[1].lower()
    path = os.path.join(temppath, f"bookimport_{uuid.uuid4().hex}{ext}")
    upload.save(path)
    return path


def start_import(app, book, username=None, text_upload=None, audio_upload=None):
    """
    Persist the uploads and run the import on a background thread.

    ``book`` is the business object the form populated; it must not
    carry live request streams (the uploads are copied to temp files
    here and their paths are set instead).  Returns ``(task_id,
    duplicate)``: duplicate is True when the same submission is already
    running (or finished moments ago), in which case the caller just
    follows that task's progress page.  The task owns the temp files and
    removes them when it ends.
    """
    temppath = app.env_config.temppath
    os.makedirs(temppath, exist_ok=True)
    saved = []
    try:
        if text_upload is not None and getattr(text_upload, "filename", ""):
            path = _save_upload(temppath, text_upload)
            saved.append(path)
            book.text_source_path = path
            book.text_stream = None
            # The staged copy has a generated name; keep the uploaded one so
            # the importer can report "book.txt is empty" rather than
            # "bookimport_<uuid>.txt is empty" (and pick the parser from the
            # user's extension).
            book.text_stream_filename = text_upload.filename
        if audio_upload is not None and getattr(audio_upload, "filename", ""):
            path = _save_upload(temppath, audio_upload)
            saved.append(path)
            book.audio_source_path = path
            book.audio_stream = None
            book.audio_stream_filename = audio_upload.filename
    except Exception:
        for path in saved:
            _safe_remove(path)
        raise

    key = _submission_key(book, username, book.text_source_path, book.audio_source_path)
    now = time.time()
    with _TASKS_LOCK:
        existing = _reusable_task_id_locked(key)
        if existing is not None:
            for path in saved:
                _safe_remove(path)
            return existing, True
        _purge_locked(now)
        task_id = uuid.uuid4().hex
        _TASKS[task_id] = {
            "state": "queued",
            "percent": 0,
            "message": "Queued.",
            "error": None,
            "book_id": None,
            "key": key,
            "finished_at": None,
        }
        _ACTIVE_KEYS[key] = task_id

    thread = threading.Thread(
        target=_run_import,
        args=(app, task_id, book, username),
        daemon=True,
    )
    thread.start()
    return task_id, False


def _discard_partial_import(book):
    """
    Remove a failed import's leftovers.

    The db work is transactional -- import_book commits last, so the
    caller's rollback drops whatever was pending.  The audio copy
    happens before that commit, though, so a failed import can leave one
    orphaned file in the user audio dir; the unique name guarantees it
    is ours to delete.
    """
    filename = getattr(book, "audio_filename", None)
    if not filename:
        return
    try:
        _safe_remove(os.path.join(current_app.env_config.useraudiopath, filename))
    except Exception:  # pylint: disable=broad-except
        logger.warning("could not remove partial import audio %s", filename)


def _run_import(app, task_id, book, username):
    """
    Thread body: import the book, then report the book id.

    Holds the app context for the whole run and re-enters the requesting
    user's scope, exactly like the whisper worker: the multiuser
    ContextVar does not cross threads, and without it both the db
    connection creator and the user-scoped env_config paths fail in
    multi-user mode.  The scoped session is removed while the context
    is still alive.  On failure the session is rolled back and any
    half-copied media dropped before the error state is published, so a
    poller that sees the terminal state also sees a clean disk and db.
    """
    text_path = book.text_source_path
    audio_path = book.audio_source_path
    try:
        with app.app_context(), mu_context.user_scope(username):
            try:
                _set_state(
                    task_id,
                    "importing",
                    percent=10,
                    message="Importing the book…",
                )
                dbbook = BookService().import_book(book, db.session)
                # Read the id while the book is still session-bound.
                book_id = dbbook.id
            except Exception:
                db.session.rollback()
                _discard_partial_import(book)
                raise
            finally:
                db.session.remove()
                _safe_remove(text_path)
                _safe_remove(audio_path)
            _set_state(task_id, "finished", percent=100, book_id=book_id)
    except BookImportException as e:
        _set_state(task_id, "error", error=e.message)
    except Exception as e:  # pylint: disable=broad-except
        logger.exception("book import task %s failed", task_id)
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


def purge_finished_tasks():
    "Drop every terminal task; used by tests to reset the task table."
    with _TASKS_LOCK:
        _purge_locked(time.time(), force=True)
