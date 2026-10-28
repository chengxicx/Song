"""
Re-split reader-mode EPUB chapters imported before chapter splitting.

A reader-mode chapter is one Lute page per extracted file.  Chapters
longer than ``epub_extract.PAGE_CHAR_BUDGET`` are written as several
files at import time (``ch/003.html``, ``ch/003-002.html``, ...) and
become one page each; a chapter imported before that existed is a
single page however long it is, which is the "one enormous page" cost
the split exists to avoid (one DOM, one token stream, one reflow).

This command re-splits those books in place.  It needs nothing but the
extracted package: the chapter's sanitised markup is on disk, so the
split is the same one the import would have done.  The ``.epub`` file
is not read and is not needed.

Dry run by default::

    flask --app lute.app_factory cli epub_resplit --user <user>
    flask --app lute.app_factory cli epub_resplit --user <user> --commit
    flask --app lute.app_factory cli epub_resplit --user <user> --book 1234 --commit

What survives: page 1's row (its id, read date, start date, bookmarks,
and the book's ``BkCurrentTxID``), and every term's status -- statuses
live on the terms, not on the pages, so nothing about what the reader
knows changes.  What does not: bookmarks and sentences that belonged to
the old single page beyond its first split (sentences are re-created
from the text when a page is read).

Safe to re-run: a book is only a candidate while it still has one page,
and a chapter that fits the budget is left alone.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional

from flask import current_app

from lute.book import epub_paths
from lute.book.epub_extract import split_page_file, write_page_files
from lute.db import db
from lute.models.book import Book, Text

# Why a book was left alone; the report groups by these.
SKIP_ALREADY_SPLIT = "already has more than one page"
SKIP_NO_FILE = "chapter file not found"
SKIP_FITS = "fits on one page"
SKIP_FIXED = "fixed-layout chapter"
SKIP_NO_PATH = "book has no stored chapter path"


@dataclass
class BookPlan:
    "One candidate book: what it is now and what it would become."

    book_id: int
    title: str
    path: str
    chars: int = 0
    pages: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    reason: str = ""

    @property
    def will_split(self):
        return not self.reason


@dataclass
class ResplitResult:
    "Everything the report needs."

    scanned: int = 0
    plans: List[BookPlan] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    committed: bool = False
    books_split: int = 0
    pages_added: int = 0

    def to_split(self):
        "The plans that would be (or were) split."
        return [p for p in self.plans if p.will_split]

    def skipped(self):
        "The plans that were left alone, by reason."
        out = {}
        for p in self.plans:
            if p.reason:
                out.setdefault(p.reason, []).append(p)
        return out


def run_resplit(commit=False, book_id=None):
    """
    Find reader-mode EPUB books held as one page, and split the long ones.

    book_id restricts the run to one book.  With commit False nothing is
    written -- not one file, not one row -- and the report says what
    would happen.
    """
    result = ResplitResult(committed=commit)
    query = db.session.query(Book).filter(Book.book_type == "epub")
    if book_id is not None:
        query = query.filter(Book.id == book_id)
    for book in query.order_by(Book.id).all():
        result.scanned += 1
        plan = _plan(book)
        result.plans.append(plan)
        if not plan.will_split:
            continue
        if not commit:
            continue
        try:
            _split(book, plan)
        except Exception as e:  # pylint: disable=broad-except
            db.session.rollback()
            plan.reason = f"failed: {e}"
            result.warnings.append(f"book {plan.book_id}: {e}")
            continue
        result.books_split += 1
        result.pages_added += len(plan.files) - 1
    return result


def _plan(book):
    "Work out what would happen to this book, reading its chapter file."
    plan = BookPlan(book_id=book.id, title=book.title or "", path=book.epub_path or "")
    if not plan.path:
        plan.reason = SKIP_NO_PATH
        return plan
    if book.page_count > 1:
        plan.reason = SKIP_ALREADY_SPLIT
        return plan
    absolute = epub_paths.located(current_app, plan.path)
    if absolute is None:
        plan.reason = SKIP_NO_FILE
        return plan
    with open(absolute, encoding="utf-8") as f:
        html = f.read()
    plan.chars = len(html)
    pages = split_page_file(html)
    if len(pages) <= 1:
        # split_page_file returns the file unchanged for a fixed-layout
        # page box too; telling those two apart is worth a line of the
        # report, because "fits" is expected and "fixed" is by design.
        plan.reason = SKIP_FIXED if "data-epub-viewport" in html else SKIP_FITS
        return plan
    plan.pages = [text for _html, text in pages]
    plan.files = _page_names(plan.path, len(pages))
    return plan


def _page_names(stored_path, count):
    'The file names the split would write ("003.html", "003-002.html", ...).'
    name = os.path.basename((stored_path or "").strip("/"))
    stem, ext = os.path.splitext(name)
    return [name if n == 1 else f"{stem}-{n:03d}{ext}" for n in range(1, count + 1)]


def _split(book, plan):
    """
    Write the pages and rebuild the book's rows.

    The extra pages are written first and page 1 last: an interrupted
    run then leaves files nothing points at (harmless, and the book
    still has one page), never a page 1 that holds only its first split
    while the book still claims to be one page.
    """
    absolute = epub_paths.located(current_app, plan.path)
    root_abs = os.path.dirname(os.path.dirname(absolute))
    with open(absolute, encoding="utf-8") as f:
        pages = split_page_file(f.read())
    if len(pages) <= 1:  # pragma: no cover - the file changed under us
        plan.reason = SKIP_FITS
        return

    write_page_files(root_abs, plan.path, pages[1:], start=2)

    first = list(book.texts)[0]
    first.text = (pages[0][1] or "").strip()
    for number, (_html, text) in enumerate(pages[1:], start=2):
        page = Text(book, (text or "").strip(), number)
        # Appending to the collection is what puts the row in the
        # session: Text.__init__ only sets the relationship, and for an
        # already-persistent book that backref append is too early to
        # cascade (Book._add_page does the same, for the same reason).
        book.texts.append(page)
    db.session.commit()

    # Page 1 is the chapter's own file, so it is the one overwrite that
    # has to happen after the rows exist (see the docstring).
    write_page_files(root_abs, plan.path, pages[:1], start=1)


def format_report(res):
    "Human readable report."
    split = res.to_split()
    lines = [
        "Reader-mode EPUB books : %d" % res.scanned,
        "To split              : %d" % len(split),
    ]
    if res.committed:
        lines.append(
            "Split                 : %d books, %d new pages"
            % (res.books_split, res.pages_added)
        )
    for plan in split:
        lines.append(
            "  #%d %s" % (plan.book_id, _short(plan.title))
            + "  %d chars -> %d pages" % (plan.chars, len(plan.pages))
        )
        if res.committed:
            lines.append("     %s" % ", ".join(plan.files))
    skipped = res.skipped()
    if skipped:
        lines.append(
            "Left alone            : %d" % sum(len(v) for v in skipped.values())
        )
        for reason, plans in sorted(skipped.items()):
            lines.append("  %-34s %d" % (reason, len(plans)))
            for plan in plans[:3]:
                lines.append("     #%d %s" % (plan.book_id, _short(plan.title)))
            if len(plans) > 3:
                lines.append("     ... and %d more" % (len(plans) - 3))
    for warning in res.warnings:
        lines.append("WARNING: %s" % warning)
    if not res.committed and split:
        lines.append("Nothing was written: re-run with --commit to split these.")
    return "\n".join(lines)


def _short(title, width=44):
    "Trim a book title to something that fits one report line."
    title = title or "(untitled)"
    return title if len(title) <= width else title[: width - 1] + "\u2026"
