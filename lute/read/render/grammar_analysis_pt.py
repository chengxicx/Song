"""
Portuguese grammar point detection (spaCy pt_core_news_sm).

Mirrors the English/Spanish engines, but the sm model (Bosque) mis-tags
several finite forms (1sg presents, some preterites and conditionals, and
clitics are written attached: fala-se), so the rules lean on the forms it
tags reliably -- imperfect, compound pluperfect, subjunctive, gerund --
and on surface patterns elsewhere.

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

        _NLP = spacy.load("pt_core_news_sm", exclude=["parser", "senter", "ner"])
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
_GER = spec_morph(VerbForm="Ger")

_PT_RULES = [
    # ---- A1 ----
    make_rule(
        "pt_estar_em",
        "estar + em (location)",
        "A1",
        "estar (state) + em states a location: está em casa.",
        {
            "seq": [
                spec_lemma("estar"),
                spec_surface("em"),
                spec_pos("DET", "NOUN", "PROPN", "PRON", "NUM"),
            ]
        },
        zh="estar + em 表示位置：está em casa（在家）。",
    ),
    make_rule(
        "pt_haver",
        "há / tem (there is)",
        "A1",
        "impersonal há (or colloquial tem) = there is/are: há um problema.",
        {
            "seq": [
                spec_surface("há", "havia", "houve", "tem", "tinha"),
                spec_pos("NOUN", "DET", "NUM", "PRON", "ADJ"),
            ]
        },
        zh="há / tem：有、存在（há um problema 有个问题）。",
    ),
    make_rule(
        "pt_gostar",
        "gostar de (to like)",
        "A1",
        "gostar takes de: gosto de música.",
        {"re": re.compile(r"(?i)\b(?:gosto|gostamos|adoro|adoramos|odeio)\b")},
        zh="gostar de：喜欢（gosto de música 我喜欢音乐）。",
    ),
    make_rule(
        "pt_questions",
        "Question words (onde / como / por que...)",
        "A1",
        "onde where, como how, quando when, por que why, quem who, qual which.",
        {
            "seq": [
                spec_surface(
                    "onde",
                    "como",
                    "quando",
                    "por que",
                    "porque",
                    "porquê",
                    "quem",
                    "qual",
                    "quanto",
                )
            ]
        },
        zh="特殊疑问词：onde 哪里 / por que 为什么 / quanto 多少。",
    ),
    # ---- A2 ----
    make_rule(
        "pt_progressivo",
        "estar a + inf / estar + gerúndio (progressive)",
        "A2",
        "European: estou a comer; Brazilian: estou comendo.",
        {
            "any_of": [
                {"seq": [spec_lemma("estar"), spec_surface("a"), _INF]},
                {"seq": [spec_lemma("estar"), _GER]},
            ]
        },
        zh="进行体：欧洲葡语 estar a + 原形；巴西葡语 estar + 副动词。",
    ),
    make_rule(
        "pt_ir_inf",
        "ir + infinitive (future)",
        "A2",
        "the common future periphrasis: vou comer.",
        {"seq": [spec_surface("vou", "vais", "vai", "vamos", "vão"), _INF]},
        zh="ir + 原形动词：将要（vou comer 我要吃）。",
    ),
    make_rule(
        "pt_imperfeito",
        "Imperfeito (era / vivia)",
        "A2",
        "an ongoing, habitual or descriptive past.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Tense": "Imp"}}]},
        zh="未完成过去时（era / vivia）：过去 ongoing、习惯或背景描写。",
    ),
    make_rule(
        "pt_modais",
        "poder / dever / querer / precisar + infinitive",
        "A2",
        "modal + infinitive: posso vir, devo estudar.",
        {
            "any_of": [
                {
                    "left": [spec_lemma("poder", "dever", "querer", "precisar")],
                    "right": [_INF],
                    "min_gap": 0,
                    "max_gap": 5,
                },
                {
                    "left": [
                        spec_surface(
                            "posso",
                            "podemos",
                            "devo",
                            "devemos",
                            "quero",
                            "queremos",
                            "preciso",
                            "precisamos",
                        )
                    ],
                    "right": [_INF],
                    "min_gap": 0,
                    "max_gap": 5,
                },
            ]
        },
        zh="情态动词 poder/dever/querer/precisar + 原形：能/必须/想/需要。",
    ),
    make_rule(
        "pt_ter_que",
        "ter que / ter de + infinitive",
        "A2",
        "obligation: tenho que estudar.",
        {
            "seq": [
                spec_surface("tenho", "tens", "temos", "têm"),
                spec_surface("que", "de"),
                _INF,
            ]
        },
        zh="ter que/de + 原形动词：必须、不得不。",
    ),
    make_rule(
        "pt_comparativo",
        "mais / menos / tão ... que / como",
        "A2",
        "comparative and equality: mais alto que, tão rápido como.",
        {
            "left": [spec_surface("mais", "menos", "tão")],
            "right": [spec_surface("que", "como")],
            "min_gap": 1,
            "max_gap": 4,
        },
        zh="比较：mais/menos … que、tão … como（比……更/一样）。",
    ),
    # ---- B1 ----
    make_rule(
        "pt_subjuntivo",
        "Conjuntivo (subjunctive)",
        "B1",
        "after will, doubt, emotion: espero que venha.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Mood": "Sub"}}]},
        zh="虚拟式：意愿/怀疑/情感触发词后用（espero que venha）。",
    ),
    make_rule(
        "pt_relative",
        "o qual / cujo / quem (relative pronouns)",
        "B1",
        "o qual agrees with the noun; cujo = whose; quem = who.",
        {
            "any_of": [
                {"re": re.compile(r"(?i)\b(?:[dnpm][oa]s?|os|as|o|a)\s+quals?\b")},
                {
                    "seq": [
                        spec_surface("cujo", "cujos", "cuja", "cujas", "quem", "ondev")
                    ]
                },
            ]
        },
        zh="关系代词：o qual（随先行词变位）/ cujo（……的）/ quem。",
    ),
    make_rule(
        "pt_ha_tempo",
        "há + time (ago / duration)",
        "B1",
        "há dois anos = two years ago (past) or for two years (up to now).",
        {
            "re": re.compile(
                r"(?i)\bhá\s+(?:\d+|dois|duas|três|tres|quatro|cinco|muitos)\s+(?:anos|meses|semanas|dias|horas|minutos)\b"
            )
        },
        zh="há + 时间：……前（过去）或……至今（持续）。",
    ),
    make_rule(
        "pt_prima_inf",
        "antes de / depois de + infinitive",
        "B1",
        "before/after doing: antes de sair, depois de comer.",
        {"seq": [spec_surface("antes", "depois"), spec_surface("de"), _INF]},
        zh="antes de / depois de + 原形：在……之前/之后。",
    ),
    # ---- B2 ----
    make_rule(
        "pt_se_passivo",
        "clitic -se (passive / impersonal)",
        "B2",
        "fala-se = Portuguese is spoken (passive); espera-se = one expects.",
        {"re": re.compile(r"(?i)\b\w+-se\b")},
        zh="代词 -se：自复被动（fala-se 葡萄牙语被讲）/ 无人称。",
    ),
    make_rule(
        "pt_mais_que_perfeito",
        "Mais-que-perfeito: tinha + participle",
        "B2",
        "the earlier of two past events: já tinha comido.",
        {
            "left": [spec_surface("tinha", "tínhamos", "tinham")],
            "right": [spec_morph(VerbForm="Part")],
            "min_gap": 0,
            "max_gap": 3,
        },
        zh="过去完成时：tinha + 过去分词（过去的过去）。",
    ),
    make_rule(
        "pt_se_condicional",
        "se + conjuntivo, condicional",
        "B2",
        "unreal present: Se eu fosse rico, viajaria.",
        {
            "left": [spec_surface("se")],
            "right": [spec_morph(Mood="Cnd")],
            "min_gap": 1,
            "max_gap": 10,
        },
        zh="se + 虚拟式，主句条件式：假设。",
    ),
    # ---- C1 ----
    make_rule(
        "pt_conjuntivo_imperfeito",
        "Imperfect subjunctive (fosse / tivesse)",
        "C1",
        "unreal conditions and past-tense subjunctive: se eu fosse.",
        {
            "seq": [
                {"pos_in": ["VERB", "AUX"], "morph": {"Mood": "Sub", "Tense": "Imp"}}
            ]
        },
        zh="虚拟式未完成时（fosse / tivesse）：假设与过去转述。",
    ),
]


def analyze_portuguese(page_text, display_lang="en"):
    """
    Analyze a page of Portuguese text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(page_text, _PT_RULES, _tokens_for, display_lang)
