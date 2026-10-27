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
    ➡   a cross-reference ("compare conscious")
    ▸   a derived word ("subconsciously adverb")
    ●   an idiom or fixed phrase (a sub-entry with its own meaning)

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

# Oxford's own pronunciations are always slash-delimited; a bracketed
# group is another dictionary's convention (or a grammar label here),
# so only the slashes start a headword's pronunciation run.
_SLASH_PHONETIC_RE = re.compile(r"/[^/]+/")

# A whole line of English part-of-speech abbreviations.  "and"/"或" are
# fillers, not POS words, and at least one real abbreviation is required.
# "f"/"m" are the gender labels the 西汉 dictionaries give a noun.
_EN_POS_WORDS = frozenset(
    "n v vt vi adj adv art abbr aux conj interj int neg num prep pron "
    "prefix suffix modal part det t i c u f m "
    "noun verb adjective adverb article preposition conjunction pronoun "
    "interjection exclamation numeral auxiliary determiner particle "
    "abbreviation".split()
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
# a definition.  The naver Korean-Chinese dictionary wraps the number in
# parentheses ("(1) 去 qù. …").
_NUMBERED_RE = re.compile(r"^(?:\((\d{1,2})\)|(\d{1,2})[.、)）])(?!\d)\s*(\S.*)$")

# An English example sentence: opens with a capital, ends in sentence
# punctuation, contains a space (single words like "Take." stay senses).
_EN_SENTENCE_RE = re.compile(r"""^["'‘“（(]?[A-Z][^.!?]*[.?!…]+["'’”）]*$""")

# Example markers some dictionaries prefix: bullets and "1." numbering.
_EXAMPLE_MARKER_RE = re.compile(r"^(?:[•▪●·◦‣\-–—]|\d{1,2}[.、)）])\s*")

# ---- Collins (line-based) conventions ----
#
# Collins flattens one line per sense: its source number and bracketed
# POS label, then the Chinese translation, then the English definition,
# with the examples on the lines below:
#
#     happy★★★★☆
#     1[ADJ-GRADED 能被表示程度的副词…形容词] 高兴的;快乐的；Someone who …
#     Marina was a confident, happy child...
#     玛丽娜是个自信、快乐的孩子。
#     happilyAlbert leaned back happily and lit a cigarette.
#
# The first line carries the frequency stars.  A derived word is glued
# straight onto its example ("happilyAlbert…"), and an example whose
# source line opens with slashes ("//...advice about exercise...")
# still pairs with the Chinese line under it.
_COLLINS_STARS_RE = re.compile(r"^(.+?)\s*([★☆]{3,})$")
_COLLINS_SENSE_RE = re.compile(r"^(\d{1,2})\[([^\]]+)\]\s*(.+)$")
_COLLINS_DEF_START_RE = re.compile(r"(?<=[；;。])\s*(?=[A-Z])")
_COLLINS_EN_LABELS_RE = re.compile(r"(?:\s*\[[A-Za-z][^\]]*\])+\s*$")
_COLLINS_GLUED_WORD_RE = re.compile(r"^([a-z][a-z'\u2019-]{2,})(?=[A-Z])")


# ---- Korean (Hangul) dictionary conventions ----
#
# The naver Korean-Chinese dictionary lays an entry out as the headword,
# a bracketed part of speech, then numbered senses, each sense's Korean
# example followed by its Chinese translation:
#
#     가다
#     [동사]
#     (1) 去 qù. 走 zǒu.
#     도서관에 가다
#     去图书馆
#
# The standard Korean dictionary (표준국어대사전) separates homographs
# with roman numerals, numbers its senses with a bare "N<tab>" and
# marks each usage example with a bullet:
#
#     I
#     사랑1
#     [명사]
#     1 	아끼고 위하는 정성스러운 마음, 또는 그러한 마음을 베푸는 일.
#     * 어머님의 ~.
#     ⇔ 내실1 (內室)1.

_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")

# "[명사]" / "[자동사]〖거라불규칙/가거라〗": a bracketed part of speech,
# optionally carrying the conjugation type in 〖…〗.
_KO_POS_RE = re.compile(r"^\[[가-힣]{1,8}\](?:〖[^〗]*〗)?$")

# "1 	아끼고…": the standard dictionary's bare-number sense.  The text
# after the number must bring Hangul or CJK of its own, so an English
# sentence that merely opens with a number stays a definition.
_KO_NUMBERED_RE = re.compile(
    r"^(\d{1,2})[\t\s]+(?=\S*[\uac00-\ud7af\u3400-\u9fff])(\S.*)$"
)

# "사랑1": a homograph headword -- Hangul with its homograph number.
_KO_HEADWORD_RE = re.compile(r"[\uac00-\ud7af]{1,12}\d{0,2}")

# "사랑2 【舍廊】": a homograph headword carrying its hanja form.
_KO_HANJA_HEADWORD_RE = re.compile(r"^[\uac00-\ud7af\d\s]+【[^】]+】$")

# A line made only of roman numerals: the standard dictionary's
# homograph divider ("I", "I I").
_ROMAN_LINE_RE = re.compile(r"^[IVXivx]{1,5}(?:\s+[IVXivx]{1,5})*$")

# "名词 房子": a CJK part-of-speech label of one or two characters
# leading the first sense.  The label alphabet is the same one _is_pos
# reads whole lines from; the space plus what follows is what makes
# this a prefix instead of a whole-line label.
_CJK_POS_PREFIX_RE = re.compile(r"^([名代动形副接连助词感叹]{1,2})[\s　]+(\S.*)$")


# ---- German (汉德) dictionary conventions ----
#
# The 汉德大辞典 marks a part of speech with a bracketed German label
# and follows each meaning with an example triple -- the Chinese
# sentence, its pinyin, then the German translation:
#
#     学习
#     [xue2 xi2]
#     [ Substantiv ]
#     Studium (n)
#     我在大学学习中文。
#     wo3 zai4 da4 xue2 xue2 xi2 zhong1 wen2 .
#     Ich studiere an der Uni Sinologie.

_DE_POS_RE = re.compile(
    r"^\[\s*(Substantiv|Verb|Adjektiv|Adverb|Präposition|Konjunktion|"
    r"Interjektion|Pronomen|Numeral|Numerale|Partikel)\s*\]$"
)

# A pinyin line: latin syllables with tone digits, no CJK anywhere.
_PINYIN_LINE_RE = re.compile(r"^[A-Za-z0-9\s,;:\.\?\!…\-'\(\)]+$")
_PINYIN_TONE_RE = re.compile(r"[a-z][1-5]\b")


# ---- Russian dictionary conventions ----
#
# The 俄汉汉俄 dictionary ends an entry with one long line whose
# grammatical sub-senses are numbered with circled digits -- both the
# bare ①..⑳ and the parenthesized ⑴..⒇ forms:
#
#     , -а或-у，复дом`а〔阳〕⑴房子，房屋. … ⑵住宅，住所，家. …
#
# Each circled digit opens a sense of its own; what leads the run is
# the word's grammar (case endings, stress), not a meaning.
_CIRCLED_RE = re.compile(r"([①-⑳⑴-⒇])")
_CIRCLED_BASES = (0x2460, 0x2474)


def _circled_number(marker):
    "The digit a circled marker stands for, or None."
    code = ord(marker)
    for base in _CIRCLED_BASES:
        if base <= code < base + 20:
            return code - base + 1
    return None


def _split_circled_senses(line):
    "A circled-number run as (leading grammar note, [(n, text), ...])."
    pieces = _CIRCLED_RE.split(line)
    head = pieces[0].strip(" ,;")
    senses = []
    for marker, text in zip(pieces[1::2], pieces[2::2]):
        text = text.strip()
        if text:
            senses.append((_circled_number(marker), text))
    return head, senses


def _collins_sense(bracket, body):
    "Collins' bracketed POS plus its 'translation；definition' body."
    pos_text = bracket.split()[0].strip()
    body = body.strip()
    match = _COLLINS_DEF_START_RE.search(body)
    if match is None:
        return pos_text, body, ""
    translation = body[: match.start()].strip().rstrip("；;。 ").strip()
    definition = _COLLINS_EN_LABELS_RE.sub("", body[match.start() :]).strip()
    return pos_text, translation or body, definition


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

# Two more in-text runs the Oxford conversion leaves inline: a
# cross-reference ("➡ compare conscious") and a derived word
# ("▸ sub·con·scious·ly ... adverb").  They belong to the word, not to
# the sense they were glued onto, so they become muted notes.
_COMPARE_MARK = "\U000027a1"  # ➡
_DERIVED_MARK = "\U000025b8"  # ▸

# ● opens an idiom or fixed phrase -- a sub-entry of its own, with its
# own definition and translation, not a continuation of the sense the
# conversion glued it onto.
_IDIOM_MARK = "\U000025cf"  # ●

_MARK_RE = re.compile(f"([{_KEY_MARK}{_GUIDE_MARK}{_EXAMPLE_MARK}{_IDIOM_MARK}])")
_MARK_KINDS = {
    _KEY_MARK: "key",
    _GUIDE_MARK: "guide",
    _EXAMPLE_MARK: "example",
    _IDIOM_MARK: "key",
}

# Where a bilingual line's translation starts: a Chinese character, or
# the wide opening punctuation a translation may open with (（、《).
# A closing bracket is never a start -- "（…）" inside an untranslated
# definition must not hand the rest of the definition to the gloss.
_ZH_START_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff（《「『“]")

