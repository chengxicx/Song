"""Tests for the Arabic grammar-analysis engine (pyarabic)."""

import pytest

pytest.importorskip("pyarabic")

from lute.read.render.grammar_analysis_ar import _AR_RULES, analyze_arabic
from lute.read.render.grammar_analysis import is_arabic_language


def _keys(text, lang="en"):
    return {e["key"] for e in analyze_arabic(text, lang)}


# One short, natural sentence per rule.
_RULE_SENTENCES = {
    "ar_questions": "هل أنت هنا؟",
    "ar_prepositions": "الكتاب على الطاولة.",
    "ar_al": "المدرسة كبيرة.",
    "ar_demonstratives": "هذا الكتاب جديد.",
    "ar_neg_la": "لا أعرف.",
    "ar_kana": "كان الجو جميلاً.",
    "ar_pronouns": "أنا طالب.",
    "ar_but": "أردت الذهاب لكنه كان مشغولاً.",
    "ar_because": "بقيت في البيت لأن المطر كان غزيراً.",
    "ar_when": "عندما وصلنا بدأ الحفل.",
    "ar_sawfa": "سوف أذهب إلى السوق.",
    "ar_also": "جاء علي أيضاً.",
    "ar_every": "كل الطلاب حاضرون.",
    "ar_anna": "قال إنه سيأتي.",
    "ar_if": "لو كان عندي وقت، سافرت.",
    "ar_lan_lam": "لم يذهب.",
    "ar_relative": "الرجل الذي رأيته هناك.",
    "ar_qad": "قد انتهى العمل.",
    "ar_suffix_ha": "كتابها على الطاولة.",
    "ar_suffix_plural": "هذا بيتهم.",
    "ar_hatta": "انتظر حتى جاء.",
    "ar_laysa": "ليس صعباً.",
    "ar_wish": "ليت عندي وقتاً.",
    "ar_kullama": "كلما كبر تحسن.",
    "ar_lam_yakun": "لم يكن هناك أحد.",
}


def test_each_rule_matches_its_sentence():
    "Every rule must fire on its illustrative sentence."
    for key, sentence in _RULE_SENTENCES.items():
        hits = _keys(sentence)
        assert key in hits, f"rule {key} did not fire on: {sentence}"


def test_match_offsets_are_precise():
    "Matched offsets point at the particle, not the whole sentence."
    for e in analyze_arabic("كان الجو جميلاً."):
        if e["key"] == "ar_kana":
            example = e["examples"][0]
            m = example["matches"][0]
            assert example["sentence"][m["start"] : m["end"]] == "كان"


def test_vocalised_text_matches():
    "Diacritics are stripped for matching, offsets stay in the original text."
    hits = _keys("الْمَدْرَسَةُ كَبِيرَةٌ.")  # vocalised al-madrasa kabira
    assert "ar_al" in hits
    for e in analyze_arabic("الْمَدْرَسَةُ كَبِيرَةٌ."):
        for ex in e["examples"]:
            for m in ex["matches"]:
                frag = ex["sentence"][m["start"] : m["end"]]
                assert frag, "empty span"


def test_display_language_switches_desc():
    "Chinese display uses the hand-written zh description."
    zh = {e["key"]: e for e in analyze_arabic("لا أعرف.", "zh")}
    en = {e["key"]: e for e in analyze_arabic("لا أعرف.", "en")}
    assert "不" in zh["ar_neg_la"]["desc"]
    assert "imperfect" in en["ar_neg_la"]["desc"]


def test_levels_are_cefr():
    "The Arabic engine grades points with CEFR bands."
    levels = {e["key"]: e["level"] for e in analyze_arabic(" ".join(_RULE_SENTENCES.values()))}
    assert levels["ar_prepositions"] == "A1"
    assert levels["ar_relative"] == "B1"


def test_analyze_output_structure():
    "Each entry exposes name/desc/examples with sentence and matches."
    for entry in analyze_arabic("كان الجو جميلاً. الكتاب على الطاولة."):
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


def test_is_arabic_language_detects_by_name():
    "Arabic detection works on language names."
    assert is_arabic_language(StubLanguage(name="Arabic"))
    assert is_arabic_language(StubLanguage(name="العربية"))
    assert is_arabic_language(StubLanguage(name="阿拉伯语"))
    assert not is_arabic_language(StubLanguage(name="Hebrew"))
    assert not is_arabic_language(None)


def test_rules_are_loaded():
    "The rule set is populated."
    assert len(_AR_RULES) >= 20
