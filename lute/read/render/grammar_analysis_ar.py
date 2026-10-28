"""
Arabic grammar point detection (pyarabic).

Arabic has rich root-and-pattern morphology, which a surface engine cannot
fully resolve; this engine therefore covers the high-frequency function
words and frames: question particles, negation, prepositions, relatives,
attached-pronoun suffixes and the kana family.  Verb tense/mood rules
(jussive after لم, subjunctive after أن) need a real morphological
disambiguator and are out of scope here.

Text is matched after stripping tashkeel (vowel diacritics) and tatweel,
so vocalised and unvocalised text match the same rules; token offsets
still point into the original sentence (the matcher uses the reported
"end" offset, since the normalised surface can be shorter than the text).

pyarabic is imported lazily so the base install works without it; the
route falls back to the generic rule library on ImportError.
"""

import json
import os
import re
import threading

from lute.read.render.grammar_analysis_matcher import (
    analyze_tokens,
    desc_for_rule,
    make_rule,
    match_rule,
    paired_examples,
    spec_surface,
)

# \w misses Arabic combining marks (harakat), so vocalised words would be
# split at every diacritic; add the harakat + superscript-alef ranges.
_WORD_RE = re.compile(r"[\w\u064B-\u0652\u0670]+", re.UNICODE)


def _normalise(text):
    "Strip vowel diacritics (tashkeel) and the tatweel stretch mark."
    from pyarabic.araby import (
        strip_shadda,
        strip_tashkeel,
        strip_tatweel,
    )  # pylint: disable=import-outside-toplevel

    return strip_shadda(strip_tatweel(strip_tashkeel(text)))


def _tokens_for_page(sentence):
    """
    Tokenize the original sentence with offsets into the original text.

    Matching runs on the tashkeel-stripped form, but the reading page shows
    the original (possibly vocalised) text, so each token reports its start
    and end in the original.  Because normalisation only deletes marks (it
    never reorders), word order is preserved.
    """
    stripped = _normalise(sentence)
    if stripped == sentence:
        tokens = []
        for m in _WORD_RE.finditer(sentence):
            tokens.append({"surface": m.group(), "idx": m.start(), "end": m.end()})
        return tokens
    tokens = []
    pos_strip = 0
    scan_orig = 0
    for surface in _WORD_RE.findall(stripped):
        idx_strip = stripped.find(surface, pos_strip)
        if idx_strip < 0:
            continue
        pos_strip = idx_strip + len(surface)
        for m in _WORD_RE.finditer(sentence):
            if m.start() < scan_orig:
                continue
            if _normalise(m.group()) == surface:
                tokens.append({"surface": surface, "idx": m.start(), "end": m.end()})
                scan_orig = m.end()
                break
        else:
            break
    return tokens


# ---- rules -------------------------------------------------------------
#
# Levels use CEFR bands (A1..C2) as a display approximation.  All rules are
# surface-based on tashkeel-stripped forms.

