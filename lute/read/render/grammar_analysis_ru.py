"""
Russian grammar point detection (pymorphy3).

Mirrors the Japanese/Korean engines, but the morphological analysis comes
from pymorphy3 (OpenCorpora dictionary): each word gets its normal form,
a POS tag and gramemes (case / number / gender / tense / aspect / mood).
pymorphy3 has no context disambiguation, so ambiguous words carry all
their readings and a condition holds when ANY reading satisfies it.

pymorphy3 is imported lazily so the base install works without it; the
route falls back to the generic rule library on ImportError.
"""

import json
import os
import re
import threading

from lute.read.render.grammar_analysis_matcher import (
    analyze_tokens,
    desc_for_rule,
    make_rule,
    match_rule,
    paired_examples,
    spec_lemma,
    spec_morph,
    spec_pos,
    spec_surface,
)

_MORPH = None

_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Adjectival endings a Russian participle can carry (full and short forms of
# -щий / -вший / -нный / -тый / -мый).  Used to keep the participle rule off
# the ordinary verb and noun forms pymorphy3 mis-tags as PRTF/PRTS.
_PARTICIPLE_RE = re.compile(
    r"[а-яё]*"
    r"(?:щ|вш|нн|т|м|ем|им)"
    r"(?:ий|ый|ой|ая|яя|ое|ее|ые|ие|ого|его|ому|ему|ым|им|ых|их|ом|ем|а|о|ы|и)"
)

# pymorphy3 gramemes surfaced to the matcher, keyed like the spaCy morph
# dicts (UPPER-CASE category, OpenCorpora grameme value).
_GRAMEME_KEYS = (
    "case",
    "number",
    "gender",
    "person",
    "tense",
    "mood",
    "aspect",
    "voice",
)


def morph_analyzer():
    """
    Lazy, process-lifetime pymorphy3 analyzer.

    Public because the lemma backfill (lute/term/lemma_provider.py) uses
    the same dictionary: one MorphAnalyzer per process, not two.
    """
    global _MORPH
    if _MORPH is None:
        from pymorphy3 import MorphAnalyzer  # pylint: disable=import-outside-toplevel

        _MORPH = MorphAnalyzer()
    return _MORPH


def _gramemes(tag):
    "pymorphy3 Tag -> feature dict for the matcher."
    out = {}
    for key in _GRAMEME_KEYS:
        value = getattr(tag, key, None)
        if value:
            out[key.capitalize()] = value
    return out


def _tokens_for(sentence):
    "Tokenize a sentence; return a list of token dicts."
    analyzer = morph_analyzer()
    tokens = []
    for m in _WORD_RE.finditer(sentence):
        parses = analyzer.parse(m.group())
        if not parses:
            continue
        best = parses[0]
        # pymorphy3 returns every dictionary reading, including ghosts with
        # a tiny score ("моря" carries a 1.6% "морить" gerund reading).
        # Keep only readings close to the best one: POS-only rules match on
        # ANY reading, and ghost readings would fire them spuriously.
        parses = [p for p in parses if p.score >= 0.1 * best.score]
        tokens.append(
            {
                "surface": m.group(),
                "lemma": best.normal_form,
                "pos": best.tag.POS or "",
                "morph": _gramemes(best.tag),
                "idx": m.start(),
                "score": best.score,
                "parses": [
                    {
                        "lemma": p.normal_form,
                        "pos": p.tag.POS or "",
                        "morph": _gramemes(p.tag),
                        "score": p.score,
                    }
                    for p in parses
                ],
            }
        )
    return tokens


# ---- rules -------------------------------------------------------------
#
# Levels use CEFR bands (A1..C2), rendered by the panel like the JLPT ones.
# pymorphy3 values: case nomn/gent/datv/accs/ablt/loct, number sing/plur,
# tense past/pres/futr, aspect impf/perf, mood indc/impr, POS INFN = infinitive.

_GEN_NOUN = spec_morph(Case="gent")
_DAT_NOUN = spec_morph(Case="datv")
_ABL_NOUN = spec_morph(Case="ablt")
_LOC_NOUN = spec_morph(Case="loct")

_RU_RULES = [
    # ---- A1 ----
    make_rule(
        "ru_u_menya",
        "у меня есть (possession)",
        "A1",
        "у + genitive + есть says someone has something; нет negates it "
        "(у меня есть / у меня нет).",
        {
            "seq": [
                spec_surface("у"),
                spec_surface("меня", "тебя", "него", "неё", "нее", "нас", "вас", "них"),
                spec_surface("есть", "нет", "будет", "было"),
            ]
        },
        zh="у + 属格 + есть 表示『有』：у меня есть（我有）；нет 表没有。",
    ),
    make_rule(
        "ru_nravitsya",
        "мне нравится (like)",
        "A1",
        "dative pronoun + нравится: the liked thing is the subject "
        "(мне нравится этот город).",
        {
            "seq": [
                spec_surface("мне", "тебе", "ему", "ей", "нам", "вам", "им"),
                spec_lemma("нравиться", "понравиться"),
            ]
        },
        zh="мне нравится：喜欢某物（物作主语，人用第三格）。",
    ),
    make_rule(
        "ru_prep_loct",
        "в / на + prepositional case (location)",
        "A1",
        "в/на + prepositional answers где? and locates the action "
        "(в городе, на работе).",
        {"seq": [spec_surface("в", "во", "на"), _LOC_NOUN]},
        zh="в/на + 前置格（第六格）：表示地点，回答 где?（в городе 在城里）。",
    ),
    make_rule(
        "ru_k_dat",
        "к + dative (direction)",
        "A1",
        "к + dative moves towards a person/place (к другу, к окну).",
        {"seq": [spec_surface("к", "ко"), _DAT_NOUN]},
        zh="к + 第三格：朝向某人/某处（к другу 走向朋友）。",
    ),
    make_rule(
        "ru_modal_inf",
        "можно / нужно / надо + infinitive",
        "A1",
        "impersonal modal + infinitive: можно?, нельзя, надо/нужно, должен.",
        {
            "seq": [
                spec_surface(
                    "можно",
                    "нельзя",
                    "надо",
                    "нужно",
                    "нужен",
                    "нужна",
                    "нужны",
                    "должен",
                    "должна",
                    "должно",
                    "должны",
                ),
                spec_pos("INFN"),
            ]
        },
        zh="можно/нельзя/надо/нужно/должен + 动词原形：可以/禁止/必须。",
    ),
    make_rule(
        "ru_imperative",
        "Imperative (-те form, читайте)",
        "A1",
        "the command form of the verb (читайте, сядьте).",
        # pymorphy3 has no context disambiguation and RANKS a fabricated
        # imperative reading first for ordinary nouns and adjectives:
        # рублей (genitive plural of рубль) comes back as рублеть/impr at
        # 0.561, above the noun reading's 0.410.  A top-reading test cannot
        # filter that, so the rule asks for an unambiguous reading instead.
        # Genuine imperatives are unambiguous here: давайте 0.996, иди /
        # скажите / читайте / делай 1.0, while every fabricated one lands
        # between 0.13 and 0.56.
        {"seq": [{"pos_in": ["VERB"], "morph": {"Mood": "impr"}, "min_score": 0.9}]},
        zh="命令式（читайте / сядьте）：表示命令或请求。",
    ),
    # ---- A2 ----
    make_rule(
        "ru_s_ablt",
        "с + instrumental (together with)",
        "A2",
        "с + instrumental = with someone/something (с другом, с интересом).",
        {"seq": [spec_surface("с", "со"), _ABL_NOUN]},
        zh="с + 第五格：和……一起（с другом 和朋友一起）。",
    ),
    make_rule(
        "ru_gen_preps",
        "Genitive after без / для / до / из / от ...",
        "A2",
        "these prepositions always govern the genitive (без денег, для работы, "
        "из Москвы).",
        {
            "seq": [
                spec_surface(
                    "без", "для", "до", "из", "от", "около", "после", "возле", "ради"
                ),
                _GEN_NOUN,
            ]
        },
        zh="без/для/до/из/от 等前置词要求第二格（без денег 没有钱）。",
    ),
    make_rule(
        "ru_o_loct",
        "о / об + prepositional (about)",
        "A2",
        "о/об + prepositional = about something (о работе, об этом).",
        {"seq": [spec_surface("о", "об", "обо"), _LOC_NOUN]},
        zh="о/об + 前置格：关于……（о работе 关于工作）。",
    ),
    make_rule(
        "ru_nums_2_4",
        "2–4 + genitive singular",
        "A2",
        "два/три/четыре take a singular genitive noun (два часа, три книги).",
        {
            "seq": [
                spec_surface("два", "три", "четыре", "2", "3", "4"),
                spec_morph(Case="gent", Number="sing"),
            ]
        },
        zh="数词 2–4 后接单数属格名词（два часа 两小时）。",
    ),
    make_rule(
        "ru_nums_5",
        "5+ + genitive plural",
        "A2",
        "пять and up (also много/несколько/сколько) take a plural genitive "
        "noun (пять книг, много книг).",
        {
            "seq": [
                spec_surface(
                    "пять",
                    "шесть",
                    "семь",
                    "восемь",
                    "девять",
                    "десять",
                    "много",
                    "несколько",
                    "сколько",
                ),
                spec_morph(Case="gent", Number="plur"),
            ]
        },
        zh="数词 5 及以上（还有 много/несколько）后接复数属格（пять книг）。",
    ),
    make_rule(
        "ru_past",
        "Past tense (aspect matters)",
        "A2",
        "-л/-ла/-ло/-ли forms; the imperfective (читал) views the action as a "
        "process or habit, the perfective (прочитал) as one completed event.",
        {"seq": [{"pos_in": ["VERB"], "morph": {"Tense": "past"}}]},
        zh="过去时（-л/-ла/-ли）：未完成体 читал 表过程/重复，完成体 прочитал 表完成。",
    ),
    make_rule(
        "ru_future",
        "Future tense",
        "A2",
        "буду + infinitive (imperfective) or one perfective form (прочитаю).",
        {"seq": [{"pos_in": ["VERB"], "morph": {"Tense": "futr"}}]},
        zh="将来时：буду + 原形（未完成体）或完成体变位形式（прочитаю）。",
    ),
    # ---- B1 ----
    make_rule(
        "ru_by",
        "Conditional with бы",
        "B1",
        "бы + past form makes the conditional: я хотел бы, если бы.",
        {"seq": [spec_surface("бы", "б")]},
        zh="语气词 бы 构成假定式：хотел бы（我想……），если бы（要是……）。",
    ),
    make_rule(
        "ru_chtoby",
        "чтобы (in order to / that)",
        "B1",
        "purpose or desired action: чтобы помочь, я хочу, чтобы ты пришёл.",
        {"seq": [spec_surface("чтобы")]},
        zh="чтобы：为了……/希望……（目的或意愿从句）。",
    ),
    make_rule(
        "ru_poka_ne",
        "пока не (until)",
        "B1",
        "until: подожди, пока не придёт врач.",
        {"seq": [spec_surface("пока"), spec_surface("не")]},
        zh="пока не：直到……为止。",
    ),
    make_rule(
        "ru_posle_togo_kak",
        "после того как (after)",
        "B1",
        "after (a past action): после того как он ушёл.",
        {"seq": [spec_surface("после"), spec_surface("того"), spec_surface("как")]},
        zh="после того как：在……之后（连接时间从句）。",
    ),
    make_rule(
        "ru_pricastie",
        "Participles (-щий / -вший / -мый)",
        "B1",
        "verbal adjectives: читающий, прочитавший, читаемый.",
        # A bare PRTF/PRTS tag is not enough.  pymorphy3 tags non-participles
        # as participles with a top-ranked reading -- поиграем and граждан
        # come back as short PRTS, and будет as a short PRTS too -- so a
        # POS test alone fires on ordinary verb and noun forms (58% of the
        # corpus pages).  A participle is an adjective in form, so the rule
        # also requires an adjectival ending; that is what actually
        # separates указанный / читающий from поиграем / граждан.
        {
            "seq": [
                {
                    "pos_in": ["PRTF", "PRTS"],
                    "surface_re": _PARTICIPLE_RE,
                }
            ]
        },
        zh="形动词（-щий/-вший/-мый）：动词性的形容词（читающий 正在读的）。",
    ),
    make_rule(
        "ru_deepricastie",
        "Adverbial participles (-я / -в)",
        "B1",
        "verbal adverbs of manner: читая, прочитав.",
        {"seq": [spec_pos("GRND")]},
        zh="副动词（читая / прочитав）：表示伴随或先行动作。",
    ),
    make_rule(
        "ru_motion_prefixes",
        "Prefixed motion verbs (при-/у-/по-...)",
        "B1",
        "идти/ехать + prefix changes meaning: прийти (arrive), уйти (leave), "
        "пойти (set off).",
        {
            "seq": [
                {
                    "lemma_in": [
                        "пойти",
                        "прийти",
                        "уйти",
                        "выйти",
                        "зайти",
                        "войти",
                        "перейти",
                        "дойти",
                        "подойти",
                        "приехать",
                        "уехать",
                        "заехать",
                        "подъехать",
                        "доехать",
                        "выехать",
                    ]
                }
            ]
        },
        zh="带前缀位移动词：прийти 到达、уйти 离开、пойти 出发等。",
    ),
    make_rule(
        "ru_sya",
        "-ся / -сь verbs (reflexive)",
        "B1",
        "reflexive verbs: учится, моется, оделась.",
        {"seq": [{"surface_re": re.compile(r".*(?:ся|сь)$"), "pos_in": ["VERB"]}]},
        zh="-ся/-сь 反身动词：учится 学习、моется 洗澡等。",
    ),
    # ---- B2 ----
    make_rule(
        "ru_dolzhen_byl",
        "должен был + infinitive (unfulfilled)",
        "B2",
        "was supposed to (but didn't): я должен был позвонить, но забыл.",
        {
            "seq": [
                spec_surface("должен", "должна", "должно", "должны"),
                spec_surface("был", "была", "было", "были"),
                spec_pos("INFN"),
            ]
        },
        zh="должен был + 原形：本应做（但没做）。",
    ),
    make_rule(
        "ru_chut_ne",
        "чуть не + perfective past (almost)",
        "B2",
        "almost did something: она чуть не упала.",
        {"seq": [spec_surface("чуть"), spec_surface("не"), spec_pos("VERB")]},
        zh="чуть не + 完成体过去时：差点就……。",
    ),
    make_rule(
        "ru_stoilo_kak",
        "стоило ... как / не успел ... как",
        "B2",
        "no sooner ... than: стоило прийти, как пошёл дождь.",
        {
            "any_of": [
                {
                    "left": [spec_surface("стоило")],
                    "right": [spec_surface("как")],
                    "min_gap": 1,
                    "max_gap": 8,
                },
                {
                    "left": [
                        spec_surface("не"),
                        spec_surface("успел", "успела", "успели"),
                    ],
                    "right": [spec_surface("как")],
                    "min_gap": 1,
                    "max_gap": 8,
                },
            ]
        },
        zh="стоило… как / не успел… как：刚一……就……。",
    ),
    make_rule(
        "ru_v_techenie",
        "в течение + genitive (during)",
        "B2",
        "within / during a period: в течение часа.",
        {"seq": [spec_surface("в"), spec_surface("течение", "продолжение")]},
        zh="в течение + 属格：在……期间（в течение часа 一小时内）。",
    ),
    make_rule(
        "ru_double_neg",
        "никогда / никто + не (double negative)",
        "B2",
        "negative pronouns keep не: я никогда не видел моря.",
        {
            "seq": [
                spec_surface("никогда", "никто", "ничто", "нигде", "никакой", "никуда"),
                spec_surface("не"),
            ]
        },
        zh="否定代词/副词 + не 构成双重否定：никогда не（从未……）。",
    ),
    # ---- C1 / C2 ----
    make_rule(
        "ru_kak_ni",
        "как ни + (however much)",
        "C1",
        "concessive: как ни старайся, как ни странно.",
        {"seq": [spec_surface("как"), spec_surface("ни")]},
        zh="как ни：无论怎样……（让步：как ни старайся 无论怎么努力）。",
    ),
    make_rule(
        "ru_edva_li",
        "едва ли / вряд ли (doubt)",
        "C1",
        "doubtful: вряд ли он придёт.",
        {
            "any_of": [
                {"seq": [spec_surface("едва"), spec_surface("ли")]},
                {"seq": [spec_surface("вряд"), spec_surface("ли")]},
            ]
        },
        zh="едва ли / вряд ли：未必，恐怕不会。",
    ),
    make_rule(
        "ru_chem_tem",
        "чем ... , тем ... (the ... the ...)",
        "C2",
        "correlative comparison: чем больше, тем лучше.",
        {
            "left": [spec_surface("чем")],
            "right": [spec_surface("тем")],
            "min_gap": 1,
            "max_gap": 8,
        },
        zh="чем …, тем …：越……越……（чем больше, тем лучше）。",
    ),
    make_rule(
        "ru_stoit_kak",
        "стоит + infinitive ..., как ...",
        "B2",
        "one only has to ... and: стоит свернуть вправо, как увидеть здание.",
        # The infinitive anchor is what makes this specific.  Without it the
        # rule fires on the ordinary verb стоит ("stands"): "Он стоит на
        # улице, как статуя" matched it in a trial of the negative set, and
        # with spec_pos("INFN") it does not.
        {
            "left": [spec_surface("стоит"), spec_pos("INFN")],
            "right": [spec_surface("как")],
            "min_gap": 0,
            "max_gap": 8,
        },
        zh="стоит + 不定式 …, как …：只要……就……（стоит свернуть вправо, как увидеть здание）。",
    ),
]


# ---- data-driven rules -------------------------------------------------
#
# Rows merged from a local grammar-point extraction land in
# jlpt_data/grammar_ru.json.  The matcher for each row is derived from its
# `focus` list -- the word forms the point is stated with, e.g.
# ["у", "меня", "есть"].
#
# Three families of spec are built, because Russian inflection cuts both
# ways and a literal spec is pinned to one surface form:
#
#   1. a literal `seq` on the surface forms -- narrowest, matches only the
#      book's own phrasing ("между обедом");
#   2. a literal `seq` on the lemmas -- for the case where pymorphy3's
#      context-free normal form differs from the surface;
#   3. an **inflected** `seq` that keeps function words literal and replaces
#      every inflected word with its POS plus the features a grammar point
#      actually turns on.  This is what makes a row about "между +
#      instrumental" fire on "между друзьями": the literal spec cannot,
#      since it is pinned to one noun form.
#
# A gapped `left/right` spec is the **last resort**, and only when nothing
# contiguous matched: a focus whose words do not sit next to each other
# ("мужчина ... жёны") cannot fire a seq at all, and those rows were being
# dropped silently.  It is a fallback rather than an addition because it is
# wider -- offered unconditionally it would loosen rows that are already
# tight.
#
# Every candidate is validated against the row's own example sentences with
# the SAME matcher the panel runs, and a row whose specs fire on none of its
# examples is dropped -- so a mis-derived focus can never report a false
# positive.  A one-word focus is kept only when the word is long enough to
# be specific (a bare `у` would fire on every page).
#
# Focus literals are stripped of stress marks (U+0300-036F) first: the
# source text keeps them, and `\w` does not match a combining acute, so an
# accented literal fragments at the accent and never matches anything.

_STRESS_RE = re.compile(r"[\u0300-\u036f]")

# key -> the examples a data rule was validated against (tests re-run this).
_RU_RULE_EXAMPLES = {}

# The morph feature the generalised spec turns on: **case**, and only case.
#
# A preposition row ("между + instrumental") is stated in the book with one
# noun form ("между обедом") but governs every form of that case, so the
# derivation replaces the noun with a case-only spec and keeps the preposition
# literal.  That is what makes "между друзьями" and "между нами" fire the same
# row as "между обедом".
#
# ⚠️ Verb-side features must NOT generalise.  ``Tense``/``Person``/``Mood``/
# ``Aspect``/``Voice`` look symmetric but are not: a focus word that is a
# predicate ("надо") or an infinitive ("включать") yields a spec like
# ``["не", {Tense: "pres"}]`` -- "не + any present-tense word" -- which is not
# a construction, it is every negated present verb in the page.  Those rows
# fired on nearly the whole example corpus (не надо / Не включать collided
# with ru_past, ru_deepricastie and nine other rules at once).  A row whose
# content word is not nominal keeps its literal spec instead.
#
# ⚠️ The key is capitalised because that is how ``_gramemes`` stores it
# (``Case``); a lowercase key silently filters everything out -- leaving an
# **empty** ``{"morph": {}}`` spec, and an empty morph subset matches *every*
# token.  That mistake once made one phrase fire 15 unrelated rules.
_POINT_FEATURES = ("Case",)

# A compound focus word ("из-за", "в течение") is tokenised into its parts
# by _WORD_RE, so a single literal spec for it could never match: the
# matcher compares spec to token, and there is no "из-за" token.
_COMPOUND_RE = re.compile(r"[-‐‑]")


def _strip_stress(text):
    "Drop combining accents (U+0300-036F); they break tokenisation."
    return _STRESS_RE.sub("", text or "")


def _norm_name(name):
    "Fold a rule name for the hand-written / data-layer overlap check."
    return re.sub(r"[^0-9a-z\u0400-\u04FF]+", "", _strip_stress(name or "").lower())


_HANDWRITTEN_NORMS = {_norm_name(r["name"]) for r in _RU_RULES}

# Merged rows whose derived matcher reports the *same span* as another rule,
# so the panel would print one construction twice.  The rows stay in the
# library (they are real points); only the duplicate panel entry is dropped.
#
# The list is derived, not guessed: ``scripts.audit_grammar_ru_overlaps``
# runs every rule over the union of all rules' own example sentences and
# reports the pairs that share a near-identical span on at least two
# sentences.  A pair seen once is an accident of that one sentence and is
# not listed.
#
# The cause is the generalisation in ``_inflected_specs``: a row stated with
# one noun ("в + 前置格", focus ``в, доме``) derives ``в + <any
# prepositional>``, which is exactly the hand-written ``ru_prep_loct``.  When
# the row's whole point is a form the hand-written rule already reports, the
# row teaches nothing new -- it only adds a second entry on the same phrase.
_DUPLICATE_RULE_IDS = frozenset(
    {
        # ---- в / на + case: all collapse to ru_prep_loct (77 sentences
        # each).  Their nouns are prepositional ("в опере", "в доме"), so the
        # generalisation is "в + any prepositional" and ru_prep_loct owns it.
        "rgm_в__4a64de",  # в + 前置格（钟点的整点半：半小时后）
        "rgm_в__68f693",  # в + 前置格（作品、活动的内容所在）
        "rgm_в__844b92",  # в + 前置格（建筑物内部：在……房子里）
        "rgm_в__a74438",  # в + 前置格（过程的阶段：开始、中间、结束；过去、现在、将来）
        "rgm_вотношениипоотношениюк__ab49d0",  # в отношении + 第二格 / по отношению к + 第三格
        "rgm_врезультате__53598d",  # в результате + 第二格
        "rgm_всамомделенасамомделеvs__8c86d8",  # в самом деле 与 на самом деле
        # в + accusative: the same collision on the accusative side.
        "rgm_в__4bb7ae",  # в + 第四格（天气：在什么样的天气里）
        "rgm_в__6e0f02",  # в + 第四格（星期几：在星期……）
        # The в + prepositional *time* rows state one time word each ("в
        # майские праздники") but derive the same "в + any prepositional"
        # spec, so they collide with each other; the first is kept.
        "rgm_в__2a9d77",  # в + 前置格（节日名称之一：五一假期）
        "rgm_в__e155d1",  # в + 前置格（泛指一段时期、季节、年代）
        # ---- с + instrumental: ru_s_ablt owns the form.
        "rgm_с__bd98c7",  # с + 第五格（对某人严厉、严厉相待）
        "rgm_с__b3beea",  # с + 第二格（地点：从高处／从某处下来）
        # ---- genitive prepositions ru_gen_preps already lists (including
        # около), so the derived "prep + genitive" spec is a duplicate.
        "rgm_без__6181f0",  # без + 第二格（差几到几点）
        "rgm_около__c3e310",  # около + 第二格（近似数量：与计量单位搭配）
        "rgm_дотогокак__68c943",  # 时间连接词 до того как 及其加长变体
        "rgm_x__76d0b7",  # 简缩句（谓语缺位，表示运动/位移）
    }
)

_DATA_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "jlpt_data",
        "grammar_ru.json",
    )
)


