"""
Spanish grammar point detection (spaCy es_core_news_md).

Mirrors the English engine: sentences are split, tokenised with lemma +
POS + morphological features, and token-aware rules recognise grammar
constructions.  The md model beats the sm one on real sentences (+2pp
rule hits, correctly tags subjunctive forms the sm model miscast as
nouns) and is faster; imperatives still need care, so rules anchor on
context (a trigger + que + subjunctive, a command form opening the
sentence) rather than trusting the mood tag.

spaCy and the model are imported lazily so the base install works without
them; the route falls back to the generic rule library on ImportError.
"""

import json
import os
import re
import threading

from lute.read.render.grammar_analysis_matcher import (
    analyze_tokens,
    desc_for_rule,
    make_rule,
    make_spacy_tokenizer,
    match_rule,
    spec_lemma,
    spec_morph,
    spec_pos,
    spec_surface,
)

_tokens_for = make_spacy_tokenizer("es_core_news_md")


# ---- rules -------------------------------------------------------------
#
# Levels use CEFR bands (A1..C2), rendered by the panel like the JLPT ones.
# Spanish verbs surface as VERB/AUX with UD morph features: Mood
# (Ind/Sub/Imp/Cnd), Tense (Pres/Past/Imp/Fut) and VerbForm (Fin/Inf/Part).

_INF = spec_morph(VerbForm="Inf")