# Where a Chinese stretch ends, so English after it starts something
# new: a Chinese character, a closing bracket or a sentence mark.
_ZH_END_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3001-\u303f\uff01-\uff60]")

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
_HEADWORD_POS_WORDS = (
    r"indefinite article|definite article|modal verb|auxiliary verb|"
    r"adjective|adverb|preposition|pronoun|conjunction|exclamation|"
    r"determiner|noun|verb|number"
)
_HEADWORD_POS_RE = re.compile(rf"\b(?:{_HEADWORD_POS_WORDS})")
# A second label of the same entry follows at once: "adverb, preposition"
# (the source glues the next word on: "prepositionon or onto").
_HEADWORD_POS_MORE_RE = re.compile(rf"[,/]\s*(?:{_HEADWORD_POS_WORDS})")

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


_ARROW_RE = re.compile(f"[{_COMPARE_MARK}{_DERIVED_MARK}]")
_ARROW_LABEL_RE = re.compile(r"\s*(?:BrE|NAmE|AmE|Br|Am|US|UK)\s*")
_ARROW_PHONETIC_RE = re.compile(r"\s*(?:/[^/]+/|\[[^\]]+\])")

# A thesaurus pointer and a trailing cross-reference are glued straight
# onto the Chinese translation, so they must follow one to be a note.
_ZH_BEFORE = r"(?<=[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff60])"
_SYN_RE = re.compile(_ZH_BEFORE + r"\s+SYN\s+")
_OPP_RE = re.compile(_ZH_BEFORE + r"\s+OPP\s+")
_SEE_ALSO_RE = re.compile(_ZH_BEFORE + r"\s*See also\s+(.+)$")

