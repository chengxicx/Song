"""
Japanese SRT subtitle cue refinement.

When importing Japanese SRT subtitles, the original cue segmentation is
often mid-sentence -- subtitles are split for display length, not
linguistic boundaries.  This module merges cues that belong to the
same sentence and splits cues that became too long after merging, so
each cue roughly corresponds to one Japanese sentence.

PRIMARY ENGINE (MeCab-aware, authoritative when MeCab is installed):
  Text cleaning -> tokenize via MeCab -> merge using POS/conjugation
  info -> re-tokenize merged long cues and split at clause boundaries
  detected from morpheme-level sentence-ending predicate + new-sentence
  starter patterns -> enforce 2.5s..8s duration band.

FALLBACK ENGINE (graceful when MeCab cannot be loaded):
  Original regex-only suffix/prefix rules.  Works for obvious cases but
  cannot disambiguate e.g. a bare か verb-stem tail vs the sentence
  particle か, so it is more conservative about merge-blocking and less
  accurate on long-sentence splitting.

Time distribution across segments uses character weights:
  kanji = 2, hiragana/katakana = 1, punctuation/space = 0.5.
"""

import os
import re

# ---------------------------------------------------------------------------
# Word lists (used in both engines, and in MeCab fallbacks)
# ---------------------------------------------------------------------------

# Strong sentence terminators (full-width and half-width).
_STRONG_TERMINATORS = "。！？!?…"

# MeCab-available probe — filled lazily by _get_mecab_instance().
# Sentinel states: None = unprobed, False = probed and unavailable.
_MECAB = None

# ---------------------------------------------------------------------------
# Fallback word lists (used when MeCab is NOT available, and for some
# start/end heuristic cross-checks even when MeCab is available).
# ---------------------------------------------------------------------------

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

_START_BLOCKLIST = (
    "なるほど", "えっと", "ええ",
    "はい", "いいえ",
    "うん",
    "ほう", "あ",
)

# Final particles (終助詞) for regex fallback splitting.
_FINAL_PARTICLES = (
    "でしょうか", "だろうか", "でしょうね", "だろうね",
    "でしょうよ", "だろうよ",
    "かしら", "だよね", "だろう", "でしょう", "だな", "だね", "だよ",
    "よね", "なあ", "ねえ", "かい", "だい", "のう", "かな",
    "か", "よ", "ね", "な", "さ", "わ", "ぞ", "ぜ", "や",
)

_CONTINUATIVE_PARTICLES = (
    "けれども", "けれど", "ながら", "つつも", "ものの", "くせに",
    "ので", "のに", "から", "たり", "だり", "ても", "でも", "つつ",
    "なり", "なら", "ば", "と", "し", "て", "で", "が",
)

_RENTAI_WORDS = (
    "この", "その", "あの", "どの", "こんな", "そんな", "あんな", "どんな",
    "あらゆる", "いわゆる", "きたる", "かかる",
)

_FILLER_WORDS = (
    "そうですね", "なるほど", "えっと", "あの", "うん", "ほう",
)

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
_MIN_SPLIT_SIDE_WEIGHT = 6   # min weight each side of a split (roughly 3-6 chars)

# ---------------------------------------------------------------------------
# Regex compilation (fallback engine + cleaning)
# ---------------------------------------------------------------------------


def _compile_word_matcher(words):
    """Compile a regex that matches any of the given words (longest first)."""
    sorted_words = sorted(set(words), key=len, reverse=True)
    return re.compile("|".join(re.escape(w) for w in sorted_words))


_FINAL_PARTICLE_RE = _compile_word_matcher(_FINAL_PARTICLES)
_FILLER_RE = _compile_word_matcher(_FILLER_WORDS)
_LONG_CONJUNCTIVE_RE = _compile_word_matcher(_LONG_CONJUNCTIVES)
_START_BLOCKLIST_RE = _compile_word_matcher(_START_BLOCKLIST)

