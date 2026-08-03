"""
Japanese SRT subtitle cue refinement.

When importing Japanese SRT subtitles, the original cue segmentation is
often mid-sentence -- subtitles are split for display length, not
linguistic boundaries.  This module merges cues that belong to the
same sentence and splits cues that became too long after merging, so
each cue roughly corresponds to one Japanese sentence.

Rules (applied only when the book language is Japanese):

Merge adjacent cues when:
  - the previous cue ends with a continuative particle (接続助詞) or
    attributive modifier (連体詞) -> force merge, regardless of gap;
  - otherwise, when the gap between them is < 400ms AND the previous
    cue does not end with a strong terminator (。！？) or a final
    particle (終助詞).

Split a merged cue whose duration > 8s, in priority order:
  1. strong terminator 。！？ -- always split;
  2. spoken final particle (終助詞) -- split into shorter sentences;
  3. comma 「、」 -- split when both sides have >= 6 characters and each
     resulting segment is >= 2.5s;
  4. long continuative word (接続詞) -- auxiliary split before the word.
"""

import re

# Strong sentence terminators (full-width and half-width).
_STRONG_TERMINATORS = "。！？!?…"

# Final particles (終助詞).  Ordered longest-first so the compiled
# regex prefers the longest match at a given position.  Combined
# forms (でしょうか, だろうか, ...) are included so that adjacent
# particle matches (でしょう + か) don't get split into a stray
# single-character segment.
_FINAL_PARTICLES = (
    "でしょうか", "だろうか", "でしょうね", "だろうね",
    "でしょうよ", "だろうよ",
    "かしら", "だよね", "だろう", "でしょう", "だな", "だね", "だよ",
    "よね", "なあ", "ねえ", "かい", "だい", "のう", "かな",
    "か", "よ", "ね", "な", "さ", "わ", "ぞ", "ぜ", "や",
)

# Continuative particles (接続助詞) -- indicate the sentence continues
# into the next cue, so force a merge.
_CONTINUATIVE_PARTICLES = (
    "けれども", "けれど", "ながら", "つつも", "ものの", "くせに",
    "ので", "のに", "から", "たり", "だり", "ても", "でも", "つつ",
    "なり", "なら", "ば", "と", "し", "て", "で", "が",
)

# Attributive modifiers (連体詞) -- always modify a following noun,
# so a cue ending with one must continue into the next.
_RENTAI_WORDS = (
    "この", "その", "あの", "どの", "こんな", "そんな", "あんな", "どんな",
    "あらゆる", "いわゆる", "きたる", "かかる",
)

# Long continuative words (接続詞) for level-4 splitting.  Split
# *before* these so they begin the new segment.
_LONG_CONJUNCTIVES = (
    "けれども", "しかし", "それで", "だから", "それから", "ところが",
    "すなわち", "つまり", "ただし", "なお", "ゆえに",
    "さらに", "しかも", "ならびに", "および", "または", "もしくは",
    "そのうえ", "おまけに", "だが", "だって", "それと", "そして",
    "それとも", "あるいは", "なぜなら", "というのは",
)

_MAX_DURATION = 8.0          # split cues longer than this (seconds)
_MIN_SEGMENT_DURATION = 2.5  # comma-split segments must be at least this
_MIN_COMMA_SIDE_CHARS = 6    # both sides of a comma split need this many chars


def _compile_word_matcher(words):
    """Compile a regex that matches any of the given words (longest first)."""
    sorted_words = sorted(set(words), key=len, reverse=True)
    return re.compile("|".join(re.escape(w) for w in sorted_words))


_FINAL_PARTICLE_RE = _compile_word_matcher(_FINAL_PARTICLES)
_LONG_CONJUNCTIVE_RE = _compile_word_matcher(_LONG_CONJUNCTIVES)


def _clean_ending(text):
    """Strip trailing whitespace so particle/terminator checks see the
    actual last significant character."""
    return text.strip()


def _is_hiragana(ch):
    """True if ch is a hiragana code point (U+3040..U+309F)."""
    return "\u3040" <= ch <= "\u309F"


def _ends_with_strong_terminator(text):
    s = _clean_ending(text)
    return bool(s) and s[-1] in _STRONG_TERMINATORS


def _ends_with_any(text, words):
    """True if the cleaned text ends with any of the given word strings."""
    s = _clean_ending(text)
    if not s:
        return False
    return any(s.endswith(w) for w in words)


def _ends_with_final_particle(text):
    return _ends_with_any(text, _FINAL_PARTICLES)


def _ends_with_continuative(text):
    return _ends_with_any(text, _CONTINUATIVE_PARTICLES)


def _ends_with_rentai(text):
    return _ends_with_any(text, _RENTAI_WORDS)


def _join_texts(a, b):
    """Join two cue texts.  Japanese needs no space, but if both
    boundary characters are ASCII alphanumerics, insert a space so
    English/romaji fragments don't get glued together."""
    a = a.rstrip()
    b = b.lstrip()
    if (
        a
        and b
        and a[-1].isascii()
        and a[-1].isalnum()
        and b[0].isascii()
        and b[0].isalnum()
    ):
        return a + " " + b
    return a + b


