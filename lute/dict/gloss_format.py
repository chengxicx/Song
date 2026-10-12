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

Anything unmatched falls through to a plain line -- a dictionary with
different conventions degrades to today's rendering, never worse.
Per-dictionary profiles can refine the rules later without touching
the callers; for now one default profile serves the pool.
"""

import re

# Hiragana, katakana (incl. the prolonged sound mark), half-width katakana.
_KANA_RE = re.compile(r"[\u3040-\u30ff\uff66-\uff9d]")

# Split a line at the first bracket of any style: what leads it decides
# whether the kana deeper in the line is content or a parenthetical.
_LEAD_RE = re.compile(r"[（(「『]")

_XREF_RE = re.compile(r"^See also:\s*(.+?)【([^】]*)】\s*$")
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


def parse_gloss(gloss):
    """
    The gloss's lines as structured blocks, in order.

    Never raises and never returns blocks for an empty gloss; any line
    that fits no rule comes back as a plain block, so an unusual
    dictionary renders exactly as much structure as its text supports.
    """
    if not gloss:
        return []
    lines = [ln.strip() for ln in gloss.split("\n") if ln.strip()]
    blocks = []
    sense_no = 0
    pending_example = None  # the example block still awaiting a translation

    def next_line():
        "The line after the one being classified, or None."
        return lines[i + 1] if i + 1 < len(lines) else None

    i = 0
    while i < len(lines):
        line = lines[i]

        match = _XREF_RE.match(line)
        if match is not None:
            blocks.append({"t": "xref", "text": match.group(1), "reading": match.group(2)})
            pending_example = None
            i += 1
            continue

        if not blocks:
            match = _HEADWORD_RE.match(line)
            if match is not None and _KANA_RE.search(match.group(2)):
                blocks.append({"t": "headword", "text": line})
                i += 1
                continue

        core = _strip_section_suffix(line)
        if _is_pos(core):
            if not (blocks and blocks[-1]["t"] == "pos" and blocks[-1]["text"] == core):
                blocks.append({"t": "pos", "text": core})
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
        elif _looks_like_kanji_example(line, next_line()):
            # 一番電車 / 電車賃 / 桜吹雪: a kanji-only example line the
            # kana test cannot see.  It carries no sentence-ending mark
            # and the line that follows is its Chinese translation.
            block = {"t": "example", "text": line}
            blocks.append(block)
            pending_example = block
            i += 1
            continue
        elif (
            len(line) > _NOTE_MIN_CHARS
            and "（" not in line
            and "(" not in line
        ):
            blocks.append({"t": "note", "text": line})
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
