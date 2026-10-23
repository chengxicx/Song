"""
Whole-book grammar analyses, computed in the background.

The reader's "This book" scope used to run every grammar engine over the
whole text on each request -- ~27s for a 1900-sentence book, which overruns
the proxy timeout and leaves the panel stuck on "Analyzing...".  Books are
now queued when they are imported (and whenever the panel asks for one that
is not ready yet), and a single daemon worker stores the engine's output in
``bookgrammar`` so the read path is a row lookup.

Why a single worker thread rather than one thread per book: importing an
EPUB queues one book per chapter -- fifty chapters would mean fifty threads
each burning ~30s of CPU on a server that only has twelve request threads.
Serialising the work keeps the reader responsive and gives the queue a
natural "queued" state to report.

The task state lives in the database, not in a process-local dict like the
whisper/shadowing tasks, because a daemon thread does not survive a restart:
``lute/app_factory.py`` re-queues anything left pending or running.

The cached result is the engine's RAW output; the language's
hidden_grammar_levels filter is applied by the read path.  Hiding a level is
a display choice and must never cost a 30-second recompute.
"""

import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, timedelta

from flask import current_app

from lute.db import db
from lute.models.book import (
    GRAMMAR_STATUS_ERROR,
    GRAMMAR_STATUS_PENDING,
    GRAMMAR_STATUS_READY,
    GRAMMAR_STATUS_RUNNING,
    GRAMMAR_STATUS_SKIPPED,
    Book,
    BookGrammar,
)
from lute.multiuser import context as mu_context
from lute.read.grammar_analysis_service import (
    analyze_text,
    book_grammar_text,
    display_lang_for,
    rules_fingerprint,
    text_hash,
)

logger = logging.getLogger(__name__)

# A failed analysis is reported to the panel for this long before a reader
# clicking "This book" again is treated as a retry.  Without the window a
# panel that polls would re-queue a permanently failing book forever.
_ERROR_RETRY_SECONDS = 300

# Books larger than this are analysed on demand instead of precomputed:
# they would hold the single worker for minutes and starve every other book.
DEFAULT_MAX_CHARS = 400_000

_cond = threading.Condition()
_urgent = deque()  # a reader is waiting for this one
_normal = deque()  # precompute warming, nobody is waiting
_pending = set()  # (username, book_id) already queued
_worker_started = False
_worker_app = None


def _enabled():
    """Precompute is on unless explicitly switched off (rollback hatch)."""
    value = os.environ.get("LUTE_GRAMMAR_PRECOMPUTE", "1")
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def _max_chars():
    raw = os.environ.get("LUTE_GRAMMAR_PRECOMPUTE_MAX_CHARS", "")
    try:
        return int(raw) if raw else DEFAULT_MAX_CHARS
    except ValueError:
        return DEFAULT_MAX_CHARS


def _row(book_id):
    return db.session.query(BookGrammar).filter(BookGrammar.BkID == book_id).first()


def _set_status(book_id, status, error=None):
    row = _row(book_id)
    if row is None:
        row = BookGrammar(BkID=book_id, status=status)
        db.session.add(row)
    row.status = status
    row.error = error
    row.updated = datetime.now()
    db.session.commit()


def _ensure_row(book_id):
    if _row(book_id) is None:
        db.session.add(BookGrammar(BkID=book_id, status=GRAMMAR_STATUS_PENDING))
        db.session.commit()


def _ensure_worker(app):
    "Start the single worker thread on first use."
    global _worker_started, _worker_app
    if _worker_started:
        return
    _worker_app = app
    thread = threading.Thread(
        target=_worker_loop,
        name="lute-grammar-precompute",
        daemon=True,
    )
    thread.start()
    _worker_started = True


def _enqueue(username, book_id, urgent):
    key = (username, book_id)
    with _cond:
        if key in _pending:
            # Already waiting: promote it rather than adding a duplicate --
            # a reader is now blocked on a book that was only warming.
            if urgent and key in _normal:
                _normal.remove(key)
                _urgent.append(key)
            return
        _pending.add(key)
        (_urgent if urgent else _normal).append(key)
        _ensure_worker(current_app._get_current_object())
        _cond.notify()


def _next_task():
    with _cond:
        while not _urgent and not _normal:
            _cond.wait()
        queue = _urgent if _urgent else _normal
        return queue.popleft()


def _worker_loop():
    while True:
        key = _next_task()
        username, book_id = key
        try:
            # user_scope is a no-op in single-user mode; in multi-user mode
            # the ContextVar does not cross threads, so the scope must be
            # re-set here or the db creator raises.
            with _worker_app.app_context(), mu_context.user_scope(username):
                _run_one(book_id)
        except Exception:  # noqa: BLE001 - a task must never kill the worker
            logger.exception("grammar precompute failed for book %s", book_id)
        finally:
            with _cond:
                _pending.discard(key)


def _claim(book_id):
    "Mark the row running; True only for the process that won the race."
    claimed = (
        db.session.query(BookGrammar)
        .filter(
            BookGrammar.BkID == book_id,
            BookGrammar.status == GRAMMAR_STATUS_PENDING,
        )
        .update(
            {
                BookGrammar.status: GRAMMAR_STATUS_RUNNING,
                BookGrammar.updated: datetime.now(),
            },
            synchronize_session=False,
        )
    )
    db.session.commit()
    return claimed == 1