# Grammar labels the source writes after a sense or an example
# ("[I] + adv./prep.", "[C] [U]", "[V n] [V-ed]").
_TRAILING_GRAMMAR_RE = re.compile(
    r"(?:\s*\[[A-Za-z][^\]]*\]|\s*\+\s*(?:adv|prep|n|v|adj|sing|pl|det)[./\w]*)+\s*$"
)

# Oxford's collocation and topic boxes ("Cooking 烹饪；烹调", "Music
# 音乐") trail the entry's last example.  They are apparatus about the
# word field, not the example's translation, so an example's text ends
# where one opens.
_BOX_HEADING_RE = re.compile(
    _ZH_BEFORE
    + r"\s*(?:"
    + r"[A-Z][a-z]+(?:\s+[A-Za-z][\w'\u2019\-]*){0,5}\s*"
    + r"(?=[\u3400-\u4dbf\u4e00-\u9fff])"
    + r"|[a-z]{2,}[A-Z][a-z]+"  # "defineDefining terms", "contrastHighlighting"
    + r")"
)


def _condense_arrow_note(note):
    "A derived word without its pronunciation: 'sub·con·scious·ly adverb'."
    note = _ARROW_LABEL_RE.sub(" ", note)
    note = _ARROW_PHONETIC_RE.sub(" ", note)
    return " ".join(note.split())


def _is_vocabulary_list(text):
    """
    True for a long pure-English run of comma-separated short items.

    The Oxford source appends spelling and vocabulary lists to the end
    of entries ("Suffixes -able, -age, …", "Masculine, feminine and
    ungendered form …", "Silent letters gnarled, gnash, …").  They are
    appendix material about the word field, not this entry's meaning.
    Prose can carry commas too, so the items must also be short.
    """
    if len(text) < 60 or _has_cjk_or_kana(text):
        return False
    if text.endswith((".", "!", "?")) or re.search(r"\.\s+[A-Z]", text):
        return False  # sentences, not a list
    if re.search(r"['\"\u2018\u201c\u201d]", text):
        return False  # quoted examples belong to a usage note
    items = [item.strip() for item in text.split(",")]
    return len(items) >= 6 and len(text) / len(items) <= 40


