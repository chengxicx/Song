"""Tests for the Mandarin grammar-analysis engine (no external dependency)."""

from lute.read.render.grammar_analysis_zh import _ZH_RULES, analyze_chinese
from lute.read.render.grammar_analysis import is_mandarin_chinese_language


def _keys(text, lang="en"):
    return {e["key"] for e in analyze_chinese(text, lang)}


# One short, natural sentence per rule.
_RULE_SENTENCES = {
    "zh_le": "我吃了饭。",
    "zh_zhe": "他坐着看书。",
    "zh_guo": "我去过中国。",
    "zh_bu": "我不去。",
    "zh_mei": "我没去。",
    "zh_zai": "我在学校。",
    "zh_question_words": "你想吃什么？",
    "zh_ma": "你去吗？",
    "zh_ne": "你呢？",
    "zh_ba_q": "我们走吧。",
    "zh_ye_dou": "他们也都是学生。",
    "zh_ge": "一个人在家。",
    "zh_shi_de": "他是昨天来的。",
    "zh_ba_sentence": "他把作业写完了。",
    "zh_bei_sentence": "杯子被弟弟打破了。",
    "zh_zhengzai": "他正在看书。",
    "zh_cai_jiu": "他五点就来了。",
    "zh_geng_zui": "这个最好。",
    "zh_bibi": "他比我高。",
    "zh_yinwei_suoyi": "因为下雨，所以我不去。",
    "zh_hai": "他还在看书。",
    "zh_yueyue": "书越看越有趣。",
    "zh_yibian_yibian": "我们一边走一边聊。",
    "zh_ruguo": "如果我有钱，就去旅行。",
    "zh_chule": "除了他，大家都知道。",
    "zh_yijing": "他已经到了。",
    "zh_zhiqian_zhihou": "下课以后我去找你。",
    "zh_rang_jiao": "妈妈让我去买菜。",
    "zh_wulun": "不管多忙，他都要来。",
    "zh_erqie": "书很好，而且便宜。",
    "zh_jingran": "他竟然来了。",
    "zh_genju": "根据天气预报，明天有雨。",
    "zh_yimian": "早点走，以免堵车。",
    "zh_bijing": "他毕竟还是个孩子。",
    "zh_shenzhi": "他甚至连水都没喝。",
    "zh_hekuang": "大人都会出错，何况孩子。",
    "zh_nanyi": "这个问题难以回答。",
    "zh_yizhi": "大意以致失败。",
    "zh_ershi": "然而事实并非如此。",
}


def test_each_rule_matches_its_sentence():
    "Every rule must fire on its illustrative sentence."
    for key, sentence in _RULE_SENTENCES.items():
        hits = _keys(sentence)
        assert key in hits, f"rule {key} did not fire on: {sentence}"


def test_match_offsets_are_precise():
    "Matched offsets point at the pattern, not the whole sentence."
    for e in analyze_chinese("他把作业写完了。"):
        if e["key"] == "zh_ba_sentence":
            example = e["examples"][0]
            m = example["matches"][0]
            assert example["sentence"][m["start"] : m["end"]] == "把作业写完了"


def test_display_language_switches_desc():
    "Chinese display uses the hand-written zh description."
    zh = {e["key"]: e for e in analyze_chinese("我吃了饭。", "zh")}
    en = {e["key"]: e for e in analyze_chinese("我吃了饭。", "en")}
    assert "完成" in zh["zh_le"]["desc"]
    assert "completed" in en["zh_le"]["desc"]


def test_levels_are_cefr():
    "The Mandarin engine grades points with CEFR bands."
    levels = {e["key"]: e["level"] for e in analyze_chinese(" ".join(_RULE_SENTENCES.values()))}
    assert levels["zh_le"] == "A1"
    assert levels["zh_ba_sentence"] == "A2"


def test_analyze_output_structure():
    "Each entry exposes name/desc/examples with sentence and matches."
    for entry in analyze_chinese("我吃了饭。他是昨天来的。"):
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


def test_is_mandarin_chinese_language_detection():
    "Mandarin detection by parser type or name, excluding Classical Chinese."
    assert is_mandarin_chinese_language(StubLanguage(parser_type="lute_mandarin"))
    assert is_mandarin_chinese_language(StubLanguage(name="Mandarin Chinese"))
    assert is_mandarin_chinese_language(StubLanguage(name="中文"))
    assert not is_mandarin_chinese_language(StubLanguage(name="Classical Chinese"))
    assert not is_mandarin_chinese_language(StubLanguage(name="Cantonese"))
    assert not is_mandarin_chinese_language(None)


def test_rules_are_loaded():
    "The rule set is populated."
    assert len(_ZH_RULES) >= 30
