"""
Thai grammar point detection (pythainlp) and the Thai grammar library.

Thai is analytic: no inflection, so unlike the European engines the rules
match particle and function-word tokens rather than morphological
features.  pythainlp word_tokenize splits the text into words; whitespace
tokens are dropped (Thai spaces separate clauses, not words) and token
offsets are recovered by scanning forward in the original sentence.

Two layers of rules:

  * ``_TH_RULES`` -- a handful of hand-written rules for shapes the corpus
    cannot state as a literal (a frame such as สามารถ ... ได้, the น่า
    prefix).  These stay token-based.
  * the corpus in ``jlpt_data/grammar_th.json`` -- one row per grammar
    point, keyed to the section number of the reference outline it came
    from (see ATTRIBUTION.md).  A row fires when one of its ``focus``
    literals occurs in the sentence.  The match runs over the sentence
    text rather than over tokens: pythainlp glues function words to their
    neighbours (มาจาก, ทางขวา, ไปเสียแล้ว), so token-exact matching would
    miss the particles a reader most wants flagged.

pythainlp is imported lazily so the base install works without it; the
route falls back to the generic rule library on ImportError.
"""

import json
import os
import re

from lute.read.render.grammar_analysis_matcher import (
    analyze_tokens,
    load_golden,
    make_rule,
    spec_surface,
)

_TOKENIZER = None


def _tokenizer():
    "Lazy, process-lifetime pythainlp word_tokenize."
    global _TOKENIZER
    if _TOKENIZER is None:
        from pythainlp.tokenize import (
            word_tokenize,
        )  # pylint: disable=import-outside-toplevel

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
        {
            "seq": [
                spec_surface(
                    "ผม", "ฉัน", "คุณ", "เขา", "เรา", "เธอ", "พวกเรา", "พวกเขา"
                )
            ]
        },
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
        {
            "seq": [
                spec_surface(
                    "อะไร",
                    "ที่ไหน",
                    "ไหน",
                    "ใคร",
                    "ทำไม",
                    "เมื่อไหร่",
                    "เมื่อไร",
                    "เท่าไหร่",
                    "กี่",
                )
            ]
        },
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
        {
            "left": [spec_surface("สามารถ")],
            "right": [spec_surface("ได้")],
            "min_gap": 1,
            "max_gap": 4,
        },
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
        {
            "left": [spec_surface("ทั้ง")],
            "right": [spec_surface("และ")],
            "min_gap": 1,
            "max_gap": 4,
        },
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


# ---- corpus ------------------------------------------------------------
#
# The grammar library in jlpt_data/grammar_th.json, one row per grammar
# point.  `section` keeps the point's number in the outline the corpus was
# written against, so a card can be traced back to where it is described.
# `topic` is the coarse area (verb, particle, question, ...), for grouping.
# Rows carry CEFR bands as a display approximation -- Thai has no
# JLPT/TOPIK-style grading.

_CORPUS_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "jlpt_data",
        "grammar_th.json",
    )
)

_CORPUS = None
_CORPUS_RULES = None


def _corpus():
    "The corpus rows, loaded once (empty list if the file is missing)."
    global _CORPUS  # pylint: disable=global-statement
    if _CORPUS is None:
        if os.path.exists(_CORPUS_PATH):
            with open(_CORPUS_PATH, encoding="utf-8") as fh:
                _CORPUS = json.load(fh)
        else:
            _CORPUS = []
    return _CORPUS


def _hand_surfaces():
    """
    Every literal the hand-written rules already match.

    The corpus rows whose only trigger is one of these are covered by a
    hand rule, so they must not build a second rule for the same text --
    the panel would then show the same sentence under two cards.
    """
    found = set()

    def walk(spec):
        if isinstance(spec, dict):
            found.update(spec.get("surface_in") or ())
            for key in ("seq", "left", "right", "any_of"):
                for sub in spec.get(key) or ():
                    walk(sub)
        elif isinstance(spec, (list, tuple)):
            for sub in spec:
                walk(sub)

    for rule in _TH_RULES:
        walk(rule["match"])
    return found


_HAND_SURFACES = _hand_surfaces()
_HAND_KEYS = {r["key"] for r in _TH_RULES}


def _corpus_match(foci):
    """
    The match shape for one corpus row: a regex over the sentence text.

    Not token-exact.  pythainlp glues function words to their neighbours
    (มาจาก, ทางขวา, ตารางเมตร, ไปเสียแล้ว), so a token-exact spec silently
    misses exactly the high-frequency particles a reader wants flagged.
    The cost is that a literal can also turn up inside an unrelated word
    (นัก inside หนัก); the corpus avoids that by not listing literals that
    are far more often the ordinary word -- see ``_TOO_COMMON`` in the
    corpus build notes -- and the reading panel treats a hit as a hint.
    """
    pattern = re.compile(
        "|".join(re.escape(f) for f in sorted(foci, key=len, reverse=True))
    )
    return {"type": "regex", "re": pattern}


def _get_corpus_rules():
    """
    Matcher rules derived from the corpus, built on first use.

    One rule per row, an alternation of its focus literals (longest first,
    so ไม่มีใคร wins over ไม่มี when both belong to the row).  Pure data:
    no tokenizer is needed to build these, so the library stays browsable
    when pythainlp is not installed.
    """
    global _CORPUS_RULES  # pylint: disable=global-statement
    if _CORPUS_RULES is None:
        rules = []
        for item in _corpus():
            foci = [f for f in item.get("focus") or () if f]
            if not foci or item["key"] in _HAND_KEYS:
                continue
            rules.append(
                make_rule(
                    item["key"],
                    item["name"],
                    item["level"],
                    item.get("en") or item["name"],
                    _corpus_match(foci),
                    zh=item.get("zh"),
                )
            )
        _CORPUS_RULES = rules
    return _CORPUS_RULES


