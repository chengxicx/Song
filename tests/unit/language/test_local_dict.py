"""
Local (MDX) dictionaries as ordinary dictionary rows.

The dictionaries themselves are a pool on the server (see lute/dict); a
language uses one or more of them via 'localdict' rows in its regular
dictionary list -- several per language, ordered and hidden like every
other tab.  The row's URL is a pool file name, so it is held to the
pool's own name rules (it round-trips into filesystem paths).
"""

import json
import os
import re
import shutil
import tempfile

import pytest

from lute.db import db
from lute.dict import service
from lute.models.language import Language, LanguageDictionary


@pytest.fixture(name="pool")
def fixture_pool(app, monkeypatch):
    """
    A scratch static folder with an empty dictionary pool in it.

    Not pytest's tmp_path: the sandbox this repo is developed in cannot
    create directories under the system temp root (see
    tests/unit/dict/conftest.py).  The files are empty -- the form and
    tab code only care that they exist, nothing here looks words up.
    """
    root = tempfile.mkdtemp(dir=os.path.dirname(__file__))
    static_dir = os.path.join(root, "static")
    dict_dir = os.path.join(static_dir, "dict")
    os.makedirs(dict_dir)
    monkeypatch.setattr(app, "static_folder", static_dir)
    yield dict_dir
    shutil.rmtree(root, ignore_errors=True)


def _touch(dict_dir, *names):
    for name in names:
        with open(os.path.join(dict_dir, name), "w", encoding="utf-8") as f:
            f.write("")


def _add_local_row(lang, filename, order=0, hidden=False):
    "Attach one local dictionary row to a language."
    d = LanguageDictionary()
    d.usefor = "terms"
    d.dicttype = "localdict"
    d.dicturi = filename
    d.is_active = True
    d.is_hidden = hidden
    d.sort_order = order
    lang.dictionaries.append(d)
    db.session.add(lang)
    db.session.commit()
    return d


def _add_online_row(lang, url="https://example.com/[LUTE]", order=0):
    "Attach one ordinary (online) dictionary row to a language."
    d = LanguageDictionary()
    d.usefor = "terms"
    d.dicttype = "embeddedhtml"
    d.dicturi = url
    d.is_active = True
    d.sort_order = order
    lang.dictionaries.append(d)
    db.session.add(lang)
    db.session.commit()
    return d


def _fresh_language(name="Dropdown Lang"):
    "A language whose dictionary rows start at index 0."
    lang = Language()
    lang.name = name
    lang.parser_type = "spacedel"
    db.session.add(lang)
    db.session.commit()
    return lang


def _input_tag(html, index):
    "The rendered URL text input of one dictionary row."
    m = re.search(r'<input[^>]*name="dictionaries-%d-dicturi"[^>]*>' % index, html)
    return m.group(0) if m else ""


def _select_block(html, index):
    "The rendered URL dropdown of one dictionary row, options included."
    m = re.search(
        r'<select[^>]*name="dictionaries-%d-dicturi".*?</select>' % index,
        html,
        re.DOTALL,
    )
    return m.group(0) if m else ""


# --- the model --------------------------------------------------------------


def test_a_new_language_has_no_local_dictionaries(app_context):
    lang = Language()

    assert lang.term_popup_show_dict_gloss is True
    assert lang.term_translation_source == "online"
    assert lang.local_dict_files() == []


def test_local_dict_files_follow_the_tab_order(app_context, empty_db):
    "The popup gloss and the term fill try the dictionaries in tab order."
    lang = Language()
    lang.name = "Local Rows Order"
    db.session.add(lang)
    db.session.commit()

    _add_local_row(lang, "b.db", order=5)
    _add_local_row(lang, "a.db", order=2)
    _add_local_row(lang, "hidden.db", order=1, hidden=True)
    # The relationship sorts by sort_order, so keep the row object.
    inactive = _add_local_row(lang, "inactive.db", order=0)
    inactive.is_active = False
    db.session.commit()

    assert lang.local_dict_files() == ["a.db", "b.db"]


def test_the_row_persists_across_a_reload(app_context, empty_db):
    lang = Language()
    lang.name = "Local Rows Persist"
    db.session.add(lang)
    db.session.commit()
    _add_local_row(lang, "ja_zh_shogakukan_v2.db")
    lang.term_translation_source = "local_dict"
    db.session.commit()
    langid = lang.id
    db.session.expunge(lang)

    reloaded = db.session.get(Language, langid)
    assert reloaded.term_translation_source == "local_dict"
    assert reloaded.local_dict_files() == ["ja_zh_shogakukan_v2.db"]


