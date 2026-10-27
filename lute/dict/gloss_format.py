"""
Structure a flattened dictionary gloss into renderable blocks.

The converter flattens every MDX definition to plain text (see
MdxReader.clean_definition), so the structural signals that reach this
module are only what survives that pass: line breaks, and the text on
each line.  This parser reads those lines the way the dictionaries
themselves lay entries out and tags each one, so the reading page's
Local tab can show parts of speech, numbered senses and example
pairs instead of an undifferentiated wall of text.

The rules are line-content heuristics tuned on the dictionaries in
this server's pool (MOJi辞書, 新世紀日漢双解) and are deliberately
generic:

    See also: X【reading】      -> cross-reference link
    headword【reading(accent)】 -> the entry's own headword line
    名 / 自动·一类 / 名·形動…    -> part-of-speech badge
    惯用语 / 补充说明            -> section header
    中文释义。（日本語の説明。）  -> sense (auto-numbered)
    日本語の例文                -> example; the kana-free line that
    中文翻译。                     immediately follows is its translation

English (Latin-script) dictionaries run through the same loop with
their own conventions:

    /wɜːd/ ; NAmE /wɜːrd/      -> phonetic line
    n. / vt., vi. / noun       -> part-of-speech badge
    n. 词；话语                 -> badge + the sense after the prefix
    1. 词；单词                 -> sense keeping the source number
    a unit of language         -> English counterpart of the Chinese
                                   sense above it (英汉双解 layout)
    He is a man of his word.   -> example; a following line with
    他说话算数。                   Chinese is its translation
    See also: take             -> cross-reference (no reading part)

Some dictionaries flatten to *one line per entry*: their source markup
put each sense and example in an inline span, so no block-level tag ever
produced a line break, and the markers the markup showed as icons are
now characters in the middle of the text:

    🔑  a new sense (or a new headword line, for another part of speech)
    ➤   a guide word -- the label of the sense group that follows
    ◆   an example
    ◇   the start of a synonym study (prose about the group, not this
        entry's meaning)
    🔊  an audio button

Left alone, such an entry is an 800-character wall.  _lines_and_hints
splits it on those markers first, so the same rules below then see
ordinary lines -- see the "marked glosses" section.

Anything unmatched falls through to a plain line -- a dictionary with
different conventions degrades to today's rendering, never worse.
Per-dictionary profiles can refine the rules later without touching
the callers; for now one default profile serves the pool.
"""

import re

# Hiragana, katakana (incl. the prolonged sound mark), half-width katakana.
_KANA_RE = re.compile(r"[\u3040-\u30ff\uff66-\uff9d]")

# CJK ideographs -- Chinese definitions and translations.  The English
# rules below key off "no CJK, no kana" to leave the pool's Japanese
# entries untouched.
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

# Split a line at the first bracket of any style: what leads it decides
# whether the kana deeper in the line is content or a parenthetical.
_LEAD_RE = re.compile(r"[（(「『]")

_XREF_RE = re.compile(r"^See also:\s*(.+?)【([^】]*)】\s*$")

# "See also: take" and friends.  The colon keeps ordinary sentences
# ("See the doctor") from matching.
_XREF_EN_RE = re.compile(
    r"^(?:See also|See|Compare|参看|参见|另见|同见)\s*[：:]\s*(.+?)\s*[。.]?\s*$"
)
_HEADWORD_RE = re.compile(r"^(.+?)【([^】]*)】$")

# Section headers the pool's dictionaries use verbatim.
_SECTION_WORDS = ("惯用语", "补充说明", "惯用词组")

# A part-of-speech line is short and made only of these characters
# (plus the middots and counters the dictionaries actually use:
# 名·形動, 自动·一类, 名·自他·三类...).  Broad enough for the family,
# narrow enough that no translation line collides with it.
_POS_CHARS = set("名代动自動他形副接续続感連助詞词接头尾叹の·・一二三类")