_REDUP_RE = re.compile(r"([\u3040-\u30FF])\1{2,}")
_KITSUNE_FIX = re.compile(r"キ\s*キネ")
_KITSUNE_FIX2 = re.compile(r"キ\s*ツネ")

_KANJI_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")


def _char_weight(ch):
    if _KANJI_RE.match(ch):
        return 2.0
    if _KANA_RE.match(ch):
        return 1.0
    return 0.5


def _text_weight(text):
    return sum(_char_weight(ch) for ch in text)


# ---------------------------------------------------------------------------
# Text cleaning (used regardless of engine)
# ---------------------------------------------------------------------------

def _clean_text(text):
    text = _KITSUNE_FIX.sub("キツネ", text)
    text = _KITSUNE_FIX2.sub("キツネ", text)
    text = _REDUP_RE.sub(r"\1", text)
    return text


# ===================================================================== #
#  MECAB LAYER  --  authoritative when MeCab is installed              #
# ===================================================================== #

def _get_mecab_instance():
    """Return a MeCab instance if one can be created, otherwise None.

    The result is cached at module level; probing is done at most once
    per process.  We do NOT use the settings-backed JapaneseParser
    class here because (1) the settings stack is not initialised during
    pytest runs on non-Japanese fixtures and (2) all we need is a raw
    natto MeCab instance with its default dictionary, which matches
    what the rest of the application will eventually use."""
    global _MECAB
    if _MECAB is None:  # not yet probed
        try:
            from natto import MeCab  # pylint: disable=import-outside-toplevel
            _MECAB = MeCab()
        except Exception:  # pylint: disable=broad-except
            # natto-py may not be installed, libmecab may be missing,
            # dictionary files may be absent, etc.  Any failure -> mark
            # unavailable so subsequent calls fall through to the regex
            # engine.
            _MECAB = False
    return _MECAB if _MECAB else None


def _tokenize_mecab(text):
    """Tokenize text with MeCab.  Returns a list of token dicts or [].

    Each token dict has keys:
      surface: str, pos0: str, pos1: str, conj_type: str, conj_form: str,
      start: int (char offset inclusive), end: int (char offset exclusive).
    Symbol/whitespace tokens whose POS is 記号 are included; callers can
    filter them out when needed.
    """
    nm = _get_mecab_instance()
    if nm is None:
        return []
    tokens = []
    offset = 0
    try:
        for n in nm.parse(text, as_nodes=True):
            if n.stat == 2 or n.surface is None or n.surface == "":  # BOS/EOS
                continue
            surface = n.surface
            flen = len(surface)
            feats = n.feature.split(",")
            while len(feats) < 6:
                feats.append("*")
            tokens.append({
                "surface": surface,
                "pos0": feats[0],
                "pos1": feats[1],
                "conj_type": feats[4],
                "conj_form": feats[5],
                "start": offset,
                "end": offset + flen,
            })
            offset += flen
    except Exception:  # pylint: disable=broad-except
        # MeCab is best-effort; fall back for this call.
        return []
    return tokens


# ----------  MeCab POS/conjugation helpers  ---------------------------

# Conjugation forms that represent a COMPLETE predicate (sentence can
# end here).  In both IPADIC and Unidic, 活用形 starts with one of
# these labels (followed by "-一般", "-促音便", etc., hence startswith).
_TERMINAL_CONJ_PREFIXES = ("終止形", "連体形", "仮定形", "命令形", "已然形")

# Conjugation forms that represent an INCOMPLETE predicate — the
# sentence MUST continue into another token.
_INCOMPLETE_CONJ_PREFIXES = ("未然形", "連用形")


def _is_meaningful_token(tok):
    """False for symbols, whitespace, punctuation-like tokens."""
    p0 = tok["pos0"]
    return p0 not in ("記号", "補助記号", "空白") and tok["surface"].strip() != ""


