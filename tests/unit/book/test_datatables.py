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
from lute.book.model import Book as ServiceBook, Repository as ServiceRepository


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


# ======================================================================
# Series aggregation (books collapsed by configured "series tag").
# ======================================================================

from lute.models.repositories import UserSettingRepository


def _set_series_setting(session, tags):
    "Configure the comma-separated book_series_tags setting."
    UserSettingRepository(session).set_value("book_series_tags", ",".join(tags))
    session.commit()


def _mk_tagged_book(title, tags, language, read=False):
    "Create a book with the given tags via the service layer."
    svcbook = ServiceBook()
    svcbook.language_id = language.id
    svcbook.title = title
    svcbook.text = f"{title} text."
    svcbook.book_tags = tags
    repo = ServiceRepository(db.session)
    dbbook = repo.add(svcbook)
    repo.commit()
    if read:
        dbbook.texts[0].read_date = datetime.now()
        db.session.add(dbbook)
        db.session.commit()
    return dbbook


@pytest.fixture(name="_series_books")
def fixture_series_books(english):
    "Three books tagged Erin (one read), one tagged only Video, one untagged."
    _mk_tagged_book("Erin-01-adv", ["Erin", "Video"], english)
    _mk_tagged_book("Erin-01-ba", ["Erin", "Video"], english)
    _mk_tagged_book("Erin-02-ba", ["Erin"], english, read=True)
    _mk_tagged_book("Other Book", ["Video"], english)
    _mk_tagged_book("Standalone", [], english)


def test_book_type_exposed_for_flat_and_series_rows(
    app_context, _dt_params, english
):
    "Flat rows carry the book's type; aggregated series rows say 'series'."
    svcbook = ServiceBook()
    svcbook.language_id = english.id
    svcbook.title = "yt book"
    svcbook.text = "yt book text."
    svcbook.book_type = "youtube"
    svcbook.book_tags = ["Erin"]
    repo = ServiceRepository(db.session)
    repo.add(svcbook)
    repo.commit()

    _set_series_setting(db.session, [])
    d = get_data_tables_list(_dt_params, False, db.session)
    flats = [r for r in d["data"] if r["BkTitle"] == "yt book"]
    assert len(flats) == 1
    assert flats[0]["BookType"] == "youtube"

    _set_series_setting(db.session, ["Erin"])
    d = get_data_tables_list(_dt_params, False, db.session)
    series = [r for r in d["data"] if r["SeriesTag"]]
    assert len(series) == 1
    assert series[0]["BookType"] == "series"


def test_series_tags_setting_default_exists(app_context):
    "The book_series_tags setting key is created with the app defaults."
    assert UserSettingRepository(db.session).get_value("book_series_tags") == ""


def test_book_type_filter_flat_rows(app_context, _dt_params, english):
    "filtType keeps only matching books; 'text' matches the default '' type."
    def _mk_typed_book(title, btype):
        svcbook = ServiceBook()
        svcbook.language_id = english.id
        svcbook.title = title
        svcbook.text = f"{title} text."
        svcbook.book_type = btype
        repo = ServiceRepository(db.session)
        repo.add(svcbook)
        repo.commit()

    _mk_typed_book("yt book", "youtube")
    _mk_typed_book("plain book", "")

    _dt_params["filtType"] = "youtube"
    d = get_data_tables_list(_dt_params, False, db.session)
    assert [r["BkTitle"] for r in d["data"]] == ["yt book"]

    _dt_params["filtType"] = "text"
    d = get_data_tables_list(_dt_params, False, db.session)
    titles = [r["BkTitle"] for r in d["data"]]
    assert "plain book" in titles
    assert "yt book" not in titles

    # Unknown values are ignored (the value is SQL-interpolated, so the
    # whitelist in datatables.py is what keeps it safe).
    _dt_params["filtType"] = "bogus"
    d = get_data_tables_list(_dt_params, False, db.session)
    assert len(d["data"]) == 2


def test_book_type_filter_with_series_rows(app_context, _dt_params, _series_books):
    "filtType='series' keeps only aggregate rows; other types exclude them."
    _set_series_setting(db.session, ["Erin"])

    _dt_params["filtType"] = "series"
    d = get_data_tables_list(_dt_params, False, db.session)
    assert d["recordsTotal"] == 1
    assert d["data"][0]["SeriesTag"] == "Erin"

    _dt_params["filtType"] = "text"
    d = get_data_tables_list(_dt_params, False, db.session)
    assert all(r["SeriesTag"] is None for r in d["data"])
    titles = sorted(r["BkTitle"] for r in d["data"])
    assert titles == ["Other Book", "Standalone"]


