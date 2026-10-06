"""
Tests for the hideable grammar-group taxonomy and the panel filter.
"""

import json
import os

from lute.models.language import Language
from lute.read.render.grammar_analysis_ja import ALL_LEVELS
from lute.read.render.grammar_levels import (
    filter_hidden_grammar,
    hideable_grammar_groups,
)


def _lang(name, parser_type="spacedel"):
    lang = Language()
    lang.name = name
    lang.parser_type = parser_type
    return lang


def test_japanese_groups_are_jlpt_plus_the_two_aggregates():
    "Japanese offers N5..N1, then the two frequent aggregate rows."
    groups = hideable_grammar_groups(_lang("Japanese", "japanese_sudachi"))
    tokens = [t for t, _ in groups]
    assert tokens[:5] == ["N5", "N4", "N3", "N2", "N1"]
    assert tokens[5:] == ["basic_forms", "basic_particles"]
    labels = dict(groups)
    assert labels["N5"] == "JLPT N5"
    assert labels["basic_forms"] == "Basic forms"
    assert labels["basic_particles"] == "Particles"


def test_korean_groups_are_the_topik_bands():
    "Korean offers the three TOPIK bands its data uses."
    groups = hideable_grammar_groups(_lang("Korean", "lute_korean"))
    assert [t for t, _ in groups] == ["TOPIK 1-2", "TOPIK 3-4", "TOPIK 5-6"]


def test_cefr_engine_groups_are_the_full_band():
    "A language served by a CEFR engine offers A1..C2."
    groups = hideable_grammar_groups(_lang("English"))
    assert [t for t, _ in groups] == ["A1", "A2", "B1", "B2", "C1", "C2"]
    assert dict(groups)["A1"] == "CEFR A1"


def test_language_without_engine_has_nothing_to_hide():
    "The generic regex rules carry no level, so there is nothing to hide."
    assert hideable_grammar_groups(_lang("Turkish")) == []
    assert hideable_grammar_groups(None) == []


def test_filter_drops_by_level_and_by_key():
    "Hiding N5 takes the N5 aggregates too; hiding a key takes only that row."
    results = [
        {"key": "some-n5-point", "level": "N5"},
        {"key": "basic_forms", "level": "N5"},
        {"key": "basic_particles", "level": "N5"},
        {"key": "some-n4-point", "level": "N4"},
    ]

    assert [e["key"] for e in filter_hidden_grammar(results, {"N5"})] == [
        "some-n4-point"
    ]
    assert [e["key"] for e in filter_hidden_grammar(results, {"basic_forms"})] == [
        "some-n5-point",
        "basic_particles",
        "some-n4-point",
    ]
    # Nothing hidden: the list comes back untouched.
    assert filter_hidden_grammar(results, set()) is results
    assert filter_hidden_grammar(results, []) is results


def test_filter_ignores_entries_without_level_or_key():
    "The generic fallback rules have neither, and survive any hidden set."
    results = [{"key": "generic_rule", "name": "Some rule"}]
    assert filter_hidden_grammar(results, {"N5", "A1"}) == results


def test_japanese_level_vocabulary_matches_the_engine():
    "Drift guard: the form offers exactly the levels the engine emits."
    groups = hideable_grammar_groups(_lang("Japanese", "japanese_sudachi"))
    assert [t for t, _ in groups][:5] == ALL_LEVELS


def test_korean_level_vocabulary_covers_the_data_file():
    "Drift guard: every level in grammar_ko.json is offered by the form."
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.normpath(
        os.path.join(
            here, "..", "..", "..", "..", "lute", "jlpt_data", "grammar_ko.json"
        )
    )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    levels = {e.get("level") for e in data if e.get("level")}
    assert levels, "sanity: the Korean grammar data carries levels"
    offered = {t for t, _ in hideable_grammar_groups(_lang("Korean", "lute_korean"))}
    assert levels <= offered
