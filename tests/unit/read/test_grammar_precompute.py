"""
Whole-book grammar analyses are precomputed in the background and stored in
the ``bookgrammar`` table.

Two properties are silent if they break, and both are pinned here:

* a ready row must answer without re-running the engine -- the whole point
  of the table is that the reader stops paying for the analysis;
* the stored rows are deliberately *unfiltered*, so hiding a grammar level
  changes the response without recomputing the book.  Filtering before
  storage would make a display toggle cost half a minute.

The worker thread is stubbed out: these tests are about the queue's
bookkeeping and the cache contract, not about a background thread racing the
test database.
"""

import json

import pytest
from sqlalchemy import inspect
from sqlalchemy import text as sql_text

from lute.db import db
from lute.models.book import (
    GRAMMAR_STATUS_READY,
    GRAMMAR_STATUS_RUNNING,
    GRAMMAR_STATUS_SKIPPED,
    Book,
    BookGrammar,
    Text,
)
from lute.models.language import Language
from lute.read import grammar_analysis_service as grammar_service
from lute.read import grammar_precompute as precompute
from lute.read import routes as read_routes


@pytest.fixture(name="book")
def fixture_book(app_context):
    "A two-page Russian book."
    language = Language()
    language.name = "Russian"
    language.lang_code = "rus"
    language.parser_type = "spacedel"
    db.session.add(language)
    db.session.commit()
    book = Book("Precompute book", language)
    db.session.add(book)
    db.session.commit()
    for order, body in enumerate(("Первая страница.", "Вторая страница."), start=1):
        page = Text(book, body, order)
        db.session.add(page)
        book.texts.append(page)
    db.session.commit()
    return book


@pytest.fixture(name="queue_only", autouse=True)
def fixture_queue_only(monkeypatch):
    "Queue books without starting the worker thread, and start clean."
    # The suite defaults to precompute off (see tests/conftest.py); these
    # tests are the ones that exercise the queue.
    monkeypatch.setenv("LUTE_GRAMMAR_PRECOMPUTE", "1")
    monkeypatch.setattr(precompute, "_ensure_worker", lambda app: None)
    with precompute._cond:
        precompute._urgent.clear()
        precompute._normal.clear()
        precompute._pending.clear()
    yield
    with precompute._cond:
        precompute._urgent.clear()
        precompute._normal.clear()
        precompute._pending.clear()


def _row(book_id):
    return db.session.query(BookGrammar).filter(BookGrammar.BkID == book_id).first()


def test_the_cache_table_and_migration_exist(app_context, empty_db):
    "A new table also needs a migration, or a fresh db is built by create_all."
    assert "bookgrammar" in inspect(db.engine).get_table_names()
    names = [
        r[0]
        for r in db.session.execute(sql_text("select filename from _migrations")).all()
    ]
    assert "20261024_create_bookgrammar.sql" in names


def test_a_stored_analysis_is_served_for_the_same_text(app_context, book):
    precompute.store_now(book.id, "hash-a", "en", [{"key": "k"}])
    assert precompute.cached_results(book.id, "hash-a", "en") == [{"key": "k"}]
    # A different text, or a different description language, is a miss:
    # both change the answer.
    assert precompute.cached_results(book.id, "hash-b", "en") is None
    assert precompute.cached_results(book.id, "hash-a", "zh") is None


def test_a_rules_change_invalidates_every_row(app_context, book, monkeypatch):
    "Deploying a new rule library makes the stored rows describe a different engine."
    precompute.store_now(book.id, "hash-a", "en", [{"key": "k"}])
    assert precompute.cached_results(book.id, "hash-a", "en") is not None
    monkeypatch.setattr(grammar_service, "_FINGERPRINT", "a-different-rule-library")
    assert precompute.cached_results(book.id, "hash-a", "en") is None


def test_deleting_a_book_drops_its_analysis(app_context, book):
    precompute.store_now(book.id, "hash-a", "en", [])
    assert _row(book.id) is not None
    db.session.delete(book)
    db.session.commit()
    assert _row(book.id) is None


def test_scheduling_is_idempotent_and_a_click_jumps_the_queue(app_context, book):
    "Import warms the queue; a reader waiting on the book is promoted, not doubled."
    assert precompute.schedule(None, book.id) == "queued"
    assert precompute.schedule(None, book.id, urgent=True) == "queued"
    assert list(precompute._urgent) == [(None, book.id)]
    assert len(precompute._normal) == 0


def test_a_running_book_is_not_queued_again(app_context, book):
    "The panel polls the same URL, so schedule() runs repeatedly while it works."
    precompute.store_now(book.id, "hash-a", "en", [])
    _row(book.id).status = GRAMMAR_STATUS_RUNNING
    db.session.commit()
    assert precompute.schedule(None, book.id, urgent=True) == "running"
    assert len(precompute._urgent) == 0


