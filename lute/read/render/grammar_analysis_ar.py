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

import re

from lute.read.render.grammar_analysis_matcher import (
    analyze_tokens,
    make_rule,
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
        "هل makes a yes/no question; ماذا/متى/أين/كيف/كم/لماذا ask for " "information.",
        {
            "seq": [
                spec_surface(
                    "هل", "ماذا", "ما", "متى", "أين", "كيف", "كم", "لماذا", "من"
                )
            ]
        },
        zh="疑问词：هل 一般疑问；ماذا/متى/أين/كيف/كم/لماذا 特殊疑问。",
    ),
    make_rule(
        "ar_prepositions",
        "Prepositions (في / من / إلى / على...)",
        "A1",
        "في/من/إلى/على/عن/مع are always followed by a genitive noun or "
        "attached pronoun.",
        {
            "seq": [
                spec_surface(
                    "في",
                    "من",
                    "إلى",
                    "على",
                    "عن",
                    "مع",
                    "بين",
                    "بعد",
                    "قبل",
                    "عند",
                    "تحت",
                    "فوق",
                )
            ]
        },
        zh="常用介词：في/من/إلى/على/عن/مع 等，后接属格名词或 Attached pronoun。",
    ),
    make_rule(
        "ar_al",
        "ال- (definite article)",
        "A1",
        "ال- prefixes the definite noun: البيت = the house.",
        {"seq": [{"surface_re": re.compile(r"ال.+")}]},
        zh="定冠词 ال-：البيت = 这个房子。",
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
        "كان / أصبح (kana verbs)",
        "A1",
        "كان = was/were, أصبح = became; the predicate stays in the " "nominative.",
        {"seq": [spec_surface("كان", "كانت", "كانوا", "يكون", "أصبح", "أصارت")]},
        zh="كان 类动词：كان = 曾经是，أصبح = 变得；表语仍为主格。",
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
        {"seq": [spec_surface("لكن", "لكنه", "لكنها", "غير أن")]},
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
        "سوف (or the prefix سـ) before a present verb makes it future: " "سوف أذهب.",
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
        "أن / إن (that)",
        "B1",
        "أن/إن introduce a content clause after verbs like قال/عرف; إنّ "
        "also emphasizes.",
        {"seq": [spec_surface("أن", "إن", "أنه", "أنها", "إنه", "إنها")]},
        zh="أن / إنّ：引导宾语从句『……说/知道……』；إنّ 亦表强调。",
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
        "the suffix ها attaches to nouns and prepositions: كتابها, فيها.",
        {"seq": [{"surface_re": re.compile(r".+ها")}]},
        zh="词尾 ها：她/它（ attached pronoun：كتابها 她的书）。",
    ),
    make_rule(
        "ar_suffix_plural",
        "ـهم / ـكم / ـنا (their / your / our)",
        "B1",
        "plural possessive suffixes attach directly to the word: بيوتهم.",
        {"seq": [{"surface_re": re.compile(r".{2,}(?:هم|هن|كم|كن|نا)")}]},
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
        "كلما pairs with وقد/كان for 'the more ... the more': كلما كبر، " "ازاد علمه.",
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


def analyze_arabic(page_text, display_lang="en"):
    """
    Analyze a page of Arabic text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    """
    return analyze_tokens(page_text, _AR_RULES, _tokens_for_page, display_lang)
