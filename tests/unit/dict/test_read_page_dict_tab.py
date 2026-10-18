"""
The reading page's local dictionary tabs.

The bottom dictionary strip is built in the browser from
LUTE_TERM_DICTS, which the server fills in (see _term_dicts_for in
lute/read/routes.py).  Local dictionaries are 'localdict' rows in the
language's regular dictionary list (Language.active_dict_uris converts
them), so several are allowed and they order with the online tabs.
"""

import os
import time
from urllib.parse import quote

from lute.db import db
from lute.dict import service
from lute.models.language import LanguageDictionary
from lute.models.term import Term

from tests.utils import make_book
from tests.unit.dict.mdx_fixture import build_mdx

ENTRIES = [
    ("たのしい", "<div>楽しい<br/>愉快yúkuài</div>"),
    ("ひさしぶり", "<div>久し振り<br/>好久不见</div>"),
]

# A neutral pool file name -- no name is special on the server any more.
DICT = "test_dict.db"


def _import(app, scratch, entries=ENTRIES, name="fixture.mdx", target=None):
    "Publish a fixture dictionary and wait for it."
    path = os.path.join(scratch, name)
    with open(path, "wb") as f:
        f.write(build_mdx(entries))
    task_id = service.start_import(app, path, name, target)
    deadline = time.time() + 10
    while time.time() < deadline:
        task = service.task_status(task_id)
        if task["state"] == "finished":
            return
        assert task["state"] != "error", task["error"]
        time.sleep(0.01)
    raise AssertionError("import never finished")


def _add_local(language, filename, order=0, hidden=False):
    "Give the language a local dictionary row (the tab's sort position)."
    d = LanguageDictionary()
    d.usefor = "terms"
    d.dicttype = "localdict"
    d.dicturi = filename
    d.is_active = True
    d.is_hidden = hidden
    d.sort_order = order
    language.dictionaries.append(d)
    db.session.commit()
    return d


def _use(language, filename, order=0):
    "Point a language at one of the pool's dictionaries."
    return _add_local(language, filename, order=order)


def _make_book(language):
    book = make_book("T", "たのしい ひさしぶり", language)
    db.session.add(book)
    db.session.commit()
    return book


def _term_dicts_json(client, book):
    "The LUTE_TERM_DICTS literal out of the rendered reading page."
    html = client.get(f"/read/{book.id}").data.decode("utf-8")
    marker = "LookupButton.TERM_DICTS = "
    start = html.index(marker) + len(marker)
    return html[start : html.index(";", start)]


def test_no_local_tab_without_a_dictionary(client, app_context, dict_dirs, japanese):
    book = _make_book(japanese)
    assert "/read/localdict/" not in _term_dicts_json(client, book)


def test_no_local_tab_when_the_language_has_no_local_row(
    client, app, app_context, dict_dirs, japanese
):
    """
    An imported dictionary on its own is not enough: the language has to
    have a Local dictionary row.  Otherwise a Korean language would be
    offered the Japanese dictionary again, which is what the rows
    replace.
    """
    _import(app, dict_dirs[1], target=DICT)
    book = _make_book(japanese)

    assert "/read/localdict/" not in _term_dicts_json(client, book)


def test_no_local_tab_when_the_chosen_file_is_gone(
    client, app, app_context, dict_dirs, japanese
):
    "The row's file was removed from the pool: no tab, no crash."
    _import(app, dict_dirs[1], target="gone.db")
    _use(japanese, "gone.db")
    service.remove("gone.db")
    book = _make_book(japanese)

    assert "/read/localdict/" not in _term_dicts_json(client, book)