# The affirmative imperative.  The model tags Mood=Imp only sporadically:
# it labels "Mira", "Cállate" and "Oiga" correctly but reads "Habla",
# "Haz", "Dime" and "Dame" as indicative and "Pon" as an infinitive, so a
# mood-based rule (the French engine's approach) would miss most real
# commands here.  Instead the rule lists the imperative forms themselves.
# A bare command is homographic with other forms ("toma" = he takes,
# "mira" = he looks, "di" = I gave) and Spanish drops the subject
# pronoun, so position does the disambiguating: a sentence that *opens*
# with one of these words is a command, while the declarative reading
# needs a subject, an auxiliary or a conjunction in front of it.  The
# enclitic forms (dame, hazlo, siéntate) cannot be anything else, since
# no finite form takes a pronoun, and they are what dialogue actually
# uses.
_IMPERATIVE_START = (
    # tú
    "haz",
    "pon",
    "ven",
    "di",
    "mira",
    "escucha",
    "espera",
    "perdona",
    "disculpa",
    "toma",
    "fíjate",
    "acuérdate",
    # usted / ustedes
    "oiga",
    "mire",
    "diga",
    "deje",
    "pase",
    "salga",
    "baje",
    "compre",
    "tome",
    "cuente",
    "aproveche",
    "disculpe",
    "diríjase",
    "prepárese",
    # verb + enclitic pronoun
    "dame",
    "dime",
    "dígame",
    "dígale",
    "dímelo",
    "dámelo",
    "hazme",
    "hazlo",
    "ponte",
    "vete",
    "vámonos",
    "siéntate",
    "siéntese",
    "siéntense",
    "levántate",
    "levántese",
    "póngase",
    "cállate",
    "pásame",
    "páseme",
    "escúchame",
    "escúcheme",
    "óigame",
    "mírame",
    "míreme",
    "sígame",
    "cuéntame",
    "espérame",
    "espéreme",
    "ayúdame",
    "ayúdeme",
    "déjame",
    "déjeme",
    "déjalo",
    "olvídalo",
    "llámame",
    "enséñame",
    "mándame",
    "pídeme",
    "prepárame",
    "discúlpeme",
)

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
        {
            "seq": [
                spec_surface("hay", "había", "hubo"),
                spec_pos("NOUN", "DET", "NUM", "PRON", "ADJ"),
            ]
        },
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
                spec_lemma(
                    "gustar", "encantar", "fascinar", "molestar", "doler", "apetecer"
                )
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
                    "qué",
                    "cómo",
                    "dónde",
                    "cuándo",
                    "quién",
                    "quiénes",
                    "cuál",
                    "cuáles",
                    "cuánto",
                    "cuánta",
                    "cuántos",
                    "cuántas",
                    "adónde",
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
        {
            "seq": [
                {
                    "pos_in": ["VERB", "AUX"],
                    "morph": {"Tense": "Past", "VerbForm": "Fin", "Mood": "Ind"},
                }
            ]
        },
        zh="简单过去时（comió 等）：过去完成的动作，有起点/终点。",
        # The tagger reads the command "dame" (and "hazlo") as an indicative
        # past form; a sentence cannot open with a preterite that has no
        # subject, so first position belongs to the imperative rule.
        not_at_start_with=spec_surface(*_IMPERATIVE_START),
    ),
    make_rule(
        "es_imperfect",
        "Imperfect (comía / vivía)",
        "A2",
        "an ongoing or repeated past action, or a past description: vivía, era, cantaba.",
        {
            "seq": [
                {
                    "pos_in": ["VERB", "AUX"],
                    "morph": {"Tense": "Imp", "VerbForm": "Fin", "Mood": "Ind"},
                }
            ]
        },
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
        {
            "left": [spec_surface("más", "menos")],
            "right": [spec_surface("que")],
            "min_gap": 1,
            "max_gap": 4,
        },
        zh="más/menos … que：比……更/更不……。",
        # "menos mal que" is a fixed B1 construction with its own row; the
        # filler between the anchors is what tells it apart from a
        # comparison.  "más vale que" is deliberately *not* vetoed: the
        # construction is a real comparison ("más vale prevenir que curar").
        not_inside=spec_surface("mal"),
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
    make_rule(
        "es_imperativo",
        "Affirmative imperative (haz, pon, dame)",
        "A2",
        "a command to tú or usted: Hazlo ahora, Dame el libro, Siéntese, por "
        "favor.  The mood tag is unreliable, so this counts the command "
        "forms that open a sentence instead.",
        {"seq": [spec_surface(*_IMPERATIVE_START)]},
        zh="肯定命令式（tú/usted）：Hazlo（做吧）、Dame（给我）、Siéntese（请坐）。"
        "带代词时连写（dímelo / siéntate），与第三人称同形的词形只看句首。",
        at_start=True,
    ),
    # ---- B1 ----
    make_rule(
        "es_subj_present",
        "Present subjunctive",
        "B1",
        "used after triggers of will, emotion, doubt or necessity: "
        "quiero que, espero que, es posible que + subjunctive verb.",
        {
            "seq": [
                {"pos_in": ["VERB", "AUX"], "morph": {"Mood": "Sub", "Tense": "Pres"}}
            ]
        },
        zh="虚拟式现在时：意愿/情感/怀疑类触发词后用（quiero que vengas）。",
    ),
    make_rule(
        "es_conditional",
        "Conditional (habría / compraría)",
        "B1",
        "hypothetical or polite: compraría = I would buy.",
        {"seq": [{"pos_in": ["VERB", "AUX"], "morph": {"Mood": "Cnd"}}]},
        zh="条件式（compraría 等）：假设或礼貌表达。",
    ),
    make_rule(
        "es_futuro",
        "Simple future (comprará)",
        "B1",
        "the -ré/-rá/-remos future: a prediction, promise or probability "
        "about what is yet to come: vendrá mañana.",
        {
            "seq": [
                {"pos_in": ["VERB", "AUX"], "morph": {"Mood": "Ind", "Tense": "Fut"}}
            ]
        },
        zh="简单将来时（-ré/-rá/-remos）：对将来的预测/承诺/推测（vendrá 明天会来）。",
    ),
    make_rule(
        "es_present_perfect",
        "Present perfect: he + participle",
        "B1",
        "he/has/ha + participle: a past action linked to now (he comido).",
        {
            "seq": [
                spec_surface("he", "has", "ha", "hemos", "habéis", "han"),
                spec_morph(VerbForm="Part"),
            ]
        },
        zh="现在完成时：he/has/ha + 过去分词（he comido 我吃过了）。",
    ),
    make_rule(
        "es_pluperfect",
        "Pluperfect: había + participle",
        "B1",
        "había + participle: the earlier of two past events (ya había comido).",
        {
            "seq": [
                spec_surface("había", "habías", "habíamos", "habían", "hube"),
                spec_morph(VerbForm="Part"),
            ]
        },
        zh="过去完成时：había + 过去分词，表示过去的过去。",
    ),
    make_rule(
        "es_si_conditional",
        "si + past subjunctive, conditional",
        "B1",
        "hypothetical: Si tuviera dinero, compraría una casa.",
        {
            "left": [spec_surface("si")],
            "right": [spec_morph(Mood="Cnd")],
            "min_gap": 1,
            "max_gap": 10,
        },
        zh="si + 虚拟式过去时，主句条件式：假设（Si tuviera…, compraría…）。",
    ),
    # ---- B2 ----
    make_rule(
        "es_subj_past",
        "Imperfect subjunctive (-ra / -se)",
        "B2",
        "the past subjunctive: si tuviera, como si fuera; needed after past "
        "triggers (quería que vinieras).",
        {
            "seq": [
                {"pos_in": ["VERB", "AUX"], "morph": {"Mood": "Sub", "Tense": "Imp"}}
            ]
        },
        zh="虚拟式过去时（-ra/-se 形式）：过去触发词或 si 条件句中用（si tuviera）。",
    ),
    make_rule(
        "es_subj_pluperfect",
        "Pluperfect subjunctive: hubiera + participle",
        "B2",
        "an unreal past: si hubiera sabido, habría venido.",
        {
            "seq": [
                spec_surface(
                    "hubiera",
                    "hubieras",
                    "hubiéramos",
                    "hubieran",
                    "hubiese",
                    "hubieses",
                    "hubiésemos",
                    "hubiesen",
                ),
                spec_morph(VerbForm="Part"),
            ]
        },
        zh="过去虚拟完成时：hubiera/hubiese + 过去分词，对过去的假设。",
    ),
    make_rule(
        "es_conditional_perfect",
        "Conditional perfect: habría + participle",
        "B2",
        "what would have happened: habría venido.",
        {
            "seq": [
                spec_surface("habría", "habrías", "habríamos", "habrían"),
                spec_morph(VerbForm="Part"),
            ]
        },
        zh="条件复合时：habría + 过去分词（本会……）。",
    ),
    make_rule(
        "es_se",
        "se constructions (reflexive / passive / impersonal)",
        "B2",
        "se lavar = wash oneself; se vende = is sold (passive); "
        "se dice = people say (impersonal).",
        {"seq": [spec_surface("se"), spec_pos("VERB")]},
        zh="se 结构：自复（se lava）、自复被动（se vende 被出售）、无人称（se dice 人们说）。",
    ),
    make_rule(
        "es_como_si",
        "como si + subjunctive",
        "B2",
        "as if: actúa como si nada hubiera pasado.",
        {"seq": [spec_surface("como"), spec_surface("si")]},
        zh="como si + 虚拟式：好像……一样。",
    ),
    make_rule(
        "es_llevar_gerund",
        "llevar + time + gerund",
        "B2",
        "duration up to now: llevo dos años viviendo aquí.",
        {
            "left": [spec_lemma("llevar")],
            "right": [spec_morph(VerbForm="Ger")],
            "min_gap": 0,
            "max_gap": 6,
        },
        zh="llevar + 时间 + 副动词：持续做某事已多久（llevo dos años viviendo）。",
    ),
    # ---- C1 / C2 ----
    make_rule(
        "es_de_haber",
        "de haber + participle (inverted condition)",
        "C1",
        "a literary unreal past: de haber sabido, habría venido "
        "(= si hubiera sabido).",
        {"seq": [spec_surface("de"), spec_lemma("haber"), spec_morph(VerbForm="Part")]},
        zh="de haber + 过去分词：倒装条件句（= si hubiera…，要是当初……）。",
    ),
    make_rule(
        "es_por_mas_que",
        "por más que + subjunctive",
        "C1",
        "concessive: however much: por más que trabaje, no acabará.",
        {"seq": [spec_surface("por"), spec_surface("más", "mas"), spec_surface("que")]},
        zh="por más que + 虚拟式：无论怎样……（让步）。",
    ),
    make_rule(
        "es_y_eso_que",
        "y eso que (and yet)",
        "C1",
        "colloquial concession: y eso que llovía, vinimos.",
        {"seq": [spec_surface("y"), spec_surface("eso"), spec_surface("que")]},
        zh="y eso que：可是话说回来……（口语让步）。",
    ),
    make_rule(
        "es_no_es_que",
        "no es que + subjunctive",
        "C2",
        "it's not that ...: no es que sea tonto, es que no quiero.",
        {"seq": [spec_surface("no"), spec_surface("es"), spec_surface("que")]},
        zh="no es que + 虚拟式：并不是说……（先否认再解释）。",
    ),
]