def _split_usage_notes(text, kind=None):
    """
    One line as (its meaning, the runs trailing it as muted notes).

    The Oxford conversion glues a cross-reference and the derived words
    onto the sense they belong to ("... 潜意识 ➡ compare conscious ▸
    subconsciously adverb"), so without this they read as part of the
    translation.

    Three more runs are attached the same way: the thesaurus and antonym
    pointers ("... 离开 SYN leave", "... 开着 OPP closed") and the
    cross-reference the entry ends with ("餐券每张 50 英镑。See also
    throw").  All are about the word, not its meaning, so they come off
    the translation.

    An example may also carry the topic box that follows it in the
    source ("... 烤蛋糕。Cooking 烹饪；烹调Preparing 准备 ..."); that is
    apparatus for the word field, so it is cut, not noted.
    """
    pieces = _ARROW_RE.split(text)
    notes = [_condense_arrow_note(p.strip()) for p in pieces[1:]]
    text = pieces[0].strip()
    if _CJK_RE.search(text):
        # Labels trail a Chinese gloss ("...受不了。 [I] + adv./prep.";
        # an English POS line "verb [T]" is all label, so it stays).
        trimmed = _TRAILING_GRAMMAR_RE.sub("", text).strip()
        if trimmed:
            text = trimmed
    for pattern, label in ((_SYN_RE, "SYN "), (_OPP_RE, "OPP ")):
        match = pattern.search(text)
        if match is not None and _CJK_RE.search(text[match.end() :]) is None:
            notes.insert(0, f"{label}{text[match.end() :].strip()}")
            text = text[: match.start()].rstrip()
    if kind == "example":
        match = _BOX_HEADING_RE.search(text)
        if match is not None:
            text = text[: match.start()].rstrip()
    match = _SEE_ALSO_RE.search(text)
    if match is not None and _CJK_RE.search(match.group(1)) is None:
        notes.insert(0, f"See also: {match.group(1).strip()}")
        text = text[: match.start()].rstrip()
    return text, [
        n for n in notes if n and not n.endswith(":") and not _is_vocabulary_list(n)
    ]


def _is_headword_line(text):
    """
    True when the line opens with its headword and then a pronunciation.

    A line-based dictionary can put the phonetic line on its own line
    ("/wɜːd/ ; NAmE /wɜːrd/"), where a pronunciation comes before the
    variety label; that line is a phonetic block, not a headword.
    """
    match = _PHONETIC_START_RE.search(text)
    return (
        match is not None and _PHONETIC_GROUP_RE.search(text[: match.start()]) is None
    )


def _headword_and_rest(text):
    """
    A headword line as (phonetic line, what follows it or '').

    Usually the entry's first sense key carries a 🔑.  Some entries
    carry none at all (only the ◆ example marks), so the headword,
    phonetics, POS and the first definition arrive as one line.  The
    definition after the POS is kept: it is the entry's first meaning,
    not a word-family tail.
    """
    match = _PHONETIC_START_RE.search(text)
    if match is not None:
        text = text[match.start() :]
    match = _HEADWORD_POS_RE.search(text)
    if match is not None:
        # A second POS label of the same entry follows the first.
        pos_end = match.end()
        while (more := _HEADWORD_POS_MORE_RE.match(text, pos_end)) is not None:
            pos_end = more.end()
        # Keep the first pronunciation run and the POS label; the other
        # forms after it ("BrE strong form /…/", "(also an …)") are
        # pronunciation variants, not headword.
        end = _phonetic_run_end(text)
        if end and end < match.start():
            labels = text[match.start() : pos_end]
            return f"{text[:end].rstrip()} {labels}", text[pos_end:].strip()
        return text[:pos_end].strip(), text[pos_end:].strip()
    # No POS label (proper nouns, interjections): the pronunciation run
    # is the headword line and everything after it is the definition.
    end = _phonetic_run_end(text)
    if end:
        return text[:end].strip(), text[end:].strip()
    return text, ""


def _phonetic_run_end(text):
    "Where a line's leading run of pronunciations and variety labels ends."
    end = 0
    for match in _SLASH_PHONETIC_RE.finditer(text):
        if end and _PHONETIC_LABEL_RE.match(text[end : match.start()]) is None:
            break
        end = match.end()
    return end


