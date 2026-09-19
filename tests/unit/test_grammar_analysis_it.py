"""Tests for the Italian grammar-analysis engine (spaCy it_core_news_sm)."""

import pytest

pytest.importorskip("spacy")
pytest.importorskip("it_core_news_sm")

from lute.read.render.grammar_analysis_it import _IT_RULES, analyze_italian
from lute.read.render.grammar_analysis import is_italian_language


def _keys(text, lang="en"):
    return {e["key"] for e in analyze_italian(text, lang)}


# One short, natural sentence per rule.
_RULE_SENTENCES = {
    "it_essere_di": "Sono di Roma.",
    "it_ce": "C'è un problema grave.",
    "it_piacere": "Mi piace la pizza.",
    "it_questions": "Come stai?",
    "it_stare_gerundio": "Sto mangiando una pizza.",
    "it_passato_prossimo": "Ho mangiato ieri.",
    "it_imperfetto": "Quando ero piccolo, giocavo fuori.",
    "it_futuro": "Domani partirà presto.",
    "it_modali": "Devo studiare stasera.",
    "it_comparativo": "Lui è più alto di me.",
    "it_bisogna": "Bisogna partire adesso.",
    "it_congiuntivo": "Penso che sia giusto.",
    "it_condizionale": "Comprerei una casa qui.",
    "it_cui": "La persona di cui parlavo è arrivata.",
    "it_da_tempo": "Abito qui da due anni.",
    "it_ne": "Ne voglio due.",
    "it_prima_di": "Prima di uscire, leggo.",
    "it_si_passivante": "Si parla italiano qui.",
    "it_stare_per": "Sto per uscire di casa.",
    "it_congiuntivo_passato": "Penso che abbia finito.",
    "it_se_condizionale": "Se fossi ricco, viaggerei sempre.",
    "it_congiuntivo_imperfetto": "Se fossi ricco, comprerei una barca.",
    "it_dopo_aver": "Dopo aver mangiato, esco.",
    "it_passato_remoto": "Caesar fu un uomo grande.",
}


def test_each_rule_matches_its_sentence():
    "Every rule must fire on its illustrative sentence."
    for key, sentence in _RULE_SENTENCES.items():
        hits = _keys(sentence)
        assert key in hits, f"rule {key} did not fire on: {sentence}"


def test_match_offsets_are_precise():
    "Matched offsets point at the construction, not the whole sentence."
    for e in analyze_italian("Ho mangiato ieri."):
        if e["key"] == "it_passato_prossimo":
            example = e["examples"][0]
            m = example["matches"][0]
            assert example["sentence"][m["start"] : m["end"]] == "Ho mangiato"


def test_display_language_switches_desc():
    "Chinese display uses the hand-written zh description."
    zh = {e["key"]: e for e in analyze_italian("C'è un problema.", "zh")}
    en = {e["key"]: e for e in analyze_italian("C'è un problema.", "en")}
    assert "存在" in zh["it_ce"]["desc"]
    assert "there is" in en["it_ce"]["desc"]


def test_levels_are_cefr():
    "The Italian engine grades points with CEFR levels."
    levels = {
        e["key"]: e["level"]
        for e in analyze_italian(" ".join(_RULE_SENTENCES.values()))
    }
    assert levels["it_ce"] == "A1"
    assert levels["it_passato_prossimo"] == "A2"


def test_analyze_output_structure():
    "Each entry exposes name/desc/examples with sentence and matches."
    for entry in analyze_italian("Ho mangiato ieri. C'è un problema."):
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


def test_is_italian_language_detects_by_name():
    "Italian detection works on language names."
    assert is_italian_language(StubLanguage(name="Italian"))
    assert is_italian_language(StubLanguage(name="Italiano"))
    assert is_italian_language(StubLanguage(name="意大利语"))
    assert not is_italian_language(StubLanguage(name="Spanish"))
    assert not is_italian_language(None)


def test_rules_are_loaded():
    "The rule set is populated."
    assert len(_IT_RULES) >= 20
