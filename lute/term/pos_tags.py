"""
Turn a parser's part-of-speech tuple into a term tag.

Pure logic: no database, no session, no parser instance -- a POS tuple
in, a string out -- so the rules can be unit tested without a sudachi
dictionary installed.  Same shape as lute/term/lemma_parents.py.

Two different strings come out of one POS tuple
-----------------------------------------------

The *stored* form (words.WoPos) keeps every slot, joined with "|":

    動詞|非自立可能|*|*|五段-サ行|意志推量形

It is stored verbatim rather than reduced to a tag so that the tag
format can be changed later without re-parsing a single book.

The *tag* form drops the empty slots and joins what is left with "/":

    動詞/非自立可能/五段-サ行/意志推量形

Why the whole tuple becomes one tag
-----------------------------------

Measured over the repo's own JLPT grammar corpus (4007 sentences,
45896 tokens, Sudachi core / mode C), after dropping particles,
auxiliaries and affixes:

    pos1 only .......................... 9 distinct tags
    pos1 + 活用型 ...................... 45
    the whole tuple ................... 279

The whole tuple is what was asked for (2026-10-16).  It is also why
tags.TgText had to be widened: 動詞/非自立可能/文語四段-ラ行/連体形-一般
is 23 characters, past the VARCHAR(20) the column used to declare.  See
migration 20261016_add_term_pos.sql.
"""

SEPARATOR = "/"
POS_JOIN = "|"
EMPTY_SLOT = "*"


def pos_to_text(pos):
    """
    The stored form of a parser POS tuple, or None if there is nothing
    to store.

    Sudachi hands back a 6-tuple: (pos1, pos2, pos3, pos4, pos5, pos6),
    e.g. ('動詞', '非自立可能', '*', '*', '五段-サ行', '意志推量形').
    Slots are kept as-is, including the "*" ones -- only the caller
    decides which of them mean anything.
    """
    if not pos:
        return None
    slots = [str(p) for p in pos]
    if not any(s and s != EMPTY_SLOT for s in slots):
        return None
    return POS_JOIN.join(slots)


def text_to_pos(text):
    """
    The POS tuple back out of its stored form; () when there is none.

    Returns a tuple rather than a list so it can be compared against
    what the parser produced.
    """
    if not text:
        return ()
    return tuple(text.split(POS_JOIN))


def tag_from_pos(pos):
    """
    The tag text for a parser POS tuple, or None when the tuple carries
    no information at all.

    Empty slots are dropped, so ('名詞', '普通名詞', '一般', '*', '*', '*')
    becomes 名詞/普通名詞/一般 rather than carrying three empty
    segments into the tag.
    """
    if not pos:
        return None
    parts = [str(p) for p in pos if p and p != EMPTY_SLOT]
    if not parts:
        return None
    return SEPARATOR.join(parts)
