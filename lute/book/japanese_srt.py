"""
Japanese SRT subtitle cue refinement.

When importing Japanese SRT subtitles, the original cue segmentation is
often mid-sentence -- subtitles are split for display length, not
linguistic boundaries.  This module merges cues that belong to the
same sentence and splits cues that became too long after merging, so
each cue roughly corresponds to one Japanese sentence.

Rules (applied only when the book language is Japanese):

Text cleaning (applied to every cue text before merge/split):
  - compress redupicated kana tails (たたた → た, ててて → て);
  - normalize fragmented katakana compounds (キ キネ → キツネ).

Merge adjacent cues when:
  - PRIORITY 1: the next cue starts with a response/interjection word
    (うん, はい, いいえ, ええ, えっと, ほう, なるほど, あ) -> never merge,
    regardless of gap;
  - the previous cue ends with a strong terminator (。！？) -> never merge;
  - the previous cue ends with a sentence-ending blocklist word
    (ました, ましたか, のですか, です, ます, ...) -> never merge.  Only
    multi-character unambiguous endings are blocked; bare ambiguous
    particles (か, な) are NOT blocked so verb stems like 連れていか
    merge with a following ない;
  - the previous cue ends with a continuative particle (接続助詞) or
    attributive modifier (連体詞) AND the gap is < 400ms -> force merge;
  - otherwise, merge when the gap between them is < 400ms.

Split a merged cue whose duration > MAX_DURATION (8s), in priority order:
  1. strong terminator 。！？ -- always split;
  2. spoken final particle (終助詞) -- split into shorter sentences;
  3. filler / interjection word (えっと, あの, うん, そうですね, ほう,
     なるほど) -- split *before* the word;
  4. long continuative word (接続詞) -- split before the word;
  5. comma 「、」 -- split when both sides have enough characters and
     each resulting segment is long enough (de-emphasized: real
     subtitle material rarely contains commas).

After splitting, segments are kept in the 2.5s..8s band: any segment
shorter than MIN_DURATION (2.5s) is merged back into its neighbour, and
any segment still longer than MAX_DURATION (8s) after all levels gets
re-split at the earliest available boundary.

Time distribution across segments uses character weights:
  kanji = 2, hiragana/katakana = 1, punctuation/space = 0.5.
"""

import re

# ---------------------------------------------------------------------------
# Word lists
# ---------------------------------------------------------------------------

# Strong sentence terminators (full-width and half-width).
_STRONG_TERMINATORS = "。！？!?…"

# Sentence-ending blocklist: if the previous cue ends with any of these,
# do NOT merge with the next cue.  These signal a complete sentence /
# predicate boundary even without 。
#
# Only MULTI-character, unambiguous endings are listed here.  Bare
# single-char particles (か, な, ...) are intentionally excluded because
# they collide with verb stems -- e.g. 連れていか + ない must merge into
# 連れていかない, so the trailing か of a verb stem must NOT be treated as
# the question particle か.  Bare final particles are still used as
# split points for over-long cues (see _FINAL_PARTICLES).
_END_BLOCKLIST = (
    # 4-char question / predicate endings
    "ましたか", "ませんか", "のですか", "でしたか",
    # 3-char predicate endings + particle combos
    "ました", "ません", "のです", "だろう", "でしょう", "でした",
    "ですよ", "ですね", "ますよ", "ますね",
    # 2-char copula / polite + unambiguous particle combos
    "です", "ます", "のか", "だね", "だよ", "だな", "だろ",
    "よね", "なあ", "ねえ",
)

# Sentence-start blocklist: if the next cue starts with any of these
# response / interjection words, never merge regardless of gap.  This
# check has the HIGHEST priority among all merge conditions.
_START_BLOCKLIST = (
    "なるほど", "えっと", "ええ",
    "はい", "いいえ",
    "うん",
    "ほう", "あ",
)

# Final particles (終助詞) for level-2 splitting of over-long cues.
# Ordered longest-first so the compiled regex prefers the longest match.
# NOTE: these are used ONLY for splitting, not for merge-blocking -- bare
# か here does not prevent 連れていか + ない from merging.
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

