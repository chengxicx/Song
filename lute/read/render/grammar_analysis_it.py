"""
Italian grammar point detection (spaCy it_core_news_sm).

Mirrors the English/Spanish engines.  The sm model (ISDT) tags mood and
tense reliably, including the subjunctive and the literary passato
remoto, so the rules lean on morph features; clitic-heavy frames stay
surface-based.

spaCy and the model are imported lazily so the base install works without
them; the route falls back to the generic rule library on ImportError.
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

_NLP = None


def _nlp():
    "Lazy, process-lifetime spaCy pipeline."
    global _NLP
    if _NLP is None:
        import spacy  # pylint: disable=import-outside-toplevel

        _NLP = spacy.load("it_core_news_sm", exclude=["parser", "senter", "ner"])
    return _NLP


def _tokens_for(sentence):
    "Tokenize a sentence; return a list of token dicts."
    doc = _nlp()(sentence)
    return [
        {
            "surface": t.text,
            "lemma": t.lemma_,
            "pos": t.pos_,
            "morph": t.morph.to_dict(),
            "idx": t.idx,
        }
        for t in doc
    ]


# ---- rules -------------------------------------------------------------
#
# Levels use CEFR bands (A1..C2).  UD morph values: Mood Ind/Sub/Cnd,
# Tense Pres/Past/Imp/Fut, VerbForm Fin/Inf/Ger/Part.

_INF = spec_morph(VerbForm="Inf")

_IT_RULES = [
    # ---- A1 ----
    make_rule(
        "it_essere_di",
        "essere + di (origin)",
        "A1",
        "essere (permanent quality) + di states origin: sono di Roma.",
        {"seq": [spec_lemma("essere"), spec_surface("di"), spec_pos("NOUN", "PROPN")]},
        zh="essere + di 表示来源：Sono di Roma（我是罗马人）。",
    ),
    make_rule(
        "it_ce",
        "c'è / ci sono (there is)",
        "A1",
        "impersonal c'è (singular) / ci sono (plural) = there is/are.",
        {"re": re.compile(r"(?i)\bc'?è\b|ci sono")},
        zh="c'è / ci sono：有、存在（单/复数）。",
    ),
    make_rule(
        "it_piacere",
        "piacere (to like)",
        "A1",
        "piacere works backwards: the liked thing is the subject "
        "(mi piace la pizza).",
        {"seq": [spec_lemma("piacere")]},
        zh="piacere 类动词：喜欢的事物作主语（mi piace…）。",
    ),
    make_rule(
        "it_questions",
        "Question words (che / come / dove...)",
        "A1",
        "che/che cosa what, come how, dove where, perché why, quando when, "
        "chi who, quanto how much.",
        {"seq": [spec_surface("che cosa", "cosa", "come", "dove", "perché", "perche", "quando", "chi", "quanto")]},
        zh="特殊疑问词：che/cosa 什么、come 怎样、dove 哪里、perché 为什么。",
    ),
    # ---- A2 ----
    make_rule(
        "it_stare_gerundio",
        "stare + gerundio (progressive)",
        "A2",
        "stare + -ando/-endo marks action in progress: sto mangiando.",
        {
            "any_of": [
                {"seq": [spec_lemma("stare"), spec_morph(VerbForm="Ger")]},
                {"seq": [spec_surface("sto", "stai", "sta", "stiamo", "state", "stanno"), spec_morph(VerbForm="Ger")]},
            ]
        },
        zh="stare + 副动词：正在（sto mangiando 正在吃）。",
    ),
    make_rule(
        "it_passato_prossimo",
        "Passato prossimo: avere/essere + participle",
        "A2",
        "the spoken past: ho mangiato, sono andato.",
        {"seq": [spec_lemma("avere", "essere"), spec_morph(VerbForm="Part")]},
        zh="近过去时：avere/essere + 过去分词（口语最常用的过去时）。",
    ),
    make_rule(
        "it_imperfetto",
        "Imperfetto (ero / giocavo)",
        "A2",
        "an ongoing, habitual or descriptive past.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Tense": "Imp"}}]},
        zh="未完成过去时（ero / giocavo）：过去 ongoing、习惯或背景描写。",
    ),
    make_rule(
        "it_futuro",
        "Futuro semplice",
        "A2",
        "the simple future: partirò, sarà.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Tense": "Fut"}}]},
        zh="简单将来时（partirò / sarà）。",
    ),
    make_rule(
        "it_modali",
        "potere / volere / dovere + infinitive",
        "A2",
        "modal + infinitive: posso venire, devo studiare.",
        {
            "left": [spec_lemma("potere", "volere", "dovere", "sapere")],
            "right": [_INF],
            "min_gap": 0,
            "max_gap": 5,
        },
        zh="情态动词 potere/volere/dovere + 原形：能/想/必须。",
    ),
    make_rule(
        "it_comparativo",
        "più / meno ... che / di",
        "A2",
        "comparative: più grande di, più che.",
        {"left": [spec_surface("più", "piu", "meno")], "right": [spec_surface("che", "di")], "min_gap": 1, "max_gap": 4},
        zh="比较级：più/meno … che/di（比……更/更不）。",
    ),
    make_rule(
        "it_bisogna",
        "bisogna (necessity)",
        "A2",
        "impersonal necessity + infinitive: bisogna partire.",
        {"seq": [spec_lemma("bisognare")]},
        zh="bisogna：必须（+ 原形动词）。",
    ),
    # ---- B1 ----
    make_rule(
        "it_congiuntivo",
        "Congiuntivo (subjunctive)",
        "B1",
        "after opinion/doubt/emotion triggers: penso che sia giusto.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Mood": "Sub"}}]},
        zh="虚拟式：观点/怀疑/情感类触发词后用（penso che sia…）。",
    ),
    make_rule(
        "it_condizionale",
        "Condizionale (conditional)",
        "B1",
        "polite requests and hypotheticals: vorrei, comprerei.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Mood": "Cnd"}}]},
        zh="条件式（vorrei / comprerei）：礼貌请求或假设。",
    ),
    make_rule(
        "it_cui",
        "cui / il quale (relative)",
        "B1",
        "cui is the prepositional relative; il quale agrees with the noun.",
        {
            "any_of": [
                {"seq": [spec_surface("cui")]},
                {"re": re.compile(r"(?i)\b(?:il|lo|la|i|gli|le)\s+quale\b")},
            ]
        },
        zh="关系代词：cui（介词关系词）/ il quale（随先行词变位）。",
    ),
    make_rule(
        "it_da_tempo",
        "da + time (duration up to now)",
        "B1",
        "Italian uses the PRESENT with da for durations: abito qui da due anni.",
        {"re": re.compile(r"(?i)\bda\s+(?:\d+|due|tre|quattro|cinque|dieci|molti)\s+(?:anni|mesi|settimane|giorni|ore|minuti)\b")},
        zh="da + 时间 + 现在时：从……到现在（英语用完成时而意语用现在时）。",
    ),
    make_rule(
        "it_ne",
        "Ne (of it / about it)",
        "B1",
        "ne replaces di-phrases and quantities: ne parlo, ne voglio due.",
        {"seq": [{"surface_in": ["ne", "Ne"], "pos_in": ["PRON"]}]},
        zh="代词 ne：由此/一些（ne parlo 谈论此事）。",
    ),
    make_rule(
        "it_prima_di",
        "prima di + infinitive",
        "B1",
        "before doing: prima di uscire.",
        {"seq": [spec_surface("prima"), spec_surface("di"), _INF]},
        zh="prima di + 原形动词：在……之前。",
    ),
    # ---- B2 ----
    make_rule(
        "it_si_passivante",
        "si + verb (passive / impersonal)",
        "B2",
        "si parla = Italian is spoken (passive); si dice = people say.",
        {"seq": [spec_surface("si"), spec_pos("VERB")]},
        zh="si 结构：自复被动（si parla 意大利语被讲）/ 无人称（si dice 人们说）。",
    ),
    make_rule(
        "it_stare_per",
        "stare per + infinitive (about to)",
        "B2",
        "an imminent action: sto per uscire.",
        {"seq": [spec_surface("sto", "stai", "sta", "stiamo", "state", "stanno"), spec_surface("per"), _INF]},
        zh="stare per + 原形：马上就要（sto per uscire）。",
    ),
    make_rule(
        "it_congiuntivo_passato",
        "Past subjunctive (abbia / sia stato)",
        "B2",
        "a prior action inside the subjunctive: penso che abbia finito.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Mood": "Sub"}}, spec_morph(VerbForm="Part")]},
        zh="虚拟式过去时（abbia fatto）：从句动作先于主句。",
    ),
    make_rule(
        "it_se_condizionale",
        "se + congiuntivo, condizionale",
        "B2",
        "unreal present: Se fossi ricco, viaggerei.",
        {"left": [spec_surface("se")], "right": [spec_morph(Mood="Cnd")], "min_gap": 1, "max_gap": 10},
        zh="se + 虚拟式，主句条件式：假设（Se fossi…, viaggerei…）。",
    ),
    # ---- C1 ----
    make_rule(
        "it_congiuntivo_imperfetto",
        "Imperfect subjunctive (fossi / avessi)",
        "C1",
        "unreal or past-tense subjunctive: se fossi, pensavo che fosse.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Mood": "Sub", "Tense": "Imp"}}]},
        zh="虚拟式未完成时（fossi / avessi）：假设与过去转述。",
    ),
    make_rule(
        "it_dopo_aver",
        "dopo avere / essere + participle",
        "C1",
        "after having done: dopo aver mangiato.",
        {"seq": [spec_surface("dopo"), _INF]},
        zh="dopo aver/essere + 过去分词：做完……之后。",
    ),
    make_rule(
        "it_passato_remoto",
        "Passato remoto (literary past)",
        "C1",
        "the historical narrative past: fu, vide, andò.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Tense": "Past", "VerbForm": "Fin"}}]},
        zh="远过去时（fu / andò）：书面叙事体（文学/历史）。",
    ),
]


def analyze_italian(page_text, display_lang="en"):
    """
    Analyze a page of Italian text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(page_text, _IT_RULES, _tokens_for, display_lang)
