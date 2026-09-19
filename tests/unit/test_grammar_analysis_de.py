"""Tests for the German grammar-analysis engine (spaCy de_core_news_sm)."""

import pytest

pytest.importorskip("spacy")
pytest.importorskip("de_core_news_sm")

from lute.read.render.grammar_analysis_de import _DE_RULES, analyze_german
from lute.read.render.grammar_analysis import is_german_language


def _keys(text, lang="en"):
    return {e["key"] for e in analyze_german(text, lang)}


# One short, natural sentence per rule.
_RULE_SENTENCES = {
    "de_es_gibt": "Es gibt einen Park hier.",
    "de_modals": "Ich kann heute nicht kommen.",
    "de_negation": "Ich habe keine Zeit.",
    "de_questions": "Wo wohnst du ?",
    "de_possessives": "Mein Bruder wohnt in Berlin.",
    "de_perfekt": "Er hat es gemacht.",
    "de_praeteritum": "Sie war müde.",
    "de_futur1": "Ich werde gehen.",
    "de_akku": "Ich sehe den Mann.",
    "de_dativ": "Ich helfe dem Mann.",
    "de_zu_inf": "Er versucht zu kommen.",
    "de_als": "Sie ist größer als ich.",
    "de_gern": "Ich schwimme gern.",
    "de_reflexive": "Er wäscht sich.",
    "de_weil": "Ich bleibe zu Hause, weil ich krank bin.",
    "de_werden_passiv": "Das Auto wird repariert.",
    "de_konjunktiv2": "Ich würde gern reisen.",
    "de_seit": "Ich wohne hier seit zwei Jahren.",
    "de_ob": "Ich weiß nicht, ob er kommt.",
    "de_trotz": "Trotzdem gehen wir spazieren.",
    "de_je_desto": "Je mehr, desto besser.",
    "de_zwar": "Es ist zwar schön, aber teuer.",
    "de_indem": "Man lernt, indem man übt.",
}


def test_each_rule_matches_its_sentence():
    "Every rule must fire on its illustrative sentence."
    for key, sentence in _RULE_SENTENCES.items():
        hits = _keys(sentence)
        assert key in hits, f"rule {key} did not fire on: {sentence}"


def test_match_offsets_are_precise():
    "Matched offsets point at the construction, not the whole sentence."
    for e in analyze_german("Es gibt einen Park hier."):
        if e["key"] == "de_es_gibt":
            example = e["examples"][0]
            m = example["matches"][0]
            assert example["sentence"][m["start"] : m["end"]] == "Es gibt"


def test_modal_gap_spans_the_clause():
    "The modal rule spans from the modal to the verb-final infinitive."
    for e in analyze_german("Ich kann heute nicht kommen."):
        if e["key"] == "de_modals":
            example = e["examples"][0]
            m = example["matches"][0]
            assert (
                example["sentence"][m["start"] : m["end"]] == "kann heute nicht kommen"
            )


def test_display_language_switches_desc():
    "Chinese display uses the hand-written zh description."
    zh = {e["key"]: e for e in analyze_german("Es gibt einen Park.", "zh")}
    en = {e["key"]: e for e in analyze_german("Es gibt einen Park.", "en")}
    assert "存在" in zh["de_es_gibt"]["desc"]
    assert "existence" in en["de_es_gibt"]["desc"]


def test_levels_are_cefr():
    "The German engine grades points with CEFR levels."
    levels = {
        e["key"]: e["level"] for e in analyze_german(" ".join(_RULE_SENTENCES.values()))
    }
    assert levels["de_es_gibt"] == "A1"
    assert levels["de_perfekt"] == "A2"


def test_analyze_output_structure():
    "Each entry exposes name/desc/examples with sentence and matches."
    for entry in analyze_german("Er hat es gemacht. Sie war müde."):
        assert entry["name"]
        assert entry["desc"]
        assert entry["examples"]
        ex = entry["examples"][0]
        assert ex["sentence"]
        assert len(ex["matches"]) >= 1
        for m in ex["matches"]:
            assert m["start"] < m["end"]


class StubLanguage:
    def __init__(self, parser_type=None, name=None):
        self.parser_type = parser_type
        self.name = name


def test_is_german_language_detects_by_name():
    "German detection works on language names (parser_type is generic spacedel)."
    assert is_german_language(StubLanguage(name="German"))
    assert is_german_language(StubLanguage(name="Deutsch"))
    assert is_german_language(StubLanguage(name="德语"))
    assert not is_german_language(StubLanguage(name="French"))
    assert not is_german_language(StubLanguage(parser_type="spacedel"))
    assert not is_german_language(None)


def test_rules_are_loaded():
    "The rule set is populated."
    assert len(_DE_RULES) >= 20
