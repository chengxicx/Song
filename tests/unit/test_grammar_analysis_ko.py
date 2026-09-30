"""Tests for the Korean grammar-analysis engine (Kiwi + kimchi-grammar data)."""

import re
import unicodedata

import pytest

pytest.importorskip("kiwipiepy")

from lute.read.render import grammar_analysis_ko as grammar_ko  # noqa: E402
from lute.read.render.grammar_analysis_ko import (  # noqa: E402
    _KO_RULES,
    _get_pattern_rules,
    analyze_korean,
)
from lute.read.render.grammar_analysis import is_korean_language


def _keys(text, lang="en"):
    return {e["key"] for e in analyze_korean(text, lang)}


# One short, natural sentence per hand-written rule.
_RULE_SENTENCES = {
    "ko_go_issda": "저는 영화를 보고 있어요.",
    "ko_su_issda": "한국어를 할 수 있어요.",
    "ko_go_sipda": "집에 가고 싶어요.",
    "ko_ji_anhda": "그걸 하지 않아요.",
    "ko_aeo_seo": "배가 아파서 쉬어요.",
    "ko_eunikka": "비가 오니까 안 나가요.",
    "ko_geo_future": "내일 갈 거예요.",
    "ko_aeo_juda": "친구를 도와줘요.",
    "ko_gi_jeone": "먹기 전에 손을 씻어요.",
    "ko_jung_ida": "공부하는 중이에요.",
    "ko_copula_polite": "저는 학생입니다.",
    "ko_eumyon": "시간 있으면 같이 가요.",
}


def test_each_handwritten_rule_matches_its_sentence():
    "Every hand-written rule must fire on its illustrative sentence."
    for key, sentence in _RULE_SENTENCES.items():
        hits = _keys(sentence)
        assert key in hits, f"rule {key} did not fire on: {sentence}"


def test_handwritten_rule_offsets_are_precise():
    "Matched offsets point at the grammar morphemes, not the whole sentence."
    for e in analyze_korean("한국어를 할 수 있어요."):
        if e["key"] == "ko_su_issda":
            example = e["examples"][0]
            start, end = example["matches"][0]["start"], example["matches"][0]["end"]
            # The rule starts at the ㄹ-ending (ETM) on the verb and runs to
            # the 있다 stem, so the matched slice spans the construction
            # "할 수 있" (Kiwi decomposes 할 into 하 + ᆯ, so the anchor is
            # Kiwi's character offset, not the surface lengths).
            assert example["sentence"][start:end] == "할 수 있"


def test_decomposed_hangul_is_composed_before_matching():
    """
    A book may store a syllable with its coda split off as a standalone
    jamo ("거세어지" + U+11AF instead of "거세어질", "원론적이" + U+11AB
    instead of "원론적인") -- a morphological-analyser signature.  Kiwi
    then reports the ㄹ/ㄴ ending at the bare jamo, so the highlighted
    slice was "ᆯ 수 있" and the panel cut the syllable in half.
    """
    broken = (
        "김 후보자를 임명하면서 동시에 공소취소 문제에도 원론적이\u11ab 답변을 내놓을 경우 야권의 공세는 더 거세어지\u11af 수 있다."
    )
    assert not unicodedata.is_normalized("NFC", broken)
    entries = {e["key"]: e for e in analyze_korean(broken)}
    example = entries["ko_su_issda"]["examples"][0]
    sentence = example["sentence"]
    assert unicodedata.is_normalized("NFC", sentence), "返回的句子应是组合形式"
    assert "거세어질" in sentence
    (match,) = example["matches"]
    assert sentence[match["start"] : match["end"]] == "질 수 있"


def test_display_language_switches_desc():
    "Chinese display uses the hand-written zh description; English the meaning."
    zh = {e["key"]: e for e in analyze_korean("저는 영화를 보고 있어요.", "zh")}
    en = {e["key"]: e for e in analyze_korean("저는 영화를 보고 있어요.", "en")}
    assert "正在做" in zh["ko_go_issda"]["desc"]
    assert "is/am" in en["ko_go_issda"]["desc"].lower()


