"""
Read service tests.
"""

import io
import json
import os
import shutil

from lute.models.term import Term
from lute.book.model import Book, Repository
from lute.book.service import Service as BookService
from lute.read.service import Service
from lute.db import db

from tests.dbasserts import assert_record_count_equals, assert_sql_result
from tests.utils import make_pdf_bytes


def test_mark_page_read(english, app_context):
    "Sanity check, field set and stat added."
    b = Book()
    b.title = "blah"
    b.language_id = english.id
    b.text = "Dog CAT dog cat."
    r = Repository(db.session)
    dbbook = r.add(b)
    r.commit()

    sql_text_started = "select * from texts where TxStartDate is not null"
    sql_text_read = "select * from texts where TxReadDate is not null"
    sql_wordsread = "select * from wordsread"
    assert_record_count_equals(sql_text_started, 0, "not started, sanity check")
    assert_record_count_equals(sql_text_read, 0, "not read")
    assert_record_count_equals(sql_wordsread, 0, "not read")

    svc = Service(db.session)
    svc.mark_page_read(dbbook.id, 1, True)
    assert_record_count_equals(sql_text_started, 0, "still not started!")
    assert_record_count_equals(sql_text_read, 1, "read, text")
    assert_record_count_equals(sql_wordsread, 1, "read, wordsread")

    svc.mark_page_read(dbbook.id, 1, True)
    assert_record_count_equals(sql_text_read, 1, "still read")
    assert_record_count_equals(sql_wordsread, 2, "extra record added")


def test_set_unknowns_to_known(english, app_context):
    "Unknowns (status 0) or new are set to well known."
    t = Term(english, "dog")
    db.session.add(t)
    db.session.commit()

    b = Book()
    b.title = "blah"
    b.language_id = english.id
    b.text = "Dog CAT dog cat."
    r = Repository(db.session)
    dbbook = r.add(b)
    r.commit()

    sql = "select WoTextLC, WoStatus from words order by WoText"
    assert_sql_result(sql, ["dog; 1"], "before start")

    service = Service(db.session)
    service.start_reading(dbbook, 1)
    assert_sql_result(sql, ["cat; 0", "dog; 1"], "after start")

    tx = dbbook.texts[0]
    tx.text = "Dog CAT dog cat extra."
    db.session.add(tx)
    db.session.commit()

    service = Service(db.session)
    service.set_unknowns_to_known(tx)
    assert_sql_result(sql, ["cat; 99", "dog; 1", "extra; 99"], "after set")


def _create_two_page_book(session, english):
    "Helper: book with 2 pages, pre-existing known term 'dog'."
    t = Term(english, "dog")
    session.add(t)
    session.commit()

    b = Book()
    b.title = "multibook"
    b.language_id = english.id
    b.text = "Dog runs fast.\n\n---\n\nBig cat sleeps."
    r = Repository(session)
    dbbook = r.add(b)
    r.commit()
    return dbbook


def test_set_book_unknowns_to_known_covers_all_pages(english, app_context):
    "New terms on ALL pages are set to well-known."
    dbbook = _create_two_page_book(db.session, english)

    sql = "select WoTextLC, WoStatus from words order by WoText"
    service = Service(db.session)
    service.start_reading(dbbook, 1)
    assert_sql_result(
        sql, ["dog; 1", "fast; 0", "runs; 0"], "only page 1 words created"
    )

    service.set_book_unknowns_to_known(dbbook)
    assert_sql_result(
        sql,
        ["big; 99", "cat; 99", "dog; 1", "fast; 99", "runs; 99", "sleeps; 99"],
        "all pages marked",
    )


def test_mark_page_read_with_rest_of_book_known(english, app_context):
    "mark_page_read with all-pages flag marks the whole book's unknowns."
    dbbook = _create_two_page_book(db.session, english)

    sql_words = "select WoTextLC, WoStatus from words order by WoText"
    sql_text_read = "select TxOrder from texts where TxReadDate is not null"
    sql_wordsread = "select * from wordsread"

    svc = Service(db.session)
    svc.mark_page_read(dbbook.id, 2, True, True)

    assert_sql_result(
        sql_words,
        ["big; 99", "cat; 99", "dog; 1", "fast; 99", "runs; 99", "sleeps; 99"],
        "whole book marked",
    )
    assert_record_count_equals(sql_text_read, 1, "only current page is read")
    assert_record_count_equals(sql_wordsread, 1, "one wordsread record")


