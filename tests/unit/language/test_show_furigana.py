"""
Tests for the per-language "show furigana" setting.

The toggle is a plain boolean column on languages, rendered on the
language edit form only for Japanese-capable parsers (see
templates/language/_form.html).  The reading page consumes it through
lute-furigana.js, which reuses the shadowing panel's readings endpoint.
"""

from lute.db import db
from lute.models.language import Language
from lute.language.forms import LanguageForm


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