def test_an_out_of_date_ready_row_is_handed_back_to_the_worker(app_context, book):
    """
    The route queues a book only when the stored answer does not match the
    current text or settings, so a ready row reaching schedule() is stale.
    Leaving it ready would make the worker's "pending" claim skip the book
    forever while the panel polls.
    """
    precompute.store_now(book.id, "hash-a", "en", [])
    assert _row(book.id).status == GRAMMAR_STATUS_READY
    assert precompute.schedule(None, book.id, urgent=True) == "queued"
    assert _row(book.id).status == "pending"
    assert list(precompute._urgent) == [(None, book.id)]


def test_the_worker_analyses_a_pending_book(app_context, book, monkeypatch):
    """
    The worker body, run synchronously: a pending row is claimed, analysed
    with the shared text builder, and stored with the identity the read path
    compares against.
    """
    seen = []
    monkeypatch.setattr(
        precompute,
        "analyze_text",
        lambda page_text, lang: seen.append(page_text) or [{"key": "w"}],
    )
    book_id = book.id
    precompute.store_now(book_id, "old-hash", "en", [])
    row = _row(book_id)
    row.status = "pending"
    db.session.commit()
    # Taken before the worker runs: it releases the session before the
    # CPU-bound pass, which detaches (and expires) the fixture's book.
    expected_hash = grammar_service.text_hash(
        grammar_service.book_grammar_text(book)
    )

    precompute._run_one(book_id)

    row = _row(book_id)
    assert row.status == GRAMMAR_STATUS_READY
    assert json.loads(row.results) == [{"key": "w"}]
    assert row.text_hash == expected_hash
    assert len(seen) == 1
    assert "Первая страница." in seen[0] and "Вторая страница." in seen[0]


def test_the_worker_leaves_a_book_it_cannot_claim_alone(app_context, book, monkeypatch):
    "Two processes can race for a book; only the one that flips it runs the engine."
    seen = []
    monkeypatch.setattr(
        precompute, "analyze_text", lambda page_text, lang: seen.append(page_text)
    )
    precompute.store_now(book.id, "old-hash", "en", [])

    precompute._run_one(book.id)

    assert seen == []
    assert _row(book.id).status == GRAMMAR_STATUS_READY


def test_a_recent_failure_is_not_retried_by_polling(app_context, book):
    "Otherwise a permanently failing book would be re-queued on every poll."
    from datetime import datetime

    precompute.store_now(book.id, "hash-a", "en", [])
    row = _row(book.id)
    row.status = "error"
    row.error = "boom"
    row.updated = datetime.now()
    db.session.commit()
    assert precompute.schedule(None, book.id, urgent=True) == "error"
    assert len(precompute._urgent) == 0


def test_too_long_books_are_skipped(app_context, book, monkeypatch):
    "One enormous book must not hold the single worker for minutes."
    monkeypatch.setenv("LUTE_GRAMMAR_PRECOMPUTE_MAX_CHARS", "5")
    assert precompute.schedule(None, book.id) == "skipped"
    assert _row(book.id).status == GRAMMAR_STATUS_SKIPPED
    assert len(precompute._urgent) == 0


def test_precompute_can_be_switched_off(app_context, book, monkeypatch):
    "The rollback hatch: the route then analyses on demand, as it always did."
    monkeypatch.setenv("LUTE_GRAMMAR_PRECOMPUTE", "0")
    assert precompute.schedule(None, book.id) == "disabled"
    assert len(precompute._urgent) == 0


def test_hiding_a_level_does_not_recompute_the_book(client, book, monkeypatch):
    """
    The stored rows are the engine's raw output, so the hidden-level filter
    is a read-time concern: hiding a level changes the response without
    paying for another whole-book analysis.
    """
    calls = []

    def fake_engine(page_text, display_lang="en"):
        calls.append(page_text)
        return [
            {"key": "a", "name": "A", "level": "A1", "desc": "d", "examples": []},
            {"key": "b", "name": "B", "level": "B1", "desc": "d", "examples": []},
        ]

    monkeypatch.setattr(
        grammar_service, "_ENGINES", ((lambda _lang: True, fake_engine, "russian"),)
    )
    monkeypatch.setenv("LUTE_GRAMMAR_PRECOMPUTE", "0")
    read_routes._book_grammar_cache.clear()

    url = f"/read/grammar_analysis/{book.id}/1?scope=book"
    first = client.get(url)
    assert first.status_code == 200, first.data
    assert len(first.get_json()) == 2
    assert len(calls) == 1

    row = _row(book.id)
    assert row.status == GRAMMAR_STATUS_READY
    assert len(json.loads(row.results)) == 2, "the raw rows were pre-filtered"

    language = book.language
    language.hidden_grammar_levels = ["B1"]
    db.session.commit()
    # Force the database path: the in-process cache would answer anyway.
    read_routes._book_grammar_cache.clear()

    second = client.get(url)
    assert second.status_code == 200, second.data
    assert [e["key"] for e in second.get_json()] == ["a"]
    assert len(calls) == 1, "hiding a level recomputed the whole book"
