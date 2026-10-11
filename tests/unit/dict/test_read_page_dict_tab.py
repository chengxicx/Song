"""
The reading page's local dictionary tab.

The bottom dictionary strip is built in the browser from
LUTE_TERM_DICTS, which the server fills in (see _term_dicts_for in
lute/read/routes.py).  The local dictionary is injected there rather than
configured per language, so these tests cover the injection, the tab's
own label, and the frame the tab loads.
"""

import os
import time
from urllib.parse import quote

from lute.db import db
from lute.dict import service
from lute.models.term import Term

from tests.utils import make_book
from tests.unit.dict.mdx_fixture import build_mdx

ENTRIES = [
    ("たのしい", "<div>楽しい<br/>愉快yúkuài</div>"),
    ("ひさしぶり", "<div>久し振り<br/>好久不见</div>"),
]


def _import(app, scratch):
    "Publish a fixture dictionary and wait for it."
    path = os.path.join(scratch, "fixture.mdx")
    with open(path, "wb") as f:
        f.write(build_mdx(ENTRIES))
    task_id = service.start_import(app, path, "fixture.mdx")
    deadline = time.time() + 10
    while time.time() < deadline:
        task = service.task_status(task_id)
        if task["state"] == "finished":
            return
        assert task["state"] != "error", task["error"]
        time.sleep(0.01)
    raise AssertionError("import never finished")


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
    assert "localdict" not in _term_dicts_json(client, book)


def test_the_local_dictionary_is_the_first_tab(
    client, app, app_context, dict_dirs, japanese
):
    _import(app, dict_dirs[1])
    book = _make_book(japanese)

    dicts = _term_dicts_json(client, book)

    assert "/read/localdict/" in dicts
    # First, so the one tab that works offline is the default lookup.
    assert dicts.strip().startswith("[{")
    assert "/read/localdict/" in dicts[: dicts.index("}")]
    assert "'label': 'Local'" in dicts


def test_the_local_tab_url_carries_the_language_and_a_placeholder(
    client, app, app_context, dict_dirs, japanese
):
    _import(app, dict_dirs[1])
    book = _make_book(japanese)

    dicts = _term_dicts_json(client, book)

    assert f"/read/localdict/{japanese.id}/[LUTE]" in dicts


def test_the_local_tab_frame_shows_the_gloss(
    client, app, app_context, dict_dirs, japanese
):
    _import(app, dict_dirs[1])

    html = client.get(f"/read/localdict/{japanese.id}/{quote('たのしい')}").data.decode(
        "utf-8"
    )

    assert "楽しい" in html
    assert "愉快yúkuài" in html
    assert "Not in the installed dictionary" not in html


def test_the_local_tab_frame_uses_the_term_reading(
    client, app, app_context, dict_dirs, japanese
):
    "A kanji spelling finds the kana-keyed entry through the reading."
    _import(app, dict_dirs[1])
    term = Term(japanese, "楽しい")
    term.romanization = "たのしい"
    db.session.add(term)
    db.session.commit()

    html = client.get(f"/read/localdict/{japanese.id}/{quote('楽しい')}").data.decode(
        "utf-8"
    )

    assert "愉快yúkuài" in html


def test_the_local_tab_frame_says_so_for_an_unknown_word(
    client, app, app_context, dict_dirs, japanese
):
    _import(app, dict_dirs[1])

    html = client.get(f"/read/localdict/{japanese.id}/{quote('存在しない')}").data.decode(
        "utf-8"
    )

    assert "Not in the installed dictionary" in html


def test_the_local_tab_frame_escapes_the_gloss(
    client, app, app_context, dict_dirs, japanese
):
    "The gloss is dictionary text: it must not be rendered as markup."
    path = os.path.join(dict_dirs[1], "html.mdx")
    with open(path, "wb") as f:
        f.write(
            build_mdx([("あぶない", "<div>&lt;script&gt;alert(1)&lt;/script&gt;</div>")])
        )
    task_id = service.start_import(app, path, "html.mdx")
    deadline = time.time() + 10
    while (
        time.time() < deadline and service.task_status(task_id)["state"] != "finished"
    ):
        time.sleep(0.01)

    html = client.get(f"/read/localdict/{japanese.id}/{quote('あぶない')}").data.decode(
        "utf-8"
    )

    assert "&lt;script&gt;" in html
    assert "<script>alert(1)</script>" not in html
