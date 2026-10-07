"""
Manga block tokenization across OCR column breaks.

A mokuro block stores its text as a list of physical rows -- for a
vertical (right-to-left) block those are the columns read right to
left, for a horizontal block the rows top to bottom.  OCR cuts those
rows wherever the text ran out of room in the speech balloon, which
routinely falls *inside* a word:

    {"vertical": true, "lines": ["プレゼン", "トです。"]}

Tokenizing each row on its own therefore yields "プレゼン" + "ト" as
two independent words, neither of which matches the real term
"プレゼント" in the database.  The reader then hovers a word whose
lookup comes back empty, so no card appears at all, and TTS pronounces
half a word.

The fix is to tokenize the block's rows as the single run of text they
actually are, then cut the resulting TextItems back at the row
boundaries for rendering.  A word that does straddle a boundary is
emitted as one TextItem per row it touches, all of them pointing at the
*same* Term, so clicking or hovering either half shows the whole word.

Cutting is done by character offset, so it depends on the tokenized
text lining up with the text that was fed in.  It does not always: the
renderer collapses runs of spaces, and some parsers normalize or drop
characters.  `tokenize_block` therefore checks the two against each
other and falls back to the old per-row tokenization when they differ,
so a parser quirk degrades to today's behaviour instead of silently
shifting every word on the page.
"""

import re

from lute.read.render.text_item import TextItem

# Row separators.  A mokuro "line" can hold several physical rows joined
# by newlines or the "¶" paragraph marker.
_ROW_SPLIT = re.compile(r"[¶\r\n]+")

# Parser types whose words are space-delimited, so rows have to be
# rejoined with a space or the words either side of the break fuse into
# one ("helloworld").
_SPACED_PARSERS = ("spacedel", "turkish")


def row_texts(block):
    """
    The block's physical rows, in reading order, blanks dropped.
    """
    rows = []
    for line in block.get("lines") or []:
        for phys in _ROW_SPLIT.split(line):
            if phys.strip():
                rows.append(phys)
    return rows


def _join_rows(rows, lang):
    """
    The rows as one string, plus each row's [start, end) offsets.

    CJK rows join with nothing between them -- that is exactly how the
    word got split in the first place.  Space-delimited languages join
    with a single space, recorded as its own span so the separator can
    never be rendered as text.
    """
    spaced = lang.parser_type in _SPACED_PARSERS
    joined = ""
    spans = []
    for row in rows:
        if joined and spaced:
            joined += " "
            spans.append((len(joined) - 1, len(joined)))
        start = len(joined)
        joined += row
        spans.append((start, len(joined)))
    return joined, spans


def _rendered_text(items):
    """
    What the browser will actually show for these items, concatenated.

    The renderer guarantees each token is displayed by exactly one item
    (see calculate_textitems.get_textitems), so in reading order the
    items' display texts reassemble the tokenized string.  That equality
    is what makes character-offset cutting safe, and it is checked
    rather than assumed.
    """
    return "".join(ti.html_display_text for ti in items)


def _spans_of(items):
    """
    Each item's [start, end) offset in the tokenized string.
    """
    spans = []
    pos = 0
    for ti in items:
        n = len(ti.html_display_text)
        spans.append((pos, pos + n))
        pos += n
    return spans


def _make_fragment(ti, text, lang):
    """
    A rendering-only TextItem showing part of a word.

    Keeps the original Term, so the popup, the status colour and the
    click-through all address the whole word; presents just its own
    characters.  Token bookkeeping is reset: the fragment is one
    indivisible piece of text as far as the browser is concerned.
    """
    frag = TextItem()
    frag.text = text
    frag.text_lc = lang.get_lowercase(text)
    frag.is_word = ti.is_word
    frag.token_count = 1
    frag.display_count = 1
    # display_text == text for a fragment, so it never picks up the
    # "overlapped" marker that a partially-shown term gets.
    frag.term = ti.term
    frag.is_split_piece = True
    return frag


def _cut_at_rows(items, row_spans, joined, lang):
    """
    Cut tokenized items at the row boundaries.

    Returns a list parallel to `row_spans`: the items to render for each
    row.  An item inside a single row is passed through untouched; one
    straddling a boundary becomes an item per row, all sharing the
    original Term.
    """
    out = [[] for _ in row_spans]
    for ti, (start, end) in zip(items, _spans_of(items)):
        if end <= start:
            continue
        touched = [
            (i, max(start, rstart), min(end, rend))
            for i, (rstart, rend) in enumerate(row_spans)
            if start < rend and end > rstart and rend > rstart
        ]
        if not touched:
            # Entirely inside an inserted separator: nothing to show.
            continue
        if len(touched) == 1 and touched[0][1] == start and touched[0][2] == end:
            out[touched[0][0]].append(ti)
            continue
        for i, piece_start, piece_end in touched:
            piece = joined[piece_start:piece_end]
            if piece:
                out[i].append(_make_fragment(ti, piece, lang))
    return out


def _tokenize_rows_separately(rs, rows, lang, mw):
    """
    Today's behaviour: one tokenization per row, ignoring the splits.

    Kept as the fallback for when the joined tokenization can't be cut
    safely, and as the reference the tests compare against.
    """
    out = []
    for row in rows:
        items = [ti for ti in rs.get_textitems(row, lang, mw) if ti.text != "¶"]
        out.append(items)
    return out


def tokenize_block(rs, block, lang, mw):
    """
    The TextItems of one manga block, grouped by the row to render them in.

    Always returns one list per row of the block, so the caller can zip
    the result against `row_texts(block)` without checking lengths.
    """
    rows = row_texts(block)
    if not rows:
        return []

    joined, row_spans = _join_rows(rows, lang)
    items = [ti for ti in rs.get_textitems(joined, lang, mw) if ti.text != "¶"]

    # The cut is by character offset, so it is only valid while the
    # tokenized text still matches the input.  `get_textitems` collapses
    # runs of spaces, and some parsers normalize or drop characters, so
    # verify instead of trusting: a mismatch here means every word on the
    # page would land on the wrong characters.
    if _rendered_text(items) == _renderer_input(joined):
        return _cut_at_rows(items, row_spans, joined, lang)

    return _tokenize_rows_separately(rs, rows, lang, mw)


def _renderer_input(joined):
    """
    The string the renderer will actually hand to the parser.

    Mirrors RenderService.get_textitems, which collapses runs of spaces
    before parsing.
    """
    return re.sub(r" +", " ", joined)
