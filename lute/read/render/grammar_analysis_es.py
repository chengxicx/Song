"""
Spanish grammar point detection (spaCy es_core_news_md).

Mirrors the English engine: sentences are split, tokenised with lemma +
POS + morphological features, and token-aware rules recognise grammar
constructions.  The md model beats the sm one on real sentences (+2pp
rule hits, correctly tags subjunctive forms the sm model miscast as
nouns) and is faster; imperatives still need care, so rules anchor on
context (e.g. a trigger + que + subjunctive).

spaCy and the model are imported lazily so the base install works without
them; the route falls back to the generic rule library on ImportError.
"""

from lute.read.render.grammar_analysis_matcher import (
    analyze_tokens,
    make_rule,
    make_spacy_tokenizer,
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
        {
            "left": [spec_surface("más", "menos")],
            "right": [spec_surface("que")],
            "min_gap": 1,
            "max_gap": 4,
        },
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
    return analyze_tokens(page_text, _ES_RULES, _tokens_for, display_lang)


# ---- library export ----------------------------------------------------
#
# The /grammar page lists the whole corpus at once, where the panel lists
# only what the current page happens to contain.  Same display fields as
# the panel entries (minus the page-specific match offsets), so the page
# can reuse the panel's markup and CSS.  The Spanish corpus is the
# hand-written rules only -- there is no merged data layer yet.

import json  # noqa: E402
import os  # noqa: E402
import threading  # noqa: E402

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
    """
    desc = rule.get("desc") or rule["name"]
    if display_lang == "zh" and (rule.get("zh") or "").strip():
        desc = rule["zh"]
    rows = []
    for example in examples:
        if isinstance(example, str):
            example = {"es": example}
        text = (example.get("es") or "").strip()
        if text:
            rows.append({"text": text, "tr": _example_gloss(example, display_lang)})
    return {
        "key": rule["key"],
        "name": rule["name"],
        "level": rule["level"],
        "desc": desc,
        "examples": rows,
        "skipped": False,
    }


def library_entries(display_lang="en"):
    "The whole Spanish grammar library, in CEFR order (A1 -> C2)."
    golden = _golden_examples()
    entries = []
    for rule in _ES_RULES:
        examples = rule.get("examples") or golden.get(rule["key"]) or []
        entries.append(_library_entry(rule, display_lang, examples))
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
