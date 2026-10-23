"""
The reading page's grammar panel can analyse the whole book, not just one
screen (``?scope=book``).

That is a different amount of text down the same pipeline: every page
joined, then the language's engine.  Two things are easy to get wrong and
both are silent --

* the page's own ``?text=`` snippet must keep winning for the page scope,
  otherwise a book-scope request would be analysed over just the visible
  screen and report the page's grammar under a "whole book" heading;
* a book-wide pass costs seconds, so it is memoised, and the memo has to be
  scoped per user and invalidated when the text changes.  A cache keyed on
  the book id alone would serve one user's analysis to another (book ids
  are per-user databases) and keep serving it after an edit.

The engine itself is monkeypatched: what is under test is the text the
route assembles and the cache around it, not Russian or Japanese grammar.
"""

import pytest

from lute.db import db
from lute.models.book import Book, Text
from lute.models.language import Language
from lute.read import grammar_analysis_service as grammar_service
from lute.read import routes as read_routes


@pytest.fixture(name="russian_book")
def fixture_russian_book(app_context):
    "A three-page Russian book."
    language = Language()
    language.name = "Russian"
    language.lang_code = "rus"
    language.parser_type = "spacedel"
    db.session.add(language)
    db.session.commit()
    book = Book("Scope book", language)
    db.session.add(book)
    db.session.commit()
    # The pages are attached through Text's own constructor; committing the
    # book first keeps SQLAlchemy from warning that the Text objects arrived
    # after the parent was already flushed.
    for order, text in enumerate(
        ("Первая страница.", "Вторая страница.", "Третья страница."), start=1
    ):
        page = Text(book, text, order)
        db.session.add(page)
        book.texts.append(page)
    db.session.commit()
    return book


@pytest.fixture(name="engine_calls")
def fixture_engine_calls(monkeypatch):
    """
    Replace the language engine with a recorder.

    The route no longer dispatches the engines itself: ``analyze_text()``
    picks one from the service module's ``_ENGINES`` table, so the table is
    what has to be replaced -- patching a detector on ``read_routes`` would
    leave the real Russian engine in the tuple and call nothing.

    Precompute is switched off for the same reason the engine is faked:
    these tests are about the text the route assembles and the cache around
    it, and a real background worker would analyse the book from another
    thread while the test counts calls.
    """
    calls = []

    def fake_engine(text, display_lang="en"):
        calls.append(text)
        return [{"key": "k", "name": "n", "level": "A1", "desc": "d",
                 "examples": []}]

    monkeypatch.setattr(
        grammar_service,
        "_ENGINES",
        ((lambda _lang: True, fake_engine, "russian"),),
    )
    monkeypatch.setattr(read_routes, "filter_hidden_grammar", lambda r, _h: r)
    monkeypatch.setenv("LUTE_GRAMMAR_PRECOMPUTE", "0")
    return calls


def test_book_scope_analyses_every_page(client, russian_book, engine_calls):
    "A whole-book request hands the engine every page, joined by newlines."
    read_routes._book_grammar_cache.clear()
    resp = client.get(f"/read/grammar_analysis/{russian_book.id}/1?scope=book")
    assert resp.status_code == 200, resp.data
    assert len(engine_calls) == 1
    text = engine_calls[0]
    for page in ("Первая страница.", "Вторая страница.", "Третья страница."):
        assert page in text, text


def test_page_scope_analyses_only_the_whole_current_page(
    client, russian_book, engine_calls
):
    "Without ?scope=book the route keeps its single-page behaviour."
    read_routes._book_grammar_cache.clear()
    resp = client.get(f"/read/grammar_analysis/{russian_book.id}/2")
    assert resp.status_code == 200, resp.data
    assert len(engine_calls) == 1
    assert "Вторая страница." in engine_calls[0]
    assert "Первая страница." not in engine_calls[0]


def test_book_scope_is_memoised(client, russian_book, engine_calls):
    "A second whole-book request is answered from the cache, not re-analysed."
    read_routes._book_grammar_cache.clear()
    first = client.get(f"/read/grammar_analysis/{russian_book.id}/1?scope=book")
    second = client.get(f"/read/grammar_analysis/{russian_book.id}/2?scope=book")
    assert first.status_code == second.status_code == 200
    assert len(engine_calls) == 1, "the book was analysed twice"
    assert first.get_json() == second.get_json()


def test_editing_a_page_invalidates_the_memo(client, russian_book, engine_calls):
    """
    The cache is keyed on the text, so a changed page misses: serving the
    old analysis after an edit would silently show grammar the book no
    longer contains.
    """
    read_routes._book_grammar_cache.clear()
    client.get(f"/read/grammar_analysis/{russian_book.id}/1?scope=book")
    assert len(engine_calls) == 1

    russian_book.texts[0].text = "Совсем другая страница."
    db.session.commit()

    client.get(f"/read/grammar_analysis/{russian_book.id}/1?scope=book")
    assert len(engine_calls) == 2, "the edit did not invalidate the memo"
    assert "Совсем другая страница." in engine_calls[1]


def test_book_cache_is_bounded():
    "The memo cannot grow without limit."
    read_routes._book_grammar_cache.clear()
    for i in range(read_routes._BOOK_GRAMMAR_CACHE_MAX + 5):
        read_routes._remember_book_grammar(("scope", i), [])
    assert len(read_routes._book_grammar_cache) == read_routes._BOOK_GRAMMAR_CACHE_MAX