def _focus_words(focus):
    words = []
    for f in focus or []:
        w = _strip_stress(f).strip().lower()
        if w:
            words.append(w)
    return words


def _literal_specs(words, lemma=False):
    """
    Literal seq over the focus words.

    A compound focus word is split on its hyphen, because ``_WORD_RE``
    tokenises "из-за" into two tokens and a single spec for it could never
    match anything.
    """
    specs = []
    for w in words:
        parts = [p for p in _COMPOUND_RE.split(w) if p]
        if len(parts) > 1:
            specs.extend(spec_lemma(p) if lemma else spec_surface(p) for p in parts)
        else:
            specs.append(spec_lemma(w) if lemma else spec_surface(w))
    return {"seq": specs}


def _inflected_specs(words, examples):
    """
    Case-only seq that keeps function words literal.

    The reading of a word differs by sentence, so one candidate per example
    is produced and the caller keeps whichever the matcher actually fires.
    A word pymorphy3 reports with an empty morph is a function word (a
    preposition, conjunction or particle carries no case of its own) and is
    kept literal; everything else is replaced by the features a grammar
    point turns on.

    ⚠️ The POS is deliberately **not** kept.  "между + instrumental" is the
    same rule whether the instrumental is a noun ("между обедом"), an
    adjective ("между русским"), a pronoun ("между нами") or a numeral
    ("между двух огней"); pinning the POS drops three of the four.  Case
    alone is what the construction actually turns on.

    ⚠️ A spec is only offered when the focus keeps **at least one literal
    anchor**.  A row whose focus is all content words ("чайная чашка",
    "Весна. Ночь. Тишина.") would otherwise degrade to a bare case sequence
    with nothing to pin it, and a bare ``{"morph": {"Case": "accs"}}`` fires
    on every accusative token in the page -- that mistake took the panel
    from a median of 6 to 17 entries per page.  The function-word anchor is
    what makes the generalisation safe.

    A focus word missing from the sentence falls back to the literal spec,
    so a row still gets a candidate rather than silently losing one.
    """
    out = []
    for sentence in examples:
        text = _strip_stress(sentence)
        tokens = _tokens_for(text)
        by_surface = {}
        for tok in tokens:
            by_surface.setdefault(tok["surface"].lower(), tok)
        seq = []
        anchored = 0
        for w in words:
            tok = by_surface.get(w)
            point = None
            if tok is not None and tok["morph"]:
                point = {
                    k: tok["morph"][k] for k in _POINT_FEATURES if k in tok["morph"]
                }
            if point:
                seq.append({"morph": point})
            else:
                # Function word (no case of its own), a word whose morph
                # carries no point feature at all, or a compound whose parts
                # the surface index cannot see: keep it literal.  Never emit
                # an empty {"morph": {}} -- an empty subset matches any
                # token, so the spec would fire on everything.
                literals = [spec_surface(p) for p in _COMPOUND_RE.split(w) if p]
                anchored += len(literals)
                seq.extend(literals)
        # Only a spec with a literal anchor is specific enough to publish.
        if seq and anchored:
            out.append({"seq": seq})
    return out


