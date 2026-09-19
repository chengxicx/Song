"""Tests for the Russian grammar-analysis engine (pymorphy3)."""

import pytest

pytest.importorskip("pymorphy3")

from lute.read.render.grammar_analysis_ru import _RU_RULES, analyze_russian
from lute.read.render.grammar_analysis import is_russian_language


def _keys(text, lang="en"):
    return {e["key"] for e in analyze_russian(text, lang)}


# One short, natural sentence per rule.
_RULE_SENTENCES = {
    "ru_u_menya": "У меня есть время.",
    "ru_nravitsya": "Мне нравится этот город.",
    "ru_prep_loct": "Мы живём в Москве.",
    "ru_k_dat": "Мы идём к другу.",
    "ru_modal_inf": "Здесь нельзя курить.",
    "ru_imperative": "Читайте медленно, пожалуйста.",
    "ru_s_ablt": "Я иду с другом в кино.",
    "ru_gen_preps": "Мы работаем без отдыха.",
    "ru_o_loct": "Мы говорили о работе.",
    "ru_nums_2_4": "Я купил три книги.",
    "ru_nums_5": "В комнате пять стульев.",
    "ru_past": "Вчера я читал интересную книгу.",
    "ru_future": "Завтра мы будем работать.",
    "ru_by": "Я хотел бы поехать в Россию.",
    "ru_chtoby": "Я пришёл, чтобы помочь тебе.",
    "ru_poka_ne": "Подожди, пока не придёт врач.",
    "ru_posle_togo_kak": "После того как он ушёл, стало тихо.",
    "ru_pricastie": "Человек, читающий книгу, мой друг.",
    "ru_deepricastie": "Он ушёл, закрыв за собой дверь.",
    "ru_motion_prefixes": "Вчера я пришёл домой поздно.",
    "ru_sya": "Он учится в университете.",
    "ru_dolzhen_byl": "Я должен был позвонить вчера.",
    "ru_chut_ne": "Она чуть не упала.",
    "ru_stoilo_kak": "Стоило прийти, как пошёл дождь.",
    "ru_v_techenie": "Он работал в течение трёх часов.",
    "ru_double_neg": "Я никогда не видел моря.",
    "ru_kak_ni": "Как ни старайся, не получится.",
    "ru_edva_li": "Вряд ли он придёт сегодня.",
    "ru_chem_tem": "Чем больше, тем лучше.",
}


def test_each_rule_matches_its_sentence():
    "Every rule must fire on its illustrative sentence."
    for key, sentence in _RULE_SENTENCES.items():
        hits = _keys(sentence)
        assert key in hits, f"rule {key} did not fire on: {sentence}"


def test_match_offsets_are_precise():
    "Matched offsets point at the construction, not the whole sentence."
    for e in analyze_russian("У меня есть время."):
        if e["key"] == "ru_u_menya":
            example = e["examples"][0]
            m = example["matches"][0]
            assert example["sentence"][m["start"] : m["end"]] == "У меня есть"


def test_display_language_switches_desc():
    "Chinese display uses the hand-written zh description."
    zh = {e["key"]: e for e in analyze_russian("У меня есть время.", "zh")}
    en = {e["key"]: e for e in analyze_russian("У меня есть время.", "en")}
    assert "有" in zh["ru_u_menya"]["desc"]
    assert "genitive" in en["ru_u_menya"]["desc"]


def test_levels_are_cefr():
    "The Russian engine grades points with CEFR levels."
    levels = {
        e["key"]: e["level"]
        for e in analyze_russian(" ".join(_RULE_SENTENCES.values()))
    }
    assert levels["ru_u_menya"] == "A1"
    assert levels["ru_past"] == "A2"


def test_analyze_output_structure():
    "Each entry exposes name/desc/examples with sentence and matches."
    for entry in analyze_russian("У меня есть время. Вчера я читал книгу."):
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


def test_is_russian_language_detects_by_name():
    "Russian detection works on language names (parser_type is generic spacedel)."
    assert is_russian_language(StubLanguage(name="Russian"))
    assert is_russian_language(StubLanguage(name="Русский"))
    assert is_russian_language(StubLanguage(name="俄语"))
    assert not is_russian_language(StubLanguage(name="English"))
    assert not is_russian_language(StubLanguage(parser_type="spacedel"))
    assert not is_russian_language(None)


def test_rules_are_loaded():
    "The rule set is populated."
    assert len(_RU_RULES) >= 10
