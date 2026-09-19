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
]


def analyze_english(page_text, display_lang="en"):
    """
    Analyze a page of English text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(page_text, _EN_RULES, _tokens_for, display_lang)