def _last_meaningful_token(tokens):
    """Return the last non-symbol/whitespace token, or None."""
    for t in reversed(tokens):
        if _is_meaningful_token(t):
            return t
    return None


def _first_meaningful_token(tokens, start_from=0):
    """Return the first non-symbol/whitespace token at or after start_from."""
    for t in tokens[start_from:]:
        if _is_meaningful_token(t):
            return t
    return None


# ----------  MeCab-based merge decision primitives ---------------------

def _mecab_sentence_complete(text):
    """True if MeCab analysis says text ends with a complete sentence.

    A sentence is complete when the last meaningful token is:
      - a 助動詞, 動詞, 形容詞, or 形状詞 in 終止形/連体形/仮定形/命令形
      - a 助詞-終助詞
      - an 感動詞 (stand-alone interjection like はい/うん)
      - a plain noun/copula tail: POS=名詞, POS=助動詞-ダ(終止形), etc.
        handled implicitly via _終止形 match.
    When text ends with an incomplete conjugation (未然形, 連用形) it is
    NEVER complete.
    Returns None if MeCab unavailable (caller falls back to regex).
    """
    toks = _tokenize_mecab(text)
    if not toks:
        return None
    last = _last_meaningful_token(toks)
    if not last:
        return None
    cf = last["conj_form"] or ""
    # Incomplete stem — definitely not a complete sentence.
    for inc in _INCOMPLETE_CONJ_PREFIXES:
        if cf.startswith(inc):
            return False
    p0, p1 = last["pos0"], last["pos1"]
    # Sentence-ending particle (助詞-終助詞) — always complete.
    if p0 == "助詞" and (p1 == "終助詞" or "終助詞" in p1):
        return True
    # Interjection (感動詞, フィラー) standalone end — complete.
    if p0 == "感動詞":
        return True
    # Predicate (verb / aux-verb / i-adj / na-adj / 形状詞) in terminal
    # conjugation form → complete.
    if (p0 in ("動詞", "助動詞", "形容詞", "形状詞", "形容動詞")
            and cf != "*"):
        for term in _TERMINAL_CONJ_PREFIXES:
            if cf.startswith(term):
                return True
    return None  # ambiguous: caller falls back to regex.


def _mecab_sentence_incomplete(text):
    """True if MeCab analysis says text MUST continue (merge with next).

    Cases:
      - last meaningful token has 未然形 or 連用形 conjugation
      - last meaningful token is 助詞-接続助詞 (で, て, から, ので, が...)
      - last meaningful token is 連体詞 (must modify a following noun)
    Returns None if MeCab unavailable or inconclusive.
    """
    toks = _tokenize_mecab(text)
    if not toks:
        return None
    last = _last_meaningful_token(toks)
    if not last:
        return None
    cf = last["conj_form"] or ""
    for inc in _INCOMPLETE_CONJ_PREFIXES:
        if cf.startswith(inc):
            return True
    p0, p1 = last["pos0"], last["pos1"]
    if p0 == "助詞" and (p1 == "接続助詞" or "接続助詞" in p1):
        return True
    if p0 == "連体詞":
        return True
    return None


def _mecab_starts_new_sentence(text):
    """True if MeCab analysis says text starts with a new-sentence marker.

    A new-sentence starter is:
      - 感動詞 (うん, はい, えっと, なるほど…)
      - 接続詞 (で, だから, しかし, それで…)
    Returns None if MeCab unavailable or inconclusive.
    """
    toks = _tokenize_mecab(text)
    if not toks:
        return None
    first = _first_meaningful_token(toks, 0)
    if not first:
        return None
    p0 = first["pos0"]
    if p0 == "感動詞":
        return True
    if p0 == "接続詞":
        return True
    return None


# ----------  MeCab-based clause-boundary finder (for split pass) --------

