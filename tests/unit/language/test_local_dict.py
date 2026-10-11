"""
The per-language local (MDX) dictionary setting.

The dictionaries themselves are a pool on the server (see lute/dict):
this page only chooses one of them, by file name.  Two rules matter
here, and they are the same rules the parser dropdown follows:

- a stored file name must stay selectable even when it is no longer in
  the pool, otherwise opening and saving the page would silently drop
  the setting (and the submitted value would be rejected);
- "none" is always offered, because a language with no local dictionary
  is a normal state, not an error.
"""

import os
import shutil
import tempfile

import pytest

from lute.db import db
from lute.dict import service
from lute.language.routes import _set_local_dict_choices
from lute.language.forms import LanguageForm
from lute.models.language import Language


@pytest.fixture(name="pool")
def fixture_pool(app, monkeypatch):
    """
    A scratch static folder with an empty dictionary pool in it.

    Not pytest's tmp_path: the sandbox this repo is developed in cannot
    create directories under the system temp root (see
    tests/unit/dict/conftest.py).  The files are empty -- the dropdown
    only cares that they exist, and nothing here opens them.
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


def _values(field):
    return [value for value, _label in field.choices]


def _labels(field):
    return {value: label for value, label in field.choices}


# --- the model --------------------------------------------------------------


def test_a_new_language_has_no_dictionary(app_context):
    lang = Language()

    assert lang.local_dict == ""
    assert lang.term_popup_show_dict_gloss is True


def test_the_choice_persists_across_a_reload(app_context, empty_db):
    lang = Language()
    lang.name = "Local Dict Persist"
    lang.local_dict = "ja_zh_shogakukan_v2.db"
    lang.term_popup_show_dict_gloss = False
    db.session.add(lang)
    db.session.commit()
    langid = lang.id
    db.session.expunge(lang)

    reloaded = db.session.get(Language, langid)
    assert reloaded.local_dict == "ja_zh_shogakukan_v2.db"
    assert reloaded.term_popup_show_dict_gloss is False


# --- the dropdown -----------------------------------------------------------


def test_the_dropdown_offers_none_plus_the_pool(app_context, pool):
    _touch(pool, service.DB_FILENAME, "ko_zh_basic.db")
    form = LanguageForm()

    _set_local_dict_choices(form, Language())

    assert _values(form.local_dict) == ["", service.DB_FILENAME, "ko_zh_basic.db"]
    # The phone's file is what a phone downloads with no setup.
    assert "phone default" in _labels(form.local_dict)[service.DB_FILENAME]


def test_the_dropdown_offers_only_none_when_the_pool_is_empty(app_context, pool):
    form = LanguageForm()

    _set_local_dict_choices(form, Language())

    assert _values(form.local_dict) == [""]


def test_the_dropdown_keeps_the_current_value_when_the_file_is_gone(app_context, pool):
    "Removing a file must not silently drop the language's setting."
    lang = Language()
    lang.local_dict = "deleted.db"
    form = LanguageForm()

    _set_local_dict_choices(form, lang)

    assert "deleted.db" in _values(form.local_dict)
    assert "not installed" in _labels(form.local_dict)["deleted.db"]


def test_the_dropdown_choices_are_not_shared_between_forms(app_context, pool):
    "Assigning choices must not mutate the field's class-level default."
    _touch(pool, "a.db")
    form = LanguageForm()
    _set_local_dict_choices(form, Language())

    assert LanguageForm().local_dict.choices == [("", "-- none --")]


# --- the page ---------------------------------------------------------------


def _edit_url(language):
    return f"/language/edit/{language.id}"


def _tag_for(html, name):
    "The whole <input ...> tag carrying this name."
    marker = f'name="{name}"'
    start = html.rindex("<", 0, html.index(marker))
    return html[start : html.index(">", start)]


def test_the_edit_page_shows_the_dropdown(client, app_context, pool, japanese):
    _touch(pool, service.DB_FILENAME)
    japanese.local_dict = service.DB_FILENAME
    db.session.commit()

    html = client.get(_edit_url(japanese)).data.decode("utf-8")

    assert "Local dictionary (MDX)" in html
    assert 'name="local_dict"' in html
    # The stored choice is what the dropdown opens on.
    assert f'<option selected value="{service.DB_FILENAME}">' in html
    assert 'name="term_popup_show_dict_gloss"' in html


def test_the_edit_page_says_so_when_the_pool_is_empty(
    client, app_context, pool, japanese
):
    html = client.get(_edit_url(japanese)).data.decode("utf-8")

    assert "No dictionary has been imported on this server yet" in html


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
        "local_dict": "",
        "dictionaries-0-usefor": "terms",
        "dictionaries-0-dicttype": "embeddedhtml",
        "dictionaries-0-dicturi": "https://example.com/[LUTE]",
        "dictionaries-0-is_active": "y",
        "dictionaries-0-sort_order": "1",
        "dictionaries-1-usefor": "sentences",
        "dictionaries-1-dicttype": "embeddedhtml",
        "dictionaries-1-dicturi": "https://example.com/sentence/[LUTE]",
        "dictionaries-1-is_active": "y",
        "dictionaries-1-sort_order": "2",
    }
    data.update(overrides)
    return data


def test_saving_the_choice_stores_it(client, app_context, pool, japanese):
    _touch(pool, "ko_zh_basic.db")

    client.post(
        _edit_url(japanese),
        data=_form_data(japanese, local_dict="ko_zh_basic.db"),
    )

    assert db.session.get(Language, japanese.id).local_dict == "ko_zh_basic.db"


def test_saving_none_clears_the_choice(client, app_context, pool, japanese):
    _touch(pool, "ko_zh_basic.db")
    japanese.local_dict = "ko_zh_basic.db"
    db.session.commit()

    client.post(_edit_url(japanese), data=_form_data(japanese, local_dict=""))

    assert db.session.get(Language, japanese.id).local_dict == ""


def test_saving_the_popup_switch_stores_it(client, app_context, pool, japanese):
    _touch(pool, "ko_zh_basic.db")

    # An unchecked checkbox is simply absent from the POST.
    client.post(_edit_url(japanese), data=_form_data(japanese))

    assert db.session.get(Language, japanese.id).term_popup_show_dict_gloss is False


def test_the_switch_is_checked_on_by_default(client, app_context, pool, japanese):
    html = client.get(_edit_url(japanese)).data.decode("utf-8")

    assert "checked" in _tag_for(html, "term_popup_show_dict_gloss")