def _gapped_specs(words, examples):
    """
    Gapped spec for a focus whose words do not sit next to each other.

    The anchors are the **first and last** focus words, not the closest
    pair.  A row stated as "мужчина ... жёны" has "взять" and "жёны" only
    one word apart, but the grammar point spans the whole phrase, and
    pinning just the tight pair throws away what makes the row recognisable
    -- it also produced nonsense anchors when a word repeats, since the
    first occurrence of "кто-то" is "кто", not the pair the row means.

    Only reached when nothing contiguous matched (see _row_specs), so it
    never loosens a row that a literal spec already covers.
    """
    first = next((p for w in words[:1] for p in _COMPOUND_RE.split(w) if p), None)
    last = next((p for p in reversed(_COMPOUND_RE.split(words[-1])) if p), None)
    if not first or not last:
        return []
    for sentence in examples:
        text = _strip_stress(sentence)
        surfaces = [t["surface"].lower() for t in _tokens_for(text)]
        if first in surfaces and last in surfaces:
            gap = surfaces.index(last) - surfaces.index(first) - 1
            return [
                {
                    "left": [spec_surface(first)],
                    "right": [spec_surface(last)],
                    "min_gap": 0,
                    # Slack for a different sentence, but nowhere near the
                    # default 8: the point is to stay near the span the book
                    # actually wrote.
                    "max_gap": min(gap + 2, 8),
                }
            ]
    return []