def _next_meaningful_idx(tokens, from_idx):
    """Index of first meaningful token with index > from_idx, or None."""
    i = from_idx + 1
    while i < len(tokens):
        if _is_meaningful_token(tokens[i]):
            return i
        i += 1
    return None


def _mecab_token_is_predicate_terminal(tok):
    """True if tok represents a potential sentence-final predicate."""
    cf = tok["conj_form"] or ""
    p0, p1 = tok["pos0"], tok["pos1"]
    # Terminal-conjugated predicate.
    if (p0 in ("動詞", "助動詞", "形容詞", "形状詞", "形容動詞") and cf != "*"):
        for term in _TERMINAL_CONJ_PREFIXES:
            if cf.startswith(term):
                return True
    # Sentence-ending particle.
    if p0 == "助詞" and (p1 == "終助詞" or "終助詞" in p1):
        return True
    return False


def _mecab_token_is_sentence_starter(tok):
    """True if tok typically begins a new sentence/clause.

    Conservative — false-positives here would break valid phrase merges
    like 「多々の [冒険…]」 where the second cue begins with a plain
    content noun that is NOT a new sentence.
    """
    p0, p1 = tok["pos0"], tok["pos1"]
    surf = tok["surface"]
    # Explicit interjections and conjunctions are always starters.
    if p0 == "感動詞":
        return True
    if p0 == "接続詞":
        return True
    # Filler / interjection words list match.
    if surf in _FILLER_WORDS or surf in _START_BLOCKLIST:
        return True
    # Wh-question pronouns definitely begin a new question clause.
    if p0 == "代名詞" and surf in ("何", "どこ", "いつ", "誰", "どうして",
                                     "なぜ", "どう", "どれ", "どちら",
                                     "なんで", "なに"):
        return True
    # Demonstrative determiners (この/その/あの/こんな/そんな…) always
    # introduce a new noun phrase.
    if p0 == "連体詞":
        return True
    # Demonstrative pronouns/adverbs typically start a new sentence.
    if p0 == "代名詞" and surf in ("それ", "これ", "あれ", "ここ", "そこ",
                                   "あそこ", "どこ", "どれ", "こちら",
                                   "そちら", "あちら", "どちら"):
        return True
    if p0 == "副詞" and surf in ("そう", "ああ", "こう", "どう", "はたして",
                                 "まだ", "もう", "また", "さらに", "それでも",
                                 "そのうえ", "それから"):
        return True
    return False