def _merge_cues(cues):
    """Merge adjacent cues per the Japanese sentence-continuation rules."""
    if not cues:
        return []
    merged = [dict(cues[0])]
    for cur in cues[1:]:
        prev = merged[-1]
        prev_text = prev["text"]
        gap = cur["start"] - prev["end"]
        if _ends_with_continuative(prev_text) or _ends_with_rentai(prev_text):
            do_merge = True
        elif _ends_with_strong_terminator(prev_text):
            do_merge = False
        elif _ends_with_final_particle(prev_text):
            do_merge = False
        else:
            do_merge = gap < 0.4
        if do_merge:
            prev["end"] = cur["end"]
            prev["text"] = _join_texts(prev_text, cur["text"])
        else:
            merged.append(dict(cur))
    return merged


def _find_split_indices(text, level):
    """Return sorted character indices where a new segment should start,
    for the given priority level."""
    indices = set()
    if level == 1:
        for i, ch in enumerate(text):
            if ch in _STRONG_TERMINATORS:
                idx = i + 1
                if 0 < idx < len(text):
                    indices.add(idx)
    elif level == 2:
        for m in _FINAL_PARTICLE_RE.finditer(text):
            idx = m.end()
            if not (0 < idx < len(text)):
                continue
            # Single-char final particles (か, よ, ね, ...) are
            # ambiguous: the same kana appears inside longer words
            # (e.g. か inside しかし).  Only treat them as a clause
            # boundary when the next character is NOT hiragana -- a
            # real new clause usually starts with a kanji/katakana
            # content word or punctuation.  Multi-char particles are
            # specific enough to trust as-is.
            if len(m.group()) == 1 and _is_hiragana(text[idx]):
                continue
            indices.add(idx)
    elif level == 3:
        for i, ch in enumerate(text):
            if ch in "、,":
                left = text[:i]
                right = text[i + 1 :]
                if (
                    len(left.strip()) >= _MIN_COMMA_SIDE_CHARS
                    and len(right.strip()) >= _MIN_COMMA_SIDE_CHARS
                ):
                    idx = i + 1
                    if 0 < idx < len(text):
                        indices.add(idx)
    elif level == 4:
        for m in _LONG_CONJUNCTIVE_RE.finditer(text):
            idx = m.start()
            if 0 < idx < len(text):
                indices.add(idx)
    return sorted(indices)


def _distribute_time(start, end, raw_texts, apply_min_duration):
    """Given a list of text segments, distribute [start, end] across them
    proportionally by character count.  When apply_min_duration is set,
    segments shorter than the minimum are merged into the previous one."""
    # Drop empty segments but remember their share of characters is 0.
    char_counts = [len(t) for t in raw_texts]
    total_chars = sum(char_counts)
    total_dur = end - start
    if total_chars == 0 or total_dur <= 0:
        return [(start, end, "".join(raw_texts))]

    segments = []
    cur_time = start
    for i, (t, n) in enumerate(zip(raw_texts, char_counts)):
        if i == len(raw_texts) - 1:
            seg_start = cur_time
            seg_end = end
        else:
            frac = n / total_chars
            seg_end = cur_time + total_dur * frac
            seg_start = cur_time
        segments.append((seg_start, seg_end, t))
        cur_time = seg_end

    if apply_min_duration:
        segments = _merge_short_segments(segments, _MIN_SEGMENT_DURATION)
    return segments


def _merge_short_segments(segments, min_duration):
    """Merge segments shorter than min_duration into the previous segment."""
    if len(segments) <= 1:
        return segments
    result = [segments[0]]
    for seg in segments[1:]:
        prev = result[-1]
        if (seg[1] - seg[0]) < min_duration:
            result[-1] = (prev[0], seg[1], prev[2] + seg[2])
        else:
            result.append(seg)
    # If the first segment ended up too short, fold it into the next.
    if len(result) > 1 and (result[0][1] - result[0][0]) < min_duration:
        result[1] = (result[0][0], result[1][1], result[0][2] + result[1][2])
        result.pop(0)
    return result


def _split_cue(cue):
    """Split a single cue (>8s) using the priority rules.  Returns a
    list of (start, end, text) tuples."""
    text = cue["text"]
    segments = [(cue["start"], cue["end"], text)]
    # Apply split levels in priority order.  Each level only operates
    # on segments that are still longer than the max duration.
    for level in (1, 2, 3, 4):
        next_segments = []
        for (s, e, t) in segments:
            if (e - s) <= _MAX_DURATION:
                next_segments.append((s, e, t))
                continue
            parts = _split_segment_by_level(s, e, t, level)
            next_segments.extend(parts)
        segments = next_segments
    return segments


def _split_segment_by_level(start, end, text, level):
    """Split one segment using the rules of a single level."""
    indices = _find_split_indices(text, level)
    if not indices:
        return [(start, end, text)]
    boundaries = sorted(set([0] + indices + [len(text)]))
    raw_texts = [text[boundaries[i] : boundaries[i + 1]] for i in range(len(boundaries) - 1)]
    apply_min = level == 3
    return _distribute_time(start, end, raw_texts, apply_min)


def _split_long_cues(cues):
    result = []
    for cue in cues:
        if (cue["end"] - cue["start"]) > _MAX_DURATION:
            for (s, e, t) in _split_cue(cue):
                t = t.strip()
                if t:
                    result.append({"start": s, "end": e, "text": t})
        else:
            result.append(cue)
    return result


def refine_japanese_cues(cues):
    """
    Refine Japanese subtitle cues: merge mid-sentence breaks and split
    over-long merged cues.

    cues: list of {"start": float, "end": float, "text": str}
    Returns a new list of cues with the same shape.
    """
    if not cues:
        return []
    merged = _merge_cues(cues)
    refined = _split_long_cues(merged)
    return refined