def analyze_spanish(page_text, display_lang="en"):
    """
    Analyze a page of Spanish text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    rules = _get_all_rules()
    return analyze_tokens(page_text, rules, _tokens_for, display_lang)


# ---- data layer (grammar_es.json) ---------------------------------------
#
# Merged material rows become rules whose matcher specs are derived from the
# row's `focus` words and kept only when they fire on the row's own example
# sentences, so a stale spec can never enter the panel silently.  Mirrors the
# French / Russian engines.


def _norm_name(name):
    "Fold a rule name for the hand-written / data-layer overlap check."
    return re.sub(r"[^0-9a-zà-ÿ]+", "", (name or "").lower())


_HANDWRITTEN_NORMS = {_norm_name(r["name"]) for r in _ES_RULES}

_DATA_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "jlpt_data",
        "grammar_es.json",
    )
)

# Rows that would report the same span as another rule on the same sentence,
# so the panel must show only one of them.  The row stays in grammar_es.json
# and on the library page; `_get_all_rules` is what the panel reads, and it
# drops these.
#
# The audit's first run found five colliding pairs and four were precision
# bugs in the rows themselves -- "por + infinitivo" firing on "acabó por
# aceptar", "tanto ... como" firing on "tan despacio como" through the lemma
# of *tan*, "nada / nadie / nunca" re-reporting *tampoco*, "si + indicativo"
# accepting the subjunctive -- so those were narrowed in their material rows
# instead of being hidden here.  What is left is a row whose point the broad
# hand-written comparison already reports on the same words.
_DUPLICATE_RULE_IDS = frozenset(
    {
        # "más vale que + subjuntivo" vs the hand-written más/menos ... que:
        # both match "más vale que"; the idiom is a comparison, and the
        # hand-written rule also carries "más vale prevenir que curar", so
        # the rule keeps the span and the row stays a library entry only.
        "esm_másvalequesubjuntivo__e5fdf7",  # x2, dupe of es_mas_que
    }
)


def _focus_words(focus):
    words = []
    for f in focus or []:
        w = (f or "").strip().lower()
        if w:
            words.append(w)
    return words


def _focus_vetoes(focus):
    """
    The "!word" entries of a focus list, as context vetoes.

    A row whose construction collides with a longer one needs to say so, and
    where the veto sits in the focus list says which side it guards:

    * before the first real word -- the token *left* of the match
      (``not_after``).  "en tanto que" would otherwise fire the
      ``tanto ... que`` row, so that row reads ``["!en", "tanto", ..., "que"]``.
    * after the last real word -- the three tokens *right* of the match
      (``not_followed_by``).  Temporal *mientras* would otherwise fire on
      every *mientras que*, so its focus is ``["mientras", "!que"]``.
    * between two real words -- any token *inside* the match
      (``not_inside``).  ``hace falta que`` is not "hace + time + que", so
      that row's focus is ``["hace", "!falta", "...", "que"]``: the veto
      sits inside the span and the gap is checked for it.

    Returns a dict of rule keys (possibly empty), ready to splat into
    ``make_rule`` / the rule dict the validator builds.

    A veto word matches inflections as well as the surface spelled in the
    list: "por + infinitivo" must stop firing after *acabó*, and "acabó" is
    not written in anybody's focus list, so ``!acabar/estar`` matches by
    lemma as well.
    """
    words = _focus_words(focus)
    real = [i for i, w in enumerate(words) if not w.startswith("!")]
    if not real:
        return {}
    first, last = real[0], real[-1]
    slots = {"not_after": [], "not_inside": [], "not_followed_by": []}
    for i, word in enumerate(words):
        if not word.startswith("!") or len(word) < 2:
            continue
        if i < first:
            slot = "not_after"
        elif i > last:
            slot = "not_followed_by"
        else:
            slot = "not_inside"
        alternatives = word[1:].split("/")
        slots[slot].append(
            {"any_of": [spec_surface(*alternatives), spec_lemma(*alternatives)]}
        )
    return {
        key: (specs[0] if len(specs) == 1 else {"any_of": specs})
        for key, specs in slots.items()
        if specs
    }


def _focus_condition(word, lemma):
    """
    One focus word as a token condition.

    Three shorthands keep the material rows declarative while still letting a
    row talk about word classes the literal vocabulary cannot spell out:
    ``"a/b"`` marks surface alternatives (always surfaces -- a slash list is
    an explicit form list), a ``§``-prefixed token names a token class
    (``§inf``, ``§ger``, ``§part``, ``§verb``, ``§ind``, ``§noun``, ``§adj``,
    ``§subj``, ``§subj2``), and every other word is matched as a surface in
    the first variant and as a lemma in the second -- so ``volver a + §inf``
    also fires on *volvió a llamar*, where the surface list cannot cover
    every form.
    """
    if word.startswith("§"):
        return _TOKEN_CLASSES.get(word, spec_pos("X"))
    if "/" in word:
        alts = word.split("/")
        if lemma:
            # The list mixes forms and base forms ("volver/vuelvo/vuelve"):
            # the lemma variant lets *volvió* match an alternative the list
            # spells only in the infinitive.
            return {"any_of": [spec_surface(*alts), spec_lemma(*alts)]}
        return spec_surface(*alts)
    return spec_lemma(word) if lemma else spec_surface(word)


def _focus_run(words, lemma):
    "The conditions for one run of adjacent focus words."
    return [_focus_condition(w, lemma) for w in words]


# Token classes a focus list may name after "§".  Anything unknown falls back
# to a spec no token matches, so a typo drops the row instead of matching
# everything (the opposite failure is silent and much worse).
#
# Note there is deliberately no "§imp": es_core_news_md tags the imperative
# mood inconsistently (Escriba as Imp, Hable as Sub, Vengan as Ind), so a mood
# spec would miss most commands.  The one reliable command shape is the
# negative one -- "no + present subjunctive, second person" -- which is
# "§subj2".
_TOKEN_CLASSES = {
    "§inf": spec_morph(VerbForm="Inf"),
    "§ger": spec_morph(VerbForm="Ger"),
    "§part": spec_morph(VerbForm="Part"),
    "§verb": spec_pos("VERB", "AUX"),
    "§ind": spec_morph(VerbForm="Fin", Mood="Ind"),
    "§noun": spec_pos("NOUN", "PROPN"),
    "§adj": spec_pos("ADJ"),
    "§subj": spec_morph(Mood="Sub"),
    "§subj2": spec_morph(Mood="Sub", Person="2"),
}


def _focus_specs(focus):
    """
    Candidate matcher specs derived from one row's focus list.

    Focus vocabulary: plain words match adjacently (seq); one "..." marks a
    bounded gap (left/right, up to 4 words); an "a/b" slash marks surface
    alternatives.  A bare function word (para, como, que, por, con, sin)
    never stands alone -- it fires on half the corpus -- so a single word
    shorter than five characters is dropped unless it carries alternatives.

    Every row is emitted twice -- once matching surfaces and once matching
    lemmas -- because a row lists base forms ("volver") while the text has
    inflected ones ("volvió").  A leading "=" on a focus word turns that
    second variant off for the whole row: "tanto/tanta/tantos/tantas" is
    already exhaustive, and matching by lemma is what would make "tan"
    (lemma *tanto*) fire the quantitative row that belongs to the
    hand-written tan + adjective + como rule.
    """
    raw = _focus_words(focus)
    surface_only = any(w.startswith("=") for w in raw)
    words = [w.lstrip("=") for w in raw if not w.startswith("!")]
    if not words:
        return []
    if words.count("...") == 1:
        i = words.index("...")
        left_w, right_w = words[:i], words[i + 1 :]
        if not left_w or not right_w:
            return []
        specs = []
        for lemma in (False,) if surface_only else (False, True):
            spec = {
                "left": _focus_run(left_w, lemma),
                "right": _focus_run(right_w, lemma),
                "min_gap": 0,
                "max_gap": 4,
            }
            if spec not in specs:
                specs.append(spec)
        return specs
    if "..." in words:
        return []  # at most one gap anchor pair is expressible
    if len(words) == 1 and len(words[0]) < 5 and "/" not in words[0]:
        return []  # a bare short word fires everywhere -- never specific
    specs = [{"seq": _focus_run(words, False)}]
    lemma_spec = {"seq": _focus_run(words, True)}
    if lemma_spec != specs[0] and not surface_only:
        specs.append(lemma_spec)
    return specs


def _flash(spec, examples, vetoes=None):
    """
    True when spec fires on at least one of the row's example sentences.

    The vetoes are part of the test: a spec that only fires in the context
    the row excludes (temporal *mientras* inside *mientras que*) must not
    count as validated, or the row would ship an example it cannot mark.
    """
    rule = {"match": spec, **(vetoes or {})}
    for sentence in examples:
        text = (sentence or "").strip()
        if text and match_rule(rule, _tokens_for(text), text):
            return True
    return False


def _load_data_rules():
    """
    Rules for the merged grammar-point rows (jlpt_data/grammar_es.json).

    Specs are derived from each row's `focus` and kept only when they match
    at least one of the row's own examples.

    Every validated row is returned, including the ones listed in
    ``_DUPLICATE_RULE_IDS``: the library page shows all of them, and
    ``_get_all_rules`` is where the panel drops the duplicates (the audit
    also needs the *un*deduped set to re-derive that list at all).
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
        examples = [
            e.strip() for e in (item.get("examples") or []) if (e or "").strip()
        ]
        if not examples:
            continue
        vetoes = _focus_vetoes(item.get("focus"))
        try:
            validated = [
                s
                for s in _focus_specs(item.get("focus"))
                if _flash(s, examples, vetoes)
            ]
        except ImportError:
            # spaCy or the Spanish model is not installed: nothing can be
            # validated, and an unvalidated matcher is exactly what this
            # layer refuses to ship.  The /grammar page still renders the
            # hand-written rules, which need no tokeniser.
            return []
        if not validated:
            continue
        key = item.get("key") or name
        zh_examples = [z for z in (item.get("example_zh") or []) if z]
        # Pair each example with its Chinese gloss when the material carries
        # one, so the library detail card can show a translation line.
        if len(zh_examples) == len(examples):
            library_examples = [
                {"es": ex, "zh": z} for ex, z in zip(examples, zh_examples)
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
            **vetoes,
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
    """
    Hand-written rules plus the validated data layer, built on first use.

    ``_DUPLICATE_RULE_IDS`` is dropped here rather than in the loader so the
    library page (which reads ``_get_data_rules``) and the overlap audit keep
    seeing every row.
    """
    global _all_rules_cache  # pylint: disable=global-statement
    with _rules_lock:
        if _all_rules_cache is None:
            _all_rules_cache = list(_ES_RULES) + [
                rule
                for rule in _get_data_rules()
                if rule["key"] not in _DUPLICATE_RULE_IDS
            ]
        return _all_rules_cache


# ---- library export ----------------------------------------------------
#
# The /grammar page lists the whole corpus at once, where the panel lists
# only what the current page happens to contain.

_CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]

