"""
EPUB chapter import: create one book per chapter of an EPUB file.

Chapter books are tagged 'EPUB' (plus any extra tags) and the book
title, and the book title is auto-added to the book_series_tags
setting, so the home page aggregates the chapters into a series row
that opens on the series overview page.
"""

import json

from lute.db import db
from lute.book.model import Book
from lute.book.service import Service as BookService
from lute.book.epub_parser import parse_epub, chapter_book_title
from lute.models.repositories import UserSettingRepository


def import_epub_chapters(
    epub_file, title, language_id, tags, selected_indices=None
):  # pylint: disable=too-many-locals
    """
    Create one book per chapter of the EPUB.

    epub_file is any object exposing .filename and .stream (e.g. a
    werkzeug FileStorage).  When selected_indices is a list, only
    those chapter indices are imported; None imports every chapter.

    Returns (imported_count, failed_count, book_title, last_error).
    """
    data = parse_epub(epub_file.stream)
    book_title = (
        (title or "").strip() or data.title or _filename_base(epub_file.filename)
    )
    book_title = book_title[:200] or "EPUB import"
    chapter_tags = _dedup_strings(list(tags) + [book_title])

    chapters = data.chapters
    if selected_indices is not None:
        wanted = set(selected_indices)
        chapters = [c for c in chapters if c.index in wanted]

    svc = BookService()
    imported = 0
    failed = 0
    last_error = None
    for c in chapters:
        b = Book()
        b.language_id = language_id
        b.title = chapter_book_title(
            book_title, c.index + 1, c.title, len(data.chapters)
        )
        b.source_uri = epub_file.filename
        b.text = c.text
        b.book_tags = chapter_tags
        b.threshold_page_tokens = 250
        b.split_by = "paragraphs"
        try:
            svc.import_book(b, db.session)
            imported += 1
        except Exception as e:  # pylint: disable=broad-except
            # One bad chapter shouldn't abort the whole import.
            failed += 1
            last_error = getattr(e, "message", None) or str(e)
            db.session.rollback()

    if imported > 0:
        add_series_tag_to_whitelist(book_title)
    return imported, failed, book_title, last_error


def add_series_tag_to_whitelist(series_tag):
    "Add the tag to the book_series_tags setting so the home page aggregates it."
    repo = UserSettingRepository(db.session)
    current = repo.get_dynamic_value("book_series_tags") or ""
    tags = [t for t in current.split(",") if t]
    if series_tag not in tags:
        tags.append(series_tag)
        repo.set_dynamic_value("book_series_tags", ",".join(tags))
        db.session.commit()


def language_id_from(raw):
    "Parse a language id form field; None when missing or invalid."
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def selected_chapter_indices(raw):
    "Parse the JSON list of selected chapter indices; None = all chapters."
    if not raw:
        return None
    try:
        values = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(values, list):
        return None
    ret = []
    for v in values:
        try:
            ret.append(int(v))
        except (TypeError, ValueError):
            continue
    return ret


def _filename_base(filename):
    "Filename without its extension."
    return ".".join((filename or "").split(".")[:-1]) or (filename or "EPUB import")


def _dedup_strings(values):
    "Drop empty strings and duplicates, preserving order."
    seen = set()
    ret = []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            ret.append(v)
    return ret
