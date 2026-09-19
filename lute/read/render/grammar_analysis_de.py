"""
German grammar point detection (spaCy de_core_news_sm).

Mirrors the English/Spanish engines: sentences are split, tokenised with
lemma + POS + morphological features, and token-aware rules recognise
grammar constructions.  The sm model tags case on articles and the
Konjunktiv reliably, which powers the case and conditional rules; word
order (verb-final clauses) is covered with gapped patterns.

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

        _NLP = spacy.load("de_core_news_sm", exclude=["parser", "senter", "ner"])
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
# Levels use CEFR bands (A1..C2).  UD morph values: Mood Ind/Sub,
# Tense Pres/Past, VerbForm Fin/Inf/Part, Case Nom/Acc/Dat/Gen,
# Reflex Yes on reflexive pronouns.

_INF = spec_morph(VerbForm="Inf")
_PART = spec_morph(VerbForm="Part")
_DET_CASE = lambda case: {"pos_in": ["DET"], "morph": {"Case": case}}  # noqa: E731

_DE_RULES = [
    # ---- A1 ----
    make_rule(
        "de_es_gibt",
        "es gibt (there is)",
        "A1",
        "es gibt + accusative states existence: es gibt einen Park.",
        {"seq": [spec_surface("es"), spec_lemma("geben")]},
        zh="es gibt + 第四格：有/存在（es gibt einen Park）。",
    ),
    make_rule(
        "de_modals",
        "Modal + infinitive at the end (kann ... kommen)",
        "A1",
        "the conjugated modal comes second, the infinitive goes to the end "
        "of the clause: ich kann heute nicht kommen.",
        {
            "left": [spec_lemma("können", "müssen", "wollen", "sollen", "dürfen", "mögen", "möchten")],
            "right": [_INF],
            "min_gap": 0,
            "max_gap": 8,
        },
        zh="情态动词框型结构：变位情态动词在第二位，原形动词在句尾。",
    ),
    make_rule(
        "de_negation",
        "nicht / kein (negation)",
        "A1",
        "nicht negates verbs/adjectives; kein(e) negates nouns.",
        {"seq": [spec_surface("nicht", "kein", "keine", "keinen", "keinem", "keiner", "nie")]},
        zh="否定：nicht 否定动词/形容词，kein 否定名词。",
    ),
    make_rule(
        "de_questions",
        "Question words (wer, was, wann, wo...)",
        "A1",
        "wer/was/wann/wo/warum/wie ask for information.",
        {"seq": [spec_surface("wer", "was", "wann", "wo", "warum", "wie", "wohin", "woher", "wieso")]},
        zh="特殊疑问词 wer/was/wann/wo/warum/wie。",
    ),
    make_rule(
        "de_possessives",
        "Possessive articles (mein, dein, unser...)",
        "A1",
        "mein/dein/unser/euer + case ending agree with the thing owned.",
        {"seq": [spec_surface("mein", "meine", "meinen", "meinem", "meiner", "dein", "deine", "deinen", "unser", "unsere", "unseren", "euer", "eure")]},
        zh="主有冠词 mein/dein/unser 等，随性数格变化。",
    ),
    # ---- A2 ----
    make_rule(
        "de_perfekt",
        "Perfekt: haben/sein + Partizip",
        "A2",
        "the spoken past: ich habe gemacht, sie ist gegangen.",
        {"left": [spec_lemma("haben", "sein")], "right": [_PART], "min_gap": 0, "max_gap": 3},
        zh="现在完成时：haben/sein + 第二分词（口语常用过去时）。",
    ),
    make_rule(
        "de_praeteritum",
        "Präteritum (war / hatte / sah)",
        "A2",
        "the written past; in speech mostly for sein/haben/modals.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Tense": "Past"}}]},
        zh="过去时（war / hatte / sah）：书面叙事；口语中多用于 sein/haben/情态动词。",
    ),
    make_rule(
        "de_futur1",
        "Futur I: werden + infinitive",
        "A2",
        "the future or an assumption: ich werde gehen.",
        {"seq": [spec_lemma("werden"), _INF]},
        zh="第一将来时：werden + 原形动词（也表示推测）。",
    ),
    make_rule(
        "de_akku",
        "Accusative object (den / einen ...)",
        "A2",
        "the direct object takes the accusative; articles mark it: den Mann.",
        {"seq": [_DET_CASE("Acc")]},
        zh="第四格宾语：直接宾语用 Akkusativ，冠词标记（den/einen）。",
    ),
    make_rule(
        "de_dativ",
        "Dative (dem / der ...)",
        "A2",
        "the indirect object and after aus/bei/mit/nach/seit/von/zū take the dative.",
        {"seq": [_DET_CASE("Dat")]},
        zh="第三格：间接宾语及 aus/bei/mit/nach/seit/von/zu 之后用 Dativ（dem）。",
    ),
    make_rule(
        "de_zu_inf",
        "zu + infinitive",
        "A2",
        "zu-infinitive with verbs like versuchen, beginnen: er versucht zu kommen.",
        {"seq": [spec_surface("zu"), _INF]},
        zh="zu + 原形动词：与 versuchen/beginnen 等连用。",
    ),
    make_rule(
        "de_als",
        "als (than / when / as)",
        "A2",
        "comparisons (größer als) and past-time clauses (als ich Kind war).",
        {"seq": [spec_surface("als")]},
        zh="als：比（比较级 + als）；当……时（过去）；作为。",
    ),
    make_rule(
        "de_gern",
        "gern / lieber (liking)",
        "A2",
        "gern expresses liking an action: ich schwimme gern.",
        {"seq": [spec_surface("gern", "gerne", "lieber", "am liebsten")]},
        zh="gern/lieber：喜欢做……（副词修饰动词）。",
    ),
    make_rule(
        "de_reflexive",
        "Reflexive pronouns (sich / mich)",
        "A2",
        "many German verbs are reflexive: sich freuen, ich wasche mich.",
        {"seq": [{"pos_in": ["PRON"], "morph": {"Reflex": "Yes"}}]},
        zh="反身代词（sich/mich）：德语大量动词为反身动词（sich freuen）。",
    ),
    # ---- B1 ----
    make_rule(
        "de_weil",
        "weil / obwohl / damit (verb-final)",
        "B1",
        "subordinating conjunctions send the verb to the end: ... weil ich krank bin.",
        {"seq": [spec_surface("weil", "obwohl", "damit", "sodass", "während", "bevor", "nachdem")]},
        zh="从属连词 weil/obwohl/damit 等：动词移到句尾（框型）。",
    ),
    make_rule(
        "de_werden_passiv",
        "Passiv: werden + Partizip",
        "B1",
        "the passive: es wird gemacht, wurde gebaut.",
        {"seq": [spec_lemma("werden"), _PART]},
        zh="被动语态：werden + 第二分词（es wird gemacht 被做）。",
    ),
    make_rule(
        "de_konjunktiv2",
        "Konjunktiv II (würde / hätte / wäre)",
        "B1",
        "polite requests and unreal statements: ich würde gehen, wenn ich könnte.",
        {
            "any_of": [
                {"left": [spec_lemma("würde")], "right": [_INF], "min_gap": 0, "max_gap": 5},
                {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Mood": "Sub"}}]},
            ]
        },
        zh="第二虚拟式（würde/hätte/wäre）：礼貌请求或非现实假设。",
    ),
    make_rule(
        "de_seit",
        "seit + present tense",
        "B1",
        "German uses the PRESENT with seit for durations up to now: "
        "ich wohne hier seit zwei Jahren.",
        {"seq": [spec_surface("seit", "seitdem")]},
        zh="seit + 现在时：从……到现在（英语用完成时而德语用现在时）。",
    ),
    # ---- B2 ----
    make_rule(
        "de_ob",
        "ob (whether / if)",
        "B2",
        "indirect yes/no questions: ich weiß nicht, ob er kommt.",
        {"seq": [spec_surface("ob")]},
        zh="ob：是否（间接一般疑问句，动词在句尾）。",
    ),
    make_rule(
        "de_trotz",
        "trotz / trotzdem (concession)",
        "B2",
        "trotz + genitive noun; trotzdem = nevertheless.",
        {"seq": [spec_surface("trotz", "trotzdem")]},
        zh="trotz + 第二格 / trotzdem：尽管如此。",
    ),
    # ---- C1 / C2 ----
    make_rule(
        "de_je_desto",
        "je ... desto (the ... the ...)",
        "C1",
        "correlative comparison: je mehr, desto besser.",
        {"left": [spec_surface("je")], "right": [spec_surface("desto")], "min_gap": 1, "max_gap": 6},
        zh="je … desto：越……越……（je mehr, desto besser）。",
    ),
    make_rule(
        "de_zwar",
        "zwar / jedoch / allerdings",
        "C1",
        "formal concession and contrast: zwar schön, aber teuer.",
        {"seq": [spec_surface("zwar", "jedoch", "dennoch", "allerdings")]},
        zh="zwar/jedoch/dennoch/allerdings：固然/然而（书面语让步转折）。",
    ),
    make_rule(
        "de_indem",
        "indem (by doing)",
        "C2",
        "manner clause with verb-final order: indem man übt.",
        {"seq": [spec_surface("indem")]},
        zh="indem：通过……方式（方式从句，动词在句尾）。",
    ),
]


def analyze_german(page_text, display_lang="en"):
    """
    Analyze a page of German text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(page_text, _DE_RULES, _tokens_for, display_lang)
