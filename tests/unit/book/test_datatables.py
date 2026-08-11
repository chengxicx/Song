"""
Book tests.
"""

import json
from datetime import datetime
import pytest
from lute.models.language import Language
from lute.book.datatables import get_data_tables_list
from lute.db import db
from lute.db.demo import Service as DemoService
from lute.book.stats import Service as StatsService
from tests.utils import make_book


@pytest.fixture(name="_dt_params")
def fixture_dt_params():
    "Sample query params."
    columns = [
        {"data": "0", "name": "BkID", "searchable": False, "orderable": False},
        {"data": "1", "name": "BkTitle", "searchable": True, "orderable": True},
        {"data": "2", "name": "IsCompleted", "searchable": False, "orderable": False},
    ]
    params = {
        "draw": "1",
        "columns": columns,
        "order": [{"column": "1", "dir": "asc"}],
        "start": "0",  # Start from page 0
        "length": "10",
        "search": {"value": "", "regex": False},
        "filtLanguage": "0",  # Ha!
    }
    return params


def test_smoke_book_datatables_query_runs(app_context, _dt_params):
    """
    Smoke test only, ensure query runs.
    """
    demosvc = DemoService(db.session)
    demosvc.load_demo_data()
    get_data_tables_list(_dt_params, False, db.session)
    # print(d['data'])
    a = 1
    assert a == 1, "dummy check"


def test_book_query_only_returns_supported_language_books(app_context, _dt_params):
    """
    Smoke test only, ensure query runs.
    """
    demosvc = DemoService(db.session)
    demosvc.load_demo_data()
    for lang in db.session.query(Language).all():
        lang.parser_type = "unknown"
        db.session.add(lang)
    db.session.commit()
    d = get_data_tables_list(_dt_params, False, db.session)
    assert len(d["data"]) == 0, "no books should be active"


def _dt_params_sorted_by_last_opened(dir):
    "Params that sort by the LastOpenedDate column (index 6)."
    columns = [
        {"data": "0", "name": "BkTitle", "searchable": True, "orderable": True},
        {"data": "1", "name": "LgName", "searchable": True, "orderable": True},
        {"data": "2", "name": "TagList", "searchable": True, "orderable": True},
        {"data": "3", "name": "WordCount", "searchable": True, "orderable": True},
        {"data": "4", "name": "UnknownPercent", "searchable": False, "orderable": True},
        {"data": "5", "name": "NewWordPercent", "searchable": False, "orderable": True},
        {"data": "6", "name": "LastOpenedDate", "searchable": False, "orderable": True},
        {"data": "7", "name": "IsCompleted", "searchable": False, "orderable": False},
    ]
    return {
        "draw": "1",
        "columns": columns,
        "order": [{"column": "6", "dir": dir}],
        "start": "0",
        "length": "10",
        "search": {"value": "", "regex": False},
        "filtLanguage": "0",
    }


def test_new_book_with_no_last_read_sorts_first_when_desc(app_context, english):
    "A newly-created book (NULL last read) ranks first on 'Last read' desc."
    keep = make_book("old book", "Kept.", english)
    keep.texts[0].start_date = datetime(2020, 1, 1)
    fresh = make_book("new book", "Fresh.", english)  # no start date set
    db.session.add(keep)
    db.session.add(fresh)
    db.session.commit()

    d = get_data_tables_list(_dt_params_sorted_by_last_opened("desc"), False, db.session)
    titles = [r["BkTitle"] for r in d["data"]]
    assert titles[0] == "new book", "NULL last-read must sort as newest (top)"
    assert titles.index("old book") > titles.index("new book")


def test_new_book_sorts_last_when_asc(app_context, english):
    "Ascending sort keeps never-read books at the bottom."
    keep = make_book("old book", "Kept.", english)
    keep.texts[0].start_date = datetime(2020, 1, 1)
    fresh = make_book("new book", "Fresh.", english)  # no start date set
    db.session.add(keep)
    db.session.add(fresh)
    db.session.commit()

    d = get_data_tables_list(_dt_params_sorted_by_last_opened("asc"), False, db.session)
    titles = [r["BkTitle"] for r in d["data"]]
    assert titles.index("new book") > titles.index("old book")


def test_book_data_says_completed_if_last_page_has_been_read(
    app_context, _dt_params, english
):
    "Add a visual cue to completed books."
    b = make_book("title", "Hello.", english)
    db.session.add(b)
    db.session.commit()
    _dt_params["search"] = {"value": "title", "regex": False}
    d = get_data_tables_list(_dt_params, False, db.session)
    actual = d["data"][0]
    assert actual["BkID"] == b.id, "correct book"
    assert actual["IsCompleted"] == 0, "not completed"
    t = b.texts[0]
    t.read_date = datetime.now()
    db.session.add(t)
    db.session.commit()
    d = get_data_tables_list(_dt_params, False, db.session)
    actual = d["data"][0]
    assert actual["BkID"] == b.id, "correct book"
    assert actual["IsCompleted"] == 1, "completed"


def test_manga_book_word_count_in_datatables(app_context, empty_db, _dt_params):
    "Manga books show WordCount from manga_word_count, not textcounts.wc."
    from lute.models.book import Book
    from lute.language.service import Service as LanguageService

    lang_svc = LanguageService(db.session)
    j = lang_svc.get_language_def("Japanese").language
    db.session.add(j)
    db.session.commit()

    book = Book()
    book.language = j
    book.title = "Manga DataTables"
    book.book_type = "manga"
    book.manga_data = json.dumps({
        "version": "0.2.1",
        "pages": [
            {
                "blocks": [
                    {"lines": ["こんにちは世界"]}
                ]
            }
        ]
    })
    db.session.add(book)
    db.session.commit()

    # Calculate and cache stats so manga_word_count is set.
    svc = StatsService(db.session)
    svc.refresh_stats()

    d = get_data_tables_list(_dt_params, False, db.session)
    rows = [r for r in d["data"] if r["BkTitle"] == "Manga DataTables"]
    assert len(rows) == 1
    wc = rows[0]["WordCount"]
    assert wc is not None and wc > 0