# A paren-free line longer than this is prose (a 补充说明 paragraph),
# not a numbered sense.
_NOTE_MIN_CHARS = 45

# ---- English (Latin-script) dictionary conventions ----

# Whole-line phonetics: one or more /…/ or […] groups with nothing but
# variety labels (BrE, NAmE ...) left over once the groups are removed.
_PHONETIC_GROUP_RE = re.compile(r"/[^/]+/|\[[^\]]+\]")
_PHONETIC_LABEL_RE = re.compile(r"^[,;.\s]*(?:(?:BrE|NAmE|AmE|Br|Am|US|UK)[,;.\s]*)*$")

# A whole line of English part-of-speech abbreviations.  "and"/"或" are
# fillers, not POS words, and at least one real abbreviation is required.
_EN_POS_WORDS = frozenset(
    "n v vt vi adj adv art abbr aux conj interj int neg num prep pron "
    "prefix suffix modal part det t i c u "
    "noun verb adjective adverb article preposition conjunction pronoun "
    "interjection numeral auxiliary determiner particle abbreviation".split()
)
_EN_POS_FILLERS = frozenset(("and", "或", "和", "及"))

# "n. 词；话语" -- POS abbreviations leading a sense line.  The dot is
# what keeps ordinary words ("art school") out.
_EN_POS_PREFIX_RE = re.compile(
    r"^((?:(?:n|v|vt|vi|adj|adv|art|abbr|aux|conj|interj|int|neg|num|"
    r"prep|pron|prefix|suffix|modal|part|det)\.\s*){1,3})(\S.*)$"
)

# A numbered sense in the source: keep its number instead of renumbering.
# The separator must not be followed by a digit, so "1.5 million" stays
# a definition.
_NUMBERED_RE = re.compile(r"^(\d{1,2})[.、)）](?!\d)\s*(\S.*)$")

# An English example sentence: opens with a capital, ends in sentence
# punctuation, contains a space (single words like "Take." stay senses).
_EN_SENTENCE_RE = re.compile(r"""^["'‘“（(]?[A-Z][^.!?]*[.?!…]["'’”）]*$""")

# Example markers some dictionaries prefix: bullets and "1." numbering.
_EXAMPLE_MARKER_RE = re.compile(r"^(?:[•▪●·◦‣\-–—]|\d{1,2}[.、)）])\s*")


def _has_cjk_or_kana(text):
    return bool(_CJK_RE.search(text) or _KANA_RE.search(text))


def _is_phonetic(line):
    "A line made only of /…/ or […] groups and variety labels."
    if len(line) > 48 or _has_cjk_or_kana(line):
        return False
    if not _PHONETIC_GROUP_RE.search(line):
        return False
    remainder = _PHONETIC_GROUP_RE.sub("", line)
    return _PHONETIC_LABEL_RE.match(remainder) is not None


def _is_en_pos(line):
    "A short line written only from English part-of-speech abbreviations."
    if len(line) > 24 or _has_cjk_or_kana(line):
        return False
    # A trailing parenthesised usage label ("adj（only before noun）")
    # does not disqualify the line.
    line = re.sub(r"[（(][^）)]*[）)]\s*$", "", line).rstrip(" .")
    # .strip("[]"): Longman-style grammatical labels, "verb [T]".
    tokens = [
        t.rstrip(".").lower().strip("[]") for t in re.split(r"[/,;·、&\s]+", line) if t
    ]
    if not tokens:
        return False
    return any(t in _EN_POS_WORDS for t in tokens) and all(
        t in _EN_POS_WORDS or t in _EN_POS_FILLERS for t in tokens
    )


def _starts_with_en_pos_word(line):
    "True when the line opens with a bare POS abbreviation (adj ...)."
    match = re.match(r"[A-Za-z]+", line)
    return match is not None and match.group(0).lower() in _EN_POS_WORDS


