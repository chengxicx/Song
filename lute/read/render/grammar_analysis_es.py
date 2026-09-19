"""
Spanish grammar point detection (spaCy es_core_news_sm).

Mirrors the English engine: sentences are split, tokenised with lemma +
POS + morphological features, and token-aware rules recognise grammar
constructions.  The sm model tags mood/tense reliably inside a sentence
context but fumbles isolated words and the simple future, so rules anchor
on context (e.g. a trigger + que + subjunctive) and the future is covered
by the "ir a + infinitive" periphrasis.

spaCy and the model are imported lazily so the base install works without
them; the route falls back to the generic rule library on ImportError.
"""

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

        _NLP = spacy.load("es_core_news_sm", exclude=["parser", "senter", "ner"])
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
# Levels use CEFR bands (A1..C2), rendered by the panel like the JLPT ones.
# Spanish verbs surface as VERB/AUX with UD morph features: Mood
# (Ind/Sub/Imp/Cnd), Tense (Pres/Past/Imp/Fut) and VerbForm (Fin/Inf/Part).

_INF = spec_morph(VerbForm="Inf")

_ES_RULES = [
    # ---- A1 ----
    make_rule(
        "es_ser_de",
        "ser + de (origin / material)",
        "A1",
        "ser (permanent quality) + de states origin or material: es de España.",
        {"seq": [spec_lemma("ser"), spec_surface("de"), spec_pos("NOUN", "PROPN")]},
        zh="ser + de 表示来源/材质：Es de España（他是西班牙人/它产自西班牙）。",
    ),
    make_rule(
        "es_estar_en",
        "estar + en (location)",
        "A1",
        "estar (state) + en states a location: está en casa.",
        {
            "seq": [
                spec_lemma("estar"),
                spec_surface("en"),
                spec_pos("DET", "NOUN", "PROPN", "PRON", "NUM"),
            ]
        },
        zh="estar + en 表示位置：Está en casa（他在家）。",
    ),
    make_rule(
        "es_hay",
        "hay / había + noun",
        "A1",
        "impersonal haber = there is / there are (no article before the noun).",
        {"seq": [spec_surface("hay", "había", "hubo"), spec_pos("NOUN", "DET", "NUM", "PRON", "ADJ")]},
        zh="hay / había 表示『有；存在』，名词前不加冠词（Hay un problema）。",
    ),
    make_rule(
        "es_gustar",
        "gustar-type verb + indirect object",
        "A1",
        "gustar/encantar/doler work backwards: the thing liked is the subject "
        "(me gusta el café / me gustan los libros).",
        {
            "seq": [
                spec_lemma("gustar", "encantar", "fascinar", "molestar", "doler", "apetecer")
            ]
        },
        zh="gustar 类动词用法特殊：喜欢的事物作主语，人用间接宾语代词（me gusta…）。",
    ),
    make_rule(
        "es_wh_questions",
        "Question words (qué, cómo, dónde...)",
        "A1",
        "qué/cómo/dónde/cuándo/quién/cuál/cuánto ask for information; "
        "they carry a written accent.",
        {
            "seq": [
                spec_surface(
                    "qué", "cómo", "dónde", "cuándo", "quién", "quiénes",
                    "cuál", "cuáles", "cuánto", "cuánta", "cuántos", "cuántas", "adónde",
                )
            ]
        },
        zh="特殊疑问词 qué/cómo/dónde/cuándo/quién/cuál/cuánto（带重音符号）。",
    ),
    # ---- A2 ----
    make_rule(
        "es_preterite",
        "Preterite (comió)",
        "A2",
        "a completed past action with clear start/end: comí, comió, fueron.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Tense": "Past"}}]},
        zh="简单过去时（comió 等）：过去完成的动作，有起点/终点。",
    ),
    make_rule(
        "es_imperfect",
        "Imperfect (comía / vivía)",
        "A2",
        "an ongoing or repeated past action, or a past description: vivía, era, cantaba.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Tense": "Imp"}}]},
        zh="未完成过去时（vivía / cantaba / era）：过去 ongoing、习惯或背景描写。",
    ),
    make_rule(
        "es_ir_a",
        "ir a + infinitive (future)",
        "A2",
        "going-to future: voy a comer. (Also the usual way to talk about the future; "
        "the simple future -ré forms are B1.)",
        {"seq": [spec_lemma("ir"), spec_surface("a"), _INF]},
        zh="ir a + 原形动词：将要/打算（口语中最常用的将来表达）。",
    ),
    make_rule(
        "es_tener_que",
        "tener que + infinitive",
        "A2",
        "obligation: tengo que estudiar.",
        {"seq": [spec_lemma("tener"), spec_surface("que"), _INF]},
        zh="tener que + 原形动词：必须/不得不。",
    ),
    make_rule(
        "es_acabar_de",
        "acabar de + infinitive",
        "A2",
        "to have just done something: acabo de comer.",
        {"seq": [spec_lemma("acabar"), spec_surface("de"), _INF]},
        zh="acabar de + 原形动词：刚刚做完某事。",
    ),
    make_rule(
        "es_para_inf",
        "para + infinitive (purpose)",
        "A2",
        "para marks purpose (in order to); por marks cause/exchange — "
        "the classic por/para contrast.",
        {"seq": [spec_surface("para"), _INF]},
        zh="para + 原形动词表示目的（为了……）；por 表原因/交换——por/para 经典对比。",
    ),
    make_rule(
        "es_desde_hace",
        "desde hace (duration up to now)",
        "A2",
        "desde hace + time = for (and still ongoing): vivo aquí desde hace dos años.",
        {"seq": [spec_surface("desde"), spec_surface("hace")]},
        zh="desde hace + 时间：从……到现在（持续至今）。",
    ),
    make_rule(
        "es_mas_que",
        "más ... que (comparison)",
        "A2",
        "más + adjective/noun + que = more ... than; menos ... que = less ... than.",
        {"left": [spec_surface("más", "menos")], "right": [spec_surface("que")], "min_gap": 1, "max_gap": 4},
        zh="más/menos … que：比……更/更不……。",
    ),
    make_rule(
        "es_tan_como",
        "tan + adjective + como",
        "A2",
        "tan ... como = as ... as (equal comparison).",
        {"seq": [spec_surface("tan"), spec_pos("ADJ", "ADV"), spec_surface("como")]},
        zh="tan + 形容词/副词 + como：和……一样。",
    ),
    make_rule(
        "es_le",
        "Indirect object pronoun (le / les)",
        "A2",
        "le/les = to him/her/them; combines with gustar-type verbs and with se "
        "(se lo doy).",
        {"seq": [{"surface_in": ["le", "les"], "morph": {"Case": "Dat"}}]},
        zh="间接宾语代词 le/les（给他/她/他们），常与 gustar 类动词、se 连用。",
    ),
]


def analyze_spanish(page_text, display_lang="en"):
    """
    Analyze a page of Spanish text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(page_text, _ES_RULES, _tokens_for, display_lang)
