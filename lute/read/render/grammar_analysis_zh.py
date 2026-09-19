"""
Mandarin Chinese grammar point detection (no external dependency).

Chinese is analytic: grammar lives in particles and word order, not
inflection, so the rules are surface-based.  Sentences are tokenised
character by character (punctuation flagged so sequence matching can skip
it), and the rules use regex matchers for single particles plus gapped
token patterns for frames like 是…的 and 越…越.

Classical Chinese falls outside this engine's design (modern particles
are rare there); the rules simply under-fire on such text.
"""

import re

from lute.read.render.grammar_analysis_matcher import (
    analyze_tokens,
    make_rule,
    spec_surface,
)

_PUNCT = set("，。！？；：、（）「」『』《》〈〉""''…—·,.!?;:\"'()`")

_HAN = r"\u4e00-\u9fff"


def _tokens_for(sentence):
    "Character-level tokens; punctuation flagged for sequence skipping."
    return [
        {"surface": ch, "pos": "PUNCT" if ch in _PUNCT or ch.isspace() else "X", "idx": i}
        for i, ch in enumerate(sentence)
    ]


# ---- rules -------------------------------------------------------------
#
# Levels use CEFR bands (A1..C2) as a display approximation.  Regex rules
# match the raw sentence; token rules work on the character stream.

def _re(pattern):
    return {"re": re.compile(pattern)}


