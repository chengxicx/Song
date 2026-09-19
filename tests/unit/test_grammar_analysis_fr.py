"""Tests for the French grammar-analysis engine (spaCy fr_core_news_sm)."""

import pytest

pytest.importorskip("spacy")
pytest.importorskip("fr_core_news_sm")

from lute.read.render.grammar_analysis_fr import _FR_RULES, analyze_french
from lute.read.render.grammar_analysis import is_french_language


def _keys(text, lang="en"):
    return {e["key"] for e in analyze_french(text, lang)}


# One short, natural sentence per rule.
_RULE_SENTENCES = {
    "fr_il_y_a": "Il y a un café près de chez moi.",
    "fr_estceque": "Est-ce que tu viens ce soir ?",
    "fr_c_est": "C'est très bon.",
    "fr_wh_questions": "Comment allez-vous ?",
    "fr_negation": "Je ne sais pas.",
    "fr_possessives": "Mon frère habite ici.",
    "fr_futur_proche": "Je vais partir demain.",
    "fr_passe_compose": "Hier j'ai mangé une pomme.",
    "fr_imparfait": "Quand il était petit, il jouait dehors.",
    "fr_futur_simple": "Elle sera là demain.",
    "fr_modals": "Je peux venir ce soir.",
    "fr_comparative": "Il est plus grand que moi.",
    "fr_venir_de": "Je viens de manger.",
    "fr_depuis": "J'habite ici depuis deux ans.",
    "fr_relative": "La femme qui parle est ma mère.",
    "fr_subjonctif": "Je doute qu'il soit là.",
    "fr_il_faut": "Il faut partir tôt.",
    "fr_conditionnel": "Je voudrais un café.",
    "fr_ce_que": "Je sais ce que tu veux.",
    "fr_y_en": "J'en veux encore.",
    "fr_sans_inf": "Il est parti sans dire au revoir.",
    "fr_si_conditionnel": "Si j'avais de l'argent, je voyagerais.",
    "fr_gerondif": "Il apprend en travaillant.",
    "fr_apres_avoir": "Après avoir mangé, il est sorti.",
    "fr_passe_simple": "Il fit tout son possible.",
}


def test_each_rule_matches_its_sentence():
    "Every rule must fire on its illustrative sentence."
    for key, sentence in _RULE_SENTENCES.items():
        hits = _keys(sentence)
        assert key in hits, f"rule {key} did not fire on: {sentence}"


def test_match_offsets_are_precise():
    "Matched offsets point at the construction, not the whole sentence."
    for e in analyze_french("Il y a un café près de chez moi."):
        if e["key"] == "fr_il_y_a":
            example = e["examples"][0]
            m = example["matches"][0]
            assert example["sentence"][m["start"] : m["end"]] == "Il y a"


def test_display_language_switches_desc():
    "Chinese display uses the hand-written zh description."
    zh = {e["key"]: e for e in analyze_french("Il y a un café.", "zh")}
    en = {e["key"]: e for e in analyze_french("Il y a un café.", "en")}
    assert "存在" in zh["fr_il_y_a"]["desc"]
    assert "existence" in en["fr_il_y_a"]["desc"]


def test_levels_are_cefr():
    "The French engine grades points with CEFR levels."
    levels = {e["key"]: e["level"] for e in analyze_french(" ".join(_RULE_SENTENCES.values()))}
    assert levels["fr_il_y_a"] == "A1"
    assert levels["fr_passe_compose"] == "A2"


def test_analyze_output_structure():
    "Each entry exposes name/desc/examples with sentence and matches."
    for entry in analyze_french("Il y a un café. Je ne sais pas."):
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


def test_is_french_language_detects_by_name():
    "French detection works on language names (parser_type is generic spacedel)."
    assert is_french_language(StubLanguage(name="French"))
    assert is_french_language(StubLanguage(name="Français"))
    assert is_french_language(StubLanguage(name="法语"))
    assert not is_french_language(StubLanguage(name="German"))
    assert not is_french_language(StubLanguage(parser_type="spacedel"))
    assert not is_french_language(None)


def test_rules_are_loaded():
    "The rule set is populated."
    assert len(_FR_RULES) >= 20