def _mecab_clause_boundaries(text):
    """Return a list of character-offsets (split start positions) found
    by MeCab clause-boundary analysis.

    A split point is recorded AFTER token[i] when:
      (A) token[i] is a predicate-terminal AND the next meaningful token
          token[j] is a sentence-starter (interjection/conjunction/wh-
          question/interjection-word), OR
      (B) token[i] itself is a sentence-ending particle (助詞-終助詞) AND
          the following character sequence is not obviously continued
          (next tok is NOT a plain hiragana particle-infix inside the
          same clause), OR
      (C) token[j] is a filler/conjunction/interjection token and the
          previous meaningful token was not mid-continuation.

    Offsets are returned in sorted order, unique.  The caller must still
    apply min-side-weight and duration checks.
    """
    tokens = _tokenize_mecab(text)
    if not tokens:
        return []

    boundaries = set()
    n = len(tokens)

    for i in range(n):
        tok = tokens[i]
        split_here = False

        # --- Case A/B: terminal predicate or sentence-ending particle ---
        if _mecab_token_is_predicate_terminal(tok):
            j = _next_meaningful_idx(tokens, i)
            if j is None:
                continue
            next_tok = tokens[j]
            # ANTI-SPLIT GUARD: if terminal form is ATTRIBUTIVE (連体形)
            # AND a following NOMINAL HEAD exists (possibly after a
            # beautification お/ご prefix), this is NOT a clause boundary
            # — it's a relative clause / noun modification.
            # Example: 思った(連体形) + お(接頭辞) + 告げ(名詞) =
            # "the [I-thought] message" NOT "I thought. The message..."
            conj = tok["conj_form"] or ""
            is_attr = (conj.startswith("連体形") or
                       tok["pos0"] == "形容詞" and conj.startswith("体言接続") or
                       tok["pos0"] == "連体詞")
            # Walk past beautification-prefix tokens (お/ご/御…) to find
            # the real nominal head they attach to.
            head_idx = j
            while (head_idx is not None and head_idx < n
                   and tokens[head_idx]["pos0"] == "接頭辞"
                   and tokens[head_idx]["surface"] in ("お", "ご", "御", "ご")):
                head_idx = _next_meaningful_idx(tokens, head_idx)
            if head_idx is not None:
                head_tok = tokens[head_idx]
                next_is_nominal_head = head_tok["pos0"] in (
                    "名詞", "代名詞", "連体詞", "助数詞", "形状詞", "形容動詞",
                    "動詞", "形容詞")
            else:
                next_is_nominal_head = False
            attr_blocked = is_attr and next_is_nominal_head

            if not attr_blocked:
                # A) followed by a new-sentence starter
                if _mecab_token_is_sentence_starter(next_tok):
                    split_here = True
                # B) terminal particle followed by content word.
                elif tok["pos1"] == "終助詞" or "終助詞" in tok["pos1"]:
                    nxt_surf = next_tok["surface"]
                    if (len(nxt_surf) >= 2 or
                            not _is_hiragana_str(nxt_surf) or
                            next_tok["pos0"] not in ("助詞", "助動詞")):
                        split_here = True
                # C-implied: aux-verb 終止形 + followed by noun/topic → new clause.
                # (Skips 連体形; that case was already filtered by attr_blocked)
                elif (tok["pos0"] == "助動詞"
                        and (tok["conj_form"] or "").startswith("終止形")
                        and next_tok["pos0"] in ("名詞", "代名詞", "副詞",
                                                 "感動詞", "接続詞")):
                    split_here = True

        if split_here:
            # Split AFTER the current token.
            off = tok["end"]
            if 0 < off < len(text):
                boundaries.add(off)

        # --- Case C: split BEFORE a filler/conjunction/long conjunctive.
        # We only need this for long conjunctions / fillers that may not
        # be caught by the "previous token was terminal" heuristic above
        # (e.g. the previous token was an incomplete noun phrase).
        if i > 0 and _mecab_token_is_sentence_starter(tok):
            surf = tok["surface"]
            # Only big enough markers count as split-worthy (a single
            # 「あ」 or 「うん」 at the beginning inside a clause is too
            # noisy); require surface len >= 2 or be in the explicit
            # split-before lists.
            if (len(surf) >= 2 or surf in _FILLER_WORDS or
                    surf in _LONG_CONJUNCTIVES):
                off = tok["start"]
                if 0 < off < len(text):
                    # But NOT if we're already at the very beginning.
                    prev_meaningful = _last_meaningful_token(tokens[:i])
                    if prev_meaningful:
                        boundaries.add(off)

    return sorted(boundaries)


# ===================================================================== #
#  END OF MECAB LAYER                                                   #
# ===================================================================== #


# ----------  Fallback regex helpers (for no-Mecab environments) ---------

def _clean_ending(text):
    return text.strip()


def _is_hiragana(ch):
    return "\u3040" <= ch <= "\u309F"


def _is_hiragana_str(s):
    return bool(s) and all(_is_hiragana(c) for c in s)


def _ends_with_strong_terminator(text):
    s = _clean_ending(text)
    return bool(s) and s[-1] in _STRONG_TERMINATORS


def _ends_with_any(text, words):
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


# ----------  Composite merge decision primitives  ------------------------
#  Each uses MeCab when available, otherwise falls back to regex.

