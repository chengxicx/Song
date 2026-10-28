"""
Tests for the series overview page (/book/series/<tag>).
"""

from datetime import datetime

import pytest

from lute.db import db
from lute.book.series import get_series_overview, series_neighbours
from lute.models.book import Book as DBBook
from lute.models.repositories import UserSettingRepository
from tests.utils import make_book
from lute.book.model import Book as ServiceBook, Repository as ServiceRepository


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


@pytest.fixture(name="_erin_books")
def fixture_erin_books(english):
    "Two Erin-tagged books (first read), one Video-tagged."
    b1 = _mk_tagged_book("Erin-01", ["Erin", "Video"], english, read=True)
    b2 = _mk_tagged_book("Erin-02", ["Erin"], english)
    other = _mk_tagged_book("Other", ["Video"], english)
    return b1, b2, other


def test_overview_counts_and_order(app_context, _erin_books):
    "Books are listed in title order; counts and continue target set."
    b1, b2, _other = _erin_books
    vm = get_series_overview(db.session, "Erin")
    assert vm is not None
    assert [b["BkTitle"] for b in vm["books"]] == ["Erin-01", "Erin-02"]
    assert vm["total_count"] == 2
    assert vm["book_count"] == 2
    assert vm["read_count"] == 1
    assert vm["continue_id"] == b2.id, "continue points at first unread"
    assert vm["books"][0]["is_completed"] is True
    assert vm["books"][0]["StatusDistribution"] is None, "stats not calculated yet"


def test_overview_reports_reading_progress(app_context, english):
    """
    Each book carries ProgressPercent (0-100), the same figure the home
    table shows in its Progress column.
    """
    svcbook = ServiceBook()
    svcbook.language_id = english.id
    svcbook.title = "Erin-long"
    svcbook.text = "Page one.\n---\nPage two.\n---\nPage three.\n---\nPage four."
    svcbook.book_tags = ["Erin"]
    repo = ServiceRepository(db.session)
    b = repo.add(svcbook)
    repo.commit()

    def _progress():
        vm = get_series_overview(db.session, "Erin")
        return vm["books"][0]["ProgressPercent"]

    assert _progress() == 0, "never opened"

    # Sitting on page 3 with pages 1-2 marked read: 2 of 4 done.
    for t in b.texts[:2]:
        t.read_date = datetime.now()
    b.current_tx_id = b.texts[2].id
    db.session.add(b)
    db.session.commit()
    assert _progress() == 50

    # Reading the last page finishes the book, even when an earlier page
    # is the one currently open.
    b.texts[3].read_date = datetime.now()
    b.current_tx_id = b.texts[0].id
    db.session.add(b)
    db.session.commit()
    assert _progress() == 100


def test_overview_natural_title_order(app_context, english):
    """
    Titles sort naturally: "Ep 2" before "Ep 10" (lexicographic SQL
    ordering would put "Ep 10" first), so after reading episode 1 the
    continue target is episode 2, not episode 10.
    """
    b1 = _mk_tagged_book("Ep 1", ["Erin"], english, read=True)
    b2 = _mk_tagged_book("Ep 2", ["Erin"], english)
    b10 = _mk_tagged_book("Ep 10", ["Erin"], english)
    vm = get_series_overview(db.session, "Erin")
    assert [b["BkTitle"] for b in vm["books"]] == ["Ep 1", "Ep 2", "Ep 10"]
    assert vm["continue_id"] == b2.id, "continue points at Ep 2, not Ep 10"


def test_overview_returns_none_for_unknown_tag(app_context, _erin_books):
    assert get_series_overview(db.session, "no-such-tag") is None


@pytest.fixture(name="_erin_is_book_set")
def fixture_erin_is_book_set(app_context):
    "Configure 'Erin' as a book set, which is what the reader page consults."
    UserSettingRepository(db.session).set_dynamic_value("book_series_tags", "Erin")
    db.session.commit()
    return "Erin"