def test_korean_display_uses_korean_description():
    "한국어 display shows Korean descriptions for hand-written and data rules."
    ko = {e["key"]: e for e in analyze_korean("저는 영화를 보고 있어요.", "ko")}
    assert "진행" in ko["ko_go_issda"]["desc"]
    data = analyze_korean("우리 집은 공원만큼 조용해요.", "ko")
    mankeum = next(e for e in data if "만큼" in e["name"])
    assert "정도" in mankeum["desc"], "data rule should use the JSON ko description"


def test_data_driven_rule_fires():
    "A kimchi-grammar snapshot rule (만큼) is loaded and detected."
    hits = _keys("우리 집은 공원만큼 조용해요.")
    assert any("만큼" in k for k in hits)


def test_multi_sense_entries_merge_by_name():
    "Different senses of one Hangul pattern collapse into a single entry."
    # (으)로 has separate method/direction senses in kimchi-grammar; in
    # English they carry distinct meanings, so the merged desc should show
    # both but the panel must contain only one "(으)로" entry.
    results = analyze_korean("자전거로 다녀요. 창밖으로 서울 타워가 보여요.", "en")
    ro_entries = [e for e in results if e["name"] == "(으)로"]
    assert len(ro_entries) == 1
    assert "direction" in ro_entries[0]["desc"].lower()


def test_analyze_output_structure():
    "Each entry exposes name/desc/examples with sentence and matches."
    for entry in analyze_korean("한국어를 할 수 있어요. 저는 학생입니다."):
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


def test_is_korean_language_detects_korean():
    "Korean detection works by parser type or by language name."
    assert is_korean_language(StubLanguage(parser_type="lute_korean"))
    assert is_korean_language(StubLanguage(parser_type="korean"))
    assert is_korean_language(StubLanguage(name="한국어"))
    assert is_korean_language(StubLanguage(name="Korean"))
    assert not is_korean_language(StubLanguage(parser_type="japanese_sudachi"))
    assert not is_korean_language(None)


def test_handwritten_rules_are_loaded():
    "The hand-written rule set is populated."
    assert len(_KO_RULES) >= 10


# ---- panel blocks (接续 / 参考例句 / 注意点) ---------------------------


def _merged_rules():
    "Pattern-derived rules for the rows merged from the study materials."
    return [r for r in _get_pattern_rules() if r["key"].startswith("kgm_")]


def _panel_entry(rule, display_lang):
    "The panel entry for one rule, from a page made of its own reference."
    sentence = rule["reference"]["korean"]
    return next(
        e
        for e in analyze_korean(sentence, display_lang)
        if e["key"] == rule["key"] or e["name"] == rule["pattern"]
    )


def test_merged_rules_carry_the_panel_blocks():
    """
    The rows merged from the private-book books carry a 接续 line
    and translated examples; the merge used to drop both, which is why the
    Korean panel showed a description where the Japanese one showed 接续 /
    参考例句 / 注意点.
    """
    rules = _merged_rules()
    assert rules, "expected merged pattern rules"
    assert [r for r in rules if r["formation"]], "no 接续 line on any merged rule"
    assert [r for r in rules if r["reference"]], "no curated example on any merged rule"
    for rule in rules:
        ref = rule["reference"]
        if ref is None:
            continue
        assert ref["korean"], rule["key"]
        assert ref["chinese"] or ref["english"], rule["key"]
        # The token list was only needed to choose the example.
        assert "tokens" not in ref, rule["key"]


