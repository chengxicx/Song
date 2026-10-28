"""
EPUB chapter import: create one book per chapter of an EPUB file.

Chapter books are tagged 'EPUB' (plus any extra tags) and the book
title, and the book title is auto-added to the book_series_tags
setting, so the home page aggregates the chapters into a series row
that opens on the series overview page.

Kindle files (MOBI / AZW / AZW3) go through the same path: they are
converted to EPUB bytes by lute.book.kindle_parser first, so chapter
splitting, preview and reader mode all behave identically.
"""

import json
from io import BytesIO

from flask import current_app

from lute.db import db
from lute.book.model import Book
from lute.book.service import Service as BookService
from lute.book.epub_parser import parse_epub, chapter_book_title
from lute.book.epub_extract import extract_epub_package
from lute.book.kindle_parser import is_kindle_ebook, kindle_to_epub
from lute.models.repositories import UserSettingRepository


def read_ebook_bytes(filename, stream):
    """
    Read an uploaded e-book, converting Kindle formats to EPUB bytes.

    Raises BookImportException for Kindle files that are encrypted or
    damaged, with the parser's user-facing message.
    """
    raw = stream.read()
    if is_kindle_ebook(filename, raw):
        return kindle_to_epub(raw)
    return raw


def import_epub_chapters(
    epub_file, title, language_id, tags, selected_indices=None, reader_mode=False
):  # pylint: disable=too-many-locals
    """
    Create one book per chapter of the EPUB.

    epub_file is any object exposing .filename and .stream (e.g. a
    werkzeug FileStorage).  When selected_indices is a list, only
    those chapter indices are imported; None imports every chapter.

    reader_mode keeps the chapter markup: each book is stored as
    book_type "epub" with the chapter's sanitised HTML (and the
    chapter's own CSS) extracted under
    <EPUB_STATIC_ROOT>/epub/<uuid>/ -- in the data directory, so the
    package is covered by a database backup rather than living in the
    code tree.  The reading screen injects word spans into that markup
    instead of rendering the flattened text.

    A chapter is normally one page, but a reflowing chapter longer than
    epub_extract.PAGE_CHAR_BUDGET is written as several files and
    imported as one page per file, so a page stays cheap to tokenise,
    render and scroll.  Either way every page carries its own text, and
    the reading screen pairs page N's tokens with page N's markup.

    Returns (imported_count, failed_count, book_title, last_error).
    """
    raw = read_ebook_bytes(epub_file.filename, epub_file.stream)
    data = parse_epub(BytesIO(raw))
    book_title = (
        (title or "").strip() or data.title or _filename_base(epub_file.filename)
    )
    book_title = book_title[:200] or "EPUB import"
    chapter_tags = _dedup_strings(list(tags) + [book_title])

    chapters = data.chapters
    if selected_indices is not None:
        wanted = set(selected_indices)
        chapters = [c for c in chapters if c.index in wanted]

    extracted = None
    if reader_mode and chapters:
        extracted = extract_epub_package(
            raw, chapters, current_app.config["EPUB_STATIC_ROOT"]
        )

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
        if extracted is not None:
            chapter = extracted.chapters.get(c.index)
            if chapter is None:
                failed += 1
                last_error = f"Chapter {c.index + 1} could not be extracted."
                continue
            b.book_type = "epub"
            b.epub_path = chapter["html"]
            # Text from the sanitised markup, so the token stream the
            # reading screen builds matches the DOM it injects into.
            b.text = chapter["text"]
            # One page per split (a short chapter is a single page).  The
            # chapter's own text above stays the whole thing: it is what
            # the book listing and the stats report.
            b.epub_page_texts = [p["text"] for p in chapter.get("pages") or []]
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