def test_the_local_dictionary_can_be_the_first_tab(
    client, app, app_context, dict_dirs, japanese
):
    "A Local row at sort order 0 opens the strip (the offline-capable tab)."
    _import(app, dict_dirs[1], target=DICT)
    _use(japanese, DICT, order=0)
    book = _make_book(japanese)

    dicts = _term_dicts_json(client, book)

    assert "/read/localdict/" in dicts
    assert dicts.strip().startswith("[{")
    assert "/read/localdict/" in dicts[: dicts.index("}")]
    # The tab names itself after the dictionary, not a generic "Local".
    assert "'label'" in dicts
    assert "jisho.org" not in dicts[: dicts.index("}")]


def test_the_local_tab_url_carries_the_language_file_and_placeholder(
    client, app, app_context, dict_dirs, japanese
):
    _import(app, dict_dirs[1], target=DICT)
    _use(japanese, DICT)
    book = _make_book(japanese)

    dicts = _term_dicts_json(client, book)

    assert f"/read/localdict/{japanese.id}/{DICT}/[LUTE]" in dicts


def test_local_tabs_follow_the_row_order(client, app, app_context, dict_dirs, japanese):
    """
    Several local dictionaries per language, positioned by drag-and-drop
    like the online tabs: order 0 opens the strip, order 99 trails it.
    """
    _import(app, dict_dirs[1], target=DICT)
    _import(
        app,
        dict_dirs[1],
        entries=[("たのしい", "<div>第二本词典</div>")],
        name="second.mdx",
        target="second.db",
    )
    _use(japanese, DICT, order=0)
    _use(japanese, "second.db", order=99)
    book = _make_book(japanese)

    dicts = _term_dicts_json(client, book)

    first = dicts.index(f"/read/localdict/{japanese.id}/{DICT}/")
    online = dicts.index("jisho.org")
    second = dicts.index(f"/read/localdict/{japanese.id}/second.db/")
    assert first < online < second


def test_a_hidden_local_row_offers_no_tab(
    client, app, app_context, dict_dirs, japanese
):
    _import(app, dict_dirs[1], target=DICT)
    # The relationship sorts by sort_order, so keep the row object.
    row = _use(japanese, DICT)
    row.is_hidden = True
    db.session.commit()
    book = _make_book(japanese)

    assert "/read/localdict/" not in _term_dicts_json(client, book)


# --- the JSON lookup the term form's auto-fill uses -------------------------


def test_the_lookup_endpoint_returns_the_gloss(
    client, app, app_context, dict_dirs, japanese
):
    _import(app, dict_dirs[1], target=DICT)
    _use(japanese, DICT)

    data = client.get(
        f"/read/localdict_lookup/{japanese.id}/{quote('たのしい')}"
    ).get_json()

    assert data["gloss"] is not None
    assert "楽しい" in data["gloss"]


def test_the_lookup_endpoint_tries_the_dictionaries_in_tab_order(
    client, app, app_context, dict_dirs, japanese
):
    "Two local dictionaries: the first one with an entry answers."
    # The tab-order-first dictionary has no たのしい entry.
    _import(
        app,
        dict_dirs[1],
        entries=[("ひさしぶり", "<div>好久不见</div>")],
        name="narrow.mdx",
        target="narrow.db",
    )
    _import(app, dict_dirs[1], target="wide.db")
    _use(japanese, "narrow.db", order=0)
    _use(japanese, "wide.db", order=1)

    data = client.get(
        f"/read/localdict_lookup/{japanese.id}/{quote('たのしい')}"
    ).get_json()

    assert "楽しい" in data["gloss"]
    assert "好久不见" not in data["gloss"]


def test_the_lookup_endpoint_uses_the_term_reading(
    client, app, app_context, dict_dirs, japanese
):
    "A kanji spelling finds the kana-keyed entry, same as the Local tab."
    _import(app, dict_dirs[1], target=DICT)
    _use(japanese, DICT)
    term = Term(japanese, "楽しい")
    term.romanization = "たのしい"
    db.session.add(term)
    db.session.commit()

    data = client.get(f"/read/localdict_lookup/{japanese.id}/{quote('楽しい')}").get_json()

    assert "愉快yúkuài" in data["gloss"]