def _row_specs(focus, examples):
    """
    The matcher specs for one library row.

    Literal first (narrowest), then the inflected variants that generalise
    across the noun forms a construction takes, then -- only if nothing
    contiguous fired -- a gapped fallback.  Each candidate is checked with
    the same matcher the panel runs, so a spec the data does not support
    never reaches a reader.
    """
    words = _focus_words(focus)
    if not words:
        return []
    if len(words) == 1 and len(words[0]) < 4:
        return []  # a bare short word fires everywhere -- never specific
    candidates = [
        _literal_specs(words),
        _literal_specs(words, lemma=True),
    ]
    candidates.extend(_inflected_specs(words, examples))
    live = [s for s in candidates if _flash(s, examples)]
    if live:
        return live
    return [s for s in _gapped_specs(words, examples) if _flash(s, examples)]


def _flash(spec, examples):
    "True when spec fires on at least one of the row's example sentences."
    for sentence in examples:
        text = _strip_stress(sentence)
        if match_rule({"match": spec}, _tokens_for(text), text):
            return True
    return False


def _load_data_rules(include_silenced=False):
    """
    Rules for the merged grammar-point rows (jlpt_data/grammar_ru.json).

    Specs are derived from each row's `focus` and kept only when they match
    at least one of the row's own examples, so the derivation can never
    invent a matcher the data does not support.  Those examples are recorded
    in _RU_RULE_EXAMPLES for the tests to re-run.

    ``include_silenced`` is for the audit, not the panel: a row listed in
    ``_DUPLICATE_RULE_IDS`` is normally dropped here so the panel never
    reports it, and the audit needs the *un*deduped set to re-derive that list
    at all.
    """
    if not os.path.exists(_DATA_PATH):
        return []
    with open(_DATA_PATH, encoding="utf-8") as fh:
        entries = json.load(fh)
    rules = []
    for item in entries:
        name = item.get("name") or ""
        if not name or _norm_name(name) in _HANDWRITTEN_NORMS:
            continue  # already covered precisely by a hand-written rule
        silenced = item.get("key") in _DUPLICATE_RULE_IDS
        if silenced and not include_silenced:
            continue  # its matcher reports a span another rule already reports
        # The row's examples carry parallel gloss lists (`example_zh` from
        # the merged material, `example_en` from the authored back-fill);
        # pair them up so the library can print a translation, and keep the
        # plain sentences for the matcher validation below.
        texts = []
        for raw in item.get("examples") or []:
            text = _strip_stress(raw or "").strip()
            if text:
                texts.append(text)
        examples = texts
        if not examples:
            continue
        validated = _row_specs(item.get("focus"), examples)
        if not validated:
            continue
        key = item.get("key") or name
        _RU_RULE_EXAMPLES[key] = examples
        library_examples = paired_examples("ru", examples, item)
        rule = make_rule(
            key,
            name,
            item.get("level") or "B1",
            (item.get("meaning") or "").strip() or name,
            {"any_of": validated} if len(validated) > 1 else validated[0],
            zh=item.get("zh") or None,
        )
        ko = (item.get("meaning_ko") or "").strip()
        if ko:
            rule["ko"] = ko
        # Library-page fields (the /grammar detail card renders them when
        # present); the panel ignores keys it does not read.
        rule["formation"] = (item.get("formation") or "").strip()
        rule["notes"] = (item.get("notes") or "").strip()
        rule["examples"] = library_examples
        # The panel's folded reference block: the row's own example with
        # its Chinese gloss, so a reader sees a translated sentence rather
        # than only the description.  The library page shows every example,
        # so this is the panel's single curated one.
        rule["reference"] = _first_glossed(library_examples)
        rules.append(rule)
    return rules


