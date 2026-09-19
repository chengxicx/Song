"""
French grammar point detection (spaCy fr_core_news_sm).

Mirrors the English/Spanish engines: sentences are split, tokenised with
lemma + POS + morphological features, and token-aware rules recognise
grammar constructions.  The sm model tags mood/tense reliably for the
common tenses; the subjunctive is only partially recognised (some forms
like "soit" get Mood=Sub, others are missed), so the subjunctive rule
under-fires rather than over-fires.

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

        _NLP = spacy.load("fr_core_news_sm", exclude=["parser", "senter", "ner"])
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
# Tense Pres/Past/Imp/Fut, VerbForm Fin/Inf/Part.

_FIN_PAST = {"pos_in": ["VERB", "AUX"], "morph": {"Tense": "Past", "VerbForm": "Fin"}}
_INF = spec_morph(VerbForm="Inf")
_PART = spec_morph(VerbForm="Part")

_FR_RULES = [
    # ---- A1 ----
    make_rule(
        "fr_il_y_a",
        "il y a (there is / there are)",
        "A1",
        "il y a states existence; il y avait = there was.",
        {"seq": [spec_surface("il"), spec_surface("y"), spec_lemma("avoir")]},
        zh="il y a 表示『有；存在』（il y avait 过去时）。",
    ),
    make_rule(
        "fr_estceque",
        "est-ce que (question)",
        "A1",
        "est-ce + que turns a statement into a yes/no question.",
        {"re": re.compile(r"(?i)\best\s*-?\s*ce\s+que\b")},
        zh="est-ce que 置于陈述句前构成一般疑问句。",
    ),
    make_rule(
        "fr_c_est",
        "c'est (it is)",
        "A1",
        "c'est + noun/adjective identifies or describes: c'est bon.",
        {"re": re.compile(r"(?i)\bc'?est\b")},
        zh="c'est：这是/那是（指认或描述）。",
    ),
    make_rule(
        "fr_wh_questions",
        "Question words (quand, comment, pourquoi...)",
        "A1",
        "quand/comment/pourquoi/combien/où/qui ask for information.",
        {"seq": [spec_surface("quand", "comment", "pourquoi", "combien", "où", "qui")]},
        zh="特殊疑问词 quand/comment/pourquoi/combien/où/qui。",
    ),
    make_rule(
        "fr_negation",
        "ne ... pas / plus / jamais",
        "A1",
        "ne before the verb, the second word after it: je ne sais pas.",
        {
            "left": [spec_surface("ne", "n'")],
            "right": [spec_surface("pas", "plus", "jamais", "rien", "personne", "aucun")],
            "min_gap": 1,
            "max_gap": 5,
        },
        zh="否定圈：ne + 动词 + pas/plus/jamais/rien。",
    ),
    make_rule(
        "fr_possessives",
        "Possessive adjectives (mon, ton, son...)",
        "A1",
        "mon/ma/mes agree with the THING owned, not the owner.",
        {
            "seq": [
                spec_surface(
                    "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses",
                    "notre", "nos", "votre", "vos", "leur", "leurs",
                )
            ]
        },
        zh="主有形容词 mon/ma/mes 等：与被拥有物的性数一致。",
    ),
    # ---- A2 ----
    make_rule(
        "fr_futur_proche",
        "Futur proche: aller + infinitive",
        "A2",
        "going-to future: je vais partir.",
        {"seq": [spec_lemma("aller"), _INF]},
        zh="近将来时：aller + 动词原形（je vais partir 我要走了）。",
    ),
    make_rule(
        "fr_passe_compose",
        "Passé composé: avoir/être + participle",
        "A2",
        "the spoken past: j'ai mangé, elle est allée.",
        {"seq": [spec_lemma("avoir", "être"), _PART]},
        zh="复合过去时：avoir/être + 过去分词（j'ai mangé / elle est allée）。",
    ),
    make_rule(
        "fr_imparfait",
        "Imparfait (était / jouait)",
        "A2",
        "an ongoing, habitual or descriptive past.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Tense": "Imp"}}]},
        zh="未完成过去时（était / jouait）：过去 ongoing、习惯或背景描写。",
    ),
    make_rule(
        "fr_futur_simple",
        "Futur simple (sera / irai)",
        "A2",
        "the simple future: je partirai.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Tense": "Fut"}}]},
        zh="简单将来时（sera / irai / partirai）。",
    ),
    make_rule(
        "fr_modals",
        "pouvoir / vouloir / devoir + infinitive",
        "A2",
        "modal + infinitive: je peux venir, tu dois partir.",
        {
            "left": [spec_lemma("pouvoir", "vouloir", "devoir", "savoir")],
            "right": [_INF],
            "min_gap": 0,
            "max_gap": 5,
        },
        zh="情态动词 pouvoir/vouloir/devoir + 原形：能/想/必须。",
    ),
    make_rule(
        "fr_comparative",
        "plus / moins / aussi ... que",
        "A2",
        "comparative and equality: plus grand que, aussi vite que.",
        {
            "left": [spec_surface("plus", "moins", "aussi")],
            "right": [spec_surface("que")],
            "min_gap": 1,
            "max_gap": 4,
        },
        zh="比较：plus/moins/aussi … que（比……更/更不/一样）。",
    ),
    # ---- B1 ----
    make_rule(
        "fr_venir_de",
        "venir de + infinitive (recent past)",
        "B1",
        "to have just done something: je viens de manger.",
        {"seq": [spec_lemma("venir"), spec_surface("de"), _INF]},
        zh="venir de + 原形动词：刚刚做完（je viens de manger）。",
    ),
    make_rule(
        "fr_depuis",
        "depuis + present tense",
        "B1",
        "French uses the PRESENT with depuis for durations up to now: "
        "j'habite ici depuis deux ans.",
        {"seq": [spec_surface("depuis")]},
        zh="depuis + 现在时：从……到现在（持续至今，英语用完成时而法语用现在时）。",
    ),
    make_rule(
        "fr_relative",
        "Relative pronouns (qui / dont / où)",
        "B1",
        "qui = subject, dont = of which, où = where; que joins an object clause.",
        {"seq": [spec_surface("qui", "dont", "où")]},
        zh="关系代词 qui（主语）/ dont（de 的关系词）/ où（地点）。",
    ),
    make_rule(
        "fr_subjonctif",
        "Subjunctive (il faut que + subjonctif)",
        "B1",
        "used after will, emotion, doubt, necessity: il faut que tu viennes.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Mood": "Sub"}}]},
        zh="虚拟式：意愿/情感/必要类触发词后用（il faut que + 虚拟式）。",
    ),
    make_rule(
        "fr_il_faut",
        "il faut (necessity)",
        "B1",
        "impersonal necessity + infinitive or subjunctive: il faut partir.",
        {"seq": [spec_lemma("falloir")]},
        zh="il faut：必须（+ 原形或 que + 虚拟式）。",
    ),
    make_rule(
        "fr_conditionnel",
        "Conditionnel (voudrais / aimerait)",
        "B1",
        "polite requests and hypotheticals: je voudrais, il aimerait.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Mood": "Cnd"}}]},
        zh="条件式（voudrais / aimerait）：礼貌请求或假设。",
    ),
    make_rule(
        "fr_ce_que",
        "ce qui / ce que (what)",
        "B1",
        "the neuter relative: je sais ce que tu veux.",
        {
            "any_of": [
                {"seq": [spec_surface("ce"), spec_surface("qui")]},
                {"seq": [spec_surface("ce"), spec_surface("que")]},
            ]
        },
        zh="ce qui / ce que：泛指『……的东西/事情』。",
    ),
    make_rule(
        "fr_y_en",
        "Clitic pronouns y / en",
        "B1",
        "y = there/about it (place); en = of it/some (quantity, de-objects).",
        {"seq": [{"surface_in": ["y", "en"], "pos_in": ["PRON"]}]},
        zh="代词 y（那里/关于它）与 en（一些/由此）。",
    ),
    make_rule(
        "fr_sans_inf",
        "sans + infinitive",
        "B1",
        "without doing: il est parti sans dire au revoir.",
        {"seq": [spec_surface("sans"), _INF]},
        zh="sans + 原形动词：没有做……就……。",
    ),
    # ---- B2 ----
    make_rule(
        "fr_si_conditionnel",
        "si + imparfait, conditionnel",
        "B2",
        "unreal present: Si j'avais de l'argent, je voyagerais.",
        {"left": [spec_surface("si")], "right": [spec_morph(Mood="Cnd")], "min_gap": 1, "max_gap": 10},
        zh="si + 未完成过去时，主句条件式：假设。",
    ),
    make_rule(
        "fr_gerondif",
        "en + participe présent (gérondif)",
        "B2",
        "while doing / by doing: en travaillant.",
        {"seq": [spec_surface("en"), {"pos_in": ["VERB"], "morph": {"VerbForm": "Part", "Tense": "Pres"}}]},
        zh="副动词：en + 现在分词，表示『一边……/通过……』。",
    ),
    make_rule(
        "fr_subjonctif_passe",
        "Past subjunctive (ait / été + subj)",
        "B2",
        "an unreal or prior action in the subjunctive: je doute qu'il ait fini.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Mood": "Sub", "Tense": "Past"}}]},
        zh="虚拟式过去时（ait / soit été）：表示先于主句或假设的过去。",
    ),
    # ---- C1 ----
    make_rule(
        "fr_apres_avoir",
        "après avoir / après être + participle",
        "C1",
        "after having done something: après avoir fini.",
        {"seq": [spec_surface("après"), _INF]},
        zh="après avoir/être + 过去分词：做完……之后。",
    ),
    make_rule(
        "fr_passe_simple",
        "Passé simple (literary past)",
        "C1",
        "the literary narrative past: il fit, elle vit.",
        {"seq": [_FIN_PAST]},
        zh="简单过去时（fit / vit）：书面叙事体（小说/历史）。",
    ),
]


def analyze_french(page_text, display_lang="en"):
    """
    Analyze a page of French text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(page_text, _FR_RULES, _tokens_for, display_lang)