def _composite_sentence_complete(text):
    """Authoritative merge-block check for the end of the previous cue."""
    if _ends_with_strong_terminator(text):
        return True
    r = _mecab_sentence_complete(text)
    if r is not None:
        return r
    return _ends_with_blocklist(text)


def _composite_starts_new(text):
    """Authoritative merge-block check for the start of the next cue."""
    r = _mecab_starts_new_sentence(text)
    if r is not None:
        return r
    return _starts_with_blocklist(text)


def _composite_incomplete(text):
    """Authoritative force-merge check for the end of the previous cue."""
    r = _mecab_sentence_incomplete(text)
    if r is not None:
        return r
    return _ends_with_continuative(text) or _ends_with_rentai(text)


# ----------  Joining  --------------------------------------------------

def _join_texts(a, b):
    a = a.rstrip()
    b = b.lstrip()
    if (a and b and a[-1].isascii() and a[-1].isalnum()
            and b[0].isascii() and b[0].isalnum()):
        return a + " " + b
    return a + b


# ---------------------------------------------------------------------------
# Merge pass
# ---------------------------------------------------------------------------

def _merge_cues(cues):
    """Merge adjacent cues based on MeCab + fallback regex analysis."""
    if not cues:
        return []
    merged = [dict(cues[0])]
    for cur in cues[1:]:
        prev = merged[-1]
        prev_text = prev["text"]
        cur_text = cur["text"]
        gap = cur["start"] - prev["end"]

        # Priority 1: next cue begins with a brand-new sentence
        # (interjection or conjunction) → never merge regardless of gap.
        if _composite_starts_new(cur_text):
            do_merge = False
        # Previous cue has a sentence-terminator mark.
        elif _ends_with_strong_terminator(prev_text):
            do_merge = False
        # Previous cue ends with a complete sentence (terminal predicate
        # or sentence-ending particle) — identified by MeCab or
        # END_BLOCKLIST fallback.
        elif _composite_sentence_complete(prev_text):
            do_merge = False
        # Force-merge: previous cue is syntactically incomplete (verb
        # stem, te-form without end, continuative particle, rentaishi)
        # AND gap is short enough that these two cues clearly were
        # split mid-sentence by YouTube.
        elif gap < 0.4 and _composite_incomplete(prev_text):
            do_merge = True
        # Default: merge on short gaps.  For Japanese conversational
        # material with small raw cue sizes this still merges frequently,
        # but the split pass (with MeCab clause boundaries) will re-cut
        # anything that became too long.
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
    """Regex fallback split-index helper.  Same priority levels as before,
    plus a new LEVEL 0.5 that uses MeCab clause boundaries when possible.
    """
    indices = set()
    if level == 0:
        # New MeCab-based clause boundaries (highest priority after
        # strong terminators).  Inserted BEFORE level 2 particles.
        for idx in _mecab_clause_boundaries(text):
            indices.add(idx)
        return sorted(indices)
    if level == 1:
        for i, ch in enumerate(text):
            if ch in _STRONG_TERMINATORS:
                idx = i + 1
                if 0 < idx < len(text):
                    indices.add(idx)
    elif level == 2:
        # Final particles (終助詞) — regex fallback when MeCab is absent.
        # CRITICAL: only MULTI-character particles are allowed to split.
        # Single-character particles (か/よ/ね/な/の/さ/わ/ぞ/ぜ/や) are
        # vastly too ambiguous and butcher noun phrases like 多々の冒険
        # ("の" = attributive, NOT sentence end).  MeCab Level 0 handles
        # real single-particle boundaries with proper POS context.
        for m in _FINAL_PARTICLE_RE.finditer(text):
            idx = m.end()
            if not (0 < idx < len(text)):
                continue
            if len(m.group()) == 1:
                continue  # skip single-kana final particles entirely
            indices.add(idx)
    elif level == 3:
        for m in _FILLER_RE.finditer(text):
            idx = m.start()
            if 0 < idx < len(text):
                indices.add(idx)
    elif level == 4:
        for m in _LONG_CONJUNCTIVE_RE.finditer(text):
            idx = m.start()
            if 0 < idx < len(text):
                indices.add(idx)
    elif level == 5:
        for i, ch in enumerate(text):
            if ch in "、,":
                left = text[:i]
                right = text[i + 1:]
                if (len(left.strip()) >= _MIN_COMMA_SIDE_CHARS
                        and len(right.strip()) >= _MIN_COMMA_SIDE_CHARS):
                    idx = i + 1
                    if 0 < idx < len(text):
                        indices.add(idx)
    return sorted(indices)