def _first_glossed(pairs):
    """
    The first example that carries a translation, shaped for the panel.

    ``sentence`` is the field the front end reads first (the Japanese
    engine ships ``japanese`` instead and the front end accepts both).
    Every translation the example carries is kept, not just the Chinese
    one: the shared ``reference_gloss`` resolves it in the panel's display
    language, so an English panel gets the English gloss rather than
    nothing.
    """
    for pair in pairs:
        if not (pair.get("zh") or pair.get("en") or pair.get("ko")):
            continue
        reference = {"sentence": pair["ru"]}
        for field in ("zh", "en", "ko"):
            value = (pair.get(field) or "").strip()
            if value:
                reference[field] = value
        return reference
    return None


# The data rules are built on first use, not at import: _load_data_rules()
# tokenizes the rows' examples, and this module is imported at app start-up
# (lute/read/routes.py).  Same reasoning as the Korean engine.
_rules_lock = threading.RLock()
_data_rules_cache = None
_all_rules_cache = None


def _get_data_rules():
    "The data-driven rules, built on first use and memoised."
    global _data_rules_cache  # pylint: disable=global-statement
    with _rules_lock:
        if _data_rules_cache is None:
            _data_rules_cache = _load_data_rules()
        return _data_rules_cache


def _get_all_rules():
    """
    Hand-written plus data-driven rules, built and memoised on first use.

    The hand-written rules carry their example sentence inline and their
    translations in the golden set, so the panel's folded reference block
    is attached here -- once, for both the panel and the library page.
    """
    global _all_rules_cache  # pylint: disable=global-statement
    with _rules_lock:
        if _all_rules_cache is None:
            _all_rules_cache = _RU_RULES + _get_data_rules()
            golden = _golden_examples()
            for rule in _all_rules_cache:
                if rule.get("reference"):
                    continue  # data rows already carry their own
                examples = rule.get("examples") or golden.get(rule["key"]) or []
                if examples:
                    rule["reference"] = _first_glossed(
                        [e if isinstance(e, dict) else {"ru": e} for e in examples]
                    )
        return _all_rules_cache