# Filler / interjection words for level-3 splitting.  Split *before*
# these so they begin the new segment.
_FILLER_WORDS = (
    "そうですね", "なるほど", "えっと", "あの", "うん", "ほう",
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

# ---------------------------------------------------------------------------
# Durations (seconds)
# ---------------------------------------------------------------------------

_MAX_DURATION = 8.0           # split cues longer than this (>8000ms)
_MIN_DURATION = 2.5          # merge segments shorter than this (<2500ms)
_MIN_COMMA_SIDE_CHARS = 6    # both sides of a comma split need this many chars

# ---------------------------------------------------------------------------
# Regex compilation
# ---------------------------------------------------------------------------


def _compile_word_matcher(words):
    """Compile a regex that matches any of the given words (longest first)."""
    sorted_words = sorted(set(words), key=len, reverse=True)
    return re.compile("|".join(re.escape(w) for w in sorted_words))


_FINAL_PARTICLE_RE = _compile_word_matcher(_FINAL_PARTICLES)
_FILLER_RE = _compile_word_matcher(_FILLER_WORDS)
_LONG_CONJUNCTIVE_RE = _compile_word_matcher(_LONG_CONJUNCTIVES)
_START_BLOCKLIST_RE = _compile_word_matcher(_START_BLOCKLIST)

# Repeated kana tail: 3+ repetitions of the same hiragana/katakana char.
_REDUP_RE = re.compile(r"([\u3040-\u30FF])\1{2,}")

# Fragmented katakana compounds: "キ キネ" -> "キツネ".  This is a
# narrow fix for MeCab/tokenisation artefacts where ツ gets dropped.
_KITSUNE_FIX = re.compile(r"キ\s*キネ")
_KITSUNE_FIX2 = re.compile(r"キ\s*ツネ")

# ---------------------------------------------------------------------------
# Character classification (for weighted time allocation)
# ---------------------------------------------------------------------------

_KANJI_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")


def _char_weight(ch):
    """Weight of a single character for time distribution."""
    if _KANJI_RE.match(ch):
        return 2.0
    if _KANA_RE.match(ch):
        return 1.0
    return 0.5


def _text_weight(text):
    """Total weight of a text string."""
    return sum(_char_weight(ch) for ch in text)


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------


def _clean_text(text):
    """Normalise subtitle text: compress reduplicated kana tails and
    fix fragmented katakana compounds."""
    # キ キネ → キツネ  (two passes for ordering variants)
    text = _KITSUNE_FIX.sub("キツネ", text)
    text = _KITSUNE_FIX2.sub("キツネ", text)
    # たたた → た, ててて → て  (3+ same kana → single)
    text = _REDUP_RE.sub(r"\1", text)
    return text


# ---------------------------------------------------------------------------
# Ending / starting checks
# ---------------------------------------------------------------------------


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
    """True if the cleaned text ends with any of the given word strings.

    Full-suffix matching (``str.endswith``) -- never truncates the tail,
    so 4-character endings such as ましたか / のですか are recognised
    exactly instead of being collapsed to their last two kana."""
    s = _clean_ending(text)
    if not s:
        return False
    return any(s.endswith(w) for w in words)


def _ends_with_continuative(text):
    return _ends_with_any(text, _CONTINUATIVE_PARTICLES)


def _ends_with_rentai(text):
    return _ends_with_any(text, _RENTAI_WORDS)


def _ends_with_blocklist(text):
    return _ends_with_any(text, _END_BLOCKLIST)


def _starts_with_blocklist(text):
    s = text.strip()
    if not s:
        return False
    return bool(_START_BLOCKLIST_RE.match(s))


# ---------------------------------------------------------------------------
# Joining
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Merge pass
# ---------------------------------------------------------------------------


def _merge_cues(cues):
    """Merge adjacent cues per the Japanese sentence-continuation rules."""
    if not cues:
        return []
    merged = [dict(cues[0])]
    for cur in cues[1:]:
        prev = merged[-1]
        prev_text = prev["text"]
        cur_text = cur["text"]
        gap = cur["start"] - prev["end"]

        # Priority 1: next cue starts with a response/interjection word
        # (うん/はい/いいえ/ええ/えっと/ほう/なるほど/...) -> never merge,
        # regardless of the gap.  This is checked first so a new turn is
        # always opened as its own cue.
        if _starts_with_blocklist(cur_text):
            do_merge = False
        # Previous cue ends with a strong terminator 。！？ -> never merge
        elif _ends_with_strong_terminator(prev_text):
            do_merge = False
        # Previous cue ends with a sentence-ending blocklist word
        # (ました/ましたか/です/ます/のですか/...) -> never merge.  Bare
        # ambiguous single-char particles (か/な) are NOT here, so a verb
        # stem like 連れていか + ない still merges.
        elif _ends_with_blocklist(prev_text):
            do_merge = False
        # Fragment merge: short gap (<400ms) AND the previous cue ends
        # with a continuative particle (で/から/ので/が/...) or an
        # attributive modifier (この/...) -> force merge to repair cues
        # that YouTube split mid-sentence.
        elif gap < 0.4 and (
            _ends_with_continuative(prev_text) or _ends_with_rentai(prev_text)
        ):
            do_merge = True
        # Default: merge when the gap is short (<400ms).
        else:
            do_merge = gap < 0.4

        if do_merge:
            prev["end"] = cur["end"]
            prev["text"] = _join_texts(prev_text, cur["text"])
        else:
            merged.append(dict(cur))
    return merged


# ---------------------------------------------------------------------------
# Split pass
# ---------------------------------------------------------------------------


def _find_split_indices(text, level):
    """Return sorted character indices where a new segment should start,
    for the given priority level."""
    indices = set()
    if level == 1:
        # Strong terminators 。！？ -- always split after.
        for i, ch in enumerate(text):
            if ch in _STRONG_TERMINATORS:
                idx = i + 1
                if 0 < idx < len(text):
                    indices.add(idx)
    elif level == 2:
        # Final particles (終助詞) -- split after.
        for m in _FINAL_PARTICLE_RE.finditer(text):
            idx = m.end()
            if not (0 < idx < len(text)):
                continue
            # Single-char final particles (か, よ, ね, ...) are
            # ambiguous: the same kana appears inside longer words.
            # Only treat them as a clause boundary when the next
            # character is NOT hiragana -- a real new clause usually
            # starts with a kanji/katakana content word or punctuation.
            # Multi-char particles are specific enough to trust as-is.
            if len(m.group()) == 1 and _is_hiragana(text[idx]):
                continue
            indices.add(idx)
    elif level == 3:
        # Filler / interjection words -- split *before*.
        for m in _FILLER_RE.finditer(text):
            idx = m.start()
            if 0 < idx < len(text):
                indices.add(idx)
    elif level == 4:
        # Long continuative words (接続詞) -- split *before*.
        for m in _LONG_CONJUNCTIVE_RE.finditer(text):
            idx = m.start()
            if 0 < idx < len(text):
                indices.add(idx)
    elif level == 5:
        # Comma 「、」 -- de-emphasized: only split when both sides have
        # enough characters (real subtitle material rarely has commas).
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
    return sorted(indices)


def _distribute_time(start, end, raw_texts, apply_min_duration):
    """Given a list of text segments, distribute [start, end] across them
    proportionally by character *weight* (kanji=2, kana=1, punct=0.5).
    When apply_min_duration is set, segments shorter than the minimum
    are merged into the previous one."""
    weights = [_text_weight(t) for t in raw_texts]
    total_weight = sum(weights)
    total_dur = end - start
    if total_weight == 0 or total_dur <= 0:
        return [(start, end, "".join(raw_texts))]

    segments = []
    cur_time = start
    for i, (t, w) in enumerate(zip(raw_texts, weights)):
        if i == len(raw_texts) - 1:
            seg_start = cur_time
            seg_end = end
        else:
            frac = w / total_weight
            seg_end = cur_time + total_dur * frac
            seg_start = cur_time
        segments.append((seg_start, seg_end, t))
        cur_time = seg_end

    if apply_min_duration:
        segments = _merge_short_segments(segments, _MIN_DURATION)
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


def _split_segment_by_level(start, end, text, level):
    """Split one segment using the rules of a single level."""
    indices = _find_split_indices(text, level)
    if not indices:
        return [(start, end, text)]
    boundaries = sorted(set([0] + indices + [len(text)]))
    raw_texts = [text[boundaries[i] : boundaries[i + 1]] for i in range(len(boundaries) - 1)]
    apply_min = level == 5  # comma level applies min-duration merge
    return _distribute_time(start, end, raw_texts, apply_min)


def _split_cue(cue):
    """Split a single cue (>MAX_DURATION) using the priority rules.
    Returns a list of (start, end, text) tuples."""
    text = cue["text"]
    segments = [(cue["start"], cue["end"], text)]
    # Apply split levels in priority order: 1=terminator, 2=particle,
    # 3=filler, 4=conjunctive, 5=comma.  Each level only operates on
    # segments that are still longer than the max duration.
    for level in (1, 2, 3, 4, 5):
        next_segments = []
        for (s, e, t) in segments:
            if (e - s) <= _MAX_DURATION:
                next_segments.append((s, e, t))
                continue
            parts = _split_segment_by_level(s, e, t, level)
            next_segments.extend(parts)
        segments = next_segments
    return segments


def _split_long_cues(cues):
    """Split cues longer than MAX_DURATION.

    Within each split result, segments shorter than MIN_DURATION are
    merged back into their neighbours so we don't produce tiny
    fragments.  Cues that were already within the duration limit are
    left untouched.
    """
    result = []
    for cue in cues:
        if (cue["end"] - cue["start"]) > _MAX_DURATION:
            split_segs = _split_cue(cue)
            # Enforce min-duration only within the split segments.
            split_segs = _merge_short_segments(split_segs, _MIN_DURATION)
            for (s, e, t) in split_segs:
                t = t.strip()
                if t:
                    result.append({"start": s, "end": e, "text": t})
        else:
            result.append(cue)
    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def refine_japanese_cues(cues):
    """
    Refine Japanese subtitle cues: merge mid-sentence breaks and split
    over-long merged cues.

    cues: list of {"start": float, "end": float, "text": str}
    Returns a new list of cues with the same shape.
    """
    if not cues:
        return []

    # Text cleaning pass.
    cleaned = []
    for c in cues:
        c = dict(c)
        c["text"] = _clean_text(c.get("text", ""))
        cleaned.append(c)

    merged = _merge_cues(cleaned)
    refined = _split_long_cues(merged)
    return refined