_ZH_RULES = [
    # ---- A1 ----
    make_rule(
        "zh_le",
        "了 (completed action)",
        "A1",
        "verb + 了 marks a completed action or new situation: 我吃了.",
        _re(r"了"),
        zh="动词 + 了：动作完成/情况变化（我吃了饭）。",
    ),
    make_rule(
        "zh_zhe",
        "着 (ongoing / state)",
        "A1",
        "verb + 着 marks a continuing state or manner: 坐着, 拿着.",
        _re(r"着"),
        zh="动词 + 着：状态持续或伴随方式（坐着 / 拿着包）。",
    ),
    make_rule(
        "zh_guo",
        "过 (past experience)",
        "A1",
        "verb + 过 marks experienced (not ongoing) past: 去过中国.",
        _re(r"过"),
        zh="动词 + 过：曾经经历（去过 / 吃过），区别于 了。",
    ),
    make_rule(
        "zh_bu",
        "不 (negation)",
        "A1",
        "不 negates present/future or habitual actions: 我不去.",
        _re(r"不"),
        zh="不：否定现在/将来或习惯性动作（我不去 / 不好）。",
    ),
    make_rule(
        "zh_mei",
        "没 / 没有 (past negation)",
        "A1",
        "没(有) negates past events or marks 'not yet': 我没去.",
        _re(r"没"),
        zh="没(有)：否定过去经历或『还没』（我没去 / 还没吃）。",
    ),
    make_rule(
        "zh_zai",
        "在 (at / doing)",
        "A1",
        "在 + place = at; 在 + verb = progressive: 在学校, 在看书.",
        _re(r"在"),
        zh="在：地点（在学校）或进行体（在看书）。",
    ),
    make_rule(
        "zh_question_words",
        "Question words (什么 / 哪 / 谁 / 怎么)",
        "A1",
        "什么 what, 哪 which/where, 谁 who, 怎么 how, 几/多少 how many.",
        _re(r"什么|哪个|哪儿|哪里|谁|几|多少|怎么"),
        zh="特殊疑问词：什么 / 哪 / 谁 / 怎么 / 几 / 多少。",
    ),
    make_rule(
        "zh_ma",
        "吗 (yes-no question)",
        "A1",
        "sentence-final 吗 turns a statement into a yes/no question.",
        _re(r"吗"),
        zh="句尾 吗：一般疑问（你去吗？）。",
    ),
    make_rule(
        "zh_ne",
        "呢 (and you? / ongoing)",
        "A1",
        "sentence-final 呢 asks the question back or softens a statement.",
        _re(r"呢"),
        zh="句尾 呢：反问（你呢？）或延续语气。",
    ),
    make_rule(
        "zh_ba_q",
        "吧 (suggestion)",
        "A1",
        "sentence-final 吧 softens into a suggestion or guess: 走吧.",
        _re(r"吧"),
        zh="句尾 吧：建议或推测（走吧 / 他是学生吧）。",
    ),
    make_rule(
        "zh_ye_dou",
        "也 / 都 (also / all)",
        "A1",
        "也 = also, 都 = all/both; both come after the topic.",
        _re(r"也|都"),
        zh="也 也；都 都（放在主语后、动词前）。",
    ),
    make_rule(
        "zh_ge",
        "个 (common measure word)",
        "A1",
        "个 is the general classifier: 一个人, 三个问题.",
        _re(r"个"),
        zh="量词 个：通用量词（一个人 / 三个问题）。",
    ),
    # ---- A2 ----
    make_rule(
        "zh_shi_de",
        "是 ... 的 (emphasis frame)",
        "A1",
        "是 ... 的 highlights when/how/where a known event happened: "
        "他是昨天来的.",
        {"left": [spec_surface("是")], "right": [spec_surface("的")], "min_gap": 1, "max_gap": 8},
        zh="是 … 的：强调已发生事件的时间/方式/地点（他是昨天来的）。",
    ),
    make_rule(
        "zh_ba_sentence",
        "把 sentence (disposal)",
        "A2",
        "把 moves the object before the verb, which takes a complement: "
        "把作业写完了.",
        _re(r"把[" + _HAN + r"]{1,6}(?:了|到|在|成|得|给|好|完)"),
        zh="把字句：宾语前移，动词带补语（把作业写完了）。",
    ),
    make_rule(
        "zh_bei_sentence",
        "被 sentence (passive)",
        "A2",
        "被 introduces the agent of a passive-like verb phrase: 被雨淋了.",
        _re(r"被[" + _HAN + r"]"),
        zh="被字句：被动（被雨淋了）；口语也用 让/叫/给。",
    ),
    make_rule(
        "zh_zhengzai",
        "正在 (right now)",
        "A2",
        "正在 marks action in progress: 他正在看书.",
        _re(r"正在"),
        zh="正在 + 动词：此刻正在进行。",
    ),
    make_rule(
        "zh_cai_jiu",
        "才 / 就 (timing)",
        "A2",
        "就 = earlier than expected, 才 = later than expected.",
        _re(r"才|就"),
        zh="就：比预期早/顺利；才：比预期晚/仅（他五点就来了 / 才来）。",
    ),
    make_rule(
        "zh_geng_zui",
        "更 / 最 (comparison)",
        "A2",
        "更 = even more, 最 = most: 更好, 最好.",
        _re(r"更|最"),
        zh="更 更加；最 最（比较级/最高级标记）。",
    ),
    make_rule(
        "zh_bibi",
        "比 (than)",
        "A2",
        "X 比 Y + adjective compares: 他比我高.",
        _re(r"比"),
        zh="比字句：X 比 Y + 形容词（他比我高）。",
    ),
    make_rule(
        "zh_yinwei_suoyi",
        "因为 / 所以 (because / so)",
        "A2",
        "因为 states the cause, 所以 the result.",
        _re(r"因为|所以"),
        zh="因为 因为；所以 所以。",
    ),
    make_rule(
        "zh_hai",
        "还 (still / also)",
        "A2",
        "还 = still, in addition: 他还在看书, 还有一个.",
        _re(r"还"),
        zh="还：还/还有（持续或追加）。",
    ),
    # ---- B1 ----
    make_rule(
        "zh_yueyue",
        "越 ... 越 ... (the more ... the more)",
        "B1",
        "paired 越 clauses correlate: 越来越好, 越说越糊涂.",
        _re(r"越[" + _HAN + r"]{1,8}越"),
        zh="越 … 越 …：越说越糊涂（两个 越 呼应）。",
    ),
    make_rule(
        "zh_yibian_yibian",
        "一边 ... 一边 ...",
        "B1",
        "two simultaneous actions: 一边走一边聊.",
        _re(r"一边[" + _HAN + r"]{1,8}一边"),
        zh="一边 … 一边 …：同时进行（一边走一边聊）。",
    ),
    make_rule(
        "zh_ruguo",
        "如果 / 要是 (if)",
        "B1",
        "如果/要是 introduce the condition, often paired with 就.",
        _re(r"如果|要是|假如|要是说"),
        zh="如果 / 要是：如果（常与 就 呼应）。",
    ),
    make_rule(
        "zh_chule",
        "除了 (except / besides)",
        "B1",
        "除了 ... 以外/都/还 patterns: 除了他都知道.",
        _re(r"除了"),
        zh="除了：除……之外（配合 以外/都/还）。",
    ),
    make_rule(
        "zh_yijing",
        "已经 (already)",
        "B1",
        "已经 marks completion relative to a reference time.",
        _re(r"已经"),
        zh="已经：已经（常与 了 呼应）。",
    ),
    make_rule(
        "zh_zhiqian_zhihou",
        "以前 / 以后 / 之前 / 之后 (before / after)",
        "B1",
        "time-relative frames: 来中国以前, 下课以后.",
        _re(r"以前|以后|之前|之后"),
        zh="以前 / 之后：在……以前 / 在……之后。",
    ),
    make_rule(
        "zh_rang_jiao",
        "让 / 叫 (causative / passive)",
        "B1",
        "让/叫 make causatives or colloquial passives: 让他去, 叫人骗了.",
        _re(r"让|叫"),
        zh="让 / 叫：使役（让他去）；也可表被动（叫人骗了）。",
    ),
    # ---- B2 ----
    make_rule(
        "zh_wulun",
        "无论 / 不管 (regardless)",
        "B2",
        "无论/不管 + question word or alternatives + 都: 不管多忙都要来.",
        _re(r"无论|不管"),
        zh="无论 / 不管：无论……都（配疑问词或并列项）。",
    ),
    make_rule(
        "zh_erqie",
        "而且 / 并且 (moreover)",
        "B2",
        "additive conjunctions, 而且 also marks escalation.",
        _re(r"而且|并且"),
        zh="而且 / 并且：而且/并且（常递进）。",
    ),
    make_rule(
        "zh_jingran",
        "竟然 / 居然 (unexpectedly)",
        "B2",
        "adverbs of surprise: 他竟然来了.",
        _re(r"竟然|居然"),
        zh="竟然 / 居然：出乎意料（他竟然来了）。",
    ),
    make_rule(
        "zh_genju",
        "根据 / 按照 (according to)",
        "B2",
        "prepositions framing the basis of an action.",
        _re(r"根据|按照|依照"),
        zh="根据 / 按照：根据……（依据）。",
    ),
    make_rule(
        "zh_yimian",
        "以免 (lest / so as to avoid)",
        "B2",
        "以免 introduces an avoided outcome: 早走以免堵车.",
        _re(r"以免"),
        zh="以免：以免……（避免某种结果）。",
    ),
    # ---- C1 ----
    make_rule(
        "zh_bijing",
        "毕竟 / 反正 (after all / anyway)",
        "C1",
        "discourse adverbs conceding or summing up.",
        _re(r"毕竟|反正|无非"),
        zh="毕竟 / 反正：说到底/横竖（总结性副词）。",
    ),
    make_rule(
        "zh_shenzhi",
        "甚至 (even)",
        "C1",
        "escalation marker: 甚至连水都没喝.",
        _re(r"甚至"),
        zh="甚至：甚至（强调递进极端）。",
    ),
    make_rule(
        "zh_hekuang",
        "何况 / 况且 (moreover)",
        "C1",
        "formal additive argument: 大人都会, 何况孩子.",
        _re(r"何况|况且"),
        zh="何况 / 况且：更不用说/况且（递进论证）。",
    ),
    make_rule(
        "zh_nanyi",
        "加以 / 予以 / 难以 (formal verb framing)",
        "C1",
        "formal written constructions: 加以解决, 难以接受.",
        _re(r"加以|予以|难以"),
        zh="加以 / 予以 / 难以：书面语动词框架。",
    ),
    # ---- C2 ----
    make_rule(
        "zh_yizhi",
        "以至 / 以致 (so that / resulting in)",
        "C2",
        "result connectors, 以致 usually negative outcomes.",
        _re(r"以至|以致"),
        zh="以至 / 以致：以至于/导致（以致多指不良结果）。",
    ),
    make_rule(
        "zh_ershi",
        "然而 / 却 (however)",
        "C2",
        "written adversatives: 然而事实并非如此.",
        _re(r"然而|却|则"),
        zh="然而 / 却 / 则：书面转折。",
    ),
]


def analyze_chinese(page_text, display_lang="en"):
    """
    Analyze a page of Mandarin text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(page_text, _ZH_RULES, _tokens_for, display_lang)
