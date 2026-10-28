"""
Re-splitting reader-mode chapters imported before chapter splitting.

An old chapter is one file holding the whole thing, so it is one Lute
page however long it is.  The backfill turns it into pages using only
the extracted markup -- these tests pin what that must not cost: page
1's row (id, read date, progress pointer), the text of every page
against its own file, and the order of the chapter.

The "old" state is produced the honest way: the import is run with the
page budget patched out of the way, which is exactly the file an import
before the split existed would have written.
"""

import io
import json
import os
from datetime import datetime

from bs4 import BeautifulSoup

from lute.book import epub_extract, epub_paths
from lute.book.epub_parser import _extract_text
from lute.cli.epub_resplit import (
    SKIP_ALREADY_SPLIT,
    SKIP_FITS,
    SKIP_FIXED,
    format_report,
    run_resplit,
)
from lute.db import db
from lute.models.book import Book as DBBook
from tests.utils import make_epub

PARAGRAPH = ("This is a paragraph of a chapter that goes on for a while. " * 2).strip()
PARAGRAPHS = [f"{i:03d} {PARAGRAPH}" for i in range(40)]


def _chapter_html(paragraphs, extra=""):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        "<html><head><title>chapter</title></head>"
        f'<body class="novel"><div class="calibre"><div class="chapter">'
        f"{extra}{body}</div></div></body></html>"
    )


def _epub_bytes(paragraphs=None, extra=""):
    paragraphs = PARAGRAPHS if paragraphs is None else paragraphs
    return make_epub(
        {"OEBPS/ch1.xhtml": _chapter_html(paragraphs, extra)},
        [("ch1", "ch1.xhtml")],
        title="Long Chapter",
    )


def _import(app, client, english, tmp_path, paragraphs=None, extra="", budget=None):
    "Import the fixture chapter; `budget` overrides PAGE_CHAR_BUDGET for it."
    app.config["EPUB_STATIC_ROOT"] = str(tmp_path / "static")
    data = {
        "epub_file": (io.BytesIO(_epub_bytes(paragraphs, extra)), "long.epub"),
        "language_id": str(english.id),
        "epub_title": "Long Chapter",
        "chapters": json.dumps([0]),
        "epub_reader_mode": "1",
    }
    original = epub_extract.PAGE_CHAR_BUDGET
    if budget is not None:
        epub_extract.PAGE_CHAR_BUDGET = budget
    try:
        resp = client.post(
            "/book/import/epub", data=data, content_type="multipart/form-data"
        )
    finally:
        epub_extract.PAGE_CHAR_BUDGET = original
    assert resp.get_json()["success"] is True, resp.get_json()
    return (
        db.session.query(DBBook)
        .filter(DBBook.title.like("Long Chapter%"))
        .order_by(DBBook.id.desc())
        .first()
    )


def _import_old(app, client, english, tmp_path, paragraphs=None, extra=""):
    "A chapter as an import before the split existed would have written it."
    return _import(
        app,
        client,
        english,
        tmp_path,
        paragraphs=paragraphs,
        extra=extra,
        budget=10**9,
    )


def _read(app, path):
    with open(
        os.path.join(app.config["EPUB_STATIC_ROOT"], path), encoding="utf-8"
    ) as f:
        return f.read()


def _reload(book_id):
    db.session.expire_all()
    return db.session.query(DBBook).filter(DBBook.id == book_id).one()


def test_the_fixture_really_is_the_old_shape(
    app, app_context, english, client, tmp_path
):
    "One page holding the whole chapter -- the state the backfill exists for."
    book = _import_old(app, client, english, tmp_path)
    assert book.page_count == 1
    assert len(book.texts[0].text) > epub_extract.PAGE_CHAR_BUDGET


