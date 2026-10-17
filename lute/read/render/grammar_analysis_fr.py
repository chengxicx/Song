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
    spec_surface,
)

_tokens_for = make_spacy_tokenizer("fr_core_news_sm")

# French typography uses the curly apostrophe (U+2019); books mix it with the
# ASCII one freely.  Every elision spec (c'est, qu'il, n'a...) is written with
# ASCII, so normalise once on the way in -- both for tokenisation and for the
# rule regexes (analyze_tokens matches surface specs against token text).
_APOS = str.maketrans({"\u2019": "'", "\u02bc": "'"})


def _norm_text(text):
    return text.translate(_APOS)


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
            "right": [
                spec_surface("pas", "plus", "jamais", "rien", "personne", "aucun")
            ],
            "min_gap": 1,
            "max_gap": 5,
        },
        zh="否定圈：ne + 动词 + pas/plus/jamais/rien。",
    ),
    make_rule(
        "fr_ne_que",
        "ne ... que (only)",
        "B1",
        "restrictive negation: il n'y a que cinq étudiants.",
        # Homographic with the complementizer ("Je ne crois pas que
        # Daniel soit coupable"), which the trailing-context veto separates:
        # restrictive que opens a noun phrase and carries no finite verb in
        # the next few words, while the complementizer opens a clause whose
        # verb sits right there.  Under-matching here beats false rows.
        {
            "left": [spec_surface("ne", "n'")],
            "right": [spec_surface("que", "qu'")],
            "min_gap": 0,
            "max_gap": 3,
        },
        not_followed_by={
            "any_of": [
                {
                    "pos_in": ["VERB", "AUX"],
                    "morph_not": {"VerbForm": "Inf"},
                },
                spec_surface(
                    "je",
                    "tu",
                    "il",
                    "elle",
                    "on",
                    "nous",
                    "vous",
                    "ils",
                    "elles",
                    "ce",
                    "tout",
                    "quand",
                    "si",
                    "pour",
                ),
            ]
        },
        zh="限制性 ne … que：只有／只做（ne … que + 名词短语）。",
    ),
    make_rule(
        "fr_possessives",
        "Possessive adjectives (mon, ton, son...)",
        "A1",
        "mon/ma/mes agree with the THING owned, not the owner.",
        {
            "seq": [
                spec_surface(
                    "mon",
                    "ma",
                    "mes",
                    "ton",
                    "ta",
                    "tes",
                    "son",
                    "sa",
                    "ses",
                    "notre",
                    "nos",
                    "votre",
                    "vos",
                    "leur",
                    "leurs",
                )
            ]
        },
        zh="主有形容词 mon/ma/mes 等：与被拥有物的性数一致。",
    ),
    make_rule(
        "fr_demo",
        "Demonstrative adjectives (ce, cette, ces)",
        "A1",
        "ce/cet/cette/ces point at a noun; they agree with it: ce livre, cet arbre.",
        {
            "seq": [
                spec_surface("ce", "cet", "cette", "ces"),
                {"pos_in": ["NOUN", "ADJ"]},
            ]
        },
        zh="指示形容词 ce/cet/cette/ces：这/那（与名词性数一致）。",
    ),
    # ---- A2 ----
    make_rule(
        "fr_futur_proche",
        "Futur proche: aller + infinitive",
        "A2",
        "going-to future: je vais partir.",
        # left/right so "je ne vais pas partir" still fires.
        {
            "left": [spec_lemma("aller")],
            "right": [_INF],
            "min_gap": 0,
            "max_gap": 3,
        },
        zh="近将来时：aller + 动词原形（je vais partir 我要走了）。",
    ),
    make_rule(
        "fr_passe_compose",
        "Passé composé: avoir/être + participle",
        "A2",
        "the spoken past: j'ai mangé, elle est allée.",
        # Mood=Ind Tense=Pres keeps "qu'il ait fini" (subj) out and lets
        # plus-que-parfait (Imp) / futur antérieur (Fut) own their rows.
        # left/right (not seq): adverbs/negation sit between aux and participle
        # ("avait déjà mangé", "n'a pas mangé").
        {
            "left": [
                {
                    "lemma_in": ["avoir", "être"],
                    "morph": {"Mood": "Ind", "Tense": "Pres"},
                }
            ],
            "right": [_PART],
            "min_gap": 0,
            "max_gap": 3,
        },
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
    make_rule(
        "fr_pendant",
        "pendant + duration",
        "A2",
        "a completed or bounded duration: il a habité Londres pendant cinq ans.",
        {"seq": [spec_surface("pendant"), {"pos_in": ["NUM", "DET"]}]},
        zh="pendant + 时长：持续……时间（与 depuis 的『至今』相对）。",
    ),
    make_rule(
        "fr_partitif",
        "Partitive articles (du, de la, des)",
        "A2",
        "an unspecified quantity: du pain, de la confiture, des amis.",
        {
            "any_of": [
                {"seq": [spec_surface("du", "des"), {"pos_in": ["NOUN"]}]},
                {"seq": [spec_surface("de"), spec_surface("la"), {"pos_in": ["NOUN"]}]},
            ]
        },
        zh="部分冠词 du/de la/des：不确定的量（吃点面包、喝点水）。",
    ),
    make_rule(
        "fr_imperatif",
        "Impératif (command form)",
        "A2",
        "Parlez ! Finis ! Va ! -- sm only tags some imperatives (Mood=Imp), "
        "so this under-fires on sentence-initial capitals.",
        {"seq": [{"pos_in": ["VERB"], "morph": {"Mood": "Imp"}}]},
        zh="命令式（Parlez ! / Finis !）：直接命令或指示。",
    ),
    make_rule(
        "fr_pronominal",
        "Pronominal verbs (se lever, se souvenir)",
        "A2",
        "the reflexive pronoun before the verb: il se lève, elle s'est levée.",
        {
            "left": [spec_surface("se", "s'")],
            "right": [{"pos_in": ["VERB", "AUX"]}],
            "min_gap": 0,
            "max_gap": 1,
        },
        zh="代动词：se + 动词（se lever 起床 / se souvenir 想起）。",
    ),
    make_rule(
        "fr_inversion",
        "Question by inversion (Parlez-vous...?)",
        "A2",
        "subject pronoun after the verb with a hyphen: comprenez-vous? "
        "voyage-t-il? The other yes/no form is est-ce que.",
        {
            "re": re.compile(
                r"(?i)\w+(?:-t)?-(?:je|tu|il|elle|on|nous|vous|ils|elles)\b"
            )
        },
        zh="主谓倒装疑问（Parlez-vous ? / Voyage-t-il ?）。",
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
        "fr_faire_causatif",
        "faire + infinitive (causative)",
        "B1",
        "having something done by someone else: il fait réparer la télévision.",
        {
            # surface, not lemma: sm mis-lemmatises "faites" as fait/ADJ.
            "left": [
                {
                    "surface_in": ["fais", "fait", "faisons", "faites", "font"],
                    "pos_in": ["VERB", "AUX", "ADJ"],
                }
            ],
            "right": [_INF],
            "min_gap": 0,
            "max_gap": 1,
        },
        zh="使役结构：faire + 原形（让人做某事 / 把东西送去修理等）。",
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
                {"seq": [spec_surface("ce"), spec_surface("dont")]},
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
    make_rule(
        "fr_superlatif",
        "Superlative (le plus / le moins)",
        "B1",
        "le/la/les + plus/moins + adjective: le plus grand, la moins chère.",
        {"seq": [spec_surface("le", "la", "les"), spec_surface("plus", "moins")]},
        zh="最高级：le/la/les + plus/moins（最……/最不……）。",
    ),
    make_rule(
        "fr_avant_de",
        "avant de + infinitive",
        "B1",
        "before doing: avant de partir, il a fermé la porte.",
        {"seq": [spec_surface("avant"), spec_surface("de"), _INF]},
        zh="avant de + 原形动词：在做……之前。",
    ),
    make_rule(
        "fr_des_que",
        "dès que / aussitôt que (as soon as)",
        "B1",
        "as soon as + indicative: dès qu'il arrivera, nous partirons.",
        {"seq": [spec_surface("dès", "aussitôt"), spec_surface("que", "qu'")]},
        zh="dès que / aussitôt que：一……就……。",
    ),
    make_rule(
        "fr_pour_que",
        "pour que + subjunctive (purpose)",
        "B1",
        "so that + subjunctive: je t'explique pour que tu comprennes.",
        {"seq": [spec_surface("pour"), spec_surface("que", "qu'")]},
        zh="pour que + 虚拟式：为了……（目的从句）。",
    ),
    # ---- B2 ----
    make_rule(
        "fr_si_conditionnel",
        "si + imparfait, conditionnel",
        "B2",
        "unreal present: Si j'avais de l'argent, je voyagerais.",
        {
            "left": [spec_surface("si")],
            "right": [spec_morph(Mood="Cnd")],
            "min_gap": 1,
            "max_gap": 10,
        },
        zh="si + 未完成过去时，主句条件式：假设。",
    ),
    make_rule(
        "fr_bien_que",
        "bien que + subjunctive (concession)",
        "B2",
        "although + subjunctive: bien qu'il soit fatigué, il lit le journal.",
        {"seq": [spec_surface("bien"), spec_surface("que", "qu'")]},
        zh="bien que + 虚拟式：尽管……（让步从句）。",
    ),
    make_rule(
        "fr_comme_si",
        "comme si + imparfait (as if)",
        "B2",
        "as if: il agit comme si tout était facile.",
        {"seq": [spec_surface("comme"), spec_surface("si")]},
        zh="comme si：就好像……（+ 未完成过去时）。",
    ),
    make_rule(
        "fr_a_moins_que",
        "à moins que + subjunctive (unless)",
        "B2",
        "unless + subjunctive: à moins qu'il ne pleuve, nous sortirons.",
        {
            "seq": [
                spec_surface("à"),
                spec_surface("moins"),
                spec_surface("que", "qu'"),
            ]
        },
        zh="à moins que + 虚拟式：除非……。",
    ),
    make_rule(
        "fr_gerondif",
        "en + participe présent (gérondif)",
        "B2",
        "while doing / by doing: en travaillant.",
        {
            "seq": [
                spec_surface("en"),
                {"pos_in": ["VERB"], "morph": {"VerbForm": "Part", "Tense": "Pres"}},
            ]
        },
        zh="副动词：en + 现在分词，表示『一边……/通过……』。",
    ),
    make_rule(
        "fr_subjonctif_passe",
        "Past subjunctive (ait fini / soit parti)",
        "B2",
        "an unreal or prior action in the subjunctive: je doute qu'il ait fini. "
        "sm tags the auxiliary Mood=Sub Tense=Pres, so match Sub + participle.",
        {
            "left": [{"pos_in": ["AUX"], "morph": {"Mood": "Sub"}}],
            "right": [_PART],
            "min_gap": 0,
            "max_gap": 3,
        },
        zh="虚拟式过去时（qu'il ait fini）：表示先于主句或假设的过去。",
    ),
    make_rule(
        "fr_plus_que_parfait",
        "Plus-que-parfait (avait fini)",
        "B2",
        "the past-before-the-past: il avait déjà mangé quand elle est arrivée.",
        {
            "left": [
                {
                    "lemma_in": ["avoir", "être"],
                    "morph": {"Mood": "Ind", "Tense": "Imp"},
                }
            ],
            "right": [_PART],
            "min_gap": 0,
            "max_gap": 3,
        },
        zh="愈过去时（avait fini）：过去的过去。",
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
        "fr_futur_anterieur",
        "Futur antérieur (aura fini)",
        "C1",
        "a future action seen as completed: quand il aura fini, nous partirons.",
        {
            "left": [
                {
                    "lemma_in": ["avoir", "être"],
                    "morph": {"Mood": "Ind", "Tense": "Fut"},
                }
            ],
            "right": [_PART],
            "min_gap": 0,
            "max_gap": 3,
        },
        zh="先将来时（aura fini）：将来某时点前完成的动作。",
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


def _get_all_rules():
    "Hand-written plus data-driven rules, built and memoised on first use."
    global _all_rules_cache  # pylint: disable=global-statement
    with _rules_lock:
        if _all_rules_cache is None:
            _all_rules_cache = list(_FR_RULES) + _get_data_rules()
        return _all_rules_cache


def analyze_french(page_text, display_lang="en"):
    """
    Analyze a page of French text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(
        _norm_text(page_text), _get_all_rules(), _tokens_for, display_lang
    )


# ---- data layer (grammar_fr.json) ---------------------------------------
#
# Merged material rows become rules whose matcher specs are derived from the
# row's `focus` words and kept only when they fire on the row's own example
# sentences, so a stale spec can never enter the panel silently.  Mirrors the
# Russian engine.


def _norm_name(name):
    "Fold a rule name for the hand-written / data-layer overlap check."
    return re.sub(r"[^0-9a-zà-ÿ]+", "", _norm_text(name or "").lower())


_HANDWRITTEN_NORMS = {_norm_name(r["name"]) for r in _FR_RULES}

_DATA_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "jlpt_data",
        "grammar_fr.json",
    )
)

_GOLDEN_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "jlpt_data",
        "grammar_fr_golden.json",
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


def _focus_specs(focus):
    """
    Candidate matcher specs derived from one row's focus list.

    Focus vocabulary: plain words match adjacently (seq); one "..." marks a
    bounded gap (left/right, up to 4 words); a "a/b" slash marks surface
    alternatives.  Short bare words (de, que, y) never stand alone.
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
    if len(words) == 1 and len(words[0]) < 4 and "/" not in words[0]:
        return []  # a bare short word fires everywhere -- never specific
    specs = [{"seq": [_focus_word_spec(w) for w in words]}]
    if not any("/" in w for w in words):
        # Slash alternatives are surfaces only; there is no lemma story for
        # them, so keep the all-lemma variant for plain word rows.
        specs.append({"seq": [spec_lemma(w) for w in words]})
    # spaCy is inconsistent on hyphenated words: "peut-être" comes through
    # as one token in some sentences and as peut/-/être in others, and a
    # seq over the split form would also fire on the verb phrase "peut
    # être" ("it can be").  A literal regex on the hyphenated surface keeps
    # the row exact for both tokenisations.
    for w in words:
        if "-" in w and not w.startswith("/"):
            pattern = (
                r"(?i)(?<![a-z\u00C0-\u00FF])"
                + re.escape(w)
                + r"(?![a-z\u00C0-\u00FF])"
            )
            specs.append({"re": re.compile(pattern)})
    return specs


def _flash(spec, examples):
    "True when spec fires on at least one of the row's example sentences."
    for sentence in examples:
        text = _norm_text(sentence)
        if match_rule({"match": spec}, _tokens_for(text), text):
            return True
    return False


def _load_data_rules():
    """
    Rules for the merged grammar-point rows (jlpt_data/grammar_fr.json).

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
        _FR_RULE_EXAMPLES[key] = examples
        zh_examples = [z for z in (item.get("example_zh") or []) if z]
        # Pair each example with its Chinese gloss when the material carries
        # one, so the library detail card can show a translation line.
        if len(zh_examples) == len(examples):
            library_examples = [
                {"fr": ex, "zh": z} for ex, z in zip(examples, zh_examples)
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
_FR_RULE_EXAMPLES = {}


def _get_data_rules():
    "The data-driven rules, built on first use and memoised."
    global _data_rules_cache  # pylint: disable=global-statement
    with _rules_lock:
        if _data_rules_cache is None:
            _data_rules_cache = _load_data_rules()
        return _data_rules_cache


# ---- library export ----------------------------------------------------
#
# The /grammar page lists the whole corpus at once, where the panel lists
# only what the current page happens to contain.

_CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]

_golden_cache = None


def _golden_examples():
    """
    Curated example sentences for the hand-written rules.

    Keyed ``key -> [{"fr", "en", "zh", "ko"}, ...]``.  The golden set is the
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
                            if (e.get("fr") or "").strip()
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
    One rule as a library row, display-ready (see the Spanish analogue).

    Each example comes back as ``{"text", "tr"}``: the French sentence and
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
            example = {"fr": example}
        text = (example.get("fr") or "").strip()
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
    The whole French grammar library, in CEFR order (A1 -> C2).

    Built on first use like the rules themselves, so an app start-up with
    no French books does not pay for the spaCy model.  Hand-written rules
    take their examples from the golden set; the merged data rows carry
    their own.
    """
    golden = _golden_examples()
    rules = []
    for rule in _FR_RULES:
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