def test_set_terms_to_known_only_updates_unknowns(english, app_context):
    "Only unknown terms become well-known; other statuses untouched."
    dbbook = _create_two_page_book(db.session, english)
    service = Service(db.session)
    service.start_reading(dbbook, 1)

    learning = Term(english, "big")
    learning.status = 1
    db.session.add(learning)
    db.session.commit()

    ids = [
        t.id
        for t in db.session.query(Term)
        .filter(Term.text_lc.in_(["fast", "runs", "big"]))
        .all()
    ]
    count = service.set_terms_to_known(ids, dbbook)

    sql = "select WoTextLC, WoStatus from words order by WoText"
    assert count == 2, "only the two unknown terms were updated"
    assert_sql_result(
        sql,
        ["big; 1", "dog; 1", "fast; 99", "runs; 99"],
        "unknown -> known, learning untouched",
    )


def test_set_terms_to_known_ignores_invalid_ids(english, app_context):
    "Empty or non-numeric ids are skipped safely."
    dbbook = _create_two_page_book(db.session, english)
    service = Service(db.session)

    assert service.set_terms_to_known([], dbbook) == 0
    assert service.set_terms_to_known(["x", None, -3], dbbook) == 0

    sql = "select WoTextLC, WoStatus from words order by WoText"
    assert_sql_result(
        sql, ["dog; 1"], "no changes made for invalid input"
    )


def test_smoke_start_reading(english, app_context):
    "Smoke test book."
    b = Book()
    b.title = "blah"
    b.language_id = english.id
    b.text = "Here is some content.  Here is more."
    r = Repository(db.session)
    dbbook = r.add(b)
    r.commit()

    sql_sentence = "select * from sentences"
    sql_text_started = "select * from texts where TxStartDate is not null"
    assert_record_count_equals(sql_sentence, 0, "before start")
    assert_record_count_equals(sql_text_started, 0, "before start")
    service = Service(db.session)
    service.start_reading(dbbook, 1)
    assert_record_count_equals(sql_sentence, 2, "after start")
    assert_record_count_equals(sql_text_started, 1, "text after start")


def test_start_reading_creates_Terms_for_unknown_words(english, app_context):
    "Unknown (status 0) terms are created for all new words."
    t = Term(english, "dog")
    db.session.add(t)
    db.session.commit()

    b = Book()
    b.title = "blah"
    b.language_id = english.id
    b.text = "Dog CAT dog cat."
    r = Repository(db.session)
    dbbook = r.add(b)
    r.commit()

    sql = "select WoTextLC from words order by WoText"
    assert_sql_result(sql, ["dog"], "before start")

    service = Service(db.session)
    paragraphs = service.start_reading(dbbook, 1)
    textitems = [
        ti
        for para in paragraphs
        for sentence in para
        for ti in sentence
        if ti.is_word and ti.wo_id is None
    ]
    assert (
        len(textitems) == 0
    ), f"All text items should have a term, but got {textitems}"
    assert_sql_result(sql, ["cat", "dog"], "after start")


def _make_pdf_book(app, english, page_texts):
    "Import a pdf book built from page_texts; return (dbbook, pdf_dir)."
    b = Book()
    b.title = "PDF book"
    b.language_id = english.id
    b.book_type = "pdf"
    b.threshold_page_tokens = 250
    b.split_by = "paragraphs"
    b.pdf_stream = io.BytesIO(make_pdf_bytes(page_texts))
    b.pdf_stream_filename = "test.pdf"
    book = BookService().import_book(b, db.session)
    pdf_dir = os.path.join(
        app.static_folder, os.path.dirname(book.pdf_path.strip("/"))
    )
    return book, pdf_dir


