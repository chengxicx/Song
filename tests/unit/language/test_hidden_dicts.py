"""
Hiding an online dictionary without deleting it.

A language can carry many dictionaries; some are rarely used but not
worth re-entering, so a row can be flagged hidden: it stays configured
(and still passes the form's "must have an active dictionary"
validation) while offering no tab anywhere -- the strip's tab lists are
all built from Language.active_dict_uris, so the filter lives there.
"""

from lute.db import db
from lute.models.language import Language, LanguageDictionary

from tests.utils import make_book


def _dict(uri, usefor="terms", active=True, hidden=False, order=1):
    d = LanguageDictionary()
    d.usefor = usefor
    d.dicttype = "embeddedhtml"
    d.dicturi = uri
    d.is_active = active
    d.is_hidden = hidden
    d.sort_order = order
    return d


def _language_with(dicts, name="Hidden Dict Test"):
    lang = Language()
    lang.name = name
    lang.dictionaries = list(dicts)
    db.session.add(lang)
    db.session.commit()
    return lang


def test_a_fresh_dictionary_row_is_not_hidden(app_context):
    assert not LanguageDictionary().is_hidden


def test_hidden_dicts_are_excluded_from_the_tab_lists(app_context, empty_db):
    lang = _language_with(
        [
            _dict("https://a.example.com/[LUTE]", "terms", order=1),
            _dict("https://b.example.com/[LUTE]", "terms", hidden=True, order=2),
            _dict("https://c.example.com/[LUTE]", "sentences", order=3),
            _dict("https://d.example.com/[LUTE]", "sentences", hidden=True, order=4),
        ]
    )

    assert [u["url"] for u in lang.active_dict_uris("terms")] == [
        "https://a.example.com/[LUTE]"
    ]
    assert [u["url"] for u in lang.active_dict_uris("sentences")] == [
        "https://c.example.com/[LUTE]"
    ]


def test_hidden_dict_keeps_its_sort_order_for_a_later_unhide(app_context, empty_db):
    "Un-hiding must put the tab back where it was, not at the end."
    lang = _language_with(
        [
            _dict("https://a.example.com/[LUTE]", "terms", order=1),
            _dict("https://b.example.com/[LUTE]", "terms", hidden=True, order=2),
        ]
    )
    hidden = next(d for d in lang.dictionaries if d.is_hidden)

    hidden.is_hidden = False
    db.session.commit()

    assert [u["url"] for u in lang.active_dict_uris("terms")] == [
        "https://a.example.com/[LUTE]",
        "https://b.example.com/[LUTE]",
    ]


def test_the_setting_survives_a_reload(app_context, empty_db):
    lang = _language_with([_dict("https://a.example.com/[LUTE]", hidden=True)])
    langid = lang.id
    db.session.expunge(lang)

    reloaded = db.session.get(Language, langid)
    assert reloaded.dictionaries[0].is_hidden is True


def test_hidden_dicts_are_absent_from_the_reading_page_tabs(
    client, app_context, empty_db, japanese
):
    "The reading page builds its strip from the same filtered list."
    for d in japanese.dictionaries:
        d.is_hidden = True
    db.session.commit()

    book = make_book("Hidden tab test", "たのしい ひさしぶり", japanese)
    db.session.add(book)
    db.session.commit()

    html = client.get(f"/read/{book.id}").data.decode("utf-8")
    marker = "LookupButton.TERM_DICTS = "
    start = html.index(marker) + len(marker)
    assert html[start : html.index(";", start)].strip() == "[]"