def _run_one(book_id):
    if not _claim(book_id):
        return
    book = db.session.query(Book).filter(Book.id == book_id).first()
    if book is None:
        _set_status(book_id, GRAMMAR_STATUS_SKIPPED, "book deleted")
        return
    lang = book.language
    work_text = book_grammar_text(book)
    digest = text_hash(work_text)
    display = display_lang_for(lang)
    rules = rules_fingerprint()
    # Reading is done: release the connection before the CPU-bound pass.
    # sqlite is in rollback-journal mode, so a long-lived reader blocks
    # other writers for as long as the analysis runs.
    db.session.commit()
    db.session.remove()
    try:
        results = analyze_text(work_text, lang)
        payload = json.dumps(results, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001 - report, do not crash the worker
        logger.exception("grammar precompute: analysis failed for book %s", book_id)
        db.session.remove()
        _set_status(book_id, GRAMMAR_STATUS_ERROR, str(e)[:500])
        return
    row = _row(book_id)
    if row is None:
        # The book was deleted while the engine was running, and its row
        # went with it (the cascade).  Nothing to store.
        return
    row.status = GRAMMAR_STATUS_READY
    row.text_hash = digest
    row.display = display
    row.rules = rules
    row.results = payload
    row.error = None
    row.updated = datetime.now()
    db.session.commit()


def schedule(username, book_id, urgent=False):
    """
    Queue a book for background analysis.

    Returns the state the caller should report to the reader:

    ``queued``    newly queued (or already waiting)
    ``running``   the worker is analysing this book right now
    ``error``     the last attempt failed, recently; do not retry yet
    ``skipped``   not worth precomputing (too long, or the book is gone)
    ``disabled``  precompute switched off -- the caller analyses on demand

    Safe to call repeatedly (the panel polls the same URL): the same book is
    queued at most once, and a running book is never queued again.
    """
    if not _enabled():
        return "disabled"
    row = _row(book_id)
    if row is not None:
        if row.status == GRAMMAR_STATUS_RUNNING:
            return "running"
        if row.status == GRAMMAR_STATUS_SKIPPED:
            return "skipped"
        if row.status == GRAMMAR_STATUS_ERROR:
            fresh = row.updated and row.updated > datetime.now() - timedelta(
                seconds=_ERROR_RETRY_SECONDS
            )
            if fresh:
                return "error"
        if row.status in (GRAMMAR_STATUS_ERROR, GRAMMAR_STATUS_READY):
            # The caller only queues a book whose stored answer it just
            # rejected -- the text or the settings changed -- so a READY row
            # is out of date and has to go back through the queue.  Leaving
            # it ready would make the worker's "pending" claim skip the book
            # forever while the panel polls.
            row.status = GRAMMAR_STATUS_PENDING
            row.updated = datetime.now()
            db.session.commit()
    book = db.session.query(Book).filter(Book.id == book_id).first()
    if book is None:
        return "skipped"
    if len(book_grammar_text(book)) > _max_chars():
        _set_status(book_id, GRAMMAR_STATUS_SKIPPED, "text too long to precompute")
        return "skipped"
    _ensure_row(book_id)
    _enqueue(username, book_id, urgent)
    return "queued"


def cached_results(book_id, digest, display):
    "The stored analysis, or None when it is missing/stale for this text."
    row = _row(book_id)
    if row is None or row.status != GRAMMAR_STATUS_READY:
        return None
    if row.text_hash != digest or row.display != display:
        return None
    if row.rules != rules_fingerprint():
        return None
    if not row.results:
        return None
    try:
        return json.loads(row.results)
    except (TypeError, ValueError):
        return None


def store_now(book_id, digest, display, results):
    """
    Store an analysis the request just paid for (the disabled/skipped path),
    so the next reader gets the fast answer.
    """
    try:
        _ensure_row(book_id)
        row = _row(book_id)
        row.status = GRAMMAR_STATUS_READY
        row.text_hash = digest
        row.display = display
        row.rules = rules_fingerprint()
        row.results = json.dumps(results, ensure_ascii=False)
        row.error = None
        row.updated = datetime.now()
        db.session.commit()
    except Exception:  # noqa: BLE001 - a cache write must never break a read
        db.session.rollback()
        logger.warning("could not store grammar analysis for book %s", book_id)


def invalidate(book_id):
    "Drop a book's cached analysis (e.g. its content was replaced)."
    try:
        db.session.query(BookGrammar).filter(BookGrammar.BkID == book_id).delete(
            synchronize_session=False
        )
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.warning("could not invalidate grammar analysis for book %s", book_id)


def requeue_interrupted():
    """
    Re-queue whatever a previous process left pending or running.

    Called once at startup per user db: ``running`` rows have no worker
    behind them any more, and ``pending`` rows lost their queue entry.
    Returns the number of books queued.
    """
    if not _enabled():
        return 0
    rows = (
        db.session.query(BookGrammar)
        .filter(
            BookGrammar.status.in_([GRAMMAR_STATUS_PENDING, GRAMMAR_STATUS_RUNNING])
        )
        .all()
    )
    for row in rows:
        row.status = GRAMMAR_STATUS_PENDING
        row.updated = datetime.now()
    if rows:
        db.session.commit()
    for row in rows:
        _enqueue(mu_context.get_current_user(), row.BkID, urgent=False)
    return len(rows)
