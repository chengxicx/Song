"Data cleanup tests."

import os
import shutil
from datetime import datetime
from flask import current_app
from sqlalchemy import text as sqltext
from lute.book.service import Service as BookService
from lute.db import db
from lute.db.data_cleanup import clean_data
from lute.models.book import Book as DBBook, Text as DBText
from lute.models.repositories import UserSettingRepository
from tests.utils import make_pdf_bytes, make_text
from tests.dbasserts import assert_sql_result


# Cleaning up missing sentence.SeTextLC records.


def test_cleanup_loads_missing_sentence_textlc(app_context, spanish):
    """
    Load the sentence.SeTextLC.

    If the sqlite LOWER(SeText) would be the same as the parser-generated lowercase text,
    store the special char '*' only, don't waste file space storing the parser-generated lc text.
    """

    t = make_text("test", "gato. Ábrelo. tengo. QUIERO. Ábrela. ábrela.", spanish)
    t.read_date = datetime.now()
    db.session.add(t)
    db.session.commit()

    # Force re-calc.
    sqlhack = """
    update sentences set SeTextLC = null
    where SeText not like '%gato%' and SeText not like '%brelo%'
    """
    db.session.execute(sqltext(sqlhack))
    db.session.commit()
    sql = "select SeText, SeTextLC from sentences order by SeID"
    preclean = [
        "/gato/./; *",
        "/Ábrelo/./; /ábrelo/./",
        "/tengo/./; None",
        "/QUIERO/./; None",
        "/Ábrela/./; None",
        "/ábrela/./; None",
    ]
    assert_sql_result(sql, preclean, "pre-clean")

    def _output(s):
        print(s, flush=True)

    clean_data(db.session, _output)

    postclean = [
        "/gato/./; *",
        "/Ábrelo/./; /ábrelo/./",
        "/tengo/./; *",
        "/QUIERO/./; *",
        "/Ábrela/./; /ábrela/./",
        "/ábrela/./; *",
    ]
    assert_sql_result(sql, postclean, "post-clean")


# One-time backfill of pdf books' page word counts.


def _make_pdf_book_with_zeroed_counts(app_context, spanish):
    "Create a pdf book with empty page texts and zeroed word counts."
    pdf_rel = "pdf/testcleanup/file.pdf"
    pdf_abs = os.path.join(current_app.static_folder, pdf_rel)
    os.makedirs(os.path.dirname(pdf_abs), exist_ok=True)
    with open(pdf_abs, "wb") as f:
        f.write(make_pdf_bytes(["Hello cat dog", "one two"]))

    b = DBBook("pdf book", spanish)
    b.book_type = "pdf"
    b.pdf_path = pdf_rel
    for i in range(2):
        _ = DBText(b, "", i + 1)
    db.session.add(b)
    db.session.commit()

    # Simulate pre-fix data: the startup cleanup zeroed the empty texts.
    db.session.execute(sqltext("update texts set TxWordCount = 0"))
    db.session.commit()
    return b, os.path.dirname(pdf_abs)


def test_cleanup_backfills_pdf_word_counts(app_context, spanish, monkeypatch):
    "Pdf books' zeroed page counts are recomputed from the PDF file."

    def _output(s):
        print(s, flush=True)

    # Remove the flag so the backfill runs.
    session = db.session
    session.execute(
        sqltext(
            "delete from settings where StKey = 'pdf_page_word_counts_backfilled'"
        )
    )
    session.commit()

    _book, pdf_dir = _make_pdf_book_with_zeroed_counts(app_context, spanish)
    try:
        clean_data(session, _output)

        sql = "select TxWordCount from texts order by TxOrder"
        assert_sql_result(sql, ["3", "2"], "pdf counts backfilled")

        flag = UserSettingRepository(session).get_dynamic_value(
            "pdf_page_word_counts_backfilled"
        )
        assert flag == "1", "flag set after backfill"

        # Second run must not re-extract: the flag short-circuits it.
        def _boom(self, dbbook, force=False):
            raise AssertionError("backfill should not rerun")

        monkeypatch.setattr(BookService, "set_pdf_page_word_counts", _boom)
        clean_data(session, _output)
        assert_sql_result(sql, ["3", "2"], "counts unchanged")
    finally:
        shutil.rmtree(pdf_dir, ignore_errors=True)
