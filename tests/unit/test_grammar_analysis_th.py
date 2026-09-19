"""Tests for the Thai grammar-analysis engine (pythainlp)."""

import pytest

pytest.importorskip("pythainlp")

from lute.read.render.grammar_analysis_th import _TH_RULES, analyze_thai
from lute.read.render.grammar_analysis import is_thai_language


def _keys(text, lang="en"):
    return {e["key"] for e in analyze_thai(text, lang)}


# One short, natural sentence per rule.  Thai tokenization splits on
# dictionary words, so sentences use words pythainlp keeps intact.
_RULE_SENTENCES = {
    "th_pronouns": "ผมชอบกาแฟ",
    "th_copula": "เขาเป็นครู",
    "th_negation": "เขาไม่ชอบกาแฟ",
    "th_questions": "คุณจะไปไหน",
    "th_have": "ฉันมีรถ",
    "th_polite": "สวัสดีครับ",
    "th_and_or": "ข้าวและน้ำ",
    "th_future": "ฉันจะกินข้าว",
    "th_progressive": "ผมกำลังอ่านหนังสือ",
    "th_completive": "เขากินแล้ว",
    "th_want": "ฉันอยากไป",
    "th_should_must": "เราต้องไป",
    "th_because": "เขาไม่มาเพราะว่าฝนตก",
    "th_thi": "คนที่มาเมื่อวานเป็นเพื่อน",
    "th_can": "เขาสามารถว่ายน้ำได้",
    "th_ever": "ฉันเคยไปกรุงเทพ",
    "th_give": "เขาให้เงินฉัน",
    "th_if": "ถ้าฝนตก ผมจะอยู่บ้าน",
    "th_na_worth": "หนังสือเล่มนี้น่าสนใจมาก",
    "th_also": "เขามาด้วย",
    "th_passive": "เขาถูกหมากัด",
    "th_both": "ทั้งพ่อและแม่มา",
    "th_almost": "เขาเกือบตาย",
    "th_concessive": "แม้ว่าฝนจะตก ก็ไป",
    "th_in_order_to": "ผมเรียนเพื่อรู้",
}


def test_each_rule_matches_its_sentence():
    "Every rule must fire on its illustrative sentence."
    for key, sentence in _RULE_SENTENCES.items():
        hits = _keys(sentence)
        assert key in hits, f"rule {key} did not fire on: {sentence}"


def test_match_offsets_are_precise():
    "Matched offsets point at the particle, not the whole sentence."
    for e in analyze_thai("ผมกำลังอ่านหนังสือ"):
        if e["key"] == "th_progressive":
            example = e["examples"][0]
            m = example["matches"][0]
            assert example["sentence"][m["start"] : m["end"]] == "กำลัง"


def test_display_language_switches_desc():
    "Chinese display uses the hand-written zh description."
    zh = {e["key"]: e for e in analyze_thai("เขาไม่ชอบกาแฟ", "zh")}
    en = {e["key"]: e for e in analyze_thai("เขาไม่ชอบกาแฟ", "en")}
    assert "否定" in zh["th_negation"]["desc"]
    assert "negates" in en["th_negation"]["desc"]


def test_levels_are_cefr():
    "The Thai engine grades points with CEFR bands."
    levels = {e["key"]: e["level"] for e in analyze_thai(" ".join(_RULE_SENTENCES.values()))}
    assert levels["th_pronouns"] == "A1"
    assert levels["th_passive"] == "B2"


def test_analyze_output_structure():
    "Each entry exposes name/desc/examples with sentence and matches."
    for entry in analyze_thai("ผมกำลังอ่านหนังสือ คุณจะไปไหน"):
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


def test_is_thai_language_detects_by_name_and_parser():
    "Thai detection works by lute-thai parser type or language name."
    assert is_thai_language(StubLanguage(parser_type="lute_thai"))
    assert is_thai_language(StubLanguage(name="Thai"))
    assert is_thai_language(StubLanguage(name="ไทย"))
    assert is_thai_language(StubLanguage(name="泰语"))
    assert not is_thai_language(StubLanguage(name="Lao"))
    assert not is_thai_language(None)


def test_rules_are_loaded():
    "The rule set is populated."
    assert len(_TH_RULES) >= 20
