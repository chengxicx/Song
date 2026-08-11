"""
Book stats tests.
"""

import json
import pytest
from sqlalchemy.sql import text

from lute.db import db
from lute.term.model import Term, Repository
from lute.book.stats import Service

from tests.utils import make_text, make_book
from tests.dbasserts import assert_record_count_equals, assert_sql_result


def add_term(lang, s, status):
    "Create and add term."
    term = Term()
    term.language = lang
    term.language_id = lang.id
    term.text = s
    term.status = status
    repo = Repository(db.session)
    repo.add(term)
    repo.commit()


def scenario(language, fulltext, terms_and_statuses, expected):
    "Run a scenario."
    t = make_text("Hola", fulltext, language)
    b = t.book
    db.session.add(t)
    db.session.add(b)
    db.session.commit()

    for ts in terms_and_statuses:
        add_term(language, ts[0], ts[1])

    svc = Service(db.session)
    stats = svc.calc_status_distribution(b)

    assert stats == expected


def test_two_words(spanish):
    scenario(
        spanish,
        "Tengo un gato.  Tengo un perro.",
        [["gato", 1], ["perro", 2]],
        {0: 2, 1: 1, 2: 1, 3: 0, 4: 0, 5: 0, 98: 0, 99: 0},
    )


def test_single_word(spanish):
    scenario(
        spanish,
        "Tengo un gato.  Tengo un perro.",
        [["gato", 3]],
        {0: 3, 1: 0, 2: 0, 3: 1, 4: 0, 5: 0, 98: 0, 99: 0},
    )


def test_new_terms_are_not_created(spanish):
    "No new terms created accidentally on calc stats."
    scenario(
        spanish,
        "Tengo un gato.  Tengo un perro.",
        [["gato", 3], ["un", 0]],
        {0: 3, 1: 0, 2: 0, 3: 1, 4: 0, 5: 0, 98: 0, 99: 0},
    )
    sql = "select WoText from words order by WoText"
    assert_sql_result(sql, ["gato", "un"], "no new terms.")


def test_with_multiword(spanish):
    scenario(
        spanish,
        "Tengo un gato.  Tengo un perro.",
        [["tengo un", 3]],
        {0: 2, 1: 0, 2: 0, 3: 1, 4: 0, 5: 0, 98: 0, 99: 0},
    )


def test_chinese_no_term_stats(classical_chinese):
    scenario(
        classical_chinese,
        "這是東西",
        [],
        {0: 4, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 98: 0, 99: 0},
    )


def test_chinese_with_terms(classical_chinese):
    scenario(
        classical_chinese,
        "這是東西",
        [["東西", 1]],
        {0: 2, 1: 1, 2: 0, 3: 0, 4: 0, 5: 0, 98: 0, 99: 0},
    )


@pytest.fixture(name="_test_book")
def fixture_make_book(empty_db, spanish):
    "Single page book."
    b = make_book("Hola.", "Hola tengo un gato.", spanish)
    db.session.add(b)
    db.session.commit()
    return b


@pytest.fixture(name="service")
def fixture_service():
    "svc."
    return Service(db.session)


def add_terms(lang, terms):
    "Create and add term."
    repo = Repository(db.session)
    for s in terms:
        term = Term()
        term.language = lang
        term.language_id = lang.id
        term.text = s
        repo.add(term)
    repo.commit()


def assert_stats(expected, msg=""):
    "helper."
    sql = """select distinctterms, distinctunknowns,
      unknownpercent, replace(status_distribution, '"', "'") from bookstats"""
    assert_sql_result(sql, expected, msg)


def test_cache_loads_when_prompted(service, _test_book):
    "Have to call refresh_stats() to load stats."
    assert_record_count_equals("bookstats", 0, "nothing loaded")
    service.refresh_stats()
    assert_record_count_equals("bookstats", 1, "loaded")


def test_stats_smoke_test(service, _test_book, spanish):
    "Terms are rendered to count stats."
    add_terms(spanish, ["gato", "TENGO"])
    service.refresh_stats()
    assert_stats(
        ["4; 2; 50; {'0': 2, '1': 2, '2': 0, '3': 0, '4': 0, '5': 0, '98': 0, '99': 0}"]
    )


@pytest.fixture(name="_manga_book")
def fixture_manga_book(empty_db):
    "Create a manga book with mock mokuro data."
    from lute.models.book import Book
    from lute.language.service import Service as LanguageService

    # Re-create Japanese language after empty_db wipes the db.
    lang_svc = LanguageService(db.session)
    j = lang_svc.get_language_def("Japanese").language
    db.session.add(j)
    db.session.commit()

    book = Book()
    book.language = j
    book.title = "Manga Test"
    book.book_type = "manga"
    book.manga_data = json.dumps({
        "version": "0.2.1",
        "pages": [
            {
                "blocks": [
                    {
                        "lines": ["こんにちは世界", "私は猫が好きです"]
                    }
                ]
            }
        ]
    })
    db.session.add(book)
    db.session.commit()
    return book