def test_backup_round_trip_keeps_local_rows(app_context, empty_db):
    "A language export/import keeps its local dictionaries."
    lang = Language()
    lang.name = "Local Rows Round Trip"
    db.session.add(lang)
    db.session.commit()
    _add_local_row(lang, "a.db", order=3)

    data = lang.to_dict()
    assert data["dictionaries"] == [
        {"for": "terms", "type": "localdict", "url": "a.db", "active": True}
    ]

    clone = Language.from_dict(data)
    assert [(d.dicttype, d.dicturi) for d in clone.dictionaries] == [
        ("localdict", "a.db")
    ]


# --- the page ---------------------------------------------------------------


def _edit_url(language):
    return f"/language/edit/{language.id}"


def _form_data(language, **overrides):
    "The minimum the language form needs to save."
    data = {
        "name": language.name,
        "parser_type": language.parser_type,
        "character_substitutions": language.character_substitutions,
        "regexp_split_sentences": language.regexp_split_sentences,
        "exceptions_split_sentences": language.exceptions_split_sentences,
        "word_characters": language.word_characters,
        "tts_lang": language.tts_lang or "",
        "translate_target_lang": language.translate_target_lang or "",
        "grammar_translate_lang": language.grammar_translate_lang or "en",
        "dictionaries-0-usefor": "terms",
        "dictionaries-0-dicttype": "embeddedhtml",
        "dictionaries-0-dicturi": "https://example.com/[LUTE]",
        "dictionaries-0-is_active": "y",
        "dictionaries-0-sort_order": "1",
        "dictionaries-1-usefor": "terms",
        "dictionaries-1-dicttype": "localdict",
        "dictionaries-1-dicturi": "ko_zh_basic.db",
        "dictionaries-1-is_active": "y",
        "dictionaries-1-sort_order": "2",
        "dictionaries-2-usefor": "sentences",
        "dictionaries-2-dicttype": "embeddedhtml",
        "dictionaries-2-dicturi": "https://example.com/sentence/[LUTE]",
        "dictionaries-2-is_active": "y",
        "dictionaries-2-sort_order": "3",
    }
    data.update(overrides)
    return data


def test_the_edit_page_shows_the_local_section(client, app_context, pool, japanese):
    _touch(pool, service.DB_FILENAME)
    japanese.term_translation_source = "local_dict"
    db.session.commit()

    html = client.get(_edit_url(japanese)).data.decode("utf-8")

    assert "Local dictionaries (MDX)" in html
    assert "ja_zh_shogakukan_v2.db" in html, "datalist should suggest pool files"
    assert 'name="term_popup_show_dict_gloss"' in html
    assert 'name="term_translation_source"' in html
    assert "local_dict_position" not in html, "superseded by the row order"


def test_the_edit_page_says_so_when_the_pool_is_empty(
    client, app_context, pool, japanese
):
    html = client.get(_edit_url(japanese)).data.decode("utf-8")

    assert "No dictionary has been imported on this server yet" in html


def test_saving_a_local_row_stores_it(client, app_context, pool, japanese):
    _touch(pool, "ko_zh_basic.db")

    client.post(
        _edit_url(japanese),
        data=_form_data(japanese, term_translation_source="local_dict"),
    )

    reloaded = db.session.get(Language, japanese.id)
    assert "ko_zh_basic.db" in reloaded.local_dict_files()
    assert reloaded.term_translation_source == "local_dict"


def test_a_local_row_needs_a_pool_file_name(client, app_context, pool, japanese):
    "The URL of a Local row lands in filesystem paths: validate the name."

    response = client.post(
        _edit_url(japanese),
        data=_form_data(japanese, **{"dictionaries-1-dicturi": "../escape"}),
    )

    assert response.status_code == 200
    html = response.data.decode("utf-8")
    assert "not a valid Local dictionary" in html
    assert db.session.get(Language, japanese.id).local_dict_files() == []


def test_the_switch_is_checked_on_by_default(client, app_context, pool, japanese):
    html = client.get(_edit_url(japanese)).data.decode("utf-8")

    assert "checked" in html


# --- the Local row's dropdown -----------------------------------------------
#
# A Local row's URL used to be a text input whose value was the pool file
# name, with the installed files offered as autocomplete suggestions.  A
# browser only offers those when the field is empty, so a row that
# already held a dictionary showed nothing on click -- the dictionary
# could not be changed from the page at all.  The row's URL is now a
# real dropdown, rendered with the row's own file selected.  Both
# controls exist per row; the one that does not apply is disabled, so a
# row still submits exactly one dicturi.