def _strip_example_marker(line):
    return _EXAMPLE_MARKER_RE.sub("", line)


def _is_en_sentence(line):
    "A capitalised English sentence ending in . ! or ? -- an example."
    stripped = _strip_example_marker(line)
    return (
        not _has_cjk_or_kana(stripped)
        and " " in stripped
        and len(stripped) >= 5
        and _EN_SENTENCE_RE.match(stripped) is not None
    )


# Block types: xref, headword, pos, section, sense, example, note, plain.
# sense blocks carry "n" (their number within the current run); example
# blocks may carry "translation".


def _is_pos(line):
    "A short line written entirely from the part-of-speech alphabet."
    return 0 < len(line) <= 12 and all(ch in _POS_CHARS for ch in line)


def _strip_section_suffix(line):
    "「他动·二类惯用语」 -> 「他动·二类」: some entries mash a section word onto the POS."
    for word in _SECTION_WORDS:
        if line.endswith(word) and line != word:
            return line[: -len(word)]
    return line


# ---- Marked glosses -------------------------------------------------------
#
# See the module docstring: these are the entries that arrived as one
# line with the source markup's icons left inline as characters.

_KEY_MARK = "\U0001f511"  # 🔑
_GUIDE_MARK = "\U000027a4"  # ➤
_EXAMPLE_MARK = "\U000025c6"  # ◆
_SYNONYM_MARK = "\U000025c7"  # ◇
_AUDIO_MARK = "\U0001f50a"  # 🔊

_MARK_RE = re.compile(f"([{_KEY_MARK}{_GUIDE_MARK}{_EXAMPLE_MARK}])")
_MARK_KINDS = {_KEY_MARK: "key", _GUIDE_MARK: "guide", _EXAMPLE_MARK: "example"}

# Where a bilingual line's translation starts: a Chinese character, or
# the wide punctuation a translation opens with (（、《) while the
# definition's own text does not.
_ZH_START_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3001-\u303f\uff01-\uff60]")

# A word-use study's list of related words is glued onto the last
# example's translation ("参考书；儿童读物；馆藏书籍 author, book,
# classic, ...").  Three or more comma-separated lowercase words at the
# end are that list, not part of the translation.
_TRAILING_WORD_LIST_RE = re.compile(
    r"\s[a-z][a-z'\u00b7-]*(?:,\s+[a-z][a-z'\u00b7-]*){2,}\s*$"
)

# A headword line carries the pronunciation; the word itself is already
# the card's heading, so the line starts at the first variety label.
_PHONETIC_START_RE = re.compile(r"(?:BrE|NAmE|AmE)\s+/")

# What a headword line ends with ("... adjective", "... indefinite
# article").  Anything after it on that line is an inflection or the
# word family, not the headword.  No trailing boundary: the dictionary
# glues the conjugation table straight onto the label ("verbpresent
# simple - I ..."), and that table is what has to go.
_HEADWORD_POS_RE = re.compile(
    r"\b(indefinite article|definite article|modal verb|auxiliary verb|"
    r"adjective|adverb|preposition|pronoun|conjunction|exclamation|"
    r"determiner|noun|verb|number)"
)

# A word family or inflection list is worth a muted line; anything
# longer than this is prose that belongs to another entry.
_HEADWORD_TAIL_MAX_CHARS = 60

# A marked key this short with no translation is a word-family line
# ("(≠ unhappy) happily adverb"), not a sense.
_WORD_FAMILY_MAX_CHARS = 60


def _segment_text(piece):
    "One marker-delimited piece of a marked gloss, as display lines."
    text = piece.replace(_AUDIO_MARK, "")
    return [" ".join(line.split()) for line in text.split("\n") if line.strip()]


def _headword_text(text):
    "A headword segment as (phonetic line, word-family tail or '')."
    match = _PHONETIC_START_RE.search(text)
    if match is not None:
        text = text[match.start() :]
    match = _HEADWORD_POS_RE.search(text)
    if match is None:
        return text, ""
    tail = text[match.end() :].strip()
    head = text[: match.end()].strip()
    return head, tail if len(tail) <= _HEADWORD_TAIL_MAX_CHARS else ""