def test_a_dry_run_writes_nothing(app, app_context, english, client, tmp_path):
    book = _import_old(app, client, english, tmp_path)
    result = run_resplit(commit=False)

    assert result.scanned == 1
    assert [p.book_id for p in result.to_split()] == [book.id]
    assert result.committed is False
    assert _reload(book.id).page_count == 1
    assert not [
        f for f in os.listdir(os.path.dirname(_chapter_file(app, book))) if "-002" in f
    ]
    assert "Nothing was written" in format_report(result)


def _chapter_file(app, book):
    return epub_paths.located(app, book.epub_path)


def test_commit_splits_the_chapter_into_pages(
    app, app_context, english, client, tmp_path
):
    book = _import_old(app, client, english, tmp_path)
    result = run_resplit(commit=True)

    assert result.books_split == 1
    book = _reload(book.id)
    assert book.page_count > 1
    assert result.pages_added == book.page_count - 1
    for page in book.texts:
        path = epub_paths.chapter_file(app, book.epub_path, page.order)
        assert path is not None, f"page {page.order} has no file"
        rendered = _extract_text(BeautifulSoup(_read(app, path), "html.parser"))
        assert page.text == rendered


def test_the_split_keeps_the_chapter_in_order(
    app, app_context, english, client, tmp_path
):
    book = _import_old(app, client, english, tmp_path)
    run_resplit(commit=True)
    book = _reload(book.id)
    assert "\n".join(p.text for p in book.texts) == "\n".join(PARAGRAPHS)


def test_page_one_keeps_its_row_and_read_date(
    app, app_context, english, client, tmp_path
):
    """
    Page 1 is updated, never replaced: its id is what BkCurrentTxID
    points at, and its read date is the reader's progress.
    """
    book = _import_old(app, client, english, tmp_path)
    page = book.texts[0]
    page_id = page.id
    read_on = datetime(2026, 10, 1, 12, 0, 0)
    page.read_date = read_on
    db.session.commit()

    run_resplit(commit=True)

    book = _reload(book.id)
    first = book.texts[0]
    assert first.id == page_id
    assert first.read_date == read_on
    assert first.text != book.texts[1].text


def test_a_short_chapter_is_left_alone(app, app_context, english, client, tmp_path):
    book = _import_old(app, client, english, tmp_path, paragraphs=PARAGRAPHS[:3])
    result = run_resplit(commit=True)
    assert result.books_split == 0
    assert result.plans[0].reason == SKIP_FITS
    assert _reload(book.id).page_count == 1


def test_an_already_split_book_is_left_alone(
    app, app_context, english, client, tmp_path
):
    book = _import(app, client, english, tmp_path)  # split at import
    assert book.page_count > 1
    result = run_resplit(commit=True)
    assert result.books_split == 0
    assert result.plans[0].reason == SKIP_ALREADY_SPLIT
    assert _reload(book.id).page_count == book.page_count


def test_a_fixed_layout_chapter_is_left_alone(
    app, app_context, english, client, tmp_path
):
    viewport = '<meta name="viewport" content="width=600,height=800"/>'
    book = _import_old(app, client, english, tmp_path, extra=viewport)
    result = run_resplit(commit=True)
    assert result.books_split == 0
    assert result.plans[0].reason == SKIP_FIXED
    assert _reload(book.id).page_count == 1


def test_book_id_limits_the_run(app, app_context, english, client, tmp_path):
    first = _import_old(app, client, english, tmp_path)
    second = _import_old(app, client, english, tmp_path)
    assert first.id != second.id

    result = run_resplit(commit=True, book_id=second.id)

    assert result.scanned == 1
    assert _reload(second.id).page_count > 1
    assert _reload(first.id).page_count == 1


def test_running_twice_changes_nothing_the_second_time(
    app, app_context, english, client, tmp_path
):
    book = _import_old(app, client, english, tmp_path)
    run_resplit(commit=True)
    pages = _reload(book.id).page_count
    second = run_resplit(commit=True)
    assert second.books_split == 0
    assert _reload(book.id).page_count == pages