def _matchable(item):
    """
    True if this row can be reported on a page: either it carries its own
    focus literals, or a hand-written rule already matches one of them.
    """
    if item.get("focus"):
        return True
    return any(f in _HAND_SURFACES for f in item.get("focus_all") or ())


# ---- library export ----------------------------------------------------
#
# Mirrors the Japanese/Korean engines' export so every language renders
# through the same template (see lute/grammar/routes.py).


# The corpus writes each example as "thai · romanisation — english ／ 中文";
# the Chinese after this separator is the only translation it carries.
_GLOSS_SEP = " ／ "


def _example_row(sentence, display_lang):
    """
    One corpus example as ``{"text", "tr"}``.

    The English is part of the sentence line itself, so ``text`` is already
    bilingual for an English display language; the Chinese after the
    separator is shown only when Chinese is the display language.
    """
    text, _, gloss = sentence.partition(_GLOSS_SEP)
    return {"text": text, "tr": gloss.strip() if display_lang == "zh" else ""}


def _golden_gloss(row, display_lang):
    """
    The translation to print under a golden example, or "".

    Golden rows carry the three glosses as separate fields (``en`` /
    ``zh`` / ``ko``) rather than inline in the sentence line, so the
    display language picks one directly.  A missing one stays empty -- the
    template then shows no gloss line.
    """
    if display_lang == "zh":
        return (row.get("zh") or "").strip()
    if display_lang == "ko":
        return (row.get("ko") or "").strip()
    return (row.get("en") or "").strip()


_GOLDEN_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "jlpt_data",
        "grammar_th_golden.json",
    )
)


def _golden_examples():
    """
    Curated examples for the hand-written rules, keyed by rule key.

    The Thai corpus rows and the hand-written rules share keys for the
    twenty rules the corpus was written to cover; the remaining nine are
    rule-only.  Where a golden row exists it wins, because its sentences
    were checked to show *this* construction rather than another reading
    of the same word (ถูก "cheap" vs the passive).
    """
    return load_golden(_GOLDEN_PATH)


def library_entries(display_lang="en"):
    """
    The whole Thai grammar library, in CEFR order (A1 -> C1).

    The corpus is stored already ordered by level then section number, so
    the rows come back in the order the book teaches them within a band.
    A row with a golden entry shows the golden sentences instead of its
    corpus examples.

    Nine hand-written rules (ที่, สามารถ, ทั้ง...ทั้ง, เกือบ, อย่างไรก็ตาม,
    ...) have no corpus row of their own -- the corpus covers the other
    twenty -- so they are merged in from the rule list, each one at the end
    of its own level band.  Without that they were matchable in the reader
    but absent from the library, which is the one place a learner browses
    what the engine knows.
    """
    golden = _golden_examples()
    entries = []
    corpus_keys = set()
    for item in _corpus():
        corpus_keys.add(item["key"])
        desc = item.get("zh") if display_lang == "zh" else item.get("en")
        curated = golden.get(item["key"])
        if curated:
            examples = [
                {"text": row["text"], "tr": _golden_gloss(row, display_lang)}
                if _golden_gloss(row, display_lang)
                else row["text"]
                for row in curated
            ]
        else:
            examples = [
                _example_row(s, display_lang) for s in item.get("examples") or ()
            ]
        entry = {
            "key": item["key"],
            "name": item["name"],
            "level": item["level"],
            "desc": desc or item.get("en") or "",
            "examples": examples,
            "skipped": not _matchable(item),
        }
        formation = (item.get("formation") or "").strip()
        if formation:
            entry["formation"] = formation
        entries.append(entry)

    for rule in _TH_RULES:
        if rule["key"] in corpus_keys:
            continue
        curated = golden.get(rule["key"]) or []
        entry = {
            "key": rule["key"],
            "name": rule["name"],
            "level": rule["level"],
            "desc": (rule.get("zh") if display_lang == "zh" else rule["desc"])
            or rule["desc"],
            "examples": [
                {"text": row["text"], "tr": _golden_gloss(row, display_lang)}
                if _golden_gloss(row, display_lang)
                else row["text"]
                for row in curated
            ],
            "skipped": False,
        }
        _insert_by_level(entries, entry)
    return entries


def _insert_by_level(entries, entry):
    """
    Put ``entry`` at the end of its own level band, in place.

    The list is the page's order *and* the detail page's prev/next chain,
    so it has to stay CEFR-ordered; appending the nine rule-only cards
    after the last C1 row left them behind the whole library (and made the
    "levels are ordered easiest first" check fail).  A level with no corpus
    rows at all -- not the case today -- falls back to appending.
    """
    last = None
    for index, existing in enumerate(entries):
        if existing["level"] == entry["level"]:
            last = index
    if last is None:
        entries.append(entry)
    else:
        entries.insert(last + 1, entry)


def analyze_thai(page_text, display_lang="en"):
    """
    Analyze a page of Thai text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    rules = _TH_RULES + _get_corpus_rules()
    return analyze_tokens(page_text, rules, _tokens_for, display_lang)
