"""Tests for the Portuguese grammar-analysis engine (spaCy pt_core_news_sm)."""

import pytest

pytest.importorskip("spacy")
pytest.importorskip("pt_core_news_sm")

from lute.read.render.grammar_analysis_pt import _PT_RULES, analyze_portuguese
from lute.read.render.grammar_analysis import is_portuguese_language


def _keys(text, lang="en"):
    return {e["key"] for e in analyze_portuguese(text, lang)}


# One short, natural sentence per rule.
_RULE_SENTENCES = {
    "pt_estar_em": "O livro está em casa.",
    "pt_haver": "Há um problema grave.",
    "pt_gostar": "Gosto de música clássica.",
    "pt_questions": "Onde você mora?",
    "pt_progressivo": "Estou comendo agora.",
    "pt_ir_inf": "Vou comer agora.",
    "pt_imperfeito": "Quando era criança, vivia aqui.",
    "pt_modais": "Devo estudar hoje.",
    "pt_ter_que": "Tenho que sair cedo.",
    "pt_comparativo": "Ele é mais alto do que eu.",
    "pt_subjuntivo": "Espero que venha amanhã.",
    "pt_relative": "A pessoa da qual falei chegou.",
    "pt_ha_tempo": "Moro aqui há dois anos.",
    "pt_prima_inf": "Antes de sair, leio o jornal.",
    "pt_se_passivo": "Fala-se português aqui.",
    "pt_mais_que_perfeito": "Ele já tinha comido quando cheguei.",
    "pt_se_condicional": "Se eu fosse rico, viajaria sempre.",
    "pt_conjuntivo_imperfeito": "Se eu fosse rico, compraria uma casa.",
}


def test_each_rule_matches_its_sentence():
    "Every rule must fire on its illustrative sentence."
    for key, sentence in _RULE_SENTENCES.items():
        hits = _keys(sentence)
        assert key in hits, f"rule {key} did not fire on: {sentence}"


def test_match_offsets_are_precise():
    "Matched offsets point at the construction, not the whole sentence."
    for e in analyze_portuguese("Vou comer agora."):
        if e["key"] == "pt_ir_inf":
            example = e["examples"][0]
            m = example["matches"][0]
            assert example["sentence"][m["start"] : m["end"]] == "Vou comer"


def test_display_language_switches_desc():
    "Chinese display uses the hand-written zh description."
    zh = {e["key"]: e for e in analyze_portuguese("Há um problema.", "zh")}
    en = {e["key"]: e for e in analyze_portuguese("Há um problema.", "en")}
    assert "存在" in zh["pt_haver"]["desc"]
    assert "there is" in en["pt_haver"]["desc"]


def test_levels_are_cefr():
    "The Portuguese engine grades points with CEFR levels."
    levels = {e["key"]: e["level"] for e in analyze_portuguese(" ".join(_RULE_SENTENCES.values()))}
    assert levels["pt_haver"] == "A1"
    assert levels["pt_imperfeito"] == "A2"


def test_analyze_output_structure():
    "Each entry exposes name/desc/examples with sentence and matches."
    for entry in analyze_portuguese("Há um problema. Vou comer agora."):
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


def test_is_portuguese_language_detects_by_name():
    "Portuguese detection works on language names."
    assert is_portuguese_language(StubLanguage(name="Portuguese"))
    assert is_portuguese_language(StubLanguage(name="Português"))
    assert is_portuguese_language(StubLanguage(name="葡萄牙语"))
    assert not is_portuguese_language(StubLanguage(name="Spanish"))
    assert not is_portuguese_language(None)


def test_rules_are_loaded():
    "The rule set is populated."
    assert len(_PT_RULES) >= 15