def test_series_aggregation_collapses_tagged_books(
    app_context, _dt_params, _series_books
):
    "Tagged books become one row; untagged books stay individual."
    _set_series_setting(db.session, ["Erin"])
    d = get_data_tables_list(_dt_params, False, db.session)
    assert d["recordsTotal"] == 3, "1 series row + 2 standalone books"

    series = [r for r in d["data"] if r["SeriesTag"]]
    assert len(series) == 1
    s = series[0]
    assert s["BkTitle"] == "Erin"
    assert s["BkID"] is None, "series rows have no book id"
    assert s["SeriesBookCount"] == 3
    assert s["SeriesReadCount"] == 1, "one episode read"
    assert s["WordCount"] > 0, "word counts summed"
    assert s["IsCompleted"] == 0, "not all episodes read"

    flats = [r["BkTitle"] for r in d["data"] if not r["SeriesTag"]]
    assert sorted(flats) == ["Other Book", "Standalone"]


def test_series_book_with_two_series_tags_grouped_once(
    app_context, _dt_params, english
):
    "A book carrying two configured series tags appears in one group only."
    _mk_tagged_book("dual", ["aaa", "bbb"], english)
    _mk_tagged_book("bbb-book", ["bbb"], english)
    _set_series_setting(db.session, ["aaa", "bbb"])

    d = get_data_tables_list(_dt_params, False, db.session)
    groups = {r["BkTitle"]: r["SeriesBookCount"] for r in d["data"] if r["SeriesTag"]}
    assert groups == {"aaa": 1, "bbb": 1}, "dual book only under first tag (aaa)"


def test_series_aggregation_flat_when_searching(
    app_context, _dt_params, _series_books
):
    "An active search disables aggregation so all books are findable."
    _set_series_setting(db.session, ["Erin"])
    _dt_params["search"] = {"value": "Erin", "regex": False}
    d = get_data_tables_list(_dt_params, False, db.session)
    titles = sorted(r["BkTitle"] for r in d["data"])
    assert titles == ["Erin-01-adv", "Erin-01-ba", "Erin-02-ba"]
    assert all(r["SeriesTag"] is None for r in d["data"])


def test_series_aggregation_flat_when_tag_filtered(
    app_context, _dt_params, _series_books
):
    "An active tag filter disables aggregation."
    _set_series_setting(db.session, ["Erin"])
    _dt_params["filtTag"] = "Erin"
    d = get_data_tables_list(_dt_params, False, db.session)
    assert d["recordsTotal"] == 3, "flat list of the tagged books"
    assert all(r["SeriesTag"] is None for r in d["data"])


def test_series_aggregation_no_tags_configured(
    app_context, _dt_params, _series_books
):
    "No aggregation when the setting is empty."
    _set_series_setting(db.session, [])
    d = get_data_tables_list(_dt_params, False, db.session)
    assert d["recordsTotal"] == 5


def test_series_aggregation_respects_language_filter(
    app_context, _dt_params, _series_books, english
):
    "Language filtering applies to series rows and standalone books alike."
    _set_series_setting(db.session, ["Erin"])
    _dt_params["filtLanguage"] = str(english.id)
    d = get_data_tables_list(_dt_params, False, db.session)
    assert d["recordsTotal"] == 3

    _dt_params["filtLanguage"] = "999999"
    d = get_data_tables_list(_dt_params, False, db.session)
    assert d["recordsTotal"] == 0


def test_series_stats_pending_lists_members_missing_or_stale_stats(
    app_context, _dt_params, english
):
    """
    Series rows carry SeriesStatsPending: the member book ids whose stats
    are missing or stale, so the frontend can batch-calculate them (books
    behind a series row never appear as flat rows, so nothing else would
    calculate their stats).
    """
    b1 = _mk_tagged_book("Pen-01", ["Pen"], english)
    b2 = _mk_tagged_book("Pen-02", ["Pen"], english)
    _set_series_setting(db.session, ["Pen"])

    def _pending_ids():
        d = get_data_tables_list(_dt_params, False, db.session)
        series = [r for r in d["data"] if r["SeriesTag"]]
        assert len(series) == 1
        flats = [r for r in d["data"] if not r["SeriesTag"]]
        for f in flats:
            assert f["SeriesStatsPending"] is None, "flat rows carry no pending ids"
        raw = series[0]["SeriesStatsPending"]
        return sorted(int(x) for x in raw.split(",")) if raw else []

    # New books have no stats yet.
    assert _pending_ids() == sorted([b1.id, b2.id])

    # Calculated stats drop a book out of the pending list.
    StatsService(db.session).get_stats(b1)
    assert _pending_ids() == [b2.id]

    # Stale stats put it back.
    StatsService(db.session).mark_stale(b1)
    assert _pending_ids() == sorted([b1.id, b2.id])
