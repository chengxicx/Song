"""
Thai grammar point detection (pythainlp).

Thai is analytic: no inflection, so unlike the European engines the rules
match particle and function-word tokens rather than morphological
features.  pythainlp word_tokenize splits the text into words; whitespace
tokens are dropped (Thai spaces separate clauses, not words) and token
offsets are recovered by scanning forward in the original sentence.

pythainlp is imported lazily so the base install works without it; the
route falls back to the generic rule library on ImportError.
"""

import re

from lute.read.render.grammar_analysis_matcher import (
    analyze_tokens,
    make_rule,
    spec_surface,
)

_TOKENIZER = None


def _tokenizer():
    "Lazy, process-lifetime pythainlp word_tokenize."
    global _TOKENIZER
    if _TOKENIZER is None:
        from pythainlp.tokenize import word_tokenize  # pylint: disable=import-outside-toplevel

        _TOKENIZER = word_tokenize
    return _TOKENIZER


def _tokens_for(sentence):
    "Tokenize a sentence; return a list of token dicts."
    tokenize = _tokenizer()
    tokens = []
    pos = 0
    for surface in tokenize(sentence, keep_whitespace=True):
        if not surface.strip():
            # Thai spaces separate clauses/phrases, not words; dropping them
            # lets sequences match across phrase breaks.
            continue
        idx = sentence.find(surface, pos)
        if idx < 0:
            continue
        pos = idx + len(surface)
        tokens.append({"surface": surface, "idx": idx})
    return tokens


# ---- rules -------------------------------------------------------------
#
# Levels use CEFR bands (A1..C2) as a display approximation.  All rules are
# surface-based; Thai carries no morphology to condition on.