def _split_after_translation(text):
    """
    A marked run cut wherever a translation is followed by more English.

    A ◆ piece usually holds one example and its translation, but the
    conversion sometimes glued the next definition onto that
    translation:

        ◆the echo of footsteps running down the corridor沿走廊跑的脚步声回声
         the fact of an idea, event, etc. being like another… 映现；暗示…

    The sense boundary in the source was a block, not a character, so
    no marker survived it -- but the order did: a Chinese gloss is
    never followed by more English, so that English starts the next
    sense.  Without the cut, the second definition ends up inside the
    first example's translation.

    The English after the gloss must bring a gloss of its own: the
    comma-separated lists the source glues onto the last translation
    ("…馆藏书籍 author, book, classic, critic") follow one too, and
    they belong to that translation.
    """
    if _KANA_RE.search(text):
        return [text]
    segments = []
    start = 0
    for match in re.finditer(r"[A-Za-z~]", text):
        if match.start() <= start:
            continue
        before = text[start : match.start()].rstrip()
        if not before:
            continue
        cut = match.start()
        if match.group(0) == "~":
            # Oxford writes the headword slot itself as "~", and a sense
            # pattern opens with it: "... 秋装。~ (with sth) full of
            # strong emotion ...".  A tilde right after a Chinese gloss
            # is the next sense, not a continuation of the translation.
            if _ZH_END_RE.match(before[-1]) is None:
                continue
        elif before[-1] in "([":
            # A definition that opens with a label -- "(also abˈductor
            # muscle) (anatomy …)" or "[usually singular] if a film …".
            # The bracket belongs to the new definition, so the cut
            # goes before it.
            if len(before) < 2 or _ZH_END_RE.match(before[:-1].rstrip()[-1:]) is None:
                continue
            cut = match.start() - 1
        elif _ZH_END_RE.match(before[-1]) is None:
            continue
        tail = text[cut:]
        # A sense may open with a labelled bracket ("(of behaviour 行为)
        # dishonest or immoral …"), so the label is transparent: the
        # definition starts after it.
        unlabelled = re.sub(r"^(?:\([^)]*\)\s*)+", "", tail)
        # The new definition must bring real Chinese text of its own,
        # and enough English to be a definition: a word list after a
        # gloss ("Silent letters gnarled, gnash, …") is another word
        # study, and a Chinese sentence with English terms inside it
        # ("about还常用在walk, drive等…") is still one gloss.
        if _CJK_RE.search(unlabelled) is None:
            continue
        first_zh = _ZH_START_RE.search(unlabelled)
        if first_zh is None:
            continue
        leading = unlabelled[: first_zh.start()]
        if len(re.findall(r"[A-Za-z]{2,}", leading)) < 3:
            continue
        if re.search(
            r"[\u3000-\u303f\uff01-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65]",
            leading,
        ):
            # English inside a Chinese sentence ("mess about，set about
            # 等短语动词"), not the English definition of a new sense.
            continue
        segments.append(text[start:cut])
        start = cut
    segments.append(text[start:])
    return [s.strip() for s in segments if s.strip()]


# The Oxford conversion glues a word's inflection table between the part
# of speech and the first sense:
#
#     verbpresent simple - I / you / we / they abase BrE /əˈbeɪs/ NAmE
#     /əˈbeɪs/present simple - he / she / it abases BrE /…/ … -ing form
#     abasing BrE /əˈbeɪsɪŋ/ NAmE /əˈbeɪsɪŋ/~ yourself (formal) to act …
#
# The rows are not this entry's meaning, they are longer than the sense
# they precede, and reading them as a sense is what buried the card.
_INFLECTION_START_RE = re.compile(
    r"(?:present simple|past simple|past participle|present participle|"
    r"-ing form|3rd person singular|third person singular)"
)
# A noun's plural form comes as its own parenthetical: "noun (plural
# ab·cuses BrE /…/ NAmE /…/ )".  Only the parenthetical, and only when
# the plural form is what it opens with -- "suffix (in plural nouns …)"
# is part of the suffix's gloss.
_INFLECTION_PAREN_RE = re.compile(r"\((?:feminine\s+[^,()]+,\s*)?plural\b[^()]*\)")
# The table's last row is "-ing form <form> BrE /…/ NAmE /…/"; the NAmE
# group is where the table stops and the definition starts.
_TABLE_END_RE = re.compile(r"NAmE\s*/[^/]+/")


# The Oxford conversion also glues the HELP usage note between the POS
# and the first sense ("... verb HELP In sense 2 abode … is also used
# for the past tense. … [T] cannot/could not abide …").  It is guidance
# about the word, not the word's meaning, and it runs until the next
# sense opens ("[T]", "~ sth", a marker) or the entry ends.
_HELP_RE = re.compile(r"\s*HELP\b.*?(?=\s*(?:\[[A-Za-z]|~|◆|▸|➡|●|SYN\b)|$)", re.S)


def _strip_help_note(text):
    "One line without the HELP usage note glued to it."
    return " ".join(_HELP_RE.sub(" ", text).split())


def _strip_inflection_table(text):
    """
    One line without the inflection table glued to its part of speech.

    A table cut short by the converter's length cap has no closing
    "-ing form" row and runs to the end of the line, which is where its
    definition would have been anyway.
    """
    if _KANA_RE.search(text):
        return text
    text = _INFLECTION_PAREN_RE.sub(" ", text)
    match = _INFLECTION_START_RE.search(text)
    if match is None:
        # A prose mention ("used with the present simple") has no
        # pronunciations after it; a table's rows always do.
        return text
    if not _PHONETIC_START_RE.search(text[match.end() : match.end() + 80]):
        return text
    last_ing = text.rfind("-ing form")
    if last_ing < match.start():
        end = len(text)
    else:
        end_match = _TABLE_END_RE.search(text, last_ing)
        end = end_match.end() if end_match is not None else len(text)
    return f"{text[: match.start()].rstrip()} {text[end:].lstrip()}".strip()