def test_the_lookup_endpoint_returns_null_for_an_unknown_word(
    client, app, app_context, dict_dirs, japanese
):
    "No entry: gloss null, the caller falls back to online translation."
    _import(app, dict_dirs[1], target=DICT)
    _use(japanese, DICT)

    data = client.get(
        f"/read/localdict_lookup/{japanese.id}/{quote('存在しない')}"
    ).get_json()

    assert data == {"gloss": None}


# --- the frame the tab loads ------------------------------------------------


def test_the_local_tab_frame_shows_the_gloss(
    client, app, app_context, dict_dirs, japanese
):
    _import(app, dict_dirs[1], target=DICT)
    _use(japanese, DICT)

    html = client.get(f"/read/localdict/{japanese.id}/{quote('たのしい')}").data.decode(
        "utf-8"
    )

    assert "楽しい" in html
    assert "愉快yúkuài" in html
    assert "Not in the installed dictionary" not in html


def test_the_named_tab_frame_serves_that_one_dictionary(
    client, app, app_context, dict_dirs, japanese
):
    "The 3-segment URL names the pool file, so each tab reads its own db."
    _import(app, dict_dirs[1], target=DICT)
    _import(
        app,
        dict_dirs[1],
        entries=[("たのしい", "<div>第二本词典</div>")],
        name="second.mdx",
        target="second.db",
    )
    _use(japanese, DICT, order=0)
    _use(japanese, "second.db", order=1)

    first = client.get(
        f"/read/localdict/{japanese.id}/{DICT}/{quote('たのしい')}"
    ).data.decode("utf-8")
    second = client.get(
        f"/read/localdict/{japanese.id}/second.db/{quote('たのしい')}"
    ).data.decode("utf-8")

    assert "愉快yúkuài" in first
    assert "第二本词典" not in first
    assert "第二本词典" in second
    assert "愉快yúkuài" not in second


def test_the_named_tab_frame_refuses_an_out_of_pool_name(
    client, app, app_context, dict_dirs, japanese
):
    "The file name lands in filesystem paths: pool names only."

    # A name with a separator never even routes (werkzeug 404s the
    # traversal); a malformed name routes but matches no pool file.
    resp = client.get(f"/read/localdict/{japanese.id}/..%2Fescape/{quote('たのしい')}")
    assert resp.status_code == 404

    html = client.get(
        f"/read/localdict/{japanese.id}/bad%2Aname.db/{quote('たのしい')}"
    ).data.decode("utf-8")
    assert "Not in the installed dictionary" in html


def test_the_local_tab_frame_uses_the_term_reading(
    client, app, app_context, dict_dirs, japanese
):
    "A kanji spelling finds the kana-keyed entry through the reading."
    _import(app, dict_dirs[1], target=DICT)
    _use(japanese, DICT)
    term = Term(japanese, "楽しい")
    term.romanization = "たのしい"
    db.session.add(term)
    db.session.commit()

    html = client.get(f"/read/localdict/{japanese.id}/{quote('楽しい')}").data.decode(
        "utf-8"
    )

    assert "愉快yúkuài" in html


def test_xref_links_carry_their_reading(client, app, app_context, dict_dirs, japanese):
    "The rendered See-also links pass the reading back as a query param."
    _import(
        app,
        dict_dirs[1],
        entries=[("ありがとう", "<div>See also: 有り難う【ありがとう②】</div>")],
        target=DICT,
    )
    _use(japanese, DICT)

    html = client.get(f"/read/localdict/{japanese.id}/{quote('ありがとう')}").data.decode(
        "utf-8"
    )

    assert "ld-xref" in html
    assert "?reading=" in html


