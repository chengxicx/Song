"""Tests for the Arabic grammar-analysis engine (pyarabic)."""

import pytest

pytest.importorskip("pyarabic")

from lute.read.render import grammar_analysis_ar as AR
from lute.read.render.grammar_analysis_ar import _AR_RULES, analyze_arabic
from lute.read.render.grammar_analysis import is_arabic_language


def _keys(text, lang="en"):
    return {e["key"] for e in analyze_arabic(text, lang)}


# One short, natural sentence per hand-written rule.
_RULE_SENTENCES = {
    "ar_questions": "هل أنت هنا؟",
    "ar_prep_pron": "وضعت الكتاب عليها.",
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
    "Every hand-written rule must fire on its illustrative sentence."
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
    hits = _keys("هَذَا الْكِتَابُ جَدِيدٌ.")  # vocalised haadhaa al-kitaabu
    assert "ar_demonstratives" in hits
    for e in analyze_arabic("هَذَا الْكِتَابُ جَدِيدٌ."):
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
    levels = {
        e["key"]: e["level"] for e in analyze_arabic(" ".join(_RULE_SENTENCES.values()))
    }
    assert levels["ar_prep_pron"] == "A1"
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


# ---- data layer (grammar_ar.json) --------------------------------------


def test_data_rows_are_loaded():
    "The merged library rows come through the data layer."
    rules = AR._get_data_rules()
    assert len(rules) >= 70
    assert all(rule["key"].startswith("agm_") for rule in rules)


def test_every_data_row_fires_on_its_own_examples():
    "No row may ship a spec that its own example sentences do not trigger."
    for rule in AR._get_data_rules():
        texts = [
            e["arabic"]
            for e in rule.get("examples") or []
            if isinstance(e, dict) and e.get("arabic")
        ]
        assert texts, f"{rule['key']} carries no example"
        fired = []
        for text in texts:
            norm = AR._normalise(text)
            fired.append(bool(AR.match_rule(rule, AR._tokens_for_page(norm), norm)))
        assert any(fired), f"{rule['key']} never fires on its own examples"


def test_data_rows_carry_formation_and_translation():
    "Library rows expose the formation line and the Chinese example gloss."
    rules = AR._get_data_rules()
    assert all(rule.get("formation") for rule in rules)
    sample = next(r for r in rules if r["key"].startswith("agm_"))
    example = sample["examples"][0]
    assert example["arabic"]
    assert example["zh"]


def test_library_entries_cover_both_layers():
    "The /grammar page lists hand-written rules and merged rows alike."
    entries = AR.library_entries("zh")
    keys = {e["key"] for e in entries}
    assert "ar_kana" in keys
    assert any(k.startswith("agm_") for k in keys)
    with_examples = [e for e in entries if e["examples"]]
    assert len(with_examples) >= 70
    row = next(e for e in entries if e["key"].startswith("agm_") and e["examples"])
    assert isinstance(row["examples"][0], dict)
    assert row["examples"][0]["tr"]


def test_library_entries_sorted_by_cefr():
    "Rows come out in CEFR order (A1 first)."
    order = ["A1", "A2", "B1", "B2", "C1", "C2"]
    levels = [e["level"] for e in AR.library_entries("en")]
    assert levels == sorted(levels, key=order.index)


def test_golden_examples_match_their_rules():
    "Every golden sentence is recognised by the rule it illustrates."
    golden = AR._golden_examples()
    rules = {r["key"]: r for r in _AR_RULES}
    assert golden, "golden set missing"
    for key, examples in golden.items():
        assert key in rules, f"golden key {key} has no rule"
        for example in examples:
            text = AR._normalise(example["arabic"])
            assert AR.match_rule(rules[key], AR._tokens_for_page(text), text), (
                f"{key} does not recognise its golden sentence: {example['arabic']}"
            )


def test_every_library_row_has_an_example():
    "A row with no example reads like a stub, so every row must carry one."
    blank = [e["key"] for e in AR.library_entries("zh") if not e["examples"]]
    assert not blank, f"rows without examples: {blank}"


# ---- panel reference block ---------------------------------------------
#
# The panel shows the page's own sentences, which carry no translation, so
# every card folds out one curated example with its gloss.  Both layers
# have to supply it: the data rows from their own example list, the
# hand-written rules from the golden set.


def test_every_panel_rule_has_a_translated_reference():
    "No card may ship without a translated example under it."
    missing = [
        rule["key"]
        for rule in AR._get_all_rules()
        if not (rule.get("reference") or {}).get("zh")
    ]
    assert not missing, f"rules with no Chinese reference example: {missing}"


def test_panel_reference_carries_the_gloss_for_the_display_language():
    "A Chinese panel gets the Chinese gloss and an English one the English."
    zh = {e["key"]: e for e in analyze_arabic("لا أعرف.", "zh")}
    assert zh["ar_neg_la"]["reference"]["sentence"] == "لا أعرف."
    assert zh["ar_neg_la"]["reference"]["text"] == "我不知道。"
    en = {e["key"]: e for e in analyze_arabic("لا أعرف.", "en")}
    assert en["ar_neg_la"]["reference"]["text"] == "I do not know."


def test_panel_reference_does_not_mutate_the_rule_list():
    "The module-level rules stay exactly as written in the source."
    AR._get_all_rules()
    assert not [rule for rule in _AR_RULES if "reference" in rule]


# ---- both glosses ------------------------------------------------------
#
# The corpus carries Chinese and English for every example.  A language
# with only one of them silently loses its gloss line for whoever reads in
# the other display language, which is exactly how the Arabic library read
# before the English back-fill.


def test_every_data_row_carries_both_glosses():
    "Chinese and English, on every example, index-aligned with the sentence."
    for rule in AR._get_data_rules():
        for example in rule["examples"]:
            assert example.get("zh"), rule["key"]
            assert example.get("en"), rule["key"]


def test_every_golden_example_carries_both_glosses():
    "The hand-written rules' curated sentences are glossed both ways too."
    for key, examples in AR._golden_examples().items():
        for example in examples:
            assert (example.get("zh") or "").strip(), key
            assert (example.get("en") or "").strip(), key


def test_library_entries_gloss_in_either_display_language():
    "No example row may reach the page without its translation."
    for lang in ("zh", "en"):
        blank = [
            entry["key"]
            for entry in AR.library_entries(lang)
            for ex in entry["examples"]
            if not isinstance(ex, dict) or not ex.get("tr")
        ]
        assert not blank, f"{lang} rows with no translation: {blank}"