def test_every_reference_example_is_highlightable():
    """
    A rule's curated reference must be one of its own examples that the rule
    actually matches -- not merely the first one in document order.  The
    derivation only proves a spec matches the row's examples, not every one
    of them, so choosing by order alone can quote a sentence the matcher
    cannot mark.
    """
    rules = [r for r in _merged_rules() if r["reference"]]
    assert rules, "expected merged rules with a curated example"
    for rule in rules:
        korean = rule["reference"]["korean"]
        spans = grammar_ko._match_spans(rule, grammar_ko._tokens_for(korean), korean)
        assert spans, f"{rule['key']} cannot highlight its own reference: {korean}"


def test_panel_reference_carries_highlight_offsets():
    """
    The reference block ships the same {start, end} offsets the page examples
    use, so the front-end highlights it with the one renderer it already has.
    """
    rule = next(r for r in _merged_rules() if r["reference"] and r["formation_notes"])
    entry = _panel_entry(rule, "zh")
    ref = entry["reference"]
    assert ref["sentence"] == rule["reference"]["korean"]
    assert ref["text"] == rule["reference"]["chinese"]
    assert ref["matches"], f"{rule['key']} reference has no offsets"
    for match in ref["matches"]:
        assert 0 <= match["start"] < match["end"] <= len(ref["sentence"])


def test_panel_entries_omit_empty_reference_fields():
    "Hand-written rules carry no formation/reference: no empty fields emitted."
    results = analyze_korean("저는 영화를 보고 있어요.")
    assert results, "expected at least one hit"
    for entry in results:
        assert "formation" not in entry or entry["formation"]
        assert "notes" not in entry or entry["notes"]
        assert "reference" not in entry or entry["reference"]["sentence"]


def test_chinese_panel_never_shows_untranslated_enrichment():
    """
    A Chinese panel must never print Korean or English under a Chinese
    heading -- the bug the Japanese panel shipped.  The books state
    ``formation`` in Korean and ``notes`` in English, so the wording lives in
    grammar_ko_enrichment.json and an untranslated field is hidden instead.
    """
    rule = next(r for r in _merged_rules() if r["formation_notes"] and r["reference"])
    entry = _panel_entry(rule, "zh")
    assert re.search(r"[\u4e00-\u9fff]", entry["desc"]), entry["desc"]
    assert entry["formation"] == grammar_ko._KO_ENRICH[rule["key"]]["formation"]
    assert entry["notes"] == grammar_ko._KO_ENRICH[rule["key"]]["notes"]
    assert re.search(r"[\u4e00-\u9fff]", entry["reference"]["text"])
    for field in ("desc", "formation", "notes"):
        assert not re.search(r"[A-Za-z]{3,}", entry[field]), f"{field}: {entry[field]}"
    assert not re.search(r"[A-Za-z]{3,}", entry["reference"]["text"])


def test_korean_panel_keeps_the_formation_but_hides_the_english_notes():
    """
    ``notes`` is English prose that quotes Korean words, so Hangul alone
    cannot decide the language the way it does for the Japanese engine (whose
    data holds no Korean at all) -- it is hidden in a 한국어 panel, while the
    Korean 接续 line stays.  The reference block is hidden too: its sentence
    is Korean already, so there is no translation to add.
    """
    rule = next(r for r in _merged_rules() if r["formation_notes"])
    entry = _panel_entry(rule, "ko")
    assert entry["formation"] == rule["formation"]
    assert "notes" not in entry
    assert "reference" not in entry


# ---- vendored rows' 参考例句 (backfilled example_en) --------------------


def test_vendored_rows_carry_a_reference():
    """
    kimchi ships a translation for every one of its examples, but the
    generator used to drop it -- so the folded 参考例句 block appeared only
    on the merged material rows, and an ordinary page, where every matched
    point is a vendored row, showed none.  With ``example_en`` backfilled
    every vendored row that has examples curates one.
    """
    rules = [r for r in grammar_ko._DATA_RULES if r["reference"]]
    assert len(rules) >= 300, f"only {len(rules)} vendored rules carry a reference"
    for rule in rules:
        ref = rule["reference"]
        assert ref["korean"], rule["key"]
        assert ref["english"], rule["key"]