def _lines_and_hints(gloss):
    """
    The gloss as (lines, hints) -- hints are None for every line of an
    ordinary, line-based gloss, so the rules below read it unchanged.

    A marked gloss is split on its markers first: its single line
    becomes the lines the same rules already understand, each tagged
    with what its marker said it was.

    Two of the marked entry's parts run on until the next sense and are
    dropped: the synonym study (◇) and the HELP usage note.  Both are
    prose *about* the word -- a list of neighbours, or when to use a
    rather than an -- and showing them as senses or examples is what
    made these entries unreadable.
    """
    lines = [ln.strip() for ln in gloss.split("\n") if ln.strip()]
    if _KEY_MARK not in gloss:
        return lines, [None] * len(lines)

    out = []
    kind = None
    dropped = False
    for piece in _MARK_RE.split(gloss):
        if piece in _MARK_KINDS:
            kind = _MARK_KINDS[piece]
            if kind == "key":
                dropped = False
            continue
        if dropped:
            continue
        if _SYNONYM_MARK in piece:
            # Its first synonym is glued onto whatever came before it
            # ("gladhappy"), so the leftover Latin run goes too.
            piece = re.sub(
                r"[A-Za-z][A-Za-z .,'\u2019\u00b7-]*$",
                "",
                piece.split(_SYNONYM_MARK)[0],
            )
            dropped = True
        texts = _segment_text(piece)
        if kind in ("example", "guide"):
            # One marker, one item: anything after the first line is
            # the entry's other material (a word-use study follows the
            # last example of some entries).
            texts = texts[:1]
        for text in texts:
            if kind == "key" and _PHONETIC_START_RE.search(text):
                head, tail = _headword_text(text)
                out.append(("headword", head))
                if tail:
                    out.append(("note", tail))
                if "HELP" in text:
                    dropped = True
                continue
            out.append((kind, text))
    return [text for _, text in out], [kind for kind, _ in out]


def _split_bilingual(text):
    """
    A bilingual line as (source-language part, translation or None),
    split where the Chinese starts.

    A marked gloss puts a sense's definition and its translation on one
    line with no separator ("...can be spoken or written 单词；词；字"),
    so the first Chinese character is the only boundary there is.  The
    boundary is the first *Chinese* character in the wide sense: the
    translation often opens with a full-width bracket the sense's own
    text does not have ("（指已提到或易领会到的人或事物）"), and leaving
    that bracket behind reads as a broken sentence.
    """
    match = _ZH_START_RE.search(text)
    if match is None or match.start() == 0:
        return text, None
    translation = _TRAILING_WORD_LIST_RE.sub("", text[match.start() :]).strip()
    return text[: match.start()].strip(), translation