_AR_RULES = [
    # ---- A1 ----
    make_rule(
        "ar_questions",
        "Question words (هل / ماذا / كيف...)",
        "A1",
        "هل makes a yes/no question; ماذا/متى/أين/كيف/كم/لماذا/أي ask for "
        "information.",
        {"seq": [spec_surface("هل", "ماذا", "متى", "أين", "كيف", "كم", "لماذا", "أي")]},
        zh="疑问词：هل 一般疑问；ماذا/متى/أين/كيف/كم/لماذا 特殊疑问。",
    ),
    make_rule(
        "ar_prep_pron",
        "Preposition + attached pronoun (فيه / منها / عليه)",
        "A1",
        "في/من/على/إلى/لـ fuse with a pronoun suffix into one word: فيه = "
        "in it, منها = from it, عليه = on him.",
        {
            "seq": [
                spec_surface(
                    "فيه",
                    "فيها",
                    "فيهم",
                    "منه",
                    "منها",
                    "منهم",
                    "عليه",
                    "عليها",
                    "عليهم",
                    "إليه",
                    "إليها",
                    "إليهم",
                    "به",
                    "بها",
                    "بهم",
                    "له",
                    "لها",
                    "لهم",
                    "لنا",
                    "لك",
                    "لكم",
                    "عنه",
                    "عنها",
                    "معه",
                    "معها",
                    "معهم",
                )
            ]
        },
        zh="介词 + 接尾代词融合成一个词：فيه = 在里面，منها = 从它，عليه = 在他身上。",
    ),
    make_rule(
        "ar_demonstratives",
        "Demonstratives (هذا / هذه / ذلك)",
        "A1",
        "هذا/هؤلاء (masculine), هذه (feminine), ذلك/تلك (that).",
        {"seq": [spec_surface("هذا", "هذه", "ذلك", "تلك", "هؤلاء", "هذان")]},
        zh="指示代词：هذا/هذه 这个，ذلك/تلك 那个，هؤلاء 这些人。",
    ),
    make_rule(
        "ar_neg_la",
        "لا (not)",
        "A1",
        "لا before an imperfect verb = does not / don't.",
        {"seq": [spec_surface("لا")]},
        zh="لا + 现在时动词：不/别（لا أعرف 我不知道）。",
    ),
    make_rule(
        "ar_kana",
        "كان (kana verbs)",
        "A1",
        "كان = was/were; the predicate stays in the nominative.  The other "
        "sisters (أصبح/ظل/بات/صار) have their own library rows.",
        {"seq": [spec_surface("كان", "كانت", "كانوا", "يكون")]},
        zh="كان 类动词：كان = 曾经是；表语仍为主格。其余姐妹词见各条目。",
    ),
    make_rule(
        "ar_pronouns",
        "Pronouns (أنا / هو / هي / نحن)",
        "A1",
        "أنا/نحن/هو/هي/هم/أنت are the standalone pronouns.",
        {"seq": [spec_surface("أنا", "أنت", "أنتم", "نحن", "هو", "هي", "هم")]},
        zh="独立人称代词：أنا 我 / هو 他 / هي 她 / نحن 我们。",
    ),
    make_rule(
        "ar_but",
        "لكن (but)",
        "A1",
        "لكن = but / however.",
        {"seq": [spec_surface("لكن", "لكنه", "لكنها", "لكنهم")]},
        zh="لكن：但是、然而。",
    ),
    # ---- A2 ----
    make_rule(
        "ar_because",
        "لأن (because)",
        "A2",
        "لأن = because; لذلك/لهذا = therefore.",
        {"seq": [spec_surface("لأن", "لان", "لذلك", "لهذا")]},
        zh="لأن 因为；لذلك/لهذا 因此。",
    ),
    make_rule(
        "ar_when",
        "عندما / حين (when)",
        "A2",
        "عندما/حين introduce time clauses.",
        {"seq": [spec_surface("عندما", "حين", "حينما")]},
        zh="عندما / حين：当……的时候。",
    ),
    make_rule(
        "ar_sawfa",
        "سوف / سـ (future)",
        "A2",
        "سوف (or the prefix سـ) before a present verb makes it future: سوف أذهب.",
        {"seq": [spec_surface("سوف")]},
        zh="将来标记：سوف（或动词前加 سـ）+ 现在时动词（سوف أذهب 我将去）。",
    ),
    make_rule(
        "ar_also",
        "أيضا / كذلك (also)",
        "A2",
        "أيضا and كذلك = also / too.",
        {"seq": [spec_surface("أيضا", "كذلك")]},
        zh="أيضا / كذلك：也、同样。",
    ),
    make_rule(
        "ar_every",
        "كل / بعض (all / some)",
        "A2",
        "كل = every/all, بعض = some (followed by a genitive).",
        {"seq": [spec_surface("كل", "بعض", "جميع")]},
        zh="كل 全/每；بعض 一些（后接属格）。",
    ),
    # ---- B1 ----
    make_rule(
        "ar_anna",
        "أن / أنّ (that)",
        "B1",
        "أن/أنّ introduce a content clause after verbs like قال/عرف; أنّ "
        "also emphasizes.",
        {"seq": [spec_surface("أن", "أنه", "إنه")]},
        zh="أن / أنّ：引导宾语从句『……说/知道……』；إنّ 亦表强调。",
    ),
    make_rule(
        "ar_if",
        "إذا / لو (if)",
        "B1",
        "إذا = if/when (likely), لو = if only (unreal).",
        {"seq": [spec_surface("إذا", "لو", "إذاما")]},
        zh="إذا 如果/当……时；لو 假如（与事实相反）。",
    ),
    make_rule(
        "ar_lan_lam",
        "لن / لم (negated future / past)",
        "B1",
        "لن + subjunctive = will not; لم + jussive = did not.",
        {"seq": [spec_surface("لن", "لم")]},
        zh="لن + 虚拟式 = 将不会；لم + 切格式 = 未曾。",
    ),
    make_rule(
        "ar_relative",
        "Relative pronouns (الذي / التي / الذين)",
        "B1",
        "الذي/التي/الذين/اللاتي agree with the noun they refer to.",
        {"seq": [spec_surface("الذي", "التي", "الذين", "اللاتي", "اللواتي", "اللذان")]},
        zh="关系代词：الذي（阳性单）/ التي（阴性单）/ الذين（阳性复）。",
    ),
    make_rule(
        "ar_qad",
        "قد (already / may)",
        "B1",
        "قد + past = already/indeed; قد + present = may/might.",
        {"seq": [spec_surface("قد")]},
        zh="قد + 过去时 = 已经/确实；قد + 现在时 = 可能。",
    ),
    make_rule(
        "ar_suffix_ha",
        "ـها (her / its)",
        "B1",
        "the suffix ها attaches to a noun to mark a possessor: كتابها = her "
        "book.  Preposition + ها fusion (فيها, منها) is reported by "
        "ar_prep_pron instead, and the particles that merely end in ها "
        "(أيها, أنها, نفسها) are excluded here.",
        {
            "seq": [
                {
                    "surface_re": re.compile(
                        r"^(?!أيها$|أيتها$|أنها$|إنها$|نفسها$|فيها$|منها$|عليها$"
                        r"|إليها$|بها$|لها$|معها$|عنها$).+ها$"
                    )
                }
            ]
        },
        zh="词尾 ها：她/它（attached pronoun：كتابها 她的书）。介词融合（فيها/منها）另见「介词+接尾代词」。",
    ),
    make_rule(
        "ar_suffix_plural",
        "ـهم / ـكم / ـنا (their / your / our)",
        "B1",
        "plural possessive suffixes attach directly to the word: بيوتهم.",
        {
            "seq": [
                {
                    "surface_re": re.compile(
                        r"^(?!أنا$|أحيانا$|هنا$|لنا$|معنا$|عندنا$|بيننا$|كلنا$"
                        r"|فيهم$|منهم$|عليهم$|إليهم$|بهم$|لهم$|لكم$|معهم$)"
                        r".{2,}(?:هم|هن|كم|كن|نا)$"
                    )
                }
            ]
        },
        zh="复数词尾：هم/كم/نا 等（بيوتهم 他们的家）。",
    ),
    make_rule(
        "ar_hatta",
        "حتى (until / even)",
        "B1",
        "حتى = until, or even before a noun.",
        {"seq": [spec_surface("حتى")]},
        zh="حتى：直到……；甚至。",
    ),
    make_rule(
        "ar_laysa",
        "ليس (not to be)",
        "B1",
        "ليس negates nominal sentences: ليس صعبا.",
        {"seq": [spec_surface("ليس", "ليست", "ليسوا")]},
        zh="ليس/ليست：不是（否定名词句）。",
    ),
    # ---- B2 / C1 ----
    make_rule(
        "ar_wish",
        "ليت / لعل (wish / maybe)",
        "B2",
        "ليت = if only (wish), لعل = maybe/perhaps; both take a nominal "
        "or attached subject.",
        {"seq": [spec_surface("ليت", "لعل")]},
        zh="ليت 但愿（愿望）；لعل 也许。",
    ),
    make_rule(
        "ar_kullama",
        "كلما (the more / whenever)",
        "B2",
        "كلما pairs with وقد/كان for 'the more ... the more': كلما كبر، ازاد علمه.",
        {"seq": [spec_surface("كلما")]},
        zh="كلما：越……越……；每当……。",
    ),
    make_rule(
        "ar_lam_yakun",
        "لم يكن (past perfect negative)",
        "C1",
        "لم يكن + participle = had not been.",
        {"seq": [spec_surface("لم"), spec_surface("يكن", "تكن")]},
        zh="لم يكن：过去不曾……（过去完成否定）。",
    ),
]


