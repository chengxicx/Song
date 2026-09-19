"""Tests for the Korean grammar-analysis engine (Kiwi + kimchi-grammar data)."""

import pytest

pytest.importorskip("kiwipiepy")

from lute.read.render.grammar_analysis_ko import _KO_RULES, analyze_korean
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
            (start, end) = example["matches"][0]["start"], example["matches"][0]["end"]
            # The rule starts at the ㄹ-ending (ETM) on the verb and runs to
            # the 있다 stem, so the matched slice spans the construction
            # "할 수 있" (Kiwi decomposes 할 into 하 + ᆯ, so the anchor is
            # Kiwi's character offset, not the surface lengths).
            assert example["sentence"][start:end] == "할 수 있"


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