def test_neighbours_are_the_volumes_either_side(
    app_context, _erin_books, _erin_is_book_set
):
    "The arrows' targets: the titles above and below this one in the set."
    b1, b2, other = _erin_books
    assert series_neighbours(db.session, b1) == {
        "prev": None,
        "next": {"id": b2.id, "title": "Erin-02"},
    }, "first volume has nothing before it"
    assert series_neighbours(db.session, b2) == {
        "prev": {"id": b1.id, "title": "Erin-01"},
        "next": None,
    }, "last volume has nothing after it"
    assert series_neighbours(db.session, other) == {"prev": None, "next": None}


def test_neighbours_need_the_series_setting(app_context, _erin_books):
    "A tag that isn't configured as a book set is not a series."
    b1, _b2, _other = _erin_books
    assert series_neighbours(db.session, b1) == {"prev": None, "next": None}


def test_neighbours_use_natural_title_order(app_context, english, _erin_is_book_set):
    "Ep 2 sits between Ep 1 and Ep 10 (lexicographic order would differ)."
    b1 = _mk_tagged_book("Ep 1", ["Erin"], english)
    b2 = _mk_tagged_book("Ep 2", ["Erin"], english)
    b10 = _mk_tagged_book("Ep 10", ["Erin"], english)
    assert series_neighbours(db.session, b2) == {
        "prev": {"id": b1.id, "title": "Ep 1"},
        "next": {"id": b10.id, "title": "Ep 10"},
    }


def test_read_page_exposes_the_neighbouring_volumes(
    app, app_context, _erin_books, _erin_is_book_set
):
    "The reader gets both neighbour ids; the ends of the set get empty ones."
    b1, b2, _other = _erin_books
    client = app.test_client()
    body = client.get(f"/read/{b1.id}").get_data(as_text=True)
    assert f'id="series_next_book_id" value="{b2.id}"' in body
    assert 'id="series_prev_book_id" value=""' in body, "first volume"
    body = client.get(f"/read/{b2.id}").get_data(as_text=True)
    assert 'id="series_next_book_id" value=""' in body, "last volume"
    assert f'id="series_prev_book_id" value="{b1.id}"' in body


def test_series_page_route(app, app_context, _erin_books):
    "GET /book/series/<tag> renders the overview."
    _b1, b2, _other = _erin_books
    client = app.test_client()
    resp = client.get("/book/series/Erin")
    assert resp.status_code == 200
    assert b"Erin-01" in resp.data
    assert b"Erin-02" in resp.data
    assert b"Continue" in resp.data
    assert f"/read/{b2.id}".encode() in resp.data, "continue links to next unread"


def test_series_page_unknown_tag_redirects(app, app_context, _erin_books):
    client = app.test_client()
    resp = client.get("/book/series/no-such-tag")
    assert resp.status_code == 302


def test_series_page_excludes_other_tags(app, app_context, _erin_books):
    "The page for one tag doesn't show books without it."
    client = app.test_client()
    resp = client.get("/book/series/Video")
    assert resp.status_code == 200
    assert b"Other" in resp.data
    assert b"Erin-02" not in resp.data


def test_delete_series_route_deletes_all_books(app, app_context, _erin_books):
    "POST /book/delete_series/<tag> deletes every book carrying the tag."
    _b1, _b2, _other = _erin_books
    client = app.test_client()
    resp = client.post("/book/delete_series/Erin")
    assert resp.status_code == 302
    db.session.expire_all()
    remaining = {b.title for b in db.session.query(DBBook).all()}
    assert remaining == {"Other"}


def test_delete_series_route_includes_archived_books(app, app_context, english):
    "Archived books in the series are deleted too."
    b1 = _mk_tagged_book("Erin-01", ["Erin"], english)
    b2 = _mk_tagged_book("Erin-02", ["Erin"], english)
    b2.archived = True
    db.session.add(b2)
    db.session.commit()
    client = app.test_client()
    resp = client.post("/book/delete_series/Erin")
    assert resp.status_code == 302
    db.session.expire_all()
    assert db.session.query(DBBook).count() == 0, "archived series book deleted"