def _filter_split_indices(text, all_indices):
    """Remove split points where either side has too few weighted chars,
    and de-dup/re-sort."""
    total_w = _text_weight(text)
    threshold = _MIN_SPLIT_SIDE_WEIGHT
    kept = []
    for idx in sorted(set(all_indices)):
        left_w = _text_weight(text[:idx])
        right_w = total_w - left_w
        if left_w >= threshold and right_w >= threshold:
            kept.append(idx)
    return kept


def _distribute_time(start, end, raw_texts, apply_min_duration):
    weights = [_text_weight(t) for t in raw_texts]
    total_weight = sum(weights)
    total_dur = end - start
    if total_weight == 0 or total_dur <= 0:
        return [(start, end, "".join(raw_texts))]
    segments = []
    cur_time = start
    for i, (t, w) in enumerate(zip(raw_texts, weights)):
        if i == len(raw_texts) - 1:
            seg_start, seg_end = cur_time, end
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
    if len(segments) <= 1:
        return segments
    result = [segments[0]]
    for seg in segments[1:]:
        prev = result[-1]
        if (seg[1] - seg[0]) < min_duration:
            result[-1] = (prev[0], seg[1], prev[2] + seg[2])
        else:
            result.append(seg)
    if len(result) > 1 and (result[0][1] - result[0][0]) < min_duration:
        result[1] = (result[0][0], result[1][1], result[0][2] + result[1][2])
        result.pop(0)
    return result


def _split_segment_by_indices(start, end, text, indices,
                              apply_min_duration=True):
    """Split one (start,end,text) segment at the given char indices."""
    indices = _filter_split_indices(text, indices)
    if not indices:
        return [(start, end, text)]
    boundaries = sorted(set([0] + indices + [len(text)]))
    raw_texts = [text[boundaries[i]:boundaries[i + 1]]
                 for i in range(len(boundaries) - 1)]
    return _distribute_time(start, end, raw_texts,
                            apply_min_duration=apply_min_duration)


def _split_cue(cue):
    """Split a single >8s cue using MeCab first, then regex levels."""
    text = cue["text"]
    segments = [(cue["start"], cue["end"], text)]

    # Priority order:
    # 1. Strong terminators (。！？) — always works, punctuation-based.
    # 0. NEW: MeCab clause boundaries — finds real sentence boundaries
    #    even without punctuation.  This is the heavy lifter for the
    #    typical YouTube-captions case of 20-character clauses jammed
    #    together with zero punctuation.
    # 2. Final particles (終助詞, regex fallback) — kept as a fallback
    #    when MeCab is unavailable or misses a boundary.
    # 3. Filler / interjection words — split before.
    # 4. Long conjunctive words — split before.
    # 5. Comma 「、」 — de-emphasized.
    priority_levels = (1, 0, 2, 3, 4, 5)

    for level in priority_levels:
        next_segments = []
        for (s, e, t) in segments:
            if (e - s) <= _MAX_DURATION:
                next_segments.append((s, e, t))
                continue
            indices = _find_split_indices(t, level)
            parts = _split_segment_by_indices(s, e, t, indices)
            next_segments.extend(parts)
        segments = next_segments

    return segments