_TH_RULES = [
    # ---- A1 ----
    make_rule(
        "th_pronouns",
        "Pronouns (ผม / ฉัน / คุณ / เขา)",
        "A1",
        "ผม (male I), ฉัน (female I), คุณ (you), เขา (he/she), เรา (we).",
        {"seq": [spec_surface("ผม", "ฉัน", "คุณ", "เขา", "เรา", "เธอ", "พวกเรา", "พวกเขา")]},
        zh="人称代词：ผม（男我）/ ฉัน（女我）/ คุณ（你）/ เขา（他/她）。",
    ),
    make_rule(
        "th_copula",
        "เป็น / อยู่ / คือ (to be)",
        "A1",
        "เป็น = is (a kind), อยู่ = is at (location), คือ = is (identity).",
        {"seq": [spec_surface("เป็น", "อยู่", "คือ", "ชื่อ")]},
        zh="系动词：เป็น（是某类）/ อยู่（在某处）/ คือ（就是）。",
    ),
    make_rule(
        "th_negation",
        "ไม่ (not)",
        "A1",
        "ไม่ before a verb or adjective negates it: ไม่ไป, ไม่ดี.",
        {"seq": [spec_surface("ไม่")]},
        zh="否定词 ไม่：置于动词/形容词前（ไม่ไป 不去）。",
    ),
    make_rule(
        "th_questions",
        "Question words (อะไร / ที่ไหน / ใคร / ทำไม)",
        "A1",
        "อะไร = what, ที่ไหน = where, ใคร = who, ทำไม = why, เมื่อไหร่ = when.",
        {"seq": [spec_surface("อะไร", "ที่ไหน", "ไหน", "ใคร", "ทำไม", "เมื่อไหร่", "เมื่อไร", "เท่าไหร่", "กี่")]},
        zh="特殊疑问词：อะไร 什么 / ที่ไหน 哪里 / ใคร 谁 / ทำไม 为什么。",
    ),
    make_rule(
        "th_have",
        "มี (have / there is)",
        "A1",
        "มี = have or there is: มีรถ, มีคนมาก.",
        {"seq": [spec_surface("มี")]},
        zh="มี：有/存在。",
    ),
    make_rule(
        "th_polite",
        "Polite particles (ครับ / ค่ะ / นะ)",
        "A1",
        "ครับ (male) and ค่ะ/คะ (female) make sentences polite.",
        {"seq": [spec_surface("ครับ", "ค่ะ", "คะ", "นะ")]},
        zh="礼貌语气词：ครับ（男）/ ค่ะ/คะ（女）/ นะ（柔和语气）。",
    ),
    make_rule(
        "th_and_or",
        "และ / หรือ / แต่ (and / or / but)",
        "A1",
        "basic conjunctions: และ = and, หรือ = or, แต่ = but.",
        {"seq": [spec_surface("และ", "หรือ", "แต่")]},
        zh="基本连词：และ 和 / หรือ 或 / แต่ 但是。",
    ),
    # ---- A2 ----
    make_rule(
        "th_future",
        "จะ (future)",
        "A2",
        "จะ before a verb marks the future: ฉันจะไป.",
        {"seq": [spec_surface("จะ")]},
        zh="จะ + 动词：将来时标记（จะไป 将去）。",
    ),
    make_rule(
        "th_progressive",
        "กำลัง / อยู่ (progressive)",
        "A2",
        "กำลัง before or อยู่ after a verb marks ongoing action: กำลังอ่าน.",
        {"seq": [spec_surface("กำลัง")]},
        zh="进行体：กำลัง + 动词（或动词 + อยู่），正在……。",
    ),
    make_rule(
        "th_completive",
        "แล้ว (already / completive)",
        "A2",
        "แล้ว marks completion or a change of state: กินแล้ว.",
        {"seq": [spec_surface("แล้ว")]},
        zh="แล้ว：完成/已经，或状态变化（กินแล้ว 已经吃了）。",
    ),
    make_rule(
        "th_want",
        "อยาก / ต้องการ (want)",
        "A2",
        "อยาก + verb = want to do; ต้องการ = want/need.",
        {"seq": [spec_surface("อยาก", "ต้องการ")]},
        zh="อยาก + 动词：想要做；ต้องการ：需要。",
    ),
    make_rule(
        "th_should_must",
        "ควร / ต้อง (should / must)",
        "A2",
        "ควร = should, ต้อง = must/have to: ต้องไป.",
        {"seq": [spec_surface("ควร", "ต้อง")]},
        zh="ควร 应该 / ต้อง 必须。",
    ),
    make_rule(
        "th_yesno",
        "ไหม / ไหมคะ (yes-no question)",
        "A2",
        "sentence-final ไหม (มั้ย colloquial) makes a yes/no question.",
        {"seq": [spec_surface("ไหม", "มั้ย", "หรือเปล่า", "เปล่า")]},
        zh="句尾 ไหม/มั้ย（或 หรือเปล่า）：构成一般疑问句。",
    ),
    make_rule(
        "th_because",
        "เพราะ / เพราะว่า (because)",
        "A2",
        "เพราะว่า = because; เพราะฉะนั้น = therefore.",
        {"seq": [spec_surface("เพราะ", "เพราะว่า", "เพราะฉะนั้น", "ดังนั้น")]},
        zh="เพราะ(ว่า) 因为；ดังนั้น/เพราะฉะนั้น 所以。",
    ),
    make_rule(
        "th_when",
        "เมื่อ / ตอนที่ (when)",
        "A2",
        "เมื่อ and ตอนที่ introduce time clauses.",
        {"seq": [spec_surface("เมื่อ", "ตอนที่", "ตอน")]},
        zh="เมื่อ / ตอนที่：当……的时候。",
    ),
    # ---- B1 ----
    make_rule(
        "th_thi",
        "ที่ (relativizer / nominalizer)",
        "B1",
        "ที่ links a describing clause to a noun: คนที่มา = the person who came.",
        {
            "any_of": [
                {"seq": [spec_surface("ที่")]},
                {"seq": [{"surface_re": re.compile(r"ที่.+")}]},
            ]
        },
        zh="ที่：定语从句标记（คนที่มา 来的人），也可名词化。",
    ),
    make_rule(
        "th_can",
        "สามารถ ... ได้ (can)",
        "B1",
        "สามารถ before and ได้ after the verb frame ability: สามารถทำได้.",
        {"left": [spec_surface("สามารถ")], "right": [spec_surface("ได้")], "min_gap": 1, "max_gap": 4},
        zh="สามารถ … ได้ 框型：能够做……。",
    ),
    make_rule(
        "th_ever",
        "เคย (ever / once)",
        "B1",
        "เคย before a verb marks past experience: เคยไป = have been.",
        {"seq": [spec_surface("เคย")]},
        zh="เคย + 动词：曾经……过（经历）。",
    ),
    make_rule(
        "th_give",
        "ให้ (give / let / for)",
        "B1",
        "ให้ = give, let someone do, or for (someone): ทำให้ฉัน.",
        {
            "any_of": [
                {"seq": [spec_surface("ให้")]},
                {"seq": [{"surface_re": re.compile(r"ให้.+")}]},
            ]
        },
        zh="ให้：给/让/为（多义高频：ทำให้ 使得）。",
    ),
    make_rule(
        "th_each_other",
        "กัน (each other)",
        "B1",
        "กัน after a verb = each other: รักกัน.",
        {"seq": [spec_surface("กัน", "กันและกัน")]},
        zh="กัน：互相（รักกัน 相爱）。",
    ),
    make_rule(
        "th_if",
        "ถ้า / หาก (if)",
        "B1",
        "ถ้า introduces the condition, often paired with ก็ in the result.",
        {"seq": [spec_surface("ถ้า", "หาก", "ถ้าหาก")]},
        zh="ถ้า/หาก：如果（常与结果分句的 ก็ 呼应）。",
    ),
    make_rule(
        "th_na_worth",
        "น่า + verb (worth / looks)",
        "B1",
        "น่า + verb = worth doing or looks ...: น่าสนใจ, น่ากลัว.",
        {
            "any_of": [
                {"seq": [spec_surface("น่า")]},
                {"seq": [{"surface_re": re.compile(r"น่า.+")}]},
            ]
        },
        zh="น่า + 动词：值得……/看上去……（น่าสนใจ 有趣）。",
    ),
    make_rule(
        "th_also",
        "ด้วย / ก็ (also / too)",
        "B1",
        "ด้วย = also/with; ก็ emphasizes or means also.",
        {"seq": [spec_surface("ด้วย", "ก็")]},
        zh="ด้วย 也/以；ก็ 也/就（强调）。",
    ),
    # ---- B2 ----
    make_rule(
        "th_passive",
        "ถูก / โดน (passive)",
        "B2",
        "ถูก (neutral) or โดน (adverse) before a verb marks the passive: ถูกขโมย.",
        {"seq": [spec_surface("ถูก", "โดน")]},
        zh="被动标记：ถูก（中性）/ โดน（遭受不愉快）。",
    ),
    make_rule(
        "th_both",
        "ทั้ง ... และ (both ... and)",
        "B2",
        "ทั้ง before the first item and และ before the last one.",
        {"left": [spec_surface("ทั้ง")], "right": [spec_surface("และ")], "min_gap": 1, "max_gap": 4},
        zh="ทั้ง … และ：既……又……。",
    ),
    make_rule(
        "th_almost",
        "เกือบ (almost)",
        "B2",
        "เกือบ before a verb or adjective: เกือบตาย.",
        {
            "any_of": [
                {"seq": [spec_surface("เกือบ")]},
                {"seq": [{"surface_re": re.compile(r"เกือบ.+")}]},
            ]
        },
        zh="เกือบ：几乎、差点。",
    ),
    make_rule(
        "th_concessive",
        "แม้ว่า / ถึงแม้ (although)",
        "B2",
        "แม้ว่า/ถึงแม้ introduces a concession, often paired with แต่.",
        {"seq": [spec_surface("แม้ว่า", "ถึงแม้", "แม้")]},
        zh="แม้ว่า / ถึงแม้：虽然、尽管。",
    ),
    # ---- C1 ----
    make_rule(
        "th_in_order_to",
        "เพื่อ (in order to)",
        "C1",
        "เพื่อ + verb/noun marks purpose: เรียนเพื่อรู้.",
        {"seq": [spec_surface("เพื่อ")]},
        zh="เพื่อ：为了（目的）。",
    ),
    make_rule(
        "th_formal_concessive",
        "อย่างไรก็ตาม (however)",
        "C1",
        "formal written concessives: อย่างไรก็ตาม, อย่างไรก็ดี.",
        {"seq": [spec_surface("อย่างไรก็ตาม", "อย่างไรก็ดี", "ไม่ว่า")]},
        zh="อย่างไรก็ตาม：然而、无论如何（书面语）。",
    ),
]


def analyze_thai(page_text, display_lang="en"):
    """
    Analyze a page of Thai text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(page_text, _TH_RULES, _tokens_for, display_lang)