def test_vendored_reference_is_highlightable_where_possible():
    """
    The curated choice prefers the zh-carrying example (each vendored row
    translates only its first one), so a row whose first example is not
    spec-matchable quotes it bare rather than switching to a later,
    matchable example -- the Chinese panel reads the translation, the
    missing mark is cosmetic.  The guard below just pins the machinery:
    wherever the chosen example IS matchable the offsets are present.
    """
    rules = [r for r in grammar_ko._DATA_RULES if r["reference"]]
    highlightable = 0
    for rule in rules:
        korean = rule["reference"]["korean"]
        spans = grammar_ko._match_spans(rule, grammar_ko._tokens_for(korean), korean)
        if spans:
            highlightable += 1
    assert highlightable >= 170, f"only {highlightable} references highlightable"


def test_panel_references_are_marked_or_bare_never_half_marked():
    """
    A rule that fires on a page quotes its curated example with the display
    language's translation.  When the matcher can mark that example the
    block carries non-empty offsets; when it cannot (some rows' focus only
    occurs mid-word in their own examples) the block carries no offsets at
    all and the front-end quotes it bare -- never an empty matches list.
    """
    marked = 0
    sentences = [
        r["reference"]["korean"] for r in grammar_ko._DATA_RULES if r["reference"]
    ]
    for sentence in sentences:
        for e in analyze_korean(sentence, "en"):
            ref = e.get("reference")
            if not ref:
                continue
            matches = ref.get("matches")
            if matches is None:
                continue
            assert matches, f"{e['key']}: empty matches on {sentence}"
            marked += 1
    assert marked >= 170, f"only {marked} references carry offsets"


def test_vendored_reference_shows_on_both_translation_panels():
    """
    The backfilled translations are English for every example and Chinese
    for each row's first one, and the curated choice prefers a zh-carrying
    example (the en half exists for every index, so the English panel loses
    nothing).  So the English panel quotes the English half, and the Chinese
    panel -- which must never print English under a Chinese heading --
    quotes the Chinese half in proper Hanzi.
    """
    rules = [r for r in grammar_ko._DATA_RULES if r["reference"]]
    with_zh = [r for r in rules if r["reference"].get("chinese")]
    assert len(with_zh) >= 340, f"only {len(with_zh)} references carry a zh half"
    # Sibling senses merge into one panel entry (랑/이랑 = "and" / "together
    # with"), so the quoted example may come from whichever sibling fired
    # first -- what must hold is that every quoted sentence is paired with
    # its OWN translation.
    en_by_sentence = {
        r["reference"]["korean"]: r["reference"]["english"] for r in rules
    }
    zh_by_sentence = {
        r["reference"]["korean"]: r["reference"]["chinese"] for r in with_zh
    }
    verified = 0
    for rule in with_zh:
        sentence = rule["reference"]["korean"]
        en_hits = [
            e for e in analyze_korean(sentence, "en") if e["name"] == rule["pattern"]
        ]
        if not en_hits:
            # The rule cannot fire on its own first example; the block still
            # shows on a page that matches elsewhere, but there is nothing
            # to assert through the panel path here.
            continue
        en = en_hits[0]
        assert (
            en_by_sentence.get(en["reference"]["sentence"]) == en["reference"]["text"]
        )
        zh = next(
            e for e in analyze_korean(sentence, "zh") if e["name"] == rule["pattern"]
        )
        assert (
            zh_by_sentence.get(zh["reference"]["sentence"]) == zh["reference"]["text"]
        )
        assert re.search(r"[\u4e00-\u9fff]", zh["reference"]["text"]), rule["key"]
        assert not re.search(r"[A-Za-z]{3,}", zh["reference"]["text"]), rule["key"]
        verified += 1
    assert verified >= 100, f"only {verified} zh references verified end to end"