# Rows the reading panel silences: the page never shows them, but the
# library page and the overlap audit still do.  Re-derived by
# scripts/audit_grammar_ar_overlaps.py whenever the library grows.
_DUPLICATE_RULE_IDS = frozenset()


# ---- data layer (grammar_ar.json) ---------------------------------------
#
# Merged material rows become rules whose matcher specs are derived from the
# row's `focus` words and kept only when they fire on the row's own example
# sentences, so a stale spec can never enter the panel silently.  Mirrors the
# French engine, minus the lemma story: pyarabic has no tagger, so every spec
# here is a literal surface sequence.


def _norm_name(name):
    "Fold a rule name for the hand-written / data-layer overlap check."
    return re.sub(r"[^0-9a-z\u0600-\u06FF]+", "", _normalise(name or "").lower())


# Folded names of the hand-written rules, for the overlap check in
# _load_data_rules.  Built on first use rather than at import: folding runs
# through pyarabic, which lives in the optional `arabic` extra, and this
# module is imported by app_factory on every start -- building the set at
# module level made the whole app unstartable on an install without that
# extra (CI's base-ci and no-mecab jobs install develop deps only).
_HANDWRITTEN_NORMS = None


def _handwritten_rule_norms():
    "Folded hand-written rule names, built on first use and memoised."
    global _HANDWRITTEN_NORMS  # pylint: disable=global-statement
    with _rules_lock:
        if _HANDWRITTEN_NORMS is None:
            _HANDWRITTEN_NORMS = {_norm_name(r["name"]) for r in _AR_RULES}
        return _HANDWRITTEN_NORMS


