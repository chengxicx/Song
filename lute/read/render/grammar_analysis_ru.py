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
        {"seq": [spec_pos("PRTF", "PRTS")]},
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
]


def analyze_russian(page_text, display_lang="en"):
    """
    Analyze a page of Russian text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(page_text, _RU_RULES, _tokens_for, display_lang)