_GOLDEN_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "jlpt_data",
        "grammar_es_golden.json",
    )
)

_golden_lock = threading.Lock()
_golden_cache = None


def _golden_examples():
    """
    Curated example sentences for the hand-written rules.

    Keyed ``key -> [{"es", "en", "zh", "ko"}, ...]``.  The golden set is the
    single store of these examples: the tests re-run every sentence through
    its own rule, and the library serves the same sentences to readers, so
    the page can never show a point whose matcher does not actually
    recognise it.
    """
    global _golden_cache  # pylint: disable=global-statement
    with _golden_lock:
        if _golden_cache is None:
            examples = {}
            if os.path.exists(_GOLDEN_PATH):
                with open(_GOLDEN_PATH, encoding="utf-8") as fh:
                    for key, entry in json.load(fh).items():
                        examples[key] = [
                            e
                            for e in entry.get("examples") or []
                            if (e.get("es") or "").strip()
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
    One rule as a library row, display-ready (see the Japanese analogue).

    Each example comes back as ``{"text", "tr"}``: the Spanish sentence and
    its translation for the display language.  The library has no page text
    to highlight, so the match offsets the panel computes are irrelevant
    here.

    The description resolves through the same table the panel uses, so a
    Korean reader gets the Korean ``desc`` that ``_KO_BY_KEY`` already
    carries instead of the English one.
    """
    desc = desc_for_rule(rule, display_lang) or rule["name"]
    rows = []
    for example in examples:
        if isinstance(example, str):
            example = {"es": example}
        text = (example.get("es") or "").strip()
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
    The whole Spanish grammar library, in CEFR order (A1 -> C2).

    Built on first use like the rules themselves, so an app start-up with
    no Spanish books does not pay for the spaCy model.  Hand-written rules
    take their examples from the golden set; the merged data rows carry
    their own.
    """
    golden = _golden_examples()
    rules = []
    for rule in _ES_RULES:
        examples = rule.get("examples") or golden.get(rule["key"]) or []
        rules.append((rule, examples))
    rules += [(rule, rule.get("examples") or []) for rule in _get_data_rules()]
    entries = [_library_entry(r, display_lang, ex) for r, ex in rules]
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
