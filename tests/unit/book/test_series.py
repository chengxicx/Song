"""
Tests for the series overview page (/book/series/<tag>).
"""

from datetime import datetime

import pytest

from lute.db import db
from lute.book.series import get_series_overview
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


def test_overview_returns_none_for_unknown_tag(app_context, _erin_books):
    assert get_series_overview(db.session, "no-such-tag") is None


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