def analyze_russian(page_text, display_lang="en"):
    """
    Analyze a page of Russian text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(page_text, _get_all_rules(), _tokens_for, display_lang)


# ---- library export ----------------------------------------------------
#
# The /grammar page lists the whole corpus at once, where the panel lists
# only what the current page happens to contain.  Same display fields as
# the panel entries (minus the page-specific match offsets), so the page
# can reuse the panel's markup and CSS.

_CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]

_GOLDEN_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "jlpt_data",
        "grammar_ru_golden.json",
    )
)

_golden_cache = None


def _golden_examples():
    """
    Curated example sentences for the hand-written rules.

    Keyed ``key -> [{"ru", "en", "zh", "ko"}, ...]``.  The golden set is the
    single store of these examples: the tests re-run every sentence through
    its own rule, and the library serves the same sentences to readers, so
    the page can never show a point whose matcher does not actually
    recognise it.
    """
    global _golden_cache  # pylint: disable=global-statement
    with _rules_lock:
        if _golden_cache is None:
            examples = {}
            if os.path.exists(_GOLDEN_PATH):
                with open(_GOLDEN_PATH, encoding="utf-8") as fh:
                    for key, entry in json.load(fh).items():
                        examples[key] = [
                            e
                            for e in entry.get("examples") or []
                            if (e.get("ru") or "").strip()
                        ]
            _golden_cache = examples
        return _golden_cache


def _example_gloss(example, display_lang):
    """
    The translation to print under an example, in the display language.

    The golden set carries all three; a missing one stays empty rather than
    falling back to English under a Chinese or Korean heading.
    """
    if display_lang == "zh":
        return (example.get("zh") or "").strip()
    if display_lang == "ko":
        return (example.get("ko") or "").strip()
    return (example.get("en") or "").strip()


def _library_entry(rule, display_lang, examples):
    """
    One rule as a library row, display-ready (see the Japanese analogue).

    Each example comes back as ``{"text", "tr"}``: the Russian sentence with
    the stress marks stripped, plus its translation for the display
    language.  Rows merged in from the data file are still plain strings, so
    both shapes have to be accepted.  The library has no page text to
    highlight, so the match offsets the panel computes are irrelevant here.
    """
    rows = []
    for example in examples:
        if isinstance(example, str):
            example = {"ru": example}
        text = _strip_stress(example.get("ru") or "").strip()
        if text:
            rows.append({"text": text, "tr": _example_gloss(example, display_lang)})
    entry = {
        "key": rule["key"],
        "name": rule["name"],
        "level": rule["level"],
        "desc": desc_for_rule(rule, display_lang) or rule["name"],
        "examples": rows,
        "skipped": False,
    }
    for field in ("formation", "notes"):
        value = (rule.get(field) or "").strip()
        if value:
            entry[field] = value
    return entry


def library_entries(display_lang="en"):
    """
    The whole Russian grammar library, in CEFR order (A1 -> C2).

    Built on first use like the rules themselves, so an app start-up with
    no Russian books does not pay for the pymorphy3 instance.  Hand-written
    rules take their examples from the golden set; the merged data rows
    carry their own.
    """
    golden = _golden_examples()
    entries = []
    for rule in _RU_RULES:
        examples = rule.get("examples") or golden.get(rule["key"]) or []
        entries.append(_library_entry(rule, display_lang, examples))
    for rule in _get_data_rules():
        entries.append(_library_entry(rule, display_lang, rule.get("examples") or []))
    ordered = sorted(
        enumerate(entries),
        key=lambda pair: (
            _CEFR_ORDER.index(pair[1]["level"])
            if pair[1]["level"] in _CEFR_ORDER
            else 9,
            pair[0],
        ),
    )
    return [entry for _, entry in ordered]