_DATA_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "jlpt_data",
        "grammar_ar.json",
    )
)


def _focus_words(focus):
    words = []
    for f in focus or []:
        w = _normalise(f).strip()
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
    alternatives.  There is no short-word floor: Arabic function words are
    one to three letters (لم, قد, ما) and each row's frequency is judged on
    the corpus instead (see scripts/measure_grammar_panel.py --engine ar).
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
    return [{"seq": [_focus_word_spec(w) for w in words]}]


def _flash(spec, examples):
    "True when spec fires on at least one of the row's example sentences."
    for sentence in examples:
        text = _normalise(sentence)
        if match_rule({"match": spec}, _tokens_for_page(text), text):
            return True
    return False


def _first_glossed(pairs):
    """
    The first example that carries a translation, shaped for the panel.

    ``sentence`` is the field the front end reads first.  Every translation
    the example carries is kept, not only the Chinese one: the shared
    ``reference_gloss`` resolves whichever the panel's display language
    asks for, so an English panel is not served a Chinese gloss.
    """
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        if not (pair.get("zh") or pair.get("en") or pair.get("ko")):
            continue
        sentence = (pair.get("arabic") or "").strip()
        if not sentence:
            continue
        reference = {"sentence": sentence}
        for field in ("zh", "en", "ko"):
            value = (pair.get(field) or "").strip()
            if value:
                reference[field] = value
        return reference
    return None