def test_an_existing_local_row_is_a_dropdown_with_its_file_selected(
    client, app_context, pool
):
    _touch(pool, "ko_zh_basic.db", service.DB_FILENAME)
    lang = _fresh_language("Dropdown Local")
    _add_local_row(lang, "ko_zh_basic.db")

    html = client.get(_edit_url(lang)).data.decode("utf-8")

    block = _select_block(html, 0)
    assert block, "a Local row's URL is a dropdown"
    assert 'value="ko_zh_basic.db"' in block
    assert "selected" in block, "the row's dictionary is preselected"
    assert "disabled" not in block.split(">")[0], "the dropdown itself is usable"
    assert "disabled" in _input_tag(html, 0), "the text input is out of the way"


def test_an_online_row_keeps_its_text_input(client, app_context, pool):
    _touch(pool, "ko_zh_basic.db")
    lang = _fresh_language("Dropdown Online")
    _add_online_row(lang)

    html = client.get(_edit_url(lang)).data.decode("utf-8")

    assert "disabled" not in _input_tag(html, 0), "the URL input stays usable"
    block = _select_block(html, 0)
    assert block, "the dropdown is rendered but not for this row's type"
    assert "disabled" in block.split(">")[0], "and it is disabled"


def test_the_dropdown_labels_name_the_dictionary(client, app_context, pool):
    """
    The option labels carry the dictionary's title, not the pool file
    name -- which for an .mdx uploaded under a non-ASCII name is a
    mangled ASCII stand-in nobody recognises.
    """
    _touch(pool, "moji-b4-c7-ca-e9.db")
    with open(
        os.path.join(pool, "moji-b4-c7-ca-e9.db.json"), "w", encoding="utf-8"
    ) as f:
        json.dump({"title": "MOJi辞書", "entries": 107314, "terms": 251874}, f)
    lang = _fresh_language("Dropdown Label")
    _add_local_row(lang, "moji-b4-c7-ca-e9.db")

    html = client.get(_edit_url(lang)).data.decode("utf-8")

    block = _select_block(html, 0)
    assert "MOJi辞書" in block, "the title is the option label"
    assert "107,314 entries" in block
    assert 'value="moji-b4-c7-ca-e9.db"' in block, "the file name is the value"


def test_a_local_row_pointing_at_a_missing_file_keeps_it_selectable(
    client, app_context, pool
):
    """
    The file is gone from the pool: the row still shows it, so opening
    the page and saving does not silently drop the dictionary.
    """
    _touch(pool, service.DB_FILENAME)
    lang = _fresh_language("Dropdown Stale")
    _add_local_row(lang, "gone_from_the_pool.db")

    html = client.get(_edit_url(lang)).data.decode("utf-8")

    block = _select_block(html, 0)
    assert "(not installed)" in block
    assert 'value="gone_from_the_pool.db"' in block
    assert "selected" in block
    assert "disabled" in _input_tag(html, 0)


def test_no_dropdown_is_rendered_when_the_pool_is_empty(client, app_context, pool):
    "Nothing imported yet: the row stays a text input, as the page says."
    lang = _fresh_language("Dropdown Empty Pool")
    _add_local_row(lang, "not_installed_anywhere.db")

    html = client.get(_edit_url(lang)).data.decode("utf-8")

    assert '<select class="dict-uri' not in html
    assert "disabled" not in _input_tag(html, 0)
    assert "No dictionary has been imported on this server yet" in html


def test_the_new_language_page_renders_the_dropdown_pair(client, app_context, pool):
    """
    The new-language page's hidden template row carries both controls,
    disabled on the dropdown, so a row cloned from it starts as an
    online URL input and the swap has a dropdown to switch to.
    """
    _touch(pool, service.DB_FILENAME)

    html = client.get("/language/new").data.decode("utf-8")

    assert _input_tag(html, 0), "the template row's input is rendered"
    assert "disabled" not in _input_tag(html, 0)
    block = _select_block(html, 0)
    assert block, "the template row's dropdown is rendered"
    assert "disabled" in block.split(">")[0]


def test_a_failed_save_keeps_the_row_in_dropdown_state(client, app_context, pool):
    """
    A validation error re-renders the page: a Local row stays a dropdown
    holding the value the user chose, disabled input and all.
    """
    _touch(pool, "ko_zh_basic.db")
    lang = _fresh_language("Dropdown Resubmit")
    _add_local_row(lang, "ko_zh_basic.db")

    response = client.post(
        _edit_url(lang),
        data=_form_data(
            lang,
            **{
                "dictionaries-0-dicttype": "localdict",
                "dictionaries-0-dicturi": "../escape",
            },
        ),
    )

    assert response.status_code == 200
    html = response.data.decode("utf-8")
    assert "not a valid Local dictionary" in html
    block = _select_block(html, 0)
    assert "(not installed)" in block and "../escape" in block
    assert "disabled" in _input_tag(html, 0)
    assert db.session.get(Language, lang.id).local_dict_files() == [
        "ko_zh_basic.db"
    ], "the failed save changed nothing"