def test_pdf_mark_page_read_marks_page_unknowns_known(app, app_context, english):
    "Mark rest as known sets the rendered pdf page's unknowns to well-known."
    book, pdf_dir = _make_pdf_book(app, english, ["Hello cat dog", "one two"])
    try:
        svc = Service(db.session)

        # Rendering a page creates its status-0 terms, exactly as the
        # reading screen does before the reader clicks the next arrow.
        assert svc.pdf_page_context(book, 1, True) is not None

        sql = "select WoTextLC, WoStatus from words order by WoTextLC"
        assert_sql_result(sql, ["cat; 0", "dog; 0", "hello; 0"], "page 1 words")

        svc.mark_page_read(book.id, 1, True)

        assert_sql_result(
            sql, ["cat; 99", "dog; 99", "hello; 99"], "page 1 marked known"
        )

        # Page 2's words are untouched.
        svc.pdf_page_context(book, 2, True)
        assert_sql_result(
            sql,
            ["cat; 99", "dog; 99", "hello; 99", "one; 0", "two; 0"],
            "page 2 still unknown",
        )
    finally:
        shutil.rmtree(pdf_dir, ignore_errors=True)


MANGA_PAGES = [
    {
        "img_path": "page_01.jpg",
        "img_width": 100,
        "img_height": 100,
        "blocks": [
            {
                "box": [0, 0, 10, 10],
                "vertical": False,
                "font_size": 10,
                "lines": ["猫が好き"],
            }
        ],
    },
    {
        "img_path": "page_02.jpg",
        "img_width": 100,
        "img_height": 100,
        "blocks": [
            {
                "box": [0, 0, 10, 10],
                "vertical": False,
                "font_size": 10,
                "lines": ["犬も好き"],
            }
        ],
    },
]


def _term_statuses():
    "All terms as 'text; status', sorted."
    return sorted(f"{t.text_lc}; {t.status}" for t in db.session.query(Term).all())


def test_manga_mark_page_read_marks_page_unknowns_known(app_context, japanese):
    "Mark rest as known sets the rendered manga page's unknowns to well-known."
    b = Book()
    b.title = "Manga book"
    b.language_id = japanese.id
    b.book_type = "manga"
    b.manga_data = json.dumps(
        {"version": "0.2.1", "title": "t", "pages": MANGA_PAGES},
        ensure_ascii=False,
    )
    r = Repository(db.session)
    dbbook = r.add(b)
    r.commit()

    svc = Service(db.session)

    # Rendering creates the page's status-0 terms.
    assert svc.manga_page_context(dbbook, 1, True) is not None
    page1 = _term_statuses()
    assert len(page1) > 0, "page 1 words created"
    assert all(s.endswith("; 0") for s in page1), "all unknown before"

    svc.mark_page_read(dbbook.id, 1, True)
    assert all(s.endswith("; 99") for s in _term_statuses()), "page 1 marked known"

    # Page 2's words are untouched.
    svc.manga_page_context(dbbook, 2, True)
    assert any(s.endswith("; 0") for s in _term_statuses()), "page 2 still unknown"


def test_pdf_page_done_route_marks_page_known(app, client, app_context, english):
    "POST /read/page_done marks the rendered pdf page's words as known."
    book, pdf_dir = _make_pdf_book(app, english, ["Hello cat dog", "one two"])
    try:
        resp = client.get(f"/read/{book.id}")
        assert resp.status_code == 200, "reading page renders"
        body = resp.get_data(as_text=True)
        assert "END_OF_BOOK_MARKS_ALL_PAGES = !(" in body, "end-of-book flag"

        # The words are loaded (and their terms created) by the page route.
        resp = client.get(f"/read/start_reading/{book.id}/1")
        assert resp.status_code == 200, "page renders"
        assert "pdf-word" in resp.get_data(as_text=True), "pdf words"

        sql = "select WoTextLC, WoStatus from words order by WoTextLC"
        assert_sql_result(sql, ["cat; 0", "dog; 0", "hello; 0"], "before")

        resp = client.post(
            "/read/page_done",
            json={"bookid": book.id, "pagenum": 1, "restknown": True},
        )
        assert resp.status_code == 200, "page_done ok"
        assert_sql_result(sql, ["cat; 99", "dog; 99", "hello; 99"], "after")
    finally:
        shutil.rmtree(pdf_dir, ignore_errors=True)
