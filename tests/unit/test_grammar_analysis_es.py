"""Tests for the Spanish grammar-analysis engine (spaCy es_core_news_sm)."""

import pytest

pytest.importorskip("spacy")
pytest.importorskip("es_core_news_sm")

from lute.read.render.grammar_analysis_es import _ES_RULES, analyze_spanish
from lute.read.render.grammar_analysis import is_spanish_language


def _keys(text, lang="en"):
    return {e["key"] for e in analyze_spanish(text, lang)}


# One short, natural sentence per rule.
_RULE_SENTENCES = {
    "es_ser_de": "Es de México.",
    "es_estar_en": "El libro está en la mesa.",
    "es_hay": "Hay un problema grave.",
    "es_gustar": "Me encanta este lugar.",
    "es_wh_questions": "¿Dónde vives ahora?",
    "es_preterite": "Ayer comió paella.",
    "es_imperfect": "Cuando era niño, vivía en Madrid.",
    "es_ir_a": "Vamos a comer ahora.",
    "es_tener_que": "Tengo que estudiar hoy.",
    "es_acabar_de": "Acabo de llegar a casa.",
    "es_para_inf": "Estudio todos los días para aprender más.",
    "es_desde_hace": "Vivo aquí desde hace dos años.",
    "es_mas_que": "Este libro es más interesante que aquel.",
    "es_tan_como": "Es tan alto como su hermano.",
    "es_le": "Le gusta el café por la mañana.",
    "es_subj_present": "Quiero que vengas.",
    "es_conditional": "Yo compraría esa casa.",
    "es_present_perfect": "He comido ya.",
    "es_pluperfect": "Ya había comido cuando llegó.",
    "es_si_conditional": "Si tuviera dinero, compraría una casa.",
    "es_subj_past": "Actuaba como si fuera el dueño.",
    "es_subj_pluperfect": "Si hubiera sabido, habría venido.",
    "es_conditional_perfect": "Habría sido mejor así.",
    "es_se": "Aquí se habla español.",
    "es_como_si": "Actúa como si nada pasara.",
    "es_llevar_gerund": "Llevo dos años viviendo aquí.",
    "es_de_haber": "De haber sabido, habría venido.",
    "es_por_mas_que": "Por más que trabaje, no terminará.",
    "es_y_eso_que": "Vinimos ayer, y eso que llovía.",
    "es_no_es_que": "No es que sea tonto, es que no quiero.",
}


def test_each_rule_matches_its_sentence():
    "Every rule must fire on its illustrative sentence."
    for key, sentence in _RULE_SENTENCES.items():
        hits = _keys(sentence)
        assert key in hits, f"rule {key} did not fire on: {sentence}"


def test_match_offsets_are_precise():
    "Matched offsets point at the construction, not the whole sentence."
    for e in analyze_spanish("Vamos a comer ahora."):
        if e["key"] == "es_ir_a":
            example = e["examples"][0]
            m = example["matches"][0]
            assert example["sentence"][m["start"] : m["end"]] == "Vamos a comer"


def test_preterite_and_imperfect_are_distinct():
    "Preterite and imperfect fire on their own verb forms."
    text = "Ayer comió paella. Cuando era niño, vivía en Madrid."
    keys = _keys(text)
    assert "es_preterite" in keys
    assert "es_imperfect" in keys


def test_display_language_switches_desc():
    "Chinese display uses the hand-written zh description."
    zh = {e["key"]: e for e in analyze_spanish("Hay un problema grave.", "zh")}
    en = {e["key"]: e for e in analyze_spanish("Hay un problema grave.", "en")}
    assert "存在" in zh["es_hay"]["desc"]
    assert "there is" in en["es_hay"]["desc"]


def test_levels_are_cefr():
    "The Spanish engine grades points with CEFR levels."
    levels = {e["key"]: e["level"] for e in analyze_spanish(" ".join(_RULE_SENTENCES.values()))}
    assert levels["es_hay"] == "A1"
    assert levels["es_preterite"] == "A2"


def test_analyze_output_structure():
    "Each entry exposes name/desc/examples with sentence and matches."
    for entry in analyze_spanish("Ayer comió paella. Hay un problema grave."):
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


def test_is_spanish_language_detects_by_name():
    "Spanish detection works on language names (parser_type is generic spacedel)."
    assert is_spanish_language(StubLanguage(name="Spanish"))
    assert is_spanish_language(StubLanguage(name="Español"))
    assert is_spanish_language(StubLanguage(name="西班牙语"))
    assert not is_spanish_language(StubLanguage(name="English"))
    assert not is_spanish_language(StubLanguage(parser_type="spacedel"))
    assert not is_spanish_language(None)


def test_rules_are_loaded():
    "The rule set is populated."
    assert len(_ES_RULES) >= 10