def test_an_xref_click_resolves_the_homograph(
    client, app, app_context, dict_dirs, japanese
):
    "下【か】's link must land on the か entry, not the same list again."
    _import(
        app,
        dict_dirs[1],
        entries=[
            ("下", "<div>See also: 下【か】</div>"),
            ("下【か】", "<div>下级。（位が下のもの。）</div>"),
        ],
        target=DICT,
    )
    _use(japanese, DICT)

    plain = client.get(f"/read/localdict/{japanese.id}/{quote('下')}").data.decode(
        "utf-8"
    )
    clicked = client.get(
        f"/read/localdict/{japanese.id}/{quote('下')}?reading=か"
    ).data.decode("utf-8")

    assert '<a class="ld-xref"' in plain  # the disambiguation list of links
    assert "位が下のもの" in clicked
    assert '<a class="ld-xref"' not in clicked


def test_the_frame_reads_the_languages_own_dictionaries(
    client, app, app_context, dict_dirs, japanese
):
    "The pre-multi-dictionary URL answers from the language's own rows."
    _import(app, dict_dirs[1], target="ja.db")
    _import(
        app,
        dict_dirs[1],
        entries=[("たのしい", "<div>别的词典</div>")],
        name="other.mdx",
        target="other.db",
    )
    _use(japanese, "other.db")

    html = client.get(f"/read/localdict/{japanese.id}/{quote('たのしい')}").data.decode(
        "utf-8"
    )

    assert "别的词典" in html
    assert "愉快yúkuài" not in html


def test_the_local_tab_frame_says_so_for_an_unknown_word(
    client, app, app_context, dict_dirs, japanese
):
    _import(app, dict_dirs[1], target=DICT)
    _use(japanese, DICT)

    html = client.get(f"/read/localdict/{japanese.id}/{quote('存在しない')}").data.decode(
        "utf-8"
    )

    assert "Not in the installed dictionary" in html


def test_the_local_tab_frame_escapes_the_gloss(
    client, app, app_context, dict_dirs, japanese
):
    "The gloss is dictionary text: it must not be rendered as markup."
    _import(
        app,
        dict_dirs[1],
        entries=[("あぶない", "<div>&lt;script&gt;alert(1)&lt;/script&gt;</div>")],
        name="html.mdx",
        target=DICT,
    )
    _use(japanese, DICT)

    html = client.get(f"/read/localdict/{japanese.id}/{quote('あぶない')}").data.decode(
        "utf-8"
    )

    assert "&lt;script&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_the_frame_drops_the_duplicated_headword_line(
    client, app, app_context, dict_dirs, japanese
):
    """
    The card's head is the looked-up term, and a converted definition
    opens with the dictionary's own headword: showing both says the
    same word twice.
    """
    _import(
        app,
        dict_dirs[1],
        entries=[("終わり", "<div>終わり【おわり◎】<br/>名<br/>终，终了。</div>")],
        name="shogakukan.mdx",
        target=DICT,
    )
    _use(japanese, DICT)

    html = client.get(f"/read/localdict/{japanese.id}/{quote('終わり')}").data.decode(
        "utf-8"
    )

    assert "localdict-head" in html and ">終わり<" in html
    assert "【おわり◎】" not in html, "the headword line is a repeat of the head"
    assert "终，终了。" in html


def test_the_frame_keeps_an_entry_found_through_its_reading(
    client, app, app_context, dict_dirs, japanese
):
    """
    The kana spelling was looked up and the entry answers under its
    kanji headword: that headword is part of the answer, not a repeat
    of what was clicked.
    """
    _import(
        app,
        dict_dirs[1],
        entries=[("おわり", "<div>終わり【おわり◎】<br/>名<br/>终，终了。</div>")],
        name="kana.mdx",
        target=DICT,
    )
    _use(japanese, DICT)

    html = client.get(f"/read/localdict/{japanese.id}/{quote('おわり')}").data.decode(
        "utf-8"
    )

    assert "終わり【おわり◎】" in html
    assert "终，终了。" in html