def _lines_and_hints(gloss):
    """
    The gloss as (lines, hints) -- hints are None for every line of an
    ordinary, line-based gloss, so the rules below read it unchanged.

    Every gloss goes through the same loop.  A marked one is split on
    its markers first: its single line becomes the lines the same rules
    already understand, each tagged with what its marker said it was.
    An ordinary line-based gloss arrives as one piece whose lines keep
    the None hint, so it reads exactly as before.

    Two of the marked entry's parts run on until the next sense and are
    dropped: the synonym study (◇) and the HELP usage note.  Both are
    prose *about* the word -- a list of neighbours, or when to use a
    rather than an -- and showing them as senses or examples is what
    made these entries unreadable.

    The ➡/▸ runs come off every line and become notes: they are what
    the *entry* attaches to a sense ("compare conscious", the derived
    words), and leaving them in is what put a "compare ..." tail inside
    a translation.
    """
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
                r"[A-Za-z][A-Za-z .,'/\u2019\u00b7-]*$",
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
            text = _strip_inflection_table(text)
            if kind is None:
                # A marked key already handles HELP by dropping the run
                # (see below); an unmarked entry has no such cut.
                text = _strip_help_note(text)
            text, arrow_notes = _split_usage_notes(text, kind)
            for index, segment in enumerate(_split_after_translation(text)):
                # The marker names the first thing after it; anything
                # that follows a translation on that same run is the
                # next definition (see _split_after_translation), i.e.
                # a sense key.
                segment_kind = kind if index == 0 else "key"
                if segment_kind == "key" and _is_headword_line(segment):
                    head, tail = _headword_and_rest(segment)
                    out.append(("headword", head))
                    if "HELP" in tail:
                        tail = _strip_help_note(tail)
                    if tail:
                        if len(tail) <= _HEADWORD_TAIL_MAX_CHARS:
                            out.append(("note", tail))
                        else:
                            # The rest of the key is the entry's first
                            # definition, not a word-family tail.
                            out.append(("key", tail))
                    if "HELP" in segment:
                        dropped = True
                    continue
                if segment_kind is None and _is_headword_line(segment):
                    # An unmarked entry's opening line: the headword, its
                    # phonetics, the POS and the first definition, all in
                    # one run ("sub·con·sciousBrE /…/ adjective [usually
                    # before noun] connected with…").  Only the phonetic
                    # lead-up is the headword line; the definition after
                    # the POS is the entry's first sense, not a tail.
                    head, rest = _headword_and_rest(segment)
                    out.append(("headword", head))
                    if rest:
                        # Marked like a key so the bilingual split below
                        # still lifts the translation off the definition.
                        out.append(("key", rest))
                    continue
                if _is_vocabulary_list(segment):
                    continue
                out.append((segment_kind, segment))
            for note in arrow_notes:
                out.append(("note", note))
    return [text for _, text in out], [kind for kind, _ in out]


def _paren_depth(text, position):
    "ASCII parenthesis depth in text[:position]."
    depth = 0
    for char in text[:position]:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
    return depth


# The conversion caps an entry at 800 characters and some source tables
# (spelling lists, "Exclamations aargh, ah, …") sit right behind a sense,
# so a translation can end with a long run of English that is not part
# of it.  This much English after a finished Chinese sentence, with no
# Chinese after it, is that other material.
_TRAILING_ENGLISH_MIN_CHARS = 20

# A spelling or vocabulary list is a run of comma-separated short items
# ("arid, climate, continental climate, equatorial, …").  No translation
# ends that way; the list is a source appendix glued to it.
_TRAILING_COMMA_LIST_RE = re.compile(r"^(?:[A-Za-z][A-Za-z'\u2019\-]*\s*,?\s*){3,}")


def _is_appendix_list(tail):
    "True for a glued spelling/vocabulary list, not a translated phrase."
    return (
        _TRAILING_COMMA_LIST_RE.match(tail) is not None
        and tail.count(",") >= 3
        and _CJK_RE.search(tail) is None
    )


