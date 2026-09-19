"""
English grammar point detection (spaCy en_core_web_sm).

Mirrors the Japanese/Korean engines: sentences are split, tokenised with
lemma + POS + morphological features (spaCy's tagger + attribute_ruler +
lemmatizer; the parser and NER are excluded for speed), and token-aware
rules recognise grammar constructions.

spaCy and the model are imported lazily so the base install works without
them; the route falls back to the generic rule library on ImportError and
the app logs a hint at startup when an English language exists without the
dependency installed.
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

        _NLP = spacy.load("en_core_web_sm", exclude=["parser", "senter", "ner"])
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

_BE_PRESENT = spec_surface("am", "is", "are", "'m", "'re", "'s")
_BE_PAST = spec_surface("was", "were")
_PROG = spec_morph(Aspect="Prog")
_GER = spec_morph(VerbForm="Ger")

_EN_RULES = [
    # ---- A1 ----
    make_rule(
        "en_there_be",
        "There is / There are",
        "A1",
        "there + is/are introduces something that exists; are goes with plurals.",
        {"seq": [spec_lemma("there"), spec_lemma("be")]},
        zh="there is / there are 表示『有；存在』，复数用 there are。",
    ),
    make_rule(
        "en_lets",
        "Let's + verb",
        "A1",
        "let's + base verb makes a suggestion (let's go).",
        {"re": re.compile(r"(?i)\blet'?s\b")},
        zh="let's + 动词原形，表示提议（咱们……吧）。",
    ),
    make_rule(
        "en_can_could",
        "can / could + verb",
        "A1",
        "can = ability or permission; could = past ability or a polite request.",
        {"seq": [spec_surface("can", "could"), spec_pos("VERB", "AUX")]},
        zh="can 表示能力/许可，could 表示过去的能力或礼貌请求。",
    ),
    make_rule(
        "en_want_to",
        "want to + verb",
        "A1",
        "want + to-infinitive expresses a desire (want to go).",
        {"seq": [spec_lemma("want"), spec_surface("to"), spec_pos("VERB")]},
        zh="want to do 表示想做某事。",
    ),
    # ---- A2 ----
    make_rule(
        "en_must_should",
        "must / should / have to + verb",
        "A2",
        "obligation or advice: must (strong), have to (required), should (advice).",
        {
            "any_of": [
                {"seq": [spec_surface("must", "should"), spec_pos("VERB", "AUX")]},
                {"seq": [spec_surface("have", "has", "had"), spec_surface("to"), spec_pos("VERB")]},
            ]
        },
        zh="must 必须（强义务），have to 不得不，should 应该（建议）。",
    ),
    make_rule(
        "en_past_simple",
        "Past simple",
        "A2",
        "a finished action in the past (went, saw, knew).",
        {"seq": [{"pos_in": ["VERB"], "morph": {"Tense": "Past", "VerbForm": "Fin"}}]},
        zh="一般过去时：过去发生的动作或状态（went / saw / knew）。",
    ),
    make_rule(
        "en_present_continuous",
        "Present continuous: am/is/are + -ing",
        "A2",
        "an action happening now or around now.",
        {
            "any_of": [
                {"seq": [_BE_PRESENT, _PROG]},
                {"seq": [_BE_PRESENT, _GER]},
            ]
        },
        zh="现在进行时：am/is/are + 动词-ing，表示正在发生。",
    ),
    make_rule(
        "en_past_continuous",
        "Past continuous: was/were + -ing",
        "A2",
        "an action in progress at a past moment, often interrupted by a shorter one.",
        {
            "any_of": [
                {"seq": [_BE_PAST, _PROG]},
                {"seq": [_BE_PAST, _GER]},
            ]
        },
        zh="过去进行时：was/were + 动词-ing，表示过去某时正在进行。",
    ),
    make_rule(
        "en_will_future",
        "will + verb",
        "A2",
        "future or spontaneous decision / promise.",
        {"seq": [spec_surface("will", "'ll"), spec_pos("VERB", "AUX")]},
        zh="will + 动词原形：将来、临时决定或承诺。",
    ),
    make_rule(
        "en_going_to",
        "be going to + verb",
        "A2",
        "a plan or intention, or something about to happen.",
        {"seq": [spec_lemma("be"), spec_surface("going"), spec_surface("to"), spec_pos("VERB")]},
        zh="be going to do：打算/将要（计划或即将发生）。",
    ),
    make_rule(
        "en_comparative",
        "Comparative (-er / more ... than)",
        "A2",
        "comparing two things; pairs with than.",
        {"seq": [spec_morph(Degree="Cmp")]},
        zh="比较级（-er / more …）：两者比较，常与 than 连用。",
    ),
    make_rule(
        "en_superlative",
        "Superlative (the -est / the most)",
        "A2",
        "the top degree within a group (the biggest, the most interesting).",
        {"seq": [spec_morph(Degree="Sup")]},
        zh="最高级（the -est / the most …）：一组中的最高程度。",
    ),
    make_rule(
        "en_like_ing",
        "like/love/hate/enjoy + -ing",
        "A2",
        "verb of preference followed by the -ing form.",
        {
            "any_of": [
                {"seq": [spec_lemma("like", "love", "hate", "enjoy"), _PROG]},
                {"seq": [spec_lemma("like", "love", "hate", "enjoy"), _GER]},
            ]
        },
        zh="like / love / hate / enjoy + 动词-ing，表示好恶。",
    ),
    make_rule(
        "en_too_to",
        "too + adjective + to ...",
        "A2",
        "so much of a quality that something is impossible.",
        {"seq": [spec_surface("too"), spec_pos("ADJ"), spec_surface("to"), spec_pos("VERB")]},
        zh="too + 形容词 + to do：太……而不能。",
    ),
    make_rule(
        "en_as_as",
        "as ... as",
        "A2",
        "equal comparison (as tall as).",
        {"left": [spec_surface("as")], "right": [spec_surface("as")], "min_gap": 1, "max_gap": 3},
        zh="as + 形容词/副词 + as：和……一样。",
    ),
    make_rule(
        "en_either_or",
        "either ... or",
        "A2",
        "a choice between two alternatives.",
        {"left": [spec_surface("either")], "right": [spec_surface("or")], "min_gap": 1, "max_gap": 8},
        zh="either ... or：二选一。",
    ),
    # ---- B1 ----
    make_rule(
        "en_present_perfect",
        "Present perfect: have/has + past participle",
        "B1",
        "a past action with a present result or an open time frame.",
        {"seq": [spec_surface("have", "has", "'ve"), spec_morph(VerbForm="Part")]},
        zh="现在完成时：have/has + 过去分词，过去的动作影响现在。",
    ),
    make_rule(
        "en_past_perfect",
        "Past perfect: had + past participle",
        "B1",
        "the earlier of two past events.",
        {"seq": [spec_surface("had"), spec_morph(VerbForm="Part")]},
        zh="过去完成时：had + 过去分词，表示过去的过去。",
    ),
    make_rule(
        "en_passive",
        "Passive: be + past participle",
        "B1",
        "the subject receives the action; the doer follows by or is omitted.",
        {"seq": [spec_lemma("be"), spec_morph(VerbForm="Part", Aspect="Perf")]},
        zh="被动语态：be + 过去分词，动作承受者作主语。",
    ),
    make_rule(
        "en_first_conditional",
        "First conditional: if + present, will",
        "B1",
        "a real future possibility: If it rains, I will stay.",
        {"left": [spec_surface("if")], "right": [spec_surface("will")], "min_gap": 1, "max_gap": 10},
        zh="第一条件句：if + 现在时，主句 will，真实可能。",
    ),
    make_rule(
        "en_second_conditional",
        "Second conditional: if + past, would",
        "B1",
        "an unreal or unlikely present/future: If I had time, I would go.",
        {"left": [spec_surface("if")], "right": [spec_surface("would")], "min_gap": 1, "max_gap": 10},
        zh="第二条件句：if + 过去时，主句 would，假设。",
    ),
    make_rule(
        "en_so_that",
        "so + adjective + that",
        "B1",
        "a result clause: so hard that I couldn't see.",
        {"seq": [spec_surface("so"), spec_pos("ADJ", "ADV"), spec_surface("that")]},
        zh="so + 形容词/副词 + that：如此……以至于。",
    ),
    make_rule(
        "en_used_to",
        "used to + verb",
        "B1",
        "a past habit or state that is no longer true.",
        {
            "seq": [
                {"lemma_in": ["use"], "morph": {"Tense": "Past", "VerbForm": "Fin"}},
                spec_surface("to"),
                spec_pos("VERB", "AUX"),
            ]
        },
        zh="used to do：过去的习惯/状态（现在没有了）。",
    ),
    make_rule(
        "en_relative_pronouns",
        "Relative clauses (who / which / whose)",
        "B1",
        "who/which/whose join a describing clause to a noun.",
        {"seq": [spec_surface("who", "whom", "whose", "which")]},
        zh="关系从句：who/which/whose 引导定语从句修饰名词。",
    ),
    make_rule(
        "en_reported_speech",
        "Reported speech (said / told)",
        "B1",
        "reporting verbs in the past shift the tense back one step.",
        {"seq": [{"lemma_in": ["say", "tell", "ask"], "morph": {"Tense": "Past"}}]},
        zh="间接引语：said/told 引出转述，时态后移。",
    ),
    # ---- B2 ----
    make_rule(
        "en_third_conditional",
        "Third conditional: if + had done, would have done",
        "B2",
        "an unreal past: If I had known, I would have come.",
        {
            "left": [spec_surface("if")],
            "right": [spec_surface("would"), spec_surface("have")],
            "min_gap": 1,
            "max_gap": 10,
        },
        zh="第三条件句：if + had done，主句 would have done，对过去的假设。",
    ),
    make_rule(
        "en_wish_past",
        "wish + past",
        "B2",
        "a wish about an unreal present: I wish I knew.",
        {"left": [spec_lemma("wish")], "right": [spec_morph(Tense="Past")], "min_gap": 1, "max_gap": 4},
        zh="wish + 过去时：对现状的遗憾/愿望。",
    ),
    make_rule(
        "en_must_have",
        "must have / can't have + participle",
        "B2",
        "a deduction about the past.",
        {
            "seq": [
                spec_surface("must", "might", "could", "can't", "cannot"),
                spec_surface("have"),
                spec_morph(VerbForm="Part"),
            ]
        },
        zh="must/can't have + 过去分词：对过去的肯定/否定推测。",
    ),
    make_rule(
        "en_have_sth_done",
        "have something done (causative)",
        "B2",
        "someone else does it for you: I had it repaired.",
        {"seq": [spec_lemma("have"), spec_pos("NOUN", "PRON"), spec_morph(VerbForm="Part")]},
        zh="have sth done 使役结构：请/让别人做某事。",
    ),
    make_rule(
        "en_despite",
        "despite / in spite of",
        "B2",
        "concession followed by a noun or -ing form (not a clause).",
        {
            "any_of": [
                {"seq": [spec_surface("despite")]},
                {"seq": [spec_surface("in"), spec_surface("spite"), spec_surface("of")]},
            ]
        },
        zh="despite / in spite of：尽管（后接名词或 -ing）。",
    ),
    make_rule(
        "en_unless",
        "unless",
        "B2",
        "if ... not: Unless it rains, we'll go.",
        {"seq": [spec_surface("unless")]},
        zh="unless = if not：除非。",
    ),
    # ---- C1 ----
    make_rule(
        "en_inversion",
        "Inversion (Never have I / Hardly had ... when)",
        "C1",
        "negative or limiting adverbials put before the auxiliary invert the "
        "word order: Never have I seen, No sooner had we left than ...",
        {
            "any_of": [
                {"seq": [spec_surface("never", "rarely", "seldom", "little"), spec_pos("AUX")]},
                {"seq": [spec_surface("not"), spec_surface("only"), spec_pos("AUX")]},
                {"left": [spec_surface("hardly", "scarcely")], "right": [spec_surface("when", "before", "than")], "min_gap": 1, "max_gap": 10},
                {"left": [spec_surface("no"), spec_surface("sooner")], "right": [spec_surface("than")], "min_gap": 1, "max_gap": 10},
            ]
        },
        zh="倒装：否定/限制副词置于句首引起倒装（Never have I seen / No sooner had ... than）。",
    ),
    make_rule(
        "en_future_perfect",
        "Future perfect: will have + participle",
        "C1",
        "completion by a future point: will have finished.",
        {"seq": [spec_surface("will"), spec_surface("have"), spec_morph(VerbForm="Part")]},
        zh="将来完成时：will have + 过去分词（到将来某时将已完成）。",
    ),
    make_rule(
        "en_future_continuous",
        "Future continuous: will be + -ing",
        "C1",
        "an action in progress at a future time: will be waiting.",
        {
            "any_of": [
                {"seq": [spec_surface("will"), spec_lemma("be"), spec_morph(Aspect="Prog")]},
                {"seq": [spec_surface("will"), spec_lemma("be"), spec_morph(VerbForm="Ger")]},
            ]
        },
        zh="将来进行时：will be + 动词-ing（将来某时正在进行）。",
    ),
    make_rule(
        "en_whereas",
        "whereas / whilst",
        "C1",
        "formal contrast or time: whereas, whilst.",
        {"seq": [spec_surface("whereas", "whilst")]},
        zh="whereas / whilst：然而/当……时（正式语体）。",
    ),
    # ---- C2 ----
    make_rule(
        "en_cleft",
        "Cleft sentence (it was ... that)",
        "C2",
        "emphasis by splitting the sentence: It was John that broke it.",
        {"re": re.compile(r"(?i)\bit\s+(?:was|is)\s+\S+\s+(?:that|who)\b")},
        zh="强调句（分裂句）：It was ... that/who，强调句子成分。",
    ),
    make_rule(
        "en_mandative_subjunctive",
        "Mandative subjunctive (suggest that ...)",
        "C2",
        "demand-type verbs take a that-clause with a base-form verb: "
        "suggest/recommend/insist that he go.",
        {
            "left": [spec_lemma("suggest", "recommend", "insist", "demand", "propose")],
            "right": [spec_surface("that")],
            "min_gap": 0,
            "max_gap": 2,
        },
        zh="命令性虚拟式：suggest/recommend/insist that + 从句用动词原形（that he go）。",
    ),
]


def analyze_english(page_text, display_lang="en"):
    """
    Analyze a page of English text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(page_text, _EN_RULES, _tokens_for, display_lang)
