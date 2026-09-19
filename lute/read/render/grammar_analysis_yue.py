"""
Cantonese grammar point detection (no external dependency).

Like the Mandarin engine, rules are surface-based over character tokens:
Cantonese grammar lives in sentence-final particles, aspect suffixes and
word order.  Vocabulary follows written colloquial Cantonese (香港粵語);
standard-written Chinese text shares characters but the Cantonese-specific
particles simply will not appear, so the engine can be attached to a
Cantonese language without affecting Mandarin books.
"""

import re

from lute.read.render.grammar_analysis_matcher import (
    analyze_tokens,
    make_rule,
    spec_surface,
)

_PUNCT = set("，。！？；：、（）「」『』《》〈〉" "''…—·,.!?;:\"'()`")

_HAN = r"\u4e00-\u9fff"


def _tokens_for(sentence):
    "Character-level tokens; punctuation flagged for sequence skipping."
    return [
        {
            "surface": ch,
            "pos": "PUNCT" if ch in _PUNCT or ch.isspace() else "X",
            "idx": i,
        }
        for i, ch in enumerate(sentence)
    ]


def _re(pattern):
    return {"re": re.compile(pattern)}


_YUE_RULES = [
    # ---- A1 ----
    make_rule(
        "yue_ge",
        "嘅 (possessive / attributive)",
        "A1",
        "嘅 = Mandarin 的: 我嘅書 (my book).",
        _re(r"嘅"),
        zh="嘅 = 的：我嘅書（我的书）。",
    ),
    make_rule(
        "yue_zo",
        "咗 (completed action)",
        "A1",
        "verb + 咗 = Mandarin 了: 食咗飯 (have eaten).",
        _re(r"咗"),
        zh="动词 + 咗 = 了：食咗飯（吃了饭）。",
    ),
    make_rule(
        "yue_gan",
        "緊 (progressive)",
        "A1",
        "verb + 緊 = is doing: 食緊飯 (eating now).",
        _re(r"緊"),
        zh="动词 + 緊：正在（食緊飯 正在吃饭）。",
    ),
    make_rule(
        "yue_m",
        "唔 (not)",
        "A1",
        "唔 negates verbs and adjectives: 唔去 (not going), 唔好 (bad).",
        _re(r"唔"),
        zh="唔 = 不：唔去（不去）/ 唔好（不好）。",
    ),
    make_rule(
        "yue_pronouns",
        "Pronouns (我 / 你 / 佢 / 我哋)",
        "A1",
        "佢 = he/she, 我哋/你哋/佢哋 = we/you(all)/they (哋 = plural).",
        _re(r"佢|哋"),
        zh="人称代词：佢 他/她；哋 们（我哋 我们 / 佢哋 他们）。",
    ),
    make_rule(
        "yue_questions",
        "Question words (乜嘢 / 邊度 / 點解)",
        "A1",
        "乜嘢/咩 = what, 邊度 = where, 邊個 = who, 點解 = why, 幾多 = how much.",
        _re(r"乜嘢|咩|邊度|邊個|點解|幾多|點樣"),
        zh="疑问词：乜嘢 什么 / 邊度 哪里 / 點解 为什么 / 幾多 多少。",
    ),
    make_rule(
        "yue_hai",
        "喺 (at / in)",
        "A1",
        "喺 = Mandarin 在: 喺屋企 (at home).",
        _re(r"喺"),
        zh="喺 = 在：喺屋企（在家）。",
    ),
    make_rule(
        "yue_yiga",
        "而家 / 依家 (now)",
        "A1",
        "而家 = now: 而家去邊?",
        _re(r"而家|依家"),
        zh="而家 / 依家：现在。",
    ),
    # ---- A2 ----
    make_rule(
        "yue_zyu",
        "住 (durative suffix)",
        "A2",
        "verb + 住 = hold the state while doing: 拎住 (holding).",
        _re(r"住"),
        zh="动词 + 住：持续态（拎住 拿着）。",
    ),
    make_rule(
        "yue_sai",
        "晒 / 完 (completely / finished)",
        "A2",
        "verb + 晒 = all/entirely done: 食晒 (all eaten); 完 = finished.",
        _re(r"晒"),
        zh="动词 + 晒：全部完成（食晒 全吃完了）。",
    ),
    make_rule(
        "yue_gam",
        "咁 (so / like that)",
        "A2",
        "咁 before adjectives = so; 噉 = like that: 咁靚 (so pretty).",
        _re(r"咁|噉"),
        zh="咁：这么/那么（咁靚 这么漂亮）；噉 那样。",
    ),
    make_rule(
        "yue_zung",
        "仲 (still / even more)",
        "A2",
        "仲 = still / additionally: 仲未食 (not eaten yet).",
        _re(r"仲"),
        zh="仲：还/还要（仲未 还没）。",
    ),
    make_rule(
        "yue_tungmaai",
        "同埋 / 定係 (and / or)",
        "A2",
        "同埋 = and, 定係 = or (questions).",
        _re(r"同埋|定係|定還是"),
        zh="同埋 和；定係 还是（选择问）。",
    ),
    make_rule(
        "yue_final_particles",
        "Sentence particles (啦 / 喎 / 囉 / 吖)",
        "A2",
        "final particles add tone: 啦 (suggestion), 喎 (obvious), 囉 (so be it).",
        _re(r"啦|喎|囉|吖|啩|咯"),
        zh="句末语气词：啦（建议）/ 喎（理所当然）/ 囉 / 吖 / 啩（吧）/ 咯。",
    ),
    make_rule(
        "yue_sik",
        "識 (know how to / can)",
        "A2",
        "識 + verb = know how to: 識講廣東話.",
        _re(r"識"),
        zh="識：会/懂得（識講 会说）。",
    ),
    make_rule(
        "yue_msai",
        "唔使 (needn't) / 使唔使 (need?)",
        "A2",
        "唔使 = no need; 使唔使 = need or not?",
        _re(r"唔使|使唔使"),
        zh="唔使 不用；使唔使 用不用。",
    ),
    # ---- B1 ----
    make_rule(
        "yue_dak",
        "得 (can / complement)",
        "B1",
        "得 after verb = able; 講得快 (speak fast) complement marker.",
        _re(r"得"),
        zh="得：能（去得）/ 补语标记（講得快 说得快）。",
    ),
    make_rule(
        "yue_maai",
        "埋 (also / in addition)",
        "B1",
        "verb + 埋 = including this too: 買埋呢個 (buy this one as well).",
        _re(r"埋"),
        zh="动词 + 埋：连……也/一并（買埋 一并买）。",
    ),
    make_rule(
        "yue_sik_jyu",
        "好似 ... 噉 (as if / like)",
        "B1",
        "好似 marks a simile, often closed by 噉/咁: 好似天文台噉講.",
        _re(r"好似[" + _HAN + r"]{1,8}(?:噉|咁)"),
        zh="好似 … 噉/咁：像……一样。",
    ),
    make_rule(
        "yue_jauh_sik",
        "就算 ... 都 (even if)",
        "B1",
        "就算 concedes, the result clause often carries 都/都會.",
        _re(r"就算[" + _HAN + r"]{0,12}都"),
        zh="就算 … 都：即使……也。",
    ),
    make_rule(
        "yue_jyuhai",
        "冇 (not have / didn't)",
        "B1",
        "冇 = 没: 冇錢 (no money), 冇去 (didn't go).",
        _re(r"冇"),
        zh="冇 = 没：冇錢（没钱）/ 冇去（没去）。",
    ),
    # ---- B2 ----
    make_rule(
        "yue_mdaanzi",
        "唔單止 ... 仲 (not only ... but also)",
        "B2",
        "唔單止 opens, 仲(要) closes the escalation.",
        _re(r"唔單[止只][" + _HAN + r"]{0,12}仲"),
        zh="唔單止 … 仲：不但……还……。",
    ),
    make_rule(
        "yue_mhtung",
        "唔通 (could it be that)",
        "B2",
        " rhetorical doubt marker: 唔通佢唔知? = Could it be he doesn't know?",
        _re(r"唔通"),
        zh="唔通：难道（唔通佢唔知？难道他不知道？）。",
    ),
    make_rule(
        "yue_mingming",
        "明明 (clearly)",
        "B2",
        "明明 stresses an obvious fact being contradicted.",
        _re(r"明明"),
        zh="明明：明明（表明显却相反）。",
    ),
    # ---- C1 ----
    make_rule(
        "yue_lahngwaah",
        "講真 / 話時話 (honestly / by the way)",
        "C1",
        "colloquial discourse markers: 講真 (honestly), 話時話 (incidentally).",
        _re(r"講真|話時話|老實講"),
        zh="講真 / 話時話 / 老實講：说实话/顺带一提（口语标记）。",
    ),
    make_rule(
        "yue_ngaanghai",
        "硬係 (insisting)",
        "C1",
        "硬係 insists obstinately: 佢硬係唔信.",
        _re(r"硬係"),
        zh="硬係：偏要/硬是（佢硬係唔信 他偏不信）。",
    ),
]


def analyze_cantonese(page_text, display_lang="en"):
    """
    Analyze a page of Cantonese text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(page_text, _YUE_RULES, _tokens_for, display_lang)