def _trim_trailing_english(translation):
    "A translation without the English run glued after its last sentence."
    for match in re.finditer(r"[A-Za-z]", translation):
        head = translation[: match.start()].rstrip()
        if head and head[-1] in "([":
            # The English run opens with a label ("[I] + adv./prep.").
            head = head[:-1].rstrip()
        if not head or _ZH_END_RE.match(head[-1]) is None:
            continue
        tail = translation[match.start() :]
        if _is_appendix_list(tail):
            return head
        if len(tail) >= _TRAILING_ENGLISH_MIN_CHARS and _CJK_RE.search(tail) is None:
            return head
    return translation


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
    if match is not None and _paren_depth(text, match.start()):
        # 解剖学 inside "(anatomy 解剖学)" translates the label, not the
        # sense; the translation proper starts at the first Chinese run
        # outside an ASCII parenthetical.
        for candidate in _ZH_START_RE.finditer(text, match.end()):
            if _paren_depth(text, candidate.start()) == 0:
                match = candidate
                break
        else:
            return text, None
    if match is None or match.start() == 0:
        return text, None
    translation = _TRAILING_WORD_LIST_RE.sub("", text[match.start() :]).strip()
    return text[: match.start()].strip(), _trim_trailing_english(translation)


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

        match = _COLLINS_STARS_RE.match(line)
        if match is not None and not blocks:
            # Collins opens with the headword and its frequency stars;
            # the stars are a rating, not part of the word.
            blocks.append({"t": "headword", "text": match.group(1).strip()})
            i += 1
            continue

        match = _COLLINS_SENSE_RE.match(line)
        if match is not None:
            pos_text, translation, definition = _collins_sense(
                match.group(2), match.group(3)
            )
            if pos_text and not (
                blocks and blocks[-1]["t"] == "pos" and blocks[-1]["text"] == pos_text
            ):
                blocks.append({"t": "pos", "text": pos_text})
            sense_no = int(match.group(1))
            block = {"t": "sense", "text": translation, "n": sense_no}
            if definition:
                block["en"] = definition
            blocks.append(block)
            pending_example = None
            i += 1
            continue

        if (
            blocks
            and blocks[-1]["t"] == "example"
            and not _has_cjk_or_kana(line)
            and (match := _COLLINS_GLUED_WORD_RE.match(line)) is not None
        ):
            # "happilyAlbert leaned back…": a derived word was glued to
            # the example that shows it.
            blocks.append({"t": "note", "text": match.group(1)})
            line = line[match.end() :]

        if line.startswith("//"):
            block = {"t": "example", "text": line.lstrip("/").strip()}
            blocks.append(block)
            pending_example = block
            i += 1
            continue

        if _KO_POS_RE.match(line):
            # "[명사]" / "[자동사]〖거라불규칙/가거라〗": the standard
            # Korean dictionary's part of speech.  Before the phonetic
            # check, which would read the brackets as pronunciation.
            blocks.append({"t": "pos", "text": line})
            sense_no = 0
            pending_example = None
            i += 1
            continue

        if _DE_POS_RE.match(line):
            # "[ Substantiv ]" / "[ Verb ]": the 汉德大辞典's part of
            # speech; the brackets are labels, not pronunciation.
            blocks.append({"t": "pos", "text": line.strip("[] ").strip()})
            sense_no = 0
            pending_example = None
            i += 1
            continue

        if _ROMAN_LINE_RE.match(line):
            # 표준국어대사전 separates homographs with roman numerals
            # ("I", "I I"): a divider, not content.  Before the part-of-
            # speech check, whose English alphabet reads a bare "I".
            blocks.append({"t": "section", "text": line})
            sense_no = 0
            pending_example = None
            i += 1
            continue

        if line.startswith("⇔"):
            # "⇔ 오다1 1." -- the standard dictionary's antonym/analogue
            # pointer into the same dictionary.  The trailing number
            # ("오다1 1.") names the target's sense, not the word.
            target = re.sub(r"\s+\d+\.$", "", line[1:].strip())
            blocks.append({"t": "xref", "text": target, "reading": None})
            pending_example = None
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
        elif (
            _HANGUL_RE.search(line)
            and not _CJK_RE.search(line)
            and _KO_HEADWORD_RE.fullmatch(line) is not None
        ):
            # "사랑1": a homograph headword of the standard dictionary.
            blocks.append({"t": "headword", "text": line})
            pending_example = None
            i += 1
            continue
        elif _KO_HANJA_HEADWORD_RE.match(line):
            # "사랑2 【舍廊】": the homograph headword and its hanja.
            blocks.append({"t": "headword", "text": line})
            pending_example = None
            i += 1
            continue
        elif _KO_NUMBERED_RE.match(line):
            match = _KO_NUMBERED_RE.match(line)
            sense_no = int(match.group(1))
            blocks.append({"t": "sense", "text": match.group(2).strip(), "n": sense_no})
            pending_example = None
            i += 1
            continue
        elif line.startswith("*") and _HANGUL_RE.search(line):
            # 표준국어대사전's usage examples: consecutive bullets, no
            # translation attached to any of them.
            block = {"t": "example", "text": line.lstrip("*").strip()}
            blocks.append(block)
            pending_example = None
            i += 1
            continue
        elif (
            _HANGUL_RE.search(line)
            and not _CJK_RE.search(line)
            and blocks
            and blocks[-1]["t"] == "sense"
            and _CJK_RE.search(blocks[-1]["text"])
        ):
            # A Hangul-only line right after a Chinese sense is the
            # naver dictionary's example; the Chinese line under it is
            # its translation (pending below).  The standard Korean
            # dictionary's Hangul *definitions* follow a POS badge, not
            # a sense, so they stay definitions.
            block = {"t": "example", "text": line}
            blocks.append(block)
            pending_example = block
            i += 1
            continue
        elif (
            pending_example is None
            and blocks
            and blocks[-1]["t"] in ("sense", "pos")
            and _CJK_RE.search(line)
            and line.endswith(("。", "！", "？"))
        ):
            # 汉德: a Chinese sentence after a German meaning opens the
            # entry's example triple; the pinyin line follows, then the
            # German translation (pending below).  Translations arriving
            # while an example is pending are untouched -- they are the
            # naver dictionary's shape, not this one.
            block = {"t": "example", "text": line}
            blocks.append(block)
            pending_example = block
            i += 1
            continue
        elif (
            pending_example is not None
            and _PINYIN_LINE_RE.match(line) is not None
            and _PINYIN_TONE_RE.search(line) is not None
        ):
            # The example's pinyin: a muted line of its own.  The
            # example stays pending -- the German translation is the
            # line that actually follows it.
            blocks.append({"t": "note", "text": line})
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
        elif _CIRCLED_RE.search(line) and len(line) > 24:
            # The 俄汉汉俄 dictionary's grammatical run: case endings
            # and stress lead, then each circled digit opens a sense of
            # its own.  Without the split the whole line is one 800-
            # character sense.
            head, senses = _split_circled_senses(line)
            if head:
                blocks.append({"t": "note", "text": head})
            for n, text in senses:
                sense_no = n
                blocks.append({"t": "sense", "text": text, "n": n})
            pending_example = None
            i += 1
            continue
        elif (
            len(line) > _NOTE_MIN_CHARS
            and "（" not in line
            and "(" not in line
            and _CJK_RE.search(line)
        ):
            blocks.append({"t": "note", "text": line})
            pending_example = None
            i += 1
            continue

        # A numbered sense the dictionary itself numbered: keep its
        # number; the auto-numbering continues from it.
        match = _NUMBERED_RE.match(line)
        if match is not None:
            sense_no = int(match.group(1) or match.group(2))
            blocks.append({"t": "sense", "text": match.group(3), "n": sense_no})
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

        # "名词 房子": a CJK part-of-speech label leading the sense --
        # the 俄汉汉俄 dictionary's way of marking a noun's first
        # translation.
        match = _CJK_POS_PREFIX_RE.match(line)
        if match is not None:
            blocks.append({"t": "pos", "text": match.group(1)})
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

    The line must actually carry CJK: a short latin line before a
    Chinese sentence ("Studium (n)" in the 汉德大辞典) is that
    dictionary's meaning, not an example.
    """
    return (
        _CJK_RE.search(line) is not None
        and following is not None
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
            lines.append(_xref_line(b))
        elif b["t"] in ("pos", "sense"):
            lines.append(b["text"])
    if not lines:
        lines = [ln.strip() for ln in gloss.split("\n") if ln.strip()]
    return _take_within(lines, max_chars)


def _xref_line(b):
    'An xref block as the plain "See also: X【reading】" line.'
    label = f"{b['text']}【{b['reading']}】" if b.get("reading") else b["text"]
    return f"See also: {label}"


def popup_gloss(gloss):
    """
    The entry as the term popup shows it: one display line per block.

    The popup is a glance, not a reading pane -- read/service caps this
    further -- so the chrome a full card carries is dropped: the
    headword line, the phonetics and the section labels (the popup
    already names the term above them), and the notes, which are word
    families and cross-references rather than meanings.  What is left
    is the part of speech, the senses and the examples that come with
    them.

    A marked entry reaches here as one line of raw text with 🔑/➤/◆/🔊
    still in it (see _lines_and_hints), which is what used to make the
    popup an unreadable wall: parse_gloss has already taken the markers
    out and cut the entry into blocks, so all this does is lay them
    back down, one per line.
    """
    if not gloss:
        return gloss
    lines = []
    for b in parse_gloss(gloss):
        if b["t"] in ("pos", "sense"):
            lines.append(b["text"])
        elif b["t"] == "example":
            lines.append(b["text"])
            if b.get("translation"):
                lines.append(b["translation"])
        elif b["t"] == "xref":
            lines.append(_xref_line(b))
    return "\n".join(lines) if lines else gloss


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