def test_manga_calc_status_distribution(_manga_book):
    "Manga status distribution is calculated from mokuro text."
    svc = Service(db.session)
    stats = svc.calc_status_distribution(_manga_book)
    assert isinstance(stats, dict)
    total = sum(stats.values())
    assert total > 0, "manga text should produce tokens"


def test_manga_calc_word_count(_manga_book):
    "Manga word count returns a positive integer."
    svc = Service(db.session)
    wc = svc.calc_manga_word_count(_manga_book)
    assert wc is not None
    assert isinstance(wc, int)
    assert wc > 0


def test_manga_calc_word_count_non_manga(_test_book):
    "Non-manga books return None for manga word count."
    svc = Service(db.session)
    wc = svc.calc_manga_word_count(_test_book)
    assert wc is None


def test_manga_stats_refresh_and_cache(service, _manga_book):
    "Manga stats are cached with manga_word_count."
    svc = Service(db.session)
    svc.refresh_stats()
    sql = "select distinctterms, manga_word_count from bookstats"
    result = db.session.execute(text(sql)).fetchone()
    assert result.distinctterms > 0
    assert result.manga_word_count is not None and result.manga_word_count > 0


def test_manga_empty_pages(service, empty_db):
    "Manga with no pages produces zero counts."
    from lute.models.book import Book
    from lute.language.service import Service as LanguageService

    lang_svc = LanguageService(db.session)
    j = lang_svc.get_language_def("Japanese").language
    db.session.add(j)
    db.session.commit()

    book = Book()
    book.language = j
    book.title = "Empty Manga"
    book.book_type = "manga"
    book.manga_data = json.dumps({"version": "0.2.1", "pages": []})
    db.session.add(book)
    db.session.commit()

    svc = Service(db.session)
    wc = svc.calc_manga_word_count(book)
    assert wc == 0

    dist = svc.calc_status_distribution(book)
    assert all(v == 0 for v in dist.values())


def test_get_stats_calculates_and_caches_stats(service, _test_book, spanish):
    "Calculating stats is expensive, so store them on get."
    add_terms(spanish, ["gato", "TENGO"])
    assert_record_count_equals("bookstats", 0, "cache not loaded")
    assert_stats([], "No stats cached at start.")

    stats = service.get_stats(_test_book)
    assert stats.BkID == _test_book.id
    assert stats.distinctterms == 4
    assert stats.distinctunknowns == 2
    assert stats.unknownpercent == 50
    assert (
        stats.status_distribution
        == '{"0": 2, "1": 2, "2": 0, "3": 0, "4": 0, "5": 0, "98": 0, "99": 0}'
    )

    assert_record_count_equals("bookstats", 1, "cache loaded")
    assert_stats(
        ["4; 2; 50; {'0': 2, '1': 2, '2': 0, '3': 0, '4': 0, '5': 0, '98': 0, '99': 0}"]
    )
    stats = service.get_stats(_test_book)
    assert stats.BkID == _test_book.id
    assert (
        stats.status_distribution
        == '{"0": 2, "1": 2, "2": 0, "3": 0, "4": 0, "5": 0, "98": 0, "99": 0}'
    )


def test_stats_calculates_rendered_text(service, _test_book, spanish):
    "Multiword term counted as one term."
    add_terms(spanish, ["tengo un"])
    service.refresh_stats()
    assert_stats(
        ["3; 2; 67; {'0': 2, '1': 1, '2': 0, '3': 0, '4': 0, '5': 0, '98': 0, '99': 0}"]
    )


def test_stats_only_update_books_marked_stale(service, _test_book, spanish):
    "Have to mark book as stale, too expensive otherwise."
    add_terms(spanish, ["gato", "TENGO"])
    service.refresh_stats()
    assert_stats(
        ["4; 2; 50; {'0': 2, '1': 2, '2': 0, '3': 0, '4': 0, '5': 0, '98': 0, '99': 0}"]
    )

    add_terms(spanish, ["hola"])
    service.refresh_stats()
    assert_stats(
        [
            "4; 2; 50; {'0': 2, '1': 2, '2': 0, '3': 0, '4': 0, '5': 0, '98': 0, '99': 0}"
        ],
        "not updated",
    )

    service.mark_stale(_test_book)
    service.refresh_stats()
    assert_stats(
        [
            "4; 1; 25; {'0': 1, '1': 3, '2': 0, '3': 0, '4': 0, '5': 0, '98': 0, '99': 0}"
        ],
        "updated",
    )


def test_stats_updated_if_field_empty(service, _test_book, spanish):
    "Have to mark book as stale, too expensive otherwise."
    add_terms(spanish, ["gato", "TENGO"])
    service.refresh_stats()
    assert_stats(
        ["4; 2; 50; {'0': 2, '1': 2, '2': 0, '3': 0, '4': 0, '5': 0, '98': 0, '99': 0}"]
    )

    db.session.execute(text("update bookstats set status_distribution = null"))
    db.session.commit()

    assert_stats(["4; 2; 50; None"], "Set to none")
    service.refresh_stats()
    assert_stats(
        ["4; 2; 50; {'0': 2, '1': 2, '2': 0, '3': 0, '4': 0, '5': 0, '98': 0, '99': 0}"]
    )