def parse_gloss(gloss):
    """
    The gloss's lines as structured blocks, in order.

    Never raises and never returns blocks for an empty gloss; any line
    that fits no rule comes back as a plain block, so an unusual
    dictionary renders exactly as much structure as its text supports.
    """
    if not gloss:
        return []
    lines, hints = _lines_and_hints(gloss)
    blocks = []
    sense_no = 0
    pending_example = None  # the example block still awaiting a translation

    def next_line():
        "The line after the one being classified, or None."
        return lines[i + 1] if i + 1 < len(lines) else None

    i = 0
    while i < len(lines):
        line = lines[i]
        hint = hints[i]

        if hint == "example":
            source, translation = _split_bilingual(line)
            block = {"t": "example", "text": source}
            if translation:
                block["translation"] = translation
            blocks.append(block)
            pending_example = None
            i += 1
            continue

        if hint == "guide":
            # The sense group's label (➤printed work 印刷品): a header,
            # not a sense, and the senses below it start at 1 again.
            blocks.append({"t": "section", "text": line})
            sense_no = 0
            pending_example = None
            i += 1
            continue

        if hint == "headword":
            blocks.append({"t": "headword", "text": line})
            pending_example = None
            i += 1
            continue

        if hint == "note":
            blocks.append({"t": "note", "text": line})
            pending_example = None
            i += 1
            continue

        if hint == "key":
            # A marked sense: the translation is the meaning, the
            # source-language definition rides under it (the bilingual
            # layout the popup already uses).  A short key with no
            # translation is the dictionary's word-family line
            # ("(≠ unhappy) happily adverb"), which is a cross-reference,
            # not a sense.
            source, translation = _split_bilingual(line)
            if translation:
                sense_no += 1
                blocks.append(
                    {"t": "sense", "text": translation, "en": source, "n": sense_no}
                )
            elif len(line) <= _WORD_FAMILY_MAX_CHARS:
                blocks.append({"t": "note", "text": line})
            else:
                sense_no += 1
                blocks.append({"t": "sense", "text": line, "n": sense_no})
            pending_example = None
            i += 1
            continue

        match = _XREF_RE.match(line)
        if match is not None:
            blocks.append(
                {"t": "xref", "text": match.group(1), "reading": match.group(2)}
            )
            pending_example = None
            i += 1
            continue

        match = _XREF_EN_RE.match(line)
        if match is not None:
            blocks.append({"t": "xref", "text": match.group(1), "reading": None})
            pending_example = None
            i += 1
            continue

        if not blocks:
            match = _HEADWORD_RE.match(line)
            if match is not None and _KANA_RE.search(match.group(2)):
                blocks.append({"t": "headword", "text": line})
                i += 1
                continue

        if _is_phonetic(line):
            blocks.append({"t": "phonetic", "text": line})
            pending_example = None
            i += 1
            continue

        core = _strip_section_suffix(line)
        # "verb," / "noun," lead a multi-POS entry; the comma is the
        # dictionary's list punctuation, not part of the label.
        pos_label = (
            core
            if _is_pos(core)
            else (line.rstrip(" ,;") if _is_en_pos(line) else None)
        )
        if pos_label is not None:
            if not (
                blocks and blocks[-1]["t"] == "pos" and blocks[-1]["text"] == pos_label
            ):
                blocks.append({"t": "pos", "text": pos_label})
                sense_no = 0
            pending_example = None
            i += 1
            continue

        if line in _SECTION_WORDS:
            blocks.append({"t": "section", "text": line})
            sense_no = 0
            pending_example = None
            i += 1
            continue

        # An English example sentence whose next line is its Chinese
        # translation (英汉双解 example blocks).
        nxt = next_line()
        if _is_en_sentence(line) and nxt is not None and _CJK_RE.search(nxt):
            block = {"t": "example", "text": _strip_example_marker(line)}
            blocks.append(block)
            pending_example = block
            i += 1
            continue

        if _KANA_RE.search(line):
            # Kana before any bracket means the line *is* Japanese: an
            # example.  Kana only inside （…） means a Chinese sense with
            # a Japanese explanation attached -- the sense rule below.
            if _KANA_RE.search(_LEAD_RE.split(line, 1)[0]):
                block = {"t": "example", "text": line}
                blocks.append(block)
                pending_example = block
                i += 1
                continue
        elif pending_example is not None:
            pending_example["translation"] = line
            pending_example = None
            i += 1
            continue
        elif (
            not _has_cjk_or_kana(line)
            and not _starts_with_en_pos_word(line)
            and blocks
            and blocks[-1]["t"] == "sense"
            and _CJK_RE.search(blocks[-1]["text"])
            and "en" not in blocks[-1]
        ):
            # "a unit of language" following "1. 词；单词": the English
            # side of a bilingual sense, shown under it rather than as
            # its own numbered sense (英汉双解 layout).
            blocks[-1]["en"] = line
            pending_example = None
            i += 1
            continue
        elif _looks_like_kanji_example(line, next_line()):
            # 一番電車 / 電車賃 / 桜吹雪: a kanji-only example line the
            # kana test cannot see.  It carries no sentence-ending mark
            # and the line that follows is its Chinese translation.
            block = {"t": "example", "text": line}
            blocks.append(block)
            pending_example = block
            i += 1
            continue
        elif len(line) > _NOTE_MIN_CHARS and "（" not in line and "(" not in line:
            blocks.append({"t": "note", "text": line})
            pending_example = None
            i += 1
            continue

        # A numbered sense the dictionary itself numbered: keep its
        # number; the auto-numbering continues from it.
        match = _NUMBERED_RE.match(line)
        if match is not None:
            sense_no = int(match.group(1))
            blocks.append({"t": "sense", "text": match.group(2), "n": sense_no})
            pending_example = None
            i += 1
            continue

        # "n. 词；话语": POS abbreviations leading a sense line split
        # into the badge and the sense under it.
        match = _EN_POS_PREFIX_RE.match(line)
        if match is not None and not _has_cjk_or_kana(match.group(1)):
            prefix = match.group(1).strip()
            if not (
                blocks and blocks[-1]["t"] == "pos" and blocks[-1]["text"] == prefix
            ):
                blocks.append({"t": "pos", "text": prefix})
                sense_no = 0
            sense_no += 1
            blocks.append({"t": "sense", "text": match.group(2).strip(), "n": sense_no})
            pending_example = None
            i += 1
            continue

        sense_no += 1
        blocks.append({"t": "sense", "text": line, "n": sense_no})
        pending_example = None
        i += 1

    return blocks