def _load_data_rules():
    """
    Rules for the merged grammar-point rows (jlpt_data/grammar_ar.json).

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
        if not name or _norm_name(name) in _handwritten_rule_norms():
            continue  # already covered precisely by a hand-written rule
        examples = [e for e in (_normalise(x) for x in item.get("examples") or []) if e]
        if not examples:
            continue
        validated = [s for s in _focus_specs(item.get("focus")) if _flash(s, examples)]
        if not validated:
            continue
        key = item.get("key") or name
        _AR_RULE_EXAMPLES[key] = examples
        library_examples = paired_examples("arabic", examples, item)
        rule = make_rule(
            key,
            name,
            item.get("level") or "B1",
            (item.get("meaning") or "").strip() or name,
            {"any_of": validated} if len(validated) > 1 else validated[0],
            zh=item.get("zh") or None,
            at_start=bool(item.get("at_start")),
        )
        # Library-page fields (the /grammar detail card renders them when
        # present); the panel ignores keys it does not read.
        rule["formation"] = (item.get("formation") or "").strip()
        rule["notes"] = (item.get("notes") or "").strip()
        rule["examples"] = library_examples
        # The panel's folded reference block: the row's own example with its
        # gloss, so a reader sees a translated sentence and not only the
        # rule description above it.
        rule["reference"] = _first_glossed(library_examples)
        rules.append(rule)
    return rules


# The data rules are built on first use, not at import: _load_data_rules()
# tokenizes the rows' examples, and this module is imported at app start-up.
_rules_lock = threading.RLock()
_data_rules_cache = None
_all_rules_cache = None
_AR_RULE_EXAMPLES = {}


def _get_data_rules():
    "The data-driven rules, built on first use and memoised."
    global _data_rules_cache  # pylint: disable=global-statement
    with _rules_lock:
        if _data_rules_cache is None:
            _data_rules_cache = _load_data_rules()
        return _data_rules_cache


def _get_all_rules():
    """
    Hand-written plus data-driven rules, built and memoised on first use.

    The hand-written rules keep their illustrative sentence in the golden
    set, so their panel reference block is attached here -- on a copy, so
    the module-level rule list stays exactly as written.
    """
    global _all_rules_cache  # pylint: disable=global-statement
    with _rules_lock:
        if _all_rules_cache is None:
            golden = _golden_examples()
            combined = []
            for rule in _AR_RULES:
                if not rule.get("reference"):
                    reference = _first_glossed(golden.get(rule["key"]) or [])
                    if reference:
                        rule = dict(rule, reference=reference)
                combined.append(rule)
            combined += [
                rule
                for rule in _get_data_rules()
                if rule["key"] not in _DUPLICATE_RULE_IDS
            ]
            _all_rules_cache = combined
        return _all_rules_cache


def analyze_arabic(page_text, display_lang="en"):
    """
    Analyze a page of Arabic text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(page_text, _get_all_rules(), _tokens_for_page, display_lang)


# ---- library export ----------------------------------------------------


_CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]

_GOLDEN_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "jlpt_data",
        "grammar_ar_golden.json",
    )
)

_golden_cache = None


def _golden_examples():
    """
    Curated example sentences for the hand-written rules.

    Keyed ``key -> [{"arabic", "zh"}, ...]``.  The golden set is the single
    store of these examples: the tests re-run every sentence through its own
    rule, and the library serves the same sentences to readers, so the page
    can never show a point whose matcher does not actually recognise it.
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
                            if (e.get("arabic") or "").strip()
                        ]
            _golden_cache = examples
        return _golden_cache


def _library_entry(rule, display_lang):
    "One rule as a library-page row, with its formation / notes / examples."
    rows = []
    for example in rule.get("examples") or []:
        if isinstance(example, dict):
            text = (example.get("arabic") or example.get("text") or "").strip()
            if not text:
                continue
            tr = ""
            if display_lang == "zh":
                tr = (example.get("zh") or "").strip()
            elif display_lang == "ko":
                tr = (example.get("ko") or "").strip()
            else:
                tr = (example.get("en") or "").strip()
            rows.append({"text": text, "tr": tr} if tr else text)
        else:
            text = (example or "").strip()
            if text:
                rows.append(text)
    entry = {
        "key": rule["key"],
        "name": rule["name"],
        "level": rule["level"],
        "desc": desc_for_rule(rule, display_lang),
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
    The whole Arabic grammar library, in CEFR order (A1 -> C2).

    Hand-written rules take their examples from the golden set; the merged
    data rows carry their own.  Built on first use so an app start-up with
    no Arabic books does not pay for pyarabic.
    """
    golden = _golden_examples()
    rules = []
    for rule in _AR_RULES:
        if not rule.get("examples"):
            rule = dict(rule)
            rule["examples"] = golden.get(rule["key"]) or []
        rules.append(rule)
    rules += _get_data_rules()
    entries = [_library_entry(rule, display_lang) for rule in rules]
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
