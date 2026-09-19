"""Tests for the Cantonese grammar-analysis engine (no external dependency)."""

from lute.read.render.grammar_analysis_yue import _YUE_RULES, analyze_cantonese
from lute.read.render.grammar_analysis import is_cantonese_language


def _keys(text, lang="en"):
    return {e["key"] for e in analyze_cantonese(text, lang)}


# One short, natural sentence per rule (written colloquial Cantonese).
_RULE_SENTENCES = {
    "yue_ge": "呢本係我嘅書。",
    "yue_zo": "我食咗飯。",
    "yue_gan": "佢睇緊電視。",
    "yue_m": "佢唔去。",
    "yue_pronouns": "佢哋都嚟。",
    "yue_questions": "你去邊度？",
    "yue_hai": "佢喺屋企。",
    "yue_yiga": "而家去邊？",
    "yue_zyu": "佢拎住個袋。",
    "yue_sai": "我食晒啲飯。",
    "yue_gam": "今日咁靚。",
    "yue_zung": "佢仲未食。",
    "yue_tungmaai": "我要飯同埋麵。",
    "yue_final_particles": "一齊去啦！",
    "yue_sik": "佢識講廣東話。",
    "yue_msai": "你唔使擔心。",
    "yue_dak": "呢道菜講得快。",
    "yue_maai": "順便買埋呢個。",
    "yue_sik_jyu": "佢好似天文台噉講。",
    "yue_jauh_sik": "就算落雨都去。",
    "yue_jyuhai": "佢冇錢。",
    "yue_mdaanzi": "佢唔單止識唱仲識跳。",
    "yue_mhtung": "唔通佢唔知？",
    "yue_mingming": "明明佢應承咗。",
    "yue_lahngwaah": "講真呢間店唔錯。",
    "yue_ngaanghai": "佢硬係唔信。",
}


def test_each_rule_matches_its_sentence():
    "Every rule must fire on its illustrative sentence."
    for key, sentence in _RULE_SENTENCES.items():
        hits = _keys(sentence)
        assert key in hits, f"rule {key} did not fire on: {sentence}"


def test_match_offsets_are_precise():
    "Matched offsets point at the particle, not the whole sentence."
    for e in analyze_cantonese("我食咗飯。"):
        if e["key"] == "yue_zo":
            example = e["examples"][0]
            m = example["matches"][0]
            assert example["sentence"][m["start"] : m["end"]] == "咗"


def test_display_language_switches_desc():
    "Chinese display uses the hand-written zh description."
    zh = {e["key"]: e for e in analyze_cantonese("我食咗飯。", "zh")}
    en = {e["key"]: e for e in analyze_cantonese("我食咗飯。", "en")}
    assert "了" in zh["yue_zo"]["desc"]
    assert "have eaten" in en["yue_zo"]["desc"].lower()


def test_levels_are_cefr():
    "The Cantonese engine grades points with CEFR bands."
    levels = {
        e["key"]: e["level"]
        for e in analyze_cantonese(" ".join(_RULE_SENTENCES.values()))
    }
    assert levels["yue_ge"] == "A1"
    assert levels["yue_maai"] == "B1"


def test_analyze_output_structure():
    "Each entry exposes name/desc/examples with sentence and matches."
    for entry in analyze_cantonese("我食咗飯。佢唔去。"):
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


def test_is_cantonese_language_detection():
    "Cantonese detection by parser type or name; Mandarin does not match."
    assert is_cantonese_language(StubLanguage(parser_type="lute_cantonese"))
    assert is_cantonese_language(StubLanguage(name="Cantonese"))
    assert is_cantonese_language(StubLanguage(name="粵語"))
    assert not is_cantonese_language(StubLanguage(name="Mandarin Chinese"))
    assert not is_cantonese_language(None)


def test_rules_are_loaded():
    "The rule set is populated."
    assert len(_YUE_RULES) >= 20