def _looks_like_kanji_example(line, following):
    """
    True for a kanji-only example line: short, no sentence-ending mark,
    and the next line is a kana-free translation that has one.
    """
    return (
        following is not None
        and len(line) <= _KANJI_EXAMPLE_MAX_CHARS
        and not line.endswith("。")
        and not _KANA_RE.search(following)
        and following.endswith("。")
        and not following.startswith("See also")
    )


# 一番電車 (4) and 執務時間 (4) are typical; a longer line is prose.
_KANJI_EXAMPLE_MAX_CHARS = 12


# The budget compact_gloss keeps within -- see there.
COMPACT_GLOSS_MAX_CHARS = 200


def compact_gloss(gloss, max_chars=COMPACT_GLOSS_MAX_CHARS):
    """
    The entry's definitions only -- headword, examples and notes dropped
    -- as one text capped at ``max_chars`` characters, with a trailing
    "…" when something was left out.

    This is what the term form's translation auto-fill saves (see
    /read/localdict_lookup): the translation is the term's meaning, and
    a dictionary card's tail of example sentences in it makes every
    popup and review card that shows the translation enormous.  The
    full entry stays in the reading page's Local tab.

    An entry whose lines parse as nothing usable still yields its first
    lines rather than nothing -- a trimmed definition beats none.
    """
    if not gloss:
        return gloss
    lines = []
    for b in parse_gloss(gloss):
        # Section headers (惯用语…) only announce the example groups
        # that follow them; with those dropped the header is noise.
        if b["t"] == "xref":
            label = f"{b['text']}【{b['reading']}】" if b.get("reading") else b["text"]
            lines.append(f"See also: {label}")
        elif b["t"] in ("pos", "sense"):
            lines.append(b["text"])
    if not lines:
        lines = [ln.strip() for ln in gloss.split("\n") if ln.strip()]
    return _take_within(lines, max_chars)


def _take_within(lines, max_chars):
    "The lines up to ``max_chars`` characters in total, plus '…' if cut."
    kept = []
    total = 0
    for line in lines:
        if kept and total + len(line) > max_chars:
            kept.append("…")
            break
        kept.append(line)
        total += len(line)
    return "\n".join(kept)
