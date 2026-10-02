"""
Tests for the per-language "hidden grammar levels" setting.

The setting is one pipe-delimited string column behind a list property,
so that WTForms can submit a list of checked boxes.
"""

from lute.db import db
from lute.models.language import Language


def test_hidden_grammar_levels_defaults_to_empty(app_context):
    "A new language hides nothing."
    lang = Language()
    assert lang.hidden_grammar_levels == []
    assert lang._hidden_grammar_levels is None


def test_hidden_grammar_levels_list_round_trips_through_column(app_context):
    "The list property maps onto the single string column."
    lang = Language()

    lang.hidden_grammar_levels = ["N5", "basic_forms"]
    assert lang._hidden_grammar_levels == "N5|basic_forms"
    assert lang.hidden_grammar_levels == ["N5", "basic_forms"]

    lang.hidden_grammar_levels = []
    assert lang._hidden_grammar_levels == ""
    assert lang.hidden_grammar_levels == []

    lang.hidden_grammar_levels = "A1|A2"
    assert lang.hidden_grammar_levels == ["A1", "A2"]

    # Legacy / unset rows read as NULL.
    lang._hidden_grammar_levels = None
    assert lang.hidden_grammar_levels == []


def test_hidden_grammar_levels_blank_tokens_are_dropped(app_context):
    "Empty and whitespace-only entries never reach the column."
    lang = Language()
    lang.hidden_grammar_levels = ["N5", "", "   ", "A1"]
    assert lang.hidden_grammar_levels == ["N5", "A1"]


def test_hidden_grammar_levels_persists_across_reload(app_context, empty_db):
    "The setting survives a commit and a fresh read."
    lang = Language()
    lang.name = "Hidden Levels Persist"
    lang.hidden_grammar_levels = ["N5", "basic_particles"]
    db.session.add(lang)
    db.session.commit()
    langid = lang.id
    db.session.expunge(lang)

    reloaded = db.session.get(Language, langid)
    assert reloaded.hidden_grammar_levels == ["N5", "basic_particles"]