def _split_long_cues(cues):
    result = []
    for cue in cues:
        if (cue["end"] - cue["start"]) > _MAX_DURATION:
            split_segs = _split_cue(cue)
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

    cleaned = []
    for c in cues:
        c = dict(c)
        c["text"] = _clean_text(c.get("text", ""))
        cleaned.append(c)

    merged = _merge_cues(cleaned)
    refined = _split_long_cues(merged)

    # Safety rebalance: any cue still >MAX_DURATION is split at the best
    # clause boundary whose left weight most closely matches MAX_DURATION
    # / duration.  Recursively split until every cue <=8s.
    def _safety_split(cue, _depth=0):
        s, e, t = cue["start"], cue["end"], cue["text"]
        dur = e - s
        if dur <= _MAX_DURATION or len(t) <= 1:
            return [cue]
        # Hard convergence guard: prevent pathological infinite recursion
        # if time distribution keeps a piece nominally >8s forever.
        if _depth > 8:
            return [cue]

        # Candidate split positions = clause boundaries (Level 0) + strong
        # terminators (Level 1) + filler/conjunction pre-splits (Levels 3-4).
        # These are the *semantically meaningful* positions; we never split
        # mid-morpheme.
        candidate_ends = set()
        for idx in _mecab_clause_boundaries(t):
            candidate_ends.add(idx)
        for i, ch in enumerate(t):
            if ch in _STRONG_TERMINATORS and 0 < i + 1 < len(t):
                candidate_ends.add(i + 1)
        for m in _FILLER_RE.finditer(t):
            if 0 < m.start() < len(t):
                candidate_ends.add(m.start())
        for m in _LONG_CONJUNCTIVE_RE.finditer(t):
            if 0 < m.start() < len(t):
                candidate_ends.add(m.start())

        ratio = min(0.5, _MAX_DURATION / dur) if dur > 0 else 0.5
        target_w = _text_weight(t) * ratio

        cands = sorted(candidate_ends)
        if not cands:
            tokens = _tokenize_mecab(t)
            if tokens:
                cands = sorted(set(
                    tk["end"] for tk in tokens
                    if 0 < tk["end"] < len(t) and _is_meaningful_token(tk)
                    and not (tk["pos0"] == "接頭辞")
                    and not (tk["pos0"] == "助詞"
                             and tk["surface"] in ("の", "が", "を", "に",
                                                   "へ", "と", "で", "から",
                                                   "より", "まで"))
                ))
            else:
                cands = list(range(1, len(t)))

        best, best_diff = None, float("inf")
        for off in cands:
            if 0 < off < len(t):
                diff = abs(_text_weight(t[:off]) - target_w)
                if diff < best_diff:
                    best_diff, best = diff, off
        if best is None or best <= 0 or best >= len(t):
            best = max(1, min(len(t) - 1, len(t) // 2))

        # Do NOT apply_min_duration here: that artificially inflates
        # segment end times and can create pieces whose (se - ss) still
        # exceeds MAX_DURATION without shrinking the text — causing
        # infinite recursion.  The per-cue min-duration is already
        # enforced outside the safety-split step.
        segs = _split_segment_by_indices(s, e, t, [best],
                                         apply_min_duration=False)
        out = []
        for ss, se, st in segs:
            piece = {"start": ss, "end": se, "text": st.strip()}
            # Only recurse if the piece actually has less text AND
            # still exceeds the max duration; this guarantees progress.
            if len(piece["text"]) < len(t) and (se - ss) > _MAX_DURATION and piece["text"]:
                out.extend(_safety_split(piece, _depth + 1))
            else:
                out.append(piece)
        return out

    fixed_any = []
    for cue in refined:
        if (cue["end"] - cue["start"]) > _MAX_DURATION and cue["text"]:
            fixed_any.extend(_safety_split(cue))
        else:
            fixed_any.append(cue)

    return fixed_any
