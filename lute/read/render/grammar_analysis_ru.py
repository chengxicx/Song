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

import re

from lute.read.render.grammar_analysis_matcher import (
    analyze_tokens,
    make_rule,
    spec_lemma,
    spec_morph,
    spec_pos,
    spec_surface,
)

_MORPH = None

_WORD_RE = re.compile(r"\w+", re.UNICODE)

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


def _morph_analyzer():
    "Lazy, process-lifetime pymorphy3 analyzer."
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
    analyzer = _morph_analyzer()
    tokens = []
    for m in _WORD_RE.finditer(sentence):
        parses = analyzer.parse(m.group())
        if not parses:
            continue
        best = parses[0]
        tokens.append(
            {
                "surface": m.group(),
                "lemma": best.normal_form,
                "pos": best.tag.POS or "",
                "morph": _gramemes(best.tag),
                "idx": m.start(),
                "parses": [
                    {
                        "lemma": p.normal_form,
                        "pos": p.tag.POS or "",
                        "morph": _gramemes(p.tag),
                    }
                    for p in parses[:4]
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
                    "можно", "нельзя", "надо", "нужно", "нужен",
                    "нужна", "нужны", "должен", "должна", "должно", "должны",
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
        {"seq": [{"pos_in": ["VERB"], "morph": {"Mood": "impr"}}]},
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
                spec_surface("без", "для", "до", "из", "от", "около", "после", "возле", "ради"),
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
                    "пять", "шесть", "семь", "восемь", "девять", "десять",
                    "много", "несколько", "сколько",
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
]


def analyze_russian(page_text, display_lang="en"):
    """
    Analyze a page of Russian text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(page_text, _RU_RULES, _tokens_for, display_lang)
