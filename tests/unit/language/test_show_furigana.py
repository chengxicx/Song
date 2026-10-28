"""
Tests for the per-language "show furigana" setting.

The toggle is a plain boolean column on languages, rendered on the
language edit form only for Japanese-capable parsers (see
templates/language/_form.html).  The reading page consumes it through
lute-furigana.js, which reuses the shadowing panel's readings endpoint.
"""

import os
import re

import lute
from lute.db import db
from lute.models.language import Language
from lute.language.forms import LanguageForm

_READ_TEMPLATES = os.path.join(os.path.dirname(lute.__file__), "templates", "read")


def _template_source(name):
    "Source of a read template, for the wiring checks below."
    with open(os.path.join(_READ_TEMPLATES, name), "r", encoding="utf-8") as f:
        return f.read()


def test_furigana_script_is_only_loaded_when_the_toggle_is_on():
    """
    "Off" has to mean "absent", not "present and ignored".

    lute-furigana.js annotates on load without looking at
    LUTE_FURIGANA.enabled -- that is the contract epub_render.html spells
    out ("the script self-annotates unconditionally ... so 'off' must
    mean 'absent'").  The reading page loaded it unconditionally, which
    made the per-language checkbox a no-op: every Japanese page drew ruby
    whether or not the user had asked for it.
    """
    src = _template_source("index.html")
    assert src.count("js/lute-furigana.js") == 1, "included exactly once"

    guarded = re.search(
        r"{%\s*if\s+show_furigana\s*%}(?:(?!{%\s*endif\s*%}).)*js/lute-furigana\.js",
        src,
        re.DOTALL,
    )
    assert guarded is not None, "the include must sit inside {% if show_furigana %}"


def test_show_furigana_defaults_to_false(app_context):
    "A new language draws no furigana."
    lang = Language()
    assert lang.show_furigana is False


def test_show_furigana_persists_across_reload(app_context, empty_db):
    "The setting survives a commit and a fresh read."
    lang = Language()
    lang.name = "Furigana Persist"
    lang.show_furigana = True
    db.session.add(lang)
    db.session.commit()
    langid = lang.id
    db.session.expunge(lang)

    reloaded = db.session.get(Language, langid)
    assert reloaded.show_furigana is True


def test_form_includes_show_furigana_field(app_context):
    "The language form exposes the toggle so populate_obj round-trips it."
    form = LanguageForm()
    assert "show_furigana" in form._fields


def test_migration_column_is_known_to_the_schema(app_context, empty_db):
    "The LgShowFurigana column exists (migration applied on the test db)."
    from sqlalchemy import inspect

    cols = [c["name"] for c in inspect(db.engine).get_columns("languages")]
    assert "LgShowFurigana" in cols
