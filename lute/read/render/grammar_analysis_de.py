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

import json
import os
import re
import threading

from lute.read.render.grammar_analysis_matcher import (
    analyze_tokens,
    make_rule,
    make_spacy_tokenizer,
    match_rule,
    spec_lemma,
    spec_morph,
    spec_pos,
    spec_surface,
)

_tokens_for = make_spacy_tokenizer("de_core_news_sm")

# German typography uses the curly apostrophe (U+2019) in verbs separable by
# an apostrophe (gehen's, haben's); books mix it with the ASCII one freely.
# The surface specs are written with ASCII, so normalise once on the way in
# -- both for tokenisation and for the rule regexes.
_APOS = str.maketrans({"\u2019": "'", "\u02bc": "'", "\u2018": "'", "\u201a": "'"})


def _norm_text(text):
    return (text or "").translate(_APOS)


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
        {
            "any_of": [
                {"seq": [spec_surface("es"), spec_lemma("geben")]},
                # Yes/no questions invert the pair: "Gibt es ...?".
                {
                    "left": [spec_lemma("geben")],
                    "right": [spec_surface("es")],
                    "min_gap": 0,
                    "max_gap": 1,
                },
            ]
        },
        zh="es gibt + 第四格：有/存在（es gibt einen Park）。",
    ),
    make_rule(
        "de_modals",
        "Modal + infinitive at the end (kann ... kommen)",
        "A1",
        "the conjugated modal comes second, the infinitive goes to the end "
        "of the clause: ich kann heute nicht kommen.",
        {
            "left": [
                spec_lemma(
                    "können", "müssen", "wollen", "sollen", "dürfen", "mögen", "möchten"
                )
            ],
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
        {
            "seq": [
                spec_surface(
                    "nicht", "kein", "keine", "keinen", "keinem", "keiner", "nie"
                )
            ]
        },
        zh="否定：nicht 否定动词/形容词，kein 否定名词。",
    ),
    make_rule(
        "de_questions",
        "Question words (wer, was, wann, wo...)",
        "A1",
        "wer/was/wann/wo/warum/wie ask for information.",
        {
            "seq": [
                spec_surface(
                    "wer",
                    "was",
                    "wann",
                    "wo",
                    "warum",
                    "wie",
                    "wohin",
                    "woher",
                    "wieso",
                )
            ]
        },
        zh="特殊疑问词 wer/was/wann/wo/warum/wie。",
    ),
    make_rule(
        "de_possessives",
        "Possessive articles (mein, dein, unser...)",
        "A1",
        "mein/dein/unser/euer + case ending agree with the thing owned.",
        {
            # The UD Poss feature is the reliable signal: a surface list
            # would have to carry every declined form, and "ihr" is
            # ambiguous with the nominative plural pronoun, which spaCy
            # lemmatises to the same token.
            "any_of": [
                {"seq": [{"pos_in": ["DET", "PRON"], "morph": {"Poss": "Yes"}}]},
                {
                    "seq": [
                        spec_surface(
                            "mein",
                            "meine",
                            "meinen",
                            "meinem",
                            "meiner",
                            "dein",
                            "deine",
                            "deinen",
                            "deinem",
                            "deiner",
                            "unser",
                            "unsere",
                            "unseren",
                            "unserem",
                            "unserer",
                            "euer",
                            "eure",
                            "euren",
                            "eurem",
                            "eurer",
                            "sein",
                            "seine",
                            "seinen",
                            "seinem",
                            "seiner",
                            "ihr",
                            "ihre",
                            "ihren",
                            "ihrem",
                            "ihrer",
                        )
                    ]
                },
            ]
        },
        zh="主有冠词 mein/dein/unser 等，随性数格变化。",
    ),
    # ---- A2 ----
    make_rule(
        "de_perfekt",
        "Perfekt: haben/sein + Partizip",
        "A2",
        "the spoken past: ich habe gemacht, sie ist gegangen.",
        {
            "left": [
                {
                    "lemma_in": ["haben", "sein"],
                    "pos_in": ["VERB", "AUX"],
                    "morph": {"VerbForm": "Fin"},
                }
            ],
            "right": [_PART],
            "min_gap": 0,
            "max_gap": 6,
        },
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
        {
            "left": [
                {"lemma_in": ["werden"], "morph": {"VerbForm": "Fin"}},
            ],
            "right": [_INF],
            "min_gap": 0,
            "max_gap": 6,
        },
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
        {
            "any_of": [
                {"seq": [spec_surface("gern", "gerne", "lieber")]},
                # "am liebsten" reaches the matcher as two tokens (am / liebsten),
                # so the superlative needs its own anchor.
                {
                    "seq": [
                        spec_surface("am"),
                        {"surface_in": ["liebsten"], "pos_in": ["ADV", "ADJ"]},
                    ]
                },
            ]
        },
        zh="gern/lieber：喜欢做……（副词修饰动词）。",
    ),
    make_rule(
        "de_reflexive",
        "Reflexive pronouns (sich / mich)",
        "A2",
        "many German verbs are reflexive: sich freuen, ich wasche mich.",
        {
            # de_core_news_sm does not set the UD Reflex feature, so the
            # pronoun's own forms are the signal.  Accusative reflexive
            # pronouns (mich/dich/sich/uns/euch) are listed by lemma: they
            # cannot be a plain indirect object, so a hit is unambiguous.
            # The dative ones (mir/dir/sich) are excluded for the same
            # reason -- sieh sind also ordinary dative objects.
            "seq": [
                {
                    "lemma_in": ["mich", "dich", "sich", "uns", "euch"],
                    "pos_in": ["PRON"],
                }
            ]
        },
        zh="反身代词（sich/mich）：德语大量动词为反身动词（sich freuen）。",
    ),
    # ---- B1 ----
    make_rule(
        "de_weil",
        "weil / obwohl / damit (verb-final)",
        "B1",
        "subordinating conjunctions send the verb to the end: ... weil ich krank bin.",
        {
            "seq": [
                spec_surface(
                    "weil", "obwohl", "damit", "sodass", "während", "bevor", "nachdem"
                )
            ]
        },
        zh="从属连词 weil/obwohl/damit 等：动词移到句尾（框型）。",
    ),
    make_rule(
        "de_werden_passiv",
        "Passiv: werden + Partizip",
        "B1",
        "the passive: es wird gemacht, wurde gebaut.",
        {
            "left": [
                {"lemma_in": ["werden"], "morph": {"VerbForm": "Fin"}},
            ],
            "right": [_PART],
            "min_gap": 0,
            "max_gap": 5,
        },
        zh="被动语态：werden + 第二分词（es wird gemacht 被做）。",
    ),
    make_rule(
        "de_konjunktiv2",
        "Konjunktiv II (würde / hätte / wäre)",
        "B1",
        "polite requests and unreal statements: ich würde gehen, wenn ich könnte.",
        {
            "any_of": [
                {
                    "left": [spec_lemma("würde")],
                    "right": [_INF],
                    "min_gap": 0,
                    "max_gap": 5,
                },
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
    make_rule(
        "de_genitiv",
        "Genitive case (des / der / -es)",
        "B1",
        "the genitive marks possession and comes after a preposition: "
        "die Farbe des Himmels, wegen des Regens.",
        {
            "any_of": [
                # The definite genitive articles: des, der genitive plural.
                {"seq": [spec_surface("des")]},
                {
                    "seq": [
                        spec_surface("der"),
                        {"morph": {"Case": "Gen"}, "pos_in": ["NOUN", "PRON"]},
                    ]
                },
                # Possessive genitives: meines, seines, ihres, unseres.
                {
                    "seq": [
                        {
                            "surface_in": [
                                "meines",
                                "deines",
                                "seines",
                                "ihres",
                                "unseres",
                                "eures",
                            ]
                        }
                    ]
                },
                # Genitive prepositions always take des / einer des ...
                {
                    "left": [
                        spec_surface(
                            "wegen",
                            "aufgrund",
                            "trotz",
                            "anlässlich",
                            "mittels",
                            "seitens",
                            "hinsichtlich",
                        )
                    ],
                    "right": [spec_surface("des")],
                    "min_gap": 0,
                    "max_gap": 2,
                },
            ]
        },
        zh="第二格（属格）：表所属，放在介词后（die Farbe des Himmels、wegen des Regens）。",
    ),
    make_rule(
        "de_komparativ",
        "Comparative (größer als / -er)",
        "B1",
        "the comparative adds -er and takes als: größer als, besser als.",
        {
            "any_of": [
                {
                    "left": [
                        {
                            "morph": {"Degree": "Cmp"},
                            "pos_in": ["ADJ", "ADV"],
                        }
                    ],
                    "right": [spec_surface("als")],
                    "min_gap": 0,
                    "max_gap": 2,
                },
                {"seq": [{"pos_in": ["ADJ", "ADV"], "morph": {"Degree": "Cmp"}}]},
            ]
        },
        zh="比较级：形容词加 -er + als（größer als）。",
    ),
    make_rule(
        "de_superlativ",
        "Superlative (am größten / -sten)",
        "B1",
        "the superlative is am + -sten, or the -ste form on its own: "
        "am größten, die größte.",
        {
            "any_of": [
                # am + superlative: the am is a separate token from the
                # -sten adjective, so the pair needs its own pattern.
                {
                    "seq": [
                        spec_surface("am"),
                        {"morph": {"Degree": "Sup"}, "pos_in": ["ADJ", "ADV"]},
                    ]
                },
                # The -ste / -ste(r/s/n) forms standing on their own.
                {
                    "seq": [
                        {
                            "morph": {"Degree": "Sup"},
                            "pos_in": ["ADJ", "ADV"],
                        }
                    ]
                },
            ]
        },
        zh="最高级：am + 形容词加 -sten（am größten），或加 -ste/-ster/-stes（die größte）。",
    ),
    make_rule(
        "de_relativsatz",
        "Relative clause (der/die/das ..., der/die/das ...)",
        "B2",
        "a relative pronoun takes the case of its antecedent and the "
        "verb goes to the end: der Mann, den ich gestern sah.",
        {
            "left": [
                spec_surface(
                    "der",
                    "die",
                    "das",
                    "welcher",
                    "welche",
                    "welches",
                    "dessen",
                    "deren",
                )
            ],
            "right": [
                {
                    "morph": {"VerbForm": "Fin"},
                    "pos_in": ["VERB", "AUX"],
                }
            ],
            "min_gap": 2,
            "max_gap": 8,
        },
        zh="关系从句：关系代词 der/die/das 承担antecedent 的格，动词置于句尾。",
    ),
    make_rule(
        "de_doppel_infinitiv",
        "Double infinitive (sehen zu / lassen machen)",
        "B2",
        "a modal of perception or causation keeps its infinitive: "
        "ich sehe ihn kommen, er lässt das Auto reparieren.",
        {
            # de_core_news_sm lemmatises the vowel-changing stems wrong
            # ("lässt" keeps its own lemma instead of "lassen"), so the
            # anchor needs a token-level alternation: the keyed specs of
            # one spec are ANDed, so lemma and surface cannot share one.
            "any_of": [
                {
                    "left": [
                        {
                            "any_of": [
                                {
                                    "lemma_in": [
                                        "sehen",
                                        "hören",
                                        "lassen",
                                        "fühlen",
                                        "spüren",
                                    ]
                                },
                                {
                                    "surface_in": [
                                        "sehen",
                                        "sehe",
                                        "sieht",
                                        "seh",
                                        "hören",
                                        "höre",
                                        "hört",
                                        "lassen",
                                        "lasse",
                                        "lässt",
                                        "lasst",
                                        "fühlen",
                                        "fühle",
                                        "fühlt",
                                        "spüren",
                                        "spüre",
                                        "spürt",
                                    ]
                                },
                            ],
                            "pos_in": ["VERB", "AUX"],
                        }
                    ],
                    "right": [_INF],
                    "min_gap": 0,
                    "max_gap": 3,
                }
            ]
        },
        zh="双不定式结构： sehen/hören/lassen + 原形动词（原形留在句尾）。",
    ),
    make_rule(
        "de_doppelobjekt",
        "Two objects: dative person + accusative thing",
        "B2",
        "verbs like geben and bringen take a dative recipient and an "
        "accusative object: ich gebe ihm das Buch.",
        {
            "any_of": [
                {
                    "left": [
                        {
                            "lemma_in": [
                                "geben",
                                "bringen",
                                "schenken",
                                "zeigen",
                                "erklären",
                                "sagen",
                                "empfehlen",
                                "schicken",
                                "leihen",
                                "beibringen",
                                "verkaufen",
                                "kaufen",
                                "senden",
                            ],
                            "pos_in": ["VERB", "AUX"],
                        }
                    ],
                    "right": [{"pos_in": ["DET", "PRON"], "morph": {"Case": "Acc"}}],
                    "min_gap": 1,
                    "max_gap": 4,
                },
                # A separable verb splits across the clause: "bringen ... bei",
                # "geben ... aus", "verkaufen ... an".  The accusative
                # object then sits between the stem and the prefix, so the
                # right anchor is the prefix itself rather than the article.
                {
                    "left": [
                        {
                            "lemma_in": [
                                "bringen",
                                "geben",
                                "verkaufen",
                                "schicken",
                                "senden",
                                "nehmen",
                                "kaufen",
                                "empfehlen",
                            ],
                            "pos_in": ["VERB", "AUX"],
                        }
                    ],
                    "right": [
                        {"pos_in": ["NOUN", "DET", "PRON", "PROPN"]},
                        {
                            "surface_in": [
                                "bei",
                                "aus",
                                "an",
                                "ab",
                                "auf",
                                "ein",
                                "mit",
                                "vor",
                                "zurück",
                                "weg",
                                "zu",
                            ]
                        },
                    ],
                    "min_gap": 1,
                    "max_gap": 3,
                },
            ]
        },
        zh="双宾语：接三格的人 + 接四格的事物（Ich gebe ihm das Buch）。",
    ),
    make_rule(
        "de_modal_perfekt",
        "Modal in the perfect (hat ... gekonnt / hat ... gemusst)",
        "B2",
        "a modal in the Perfekt keeps its infinitive instead of taking a "
        "participle: Viele Leute haben schwimmen können.",
        {
            "left": [{"lemma_in": ["haben", "sein"], "morph": {"VerbForm": "Fin"}}],
            "right": [
                {
                    "pos_in": ["AUX", "VERB"],
                    "morph": {"VerbForm": "Inf"},
                }
            ],
            "min_gap": 1,
            "max_gap": 3,
        },
        zh="情态动词的完成时用原形而非分词：hat gekonnt / hat gemusst。",
    ),
    make_rule(
        "de_zeitangabe",
        "Time adverbials (heute / morgen / gestern)",
        "A1",
        "German time words are adverbs: they do not inflect, and their "
        "position in the clause is flexible.",
        {
            "seq": [
                {
                    "pos_in": ["ADV"],
                    "surface_in": [
                        "heute",
                        "morgen",
                        "gestern",
                        "vorgestern",
                        "übermorgen",
                        "abends",
                        "morgens",
                        "mittags",
                        "nachts",
                        "damals",
                        "heute abend",
                        "heute morgen",
                    ],
                }
            ]
        },
        zh="时间副词（heute/morgen/gestern）：无词形变化，位置灵活。",
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
    make_rule(
        "de_hauptsatzkoord",
        "Coordinated main clauses (..., und/aber/denn ...)",
        "B2",
        "two main clauses join with a comma plus a coordinating "
        "conjunction, and both keep verb-second order: Der Hund ist krank, "
        "und die Katze ist alt.",
        {
            "any_of": [
                # The comma is dropped before the token stream the matcher
                # sees, so the conjunction between two finite verbs is the
                # actual signal.
                {
                    "left": [{"morph": {"VerbForm": "Fin"}, "pos_in": ["VERB", "AUX"]}],
                    "right": [
                        spec_surface("und", "aber", "oder", "denn", "sondern"),
                        {"morph": {"VerbForm": "Fin"}, "pos_in": ["VERB", "AUX"]},
                    ],
                    "min_gap": 0,
                    "max_gap": 5,
                },
                # An elliptical second clause still counts when the
                # conjunction sits between a closed first clause and a
                # subject: "..., aber die Katze ist alt."
                {
                    "left": [{"morph": {"VerbForm": "Fin"}, "pos_in": ["VERB", "AUX"]}],
                    "right": [
                        spec_surface("und", "aber", "oder", "denn", "sondern"),
                    ],
                    "min_gap": 1,
                    "max_gap": 6,
                },
            ]
        },
        zh="并列主句：用逗号 + 连词（und/aber/oder/denn/sondern）连接，两个分句都保持动词第二位。",
    ),
    # ---- C1 / C2 ----
    make_rule(
        "de_je_desto",
        "je ... desto (the ... the ...)",
        "C1",
        "correlative comparison: je mehr, desto besser.",
        {
            "left": [spec_surface("je")],
            "right": [spec_surface("desto")],
            "min_gap": 1,
            "max_gap": 6,
        },
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


# ---- data layer (grammar_de.json) ---------------------------------------
#
# Merged material rows become rules whose matcher specs are derived from the
# row's `focus` words and kept only when they fire on the row's own example
# sentences, so a stale spec can never enter the panel silently.  Mirrors the
# French / Russian engines.


def _norm_name(name):
    "Fold a rule name for the hand-written / data-layer overlap check."
    return re.sub(r"[^0-9a-zäöüß]+", "", _norm_text(name or "").lower())


_HANDWRITTEN_NORMS = {_norm_name(r["name"]) for r in _DE_RULES}

_DATA_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "jlpt_data",
        "grammar_de.json",
    )
)

_GOLDEN_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "jlpt_data",
        "grammar_de_golden.json",
    )
)


def _focus_words(focus):
    words = []
    for f in focus or []:
        w = _norm_text(f).strip().lower()
        if w:
            words.append(w)
    return words


def _focus_word_spec(word):
    "One focus word as a spec; 'a/b' alternatives become surface variants."
    return spec_surface(*word.split("/"))


# German nouns are capitalised and so is every sentence start, so a bare
# one-word focus is far too weak to stand as a rule: "haus" would fire on
# every sentence mentioning a house.  Two rules keep the derived specs
# honest -- a short bare word is refused outright, and a hyphenated
# compound gets a literal regex as well (German writes verbs separable
# apart, so "aufstehen" may reach the matcher as two tokens).
_MIN_BARE_WORD = 4


def _focus_specs(focus):
    """
    Candidate matcher specs derived from one row's focus list.

    Focus vocabulary: plain words match adjacently (seq); one "..." marks a
    bounded gap (left/right, up to 4 words); a "a/b" slash marks surface
    alternatives.  Short bare words (zu, um, je) never stand alone.
    """
    words = _focus_words(focus)
    if not words:
        return []
    if words.count("...") == 1:
        i = words.index("...")
        left_w, right_w = words[:i], words[i + 1 :]
        if not left_w or not right_w:
            return []
        return [
            {
                "left": [_focus_word_spec(w) for w in left_w],
                "right": [_focus_word_spec(w) for w in right_w],
                "min_gap": 0,
                "max_gap": 4,
            }
        ]
    if "..." in words:
        return []  # at most one gap anchor pair is expressible
    if len(words) == 1 and len(words[0]) < _MIN_BARE_WORD and "/" not in words[0]:
        return []
    specs = [{"seq": [_focus_word_spec(w) for w in words]}]
    if not any("/" in w for w in words):
        # Slash alternatives are surfaces only; there is no lemma story for
        # them, so keep the all-lemma variant for plain word rows.
        specs.append({"seq": [spec_lemma(w) for w in words]})
    # spaCy is inconsistent on German separable verbs and compounds: the
    # lemma comes back whole but the surface is split, so a literal regex
    # on the hyphenated/joined surface keeps the row exact either way.
    for w in words:
        if "-" in w and not w.startswith("/"):
            pattern = r"(?i)(?<![a-zäöüß])" + re.escape(w) + r"(?![a-zäöüß])"
            specs.append({"re": re.compile(pattern)})
    return specs


def _flash(spec, examples):
    "True when spec fires on at least one of the row's example sentences."
    for sentence in examples:
        text = _norm_text(sentence)
        if match_rule({"match": spec}, _tokens_for(text), text):
            return True
    return False


_DE_RULE_EXAMPLES = {}


def _load_data_rules():
    """
    Rules for the merged grammar-point rows (jlpt_data/grammar_de.json).

    Specs are derived from each row's `focus` and kept only when they match
    at least one of the row's own examples.
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
        examples = [e for e in (_norm_text(x) for x in item.get("examples") or []) if e]
        if not examples:
            continue
        validated = [s for s in _focus_specs(item.get("focus")) if _flash(s, examples)]
        if not validated:
            continue
        key = item.get("key") or name
        _DE_RULE_EXAMPLES[key] = examples
        zh_examples = [z for z in (item.get("example_zh") or []) if z]
        # Pair each example with its Chinese gloss when the material carries
        # one, so the library detail card can show a translation line.
        if len(zh_examples) == len(examples):
            library_examples = [
                {"de": ex, "zh": z} for ex, z in zip(examples, zh_examples)
            ]
        else:
            library_examples = list(examples)
        rule = make_rule(
            key,
            name,
            item.get("level") or "B1",
            (item.get("meaning") or "").strip() or name,
            {"any_of": validated} if len(validated) > 1 else validated[0],
            zh=item.get("zh") or None,
        )
        # Library-page fields (the /grammar detail card renders them when
        # present); the panel ignores keys it does not read.
        rule["formation"] = (item.get("formation") or "").strip()
        rule["notes"] = (item.get("notes") or "").strip()
        rule["examples"] = library_examples
        rules.append(rule)
    return rules


# The data rules are built on first use, not at import: _load_data_rules()
# tokenizes the rows' examples, and this module is imported at app start-up
# (lute/read/routes.py).
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
    "Hand-written plus data-driven rules, built and memoised on first use."
    global _all_rules_cache  # pylint: disable=global-statement
    with _rules_lock:
        if _all_rules_cache is None:
            _all_rules_cache = list(_DE_RULES) + _get_data_rules()
        return _all_rules_cache


def analyze_german(page_text, display_lang="en"):
    """
    Analyze a page of German text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(
        _norm_text(page_text), _get_all_rules(), _tokens_for, display_lang
    )


# ---- library export ----------------------------------------------------
#
# The /grammar page lists the whole corpus at once, where the panel lists
# only what the current page happens to contain.

_CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]

_golden_cache = None


def _golden_examples():
    """
    Curated example sentences for the hand-written rules.

    Keyed ``key -> [{"de", "en", "zh", "ko"}, ...]``.  The golden set is the
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
                            if (e.get("de") or "").strip()
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
    One rule as a library row, display-ready (see the French analogue).

    Each example comes back as ``{"text", "tr"}``: the German sentence and
    its translation for the display language.  The library has no page text
    to highlight, so the match offsets the panel computes are irrelevant
    here.
    """
    desc = rule.get("desc") or rule["name"]
    if display_lang == "zh" and (rule.get("zh") or "").strip():
        desc = rule["zh"]
    rows = []
    for example in examples:
        if isinstance(example, str):
            example = {"de": example}
        text = (example.get("de") or "").strip()
        if text:
            rows.append({"text": text, "tr": _example_gloss(example, display_lang)})
    entry = {
        "key": rule["key"],
        "name": rule["name"],
        "level": rule["level"],
        "desc": desc,
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
    The whole German grammar library, in CEFR order (A1 -> C2).

    Built on first use like the rules themselves, so an app start-up with
    no German books does not pay for the spaCy model.  Hand-written rules
    take their examples from the golden set; the merged data rows carry
    their own.
    """
    golden = _golden_examples()
    rules = []
    for rule in _DE_RULES:
        if not rule.get("examples"):
            rule = dict(rule)
            rule["examples"] = golden.get(rule["key"]) or []
        rules.append(rule)
    rules += _get_data_rules()
    entries = [_library_entry(r, display_lang, r.get("examples") or []) for r in rules]
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
