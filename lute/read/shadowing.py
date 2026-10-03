"""
Shadowing (read-aloud) scoring: transcribe a short recording of the
user reading a sentence aloud, and diff the transcription against the
sentence's own word tokens to flag missed / misread words.

Reuses the faster-whisper integration from lute.book.whisper_transcribe
(shared lazy install, model cache and language-code mapping); the diff
side runs the language's own parser over the transcription, so Japanese
morphemes and space-delimited words land in the same token space as the
rendered sentence spans the client sends back.
"""

import difflib
import logging
import os
import re
import threading
import time
import uuid

import jaconv

from lute.book import sensevoice
from lute.book.whisper_transcribe import (
    ALLOWED_MODEL_SIZES,
    DEFAULT_MODEL_SIZE,
    _load_model,
    model_in_use,
    whisper_lang_code,
    whisper_language_note,
    whisper_status,
)
from lute.db import db
from lute.models.repositories import LanguageRepository
from lute.multiuser import context as mu_context
from lute.read.render.grammar_analysis import is_japanese_language

# Per-word verdicts, parallel to the sentence's word spans.
STATUS_MISS = 0  # never spoken (漏读)
STATUS_FUZZY = 1  # probably misread: near-match token (错读)
STATUS_MATCH = 2  # spoken as-is
STATUS_SKIP = 3  # punctuation etc.: never scored, never marked

# In a replace block, a near-match pair still counts as an attempt
# (misread) instead of a skip.  Pairs are compared on the diff keys,
# i.e. readings for Japanese.
FUZZY_MATCH_RATIO = 0.6

# Japanese diff keys are hiragana strings a few morae long, where
# SequenceMatcher ratios quantize coarsely: half the morae of a word
# matching lands at exactly 0.5, which the 0.6 threshold would call a
# flat miss.  A learner judged against what the panel heard should get
# the half-credit "misread" verdict (with the heard word shown) rather
# than a silent miss, so Japanese pairs near-match from 0.45 up.
FUZZY_MATCH_RATIO_JA = 0.45


def transcribe_clip(audio_path, lang_code, model_size=DEFAULT_MODEL_SIZE):
    """
    Transcribe one short recording.

    Returns (text, duration_secs).  The caller loads the model first
    (see _run_task) so it can report a cold start separately; this call
    then finds it in the cache.
    """
    model = _load_model(model_size)
    with model_in_use():
        segments_iter, info = model.transcribe(
            audio_path,
            language=lang_code,
            # Greedy decoding: on a few seconds of speech the accuracy loss
            # vs beam search is negligible and CPU inference is ~2x faster.
            beam_size=1,
            # No word timestamps: the diff only needs the text, and the
            # alignment pass costs ~15% extra inference on a CPU that is
            # already the bottleneck.
            word_timestamps=False,
            # Same guards as the audiobook transcription (see there).
            vad_filter=True,
            condition_on_previous_text=False,
            # No initial_prompt: hinting the expected sentence would let the
            # model echo it back, inflating the score.
        )

        texts = []
        for seg in segments_iter:
            if seg.text:
                texts.append(seg.text)
        # Logged so a wrong-language transcription can be told apart:
        # a forced language here means the code mapping was wrong, a
        # low probability means whisper drifted despite the hint.
        logging.getLogger(__name__).info(
            "shadowing transcribe: requested language=%r, detected=%r "
            "(p=%.2f), duration=%.1fs",
            lang_code,
            getattr(info, "language", None),
            getattr(info, "language_probability", None) or 0.0,
            getattr(info, "duration", None) or 0.0,
        )
    return "".join(texts).strip(), (info.duration or 0.0)


# Punctuation parsers glue onto the edges of tokens: a page's 100% is
# one non-word token reading ' 100%," ', and transcriptions come back
# with their own trailing punctuation.  Stripped from both sides so the
# two spellings of the same spoken thing compare equal.  Symbols that
# are part of what was said (%, $, +) are kept.
_EDGE_JUNK = (
    " \t\n\r\u00a0.,!?;:\"'`“”‘’«»„()[]{}/\\…·•*|~^\u2013\u2014-" "，。、！？：；（）【】「」『』"
)


def _clean_token(token):
    "Strip display artifacts and glued-on edge punctuation from a token."
    return (
        (token or "").replace("\u200B", "").replace("🔊", "").strip().strip(_EDGE_JUNK)
    )


def _has_word_chars(token):
    """
    True if the token contains any letter or digit (Unicode-aware).

    Punctuation-only tokens are never scored: some parsers can attach
    punctuation to a word token or mark it as a word, and whisper's
    transcription is full of punctuation -- neither side may let it
    affect the verdicts or the score.
    """
    return any(ch.isalnum() for ch in token)


def _spoken_tokens(spoken_text, language):
    """
    The word tokens of a transcription, in spoken order.

    Shared by the diff (which aligns them against the sentence's own
    tokens) and by the furigana annotation of the "heard" sentence, so
    both see exactly the same token space.

    Non-word parsed tokens that still contain letters or digits are
    kept: many languages' word characters exclude digits, so a spoken
    "100%" is one non-word token -- dropping it would make a word the
    user actually said unscoreable.
    """
    return [
        s
        for s in (
            _clean_token(pt.token)
            for pt in language.get_parsed_tokens(spoken_text or "")
            if pt.is_word or _has_word_chars(pt.token)
        )
        if s and _has_word_chars(s)
    ]


def _align_context_readings(tokens, morphs):
    """
    Spread a contextual parse's morpheme readings across the panel's tokens.

    morphs is the parser's get_context_readings answer for the sentence:
    [(surface, reading-or-None)] in morpheme order.  The panel's tokens
    are the reading page's spans, cut by the same parser, so each token
    is the join of one or more morphemes; its reading is the join of
    those morphemes' readings (一 + つ -> ひとつ).  Punctuation-only
    spans are never sent as tokens, so pure-punctuation morphemes are
    skipped between tokens.

    Returns [{"text", "reading"}] parallel to the input tokens, or None
    when the two sides drift apart (a token the morphemes can't rebuild
    exactly) -- the caller then falls back to per-token readings rather
    than show a misaligned furigana.
    """
    out = []
    mi = 0
    for raw in tokens:
        text = _clean_token(raw)
        if not text:
            out.append({"text": text, "reading": None})
            continue
        while mi < len(morphs) and not _has_word_chars(morphs[mi][0]):
            mi += 1  # punctuation/whitespace between spans, unsent
        acc = ""
        readings = []
        covered = True
        j = mi
        while j < len(morphs) and len(acc) < len(text):
            surface, reading = morphs[j]
            j += 1
            if not surface.strip():
                continue
            acc += surface
            if reading:
                readings.append(reading)
            else:
                # Symbols, or kanji the dictionary has no kana for: a
                # partial reading would hang wrong furigana.
                covered = False
        if acc != text:
            return None
        # A kana token's own kana is no furigana.
        joined = "".join(readings) if covered else None
        out.append({"text": text, "reading": joined if joined != text else None})
        mi = j
    return out


def annotate_tokens(tokens, language, full_text=None):
    """
    Pair each token with its kana reading for the shadowing panel.

    Returns [{"text": surface, "reading": kana-or-None}, ...], parallel to
    the input.  The panel draws the reading above the word (furigana) and
    speaks it when the word is tapped.

    When full_text (the whole sentence the tokens belong to) is given,
    the parser reads it as one piece and the per-morpheme readings are
    aligned onto the tokens, so readings follow the sentence's context
    (一つ -> 一=ひと, not the isolated 一=いち).  Tokens that can't be
    covered that way fall back to the per-token reading -- the same
    source term lookups use.  Non-Japanese languages (and the
    japanese_reading setting being off) yield reading=None, so the panel
    simply shows the plain word.
    """
    cleaned = [{"text": _clean_token(t), "reading": None} for t in tokens]
    if not is_japanese_language(language):
        return cleaned

    parser = language.parser
    if full_text:
        try:
            morphs = parser.get_context_readings(full_text)
        except Exception:  # pylint: disable=broad-exception-caught
            morphs = None
        if morphs:
            aligned = _align_context_readings(tokens, morphs)
            if aligned is not None:
                return aligned

    out = []
    for entry in cleaned:
        text = entry["text"]
        reading = None
        if text:
            try:
                reading = parser.get_reading(text)
            except Exception:  # pylint: disable=broad-exception-caught
                reading = None
        out.append({"text": text, "reading": (reading or "").strip() or None})
    return out


def _is_chinese_language(language):
    "Mandarin, Cantonese, Classical Chinese -- anything whisper codes zh."
    return whisper_lang_code(language) == "zh"


_OPENCC_T2S = None
_OPENCC_CHECKED = False


def _simplified_converter():
    """
    A Traditional->Simplified Han converter, or None when opencc is not
    installed.  SenseVoice emits simplified Chinese even for Cantonese,
    while book text is often traditional; both diff sides are folded to
    simplified so 天氣/天气, 係/系 etc. compare equal.
    """
    global _OPENCC_T2S, _OPENCC_CHECKED  # pylint: disable=global-statement
    if not _OPENCC_CHECKED:
        _OPENCC_CHECKED = True
        try:
            from opencc import (
                OpenCC,
            )  # pylint: disable=import-error,import-outside-toplevel

            _OPENCC_T2S = OpenCC("t2s")
        except Exception:  # pylint: disable=broad-exception-caught
            # Logged once: without the fold, a traditional sentence is
            # scored against a simplified transcription character by
            # character, and correctly-read words come back as misses.
            _OPENCC_T2S = None
            logging.getLogger(__name__).warning(
                "opencc is not installed: Traditional Chinese text will "
                "not match the Simplified transcriptions when scoring "
                "shadowing takes (pip install opencc-python-reimplemented)"
            )
    return _OPENCC_T2S


_OPENCC_S2T = None
_OPENCC_S2T_CHECKED = False


def _traditional_converter():
    """
    A Simplified->Traditional Han converter, or None when opencc is not
    installed.  Used only for display: the engines emit simplified Han,
    but a traditional book sentence should not come back in the "heard"
    panel looking like a different sentence.
    """
    global _OPENCC_S2T, _OPENCC_S2T_CHECKED  # pylint: disable=global-statement
    if not _OPENCC_S2T_CHECKED:
        _OPENCC_S2T_CHECKED = True
        try:
            from opencc import (
                OpenCC,
            )  # pylint: disable=import-error,import-outside-toplevel

            _OPENCC_S2T = OpenCC("s2t")
        except Exception:  # pylint: disable=broad-exception-caught
            _OPENCC_S2T = None
    return _OPENCC_S2T


def _match_book_script(text, original_tokens, language):
    """
    Convert a Chinese transcription to the book's Han script for display.

    SenseVoice (and whisper zh) answer in simplified Han even when the
    book text is traditional -- Cantonese courses usually are.  Scoring
    folds both sides to simplified (see _make_key_fn), but the "heard"
    panel shows the transcription verbatim, and 个个 都 唔 一样 next to
    a 個個 都 唔 一樣 sentence reads as wrong even at 100%.  When the
    sentence itself is traditional, convert the transcription back;
    a simplified book sentence needs no conversion (the engines already
    emit simplified).  No-op when opencc is missing or the language is
    not Chinese.
    """
    if not _is_chinese_language(language):
        return text
    t2s = _simplified_converter()
    s2t = _traditional_converter()
    if t2s is None or s2t is None:
        return text
    sample = "".join(t or "" for t in (original_tokens or []))
    if t2s.convert(sample) == sample:
        # The sentence is already simplified -- nothing to restore.
        return text
    return s2t.convert(text or "")


# A token written entirely in kana, the prolonged sound mark and the kana
# iteration marks included.  The kana script restore below only ever
# rewrites tokens that match this: hira2kata would turn a kanji word's
# okurigana into katakana too (冬休み -> 冬休ミ).
_KANA_ONLY_RE = re.compile(r"^[\u3041-\u309F\u30A1-\u30FA\u30FC-\u30FE]+$")


def _is_kana_only(token):
    "True for a token written entirely in hiragana/katakana."
    return bool(token) and _KANA_ONLY_RE.match(token) is not None


def _kana_script_restorer(original_tokens, language):
    """
    Return token -> token, re-rendering the transcription's kana in the
    script the book wrote the same word in.

    Scoring folds kana on both sides (see _make_key_fn), so a book's
    わくわく and an engine's ワクワク are the same word; the difference
    shows up only in the panel, where a "heard" line spelling a word
    differently from the sentence right above it reads as a
    mis-transcription.  SenseVoice is especially prone to it: it answers
    hiragana onomatopoeia (わくわく) in katakana.

    The sentence's own tokens decide, word by word, keyed on the same
    hiragana-folded form scoring compares on.  A kana token with no
    counterpart in the sentence (something the engine invented) keeps
    whatever script it came back in, and a mixed kanji/kana token never
    sets a script.  Non-Japanese languages are a no-op.
    """
    scripts = {}
    if is_japanese_language(language):
        for raw in original_tokens or []:
            token = _clean_token(raw)
            if not _is_kana_only(token):
                continue
            folded = jaconv.kata2hira(token)
            scripts.setdefault(folded, "katakana" if folded != token else "hiragana")

    def restore(token):
        cleaned = _clean_token(token)
        script = (
            scripts.get(jaconv.kata2hira(cleaned)) if _is_kana_only(cleaned) else None
        )
        if script == "katakana":
            return jaconv.hira2kata(token)
        if script == "hiragana":
            return jaconv.kata2hira(token)
        return token

    return restore


# Unvoiced base of each voiced kana.  ASR very commonly swaps voicing
# on weak syllables (そうですか heard as そうです が); a key pair that
# matches once the dakuten/handakuten marks are stripped is a near-miss
# (fuzzy), not a complete mismatch (miss).
_DAKUTEN_BASE = {
    "が": "か",
    "ぎ": "き",
    "ぐ": "く",
    "げ": "け",
    "ご": "こ",
    "ざ": "さ",
    "じ": "し",
    "ず": "す",
    "ぜ": "せ",
    "ぞ": "そ",
    "だ": "た",
    "ぢ": "ち",
    "づ": "つ",
    "で": "て",
    "ど": "と",
    "ば": "は",
    "び": "ひ",
    "ぶ": "ふ",
    "べ": "へ",
    "ぼ": "ほ",
    "ぱ": "は",
    "ぴ": "ひ",
    "ぷ": "ふ",
    "ぺ": "へ",
    "ぽ": "ほ",
    "ゔ": "う",
}


def _strip_dakuten(key):
    return "".join(_DAKUTEN_BASE.get(ch, ch) for ch in key)


def _make_key_fn(language):
    """
    Return token -> comparable diff key, with the parser resolved once.

    Japanese compares kana readings, so 良い read as いい still matches;
    Chinese languages fold both sides to simplified Han (see
    _simplified_converter); everything else compares lowercased surface
    forms.  When no reading is available (e.g. the japanese_reading
    setting is unset) the surface form is used as-is -- folded to
    hiragana, since a kana word is its own reading (see below).
    """
    parser = language.parser
    t2s = _simplified_converter() if _is_chinese_language(language) else None

    def key(token):
        token = _clean_token(token)
        if not token:
            return ""
        if is_japanese_language(language):
            try:
                reading = parser.get_reading(token)
            except Exception:  # pylint: disable=broad-exception-caught
                reading = None
            # The parser answers None for a token that is already kana
            # (Sudachi reports a reading only when it differs from the
            # surface), so the surface is the reading and must be folded
            # too: without that, a book's わくわく never matches the
            # ワクワク an ASR engine returns for the same word, and the
            # two keys share no characters at all (ratio 0.0, a flat
            # miss).  kata2hira leaves kanji alone, so the reading path
            # is unaffected.
            return jaconv.kata2hira((reading or token).strip())
        try:
            out = parser.get_lowercase(token)
        except Exception:  # pylint: disable=broad-exception-caught
            out = token.lower()
        return t2s.convert(out) if t2s is not None else out

    return key


def _context_keys(tokens, full_text, language, base_key_of):
    """
    Token keys from one contextual parse of the sentence, falling back to
    the per-token base keys.

    The panel's furigana is drawn from a contextual parse of the whole
    sentence (annotate_tokens), and scoring must judge the user against
    the SAME readings: the isolated per-token lookup can pick a different
    morpheme split than the sentence does (香山 is かやま alone but
    こうやま in its sentence), which would grade a correctly-read word
    against a reading the user was never shown.  The transcription side
    runs the same machinery with the heard text as its context, so the
    "heard" furigana and the spoken keys agree as well.

    A token the morphemes cannot rebuild (or a sentence that fails to
    parse) falls back to the base key, per token.
    """
    if not is_japanese_language(language) or not (full_text or "").strip():
        return [base_key_of(t) for t in tokens]
    try:
        morphs = language.parser.get_context_readings(full_text)
    except Exception:  # pylint: disable=broad-exception-caught
        morphs = None
    if not morphs:
        return [base_key_of(t) for t in tokens]
    aligned = _align_context_readings(tokens, morphs)
    if aligned is None:
        return [base_key_of(t) for t in tokens]
    keys = []
    for entry, raw in zip(aligned, tokens):
        reading = (entry.get("reading") or "").strip()
        keys.append(jaconv.kata2hira(reading) if reading else base_key_of(raw))
    return keys


# Pinyin tone marks -> plain vowel + tone digit.  pypinyin emits marked
# vowels (mā) while pycantonese emits digits (maa1); folding both to
# syllable+digit gives the fuzzy rescue one comparable shape, and keeps
# a tone slip (妈 mā read as 骂 mà) eligible as a near-miss.
_TONE_MARKS = {
    "ā": "a1",
    "á": "a2",
    "ǎ": "a3",
    "à": "a4",
    "ē": "e1",
    "é": "e2",
    "ě": "e3",
    "è": "e4",
    "ī": "i1",
    "í": "i2",
    "ǐ": "i3",
    "ì": "i4",
    "ō": "o1",
    "ó": "o2",
    "ǒ": "o3",
    "ò": "o4",
    "ū": "u1",
    "ú": "u2",
    "ǔ": "u3",
    "ù": "u4",
    "ǖ": "v1",
    "ǘ": "v2",
    "ǚ": "v3",
    "ǜ": "v4",
}


def _normalize_reading(reading):
    "A parser reading -> compact sound key (lowercase, digit tones, no spaces)."
    return (
        "".join(_TONE_MARKS.get(ch, ch) for ch in (reading or "").lower()).replace(
            " ", ""
        )
        or None
    )


def _reading_syllables(reading):
    """
    A normalized sound key -> its syllables.

    Jyutping and digit-toned pinyin syllables both end in a tone digit,
    the only digit in a key, so the split needs no dictionary
    (si2jing1waa4 -> [si2, jing1, waa4]).  Raw characters that leaked
    into the key (unromanizable pieces) contribute nothing, which just
    keeps those pairs out of the syllable checks.
    """
    return re.findall(r"[a-z]+\d", reading or "")


def _syllables_contained(needle, haystack):
    """
    True if the needle's syllables all match a contiguous run of the
    haystack's syllables, each pair at least FUZZY_MATCH_RATIO similar.
    """
    if not needle or len(needle) > len(haystack):
        return False
    for start in range(len(haystack) - len(needle) + 1):
        run = haystack[start : start + len(needle)]
        if all(
            difflib.SequenceMatcher(None, a, b).ratio() >= FUZZY_MATCH_RATIO
            for a, b in zip(needle, run)
        ):
            return True
    return False


def _make_sound_fn(language):
    """
    Return token -> normalized reading candidates for the fuzzy rescue,
    or None.

    The diff keys for Chinese are Han surface forms, so a misread
    character is graphically unrelated to the target and scores a flat
    miss.  The rescue re-judges such a pair through the parser's own
    readings -- jyutping for Cantonese, pinyin for Mandarin -- so 你 read
    as 李 (nei5/lei5) is the misread it sounds like, not a skipped word.

    The parser may offer several readings per token (get_readings): a
    dictionary's single pick can be the wrong sense of a polyphone (阿
    filed under o1, while the name prefix is aa3), which would score a
    correctly pronounced word a flat miss.  Pairs are judged on their
    most similar candidate; with no get_readings, the plain
    get_reading answer is the only candidate.
    """
    if not _is_chinese_language(language):
        return None
    parser = language.parser
    readings_of = getattr(parser, "get_readings", None)

    def sound(token):
        token = _clean_token(token)
        if not token:
            return []
        if readings_of is not None:
            try:
                raw = readings_of(token)
            except Exception:  # pylint: disable=broad-exception-caught
                raw = None
            if raw:
                out = []
                for r in raw:
                    key = _normalize_reading(r)
                    if key and key not in out:
                        out.append(key)
                if out:
                    return out
        try:
            reading = parser.get_reading(token)
        except Exception:  # pylint: disable=broad-exception-caught
            reading = None
        key = _normalize_reading(reading)
        return [key] if key else []

    return sound


def compare_tokens(
    original_tokens,
    spoken_text,
    language,
    spoken_tokens=None,
    original_full_text=None,
):
    """
    Diff the user's transcription against the sentence's word tokens.

    original_tokens: the sentence's word spans' data-text, in DOM order
      (the ground truth the verdict indexes are aligned against).
    spoken_text: raw whisper transcription of the user's recording.
    spoken_tokens: optional pre-parsed transcription tokens (from
      _spoken_tokens); the caller may already have them for the furigana
      annotation and re-parsing would only repeat the parser work.
    original_full_text: the sentence the tokens belong to, when the
      client sent it.  Japanese keys are then read in sentence context
      (see _context_keys), judging the user against the readings the
      panel actually displays; without it the isolated per-token
      readings are used, as before.

    Returns {
      "statuses": [STATUS_* per original token],
      "spoken_for_fuzzy": {original index: spoken token},
      "extras": [spoken tokens that aren't part of the sentence],
      "matched": int, "fuzzy": int, "total": int,
      "spoken_count": int, "score": 0-100,
    }

    Tokens without any letter/digit (punctuation, on either side) are
    never scored and get STATUS_SKIP, so parser quirks that attach or
    tag punctuation cannot skew the verdicts.
    """
    statuses = [STATUS_MISS] * len(original_tokens)
    result = {
        "statuses": statuses,
        "spoken_for_fuzzy": {},
        "extras": [],
        "matched": 0,
        "fuzzy": 0,
        "total": 0,
        "spoken_count": 0,
        "score": 100,
    }

    base_key_of = _make_key_fn(language)
    sound_of = _make_sound_fn(language)
    is_japanese = is_japanese_language(language)
    spoken = (
        spoken_tokens
        if spoken_tokens is not None
        else _spoken_tokens(spoken_text, language)
    )
    result["spoken_count"] = len(spoken)

    if not original_tokens:
        return result

    orig_keys = _context_keys(
        original_tokens, original_full_text, language, base_key_of
    )
    # The transcription is a sentence too: its keys follow the same
    # contextual parse the "heard" furigana is drawn from, so a word the
    # engine kanji-ized is judged by the reading the panel shows the
    # user, not by an isolated dictionary lookup.
    spoken_keys = _context_keys(spoken, spoken_text, language, base_key_of)

    # Punctuation-only or empty original tokens (parser quirks,
    # rendering artifacts) can never be spoken; skip them entirely --
    # no mark, and out of the score.
    scored = []
    for i, k in enumerate(orig_keys):
        raw = _clean_token(original_tokens[i])
        if k and _has_word_chars(raw):
            scored.append(i)
        else:
            statuses[i] = STATUS_SKIP
    keyed = [orig_keys[i] for i in scored]
    result["total"] = len(keyed)

    def align_replace(i1, i2, j1, j2):
        """
        Pair a replace block positionally; near-matches become misreads.

        Before the positional pairing, absorb tokenization drift: a word
        spoken correctly can come back split or merged (the page's term
        見たい vs the parser's 見 + たい), which positional pairing alone
        scores as a miss plus a bogus extra.  When one side of the block
        is a single token and exactly the join of the other side's keys,
        the word was said right.

        The same drift can re-cut several words at once: SenseVoice may
        answer the sentence's 呢 + 個 + 係 + 阿樂 with the parser's 呢个
        + 系阿乐.  Scan for points where the joined keys from both sides
        meet; each such run was spoken correctly and is a match, and
        only the genuinely different remainder is paired positionally.

        The pairing is 1:1, so a word inside a spoken chunk that covered
        several words at once (侍應話 heard as the name 史英華) has no
        counterpart of its own; those left behind get a final
        containment pass before being called misses.
        """
        if i2 - i1 == 1 and j2 - j1 > 1 and keyed[i1] == "".join(spoken_keys[j1:j2]):
            statuses[scored[i1]] = STATUS_MATCH
            return
        if j2 - j1 == 1 and i2 - i1 > 1 and spoken_keys[j1] == "".join(keyed[i1:i2]):
            for k in range(i1, i2):
                statuses[scored[k]] = STATUS_MATCH
            return

        oi, si = i1, j1
        while oi < i2 and si < j2:
            no, ns = oi, si
            ojoin, sjoin = "", ""
            while (no < i2 or ns < j2) and not (ojoin and ojoin == sjoin):
                if len(ojoin) <= len(sjoin) and no < i2:
                    ojoin += keyed[no]
                    no += 1
                elif ns < j2:
                    sjoin += spoken_keys[ns]
                    ns += 1
                else:
                    ojoin += keyed[no]
                    no += 1
            if not (ojoin and ojoin == sjoin):
                # The joins never meet: a real difference.  Pair the
                # rest positionally, as if the block had no drift.
                break
            for k in range(oi, no):
                statuses[scored[k]] = STATUS_MATCH
            oi, si = no, ns

        n = min(i2 - oi, j2 - si)
        for k in range(n):
            okey, skey = keyed[oi + k], spoken_keys[si + k]
            if is_japanese and len(okey) > 1 and okey in skey:
                # The heard chunk contains the word's kana verbatim: the
                # word was said, with the engine gluing extra material
                # around it (こうやま transcribed as the non-word 紅う山,
                # whose reading あこうやま wraps the kana).  Said as-is,
                # not a near-miss.
                statuses[scored[oi + k]] = STATUS_MATCH
                continue
            threshold = FUZZY_MATCH_RATIO_JA if is_japanese else FUZZY_MATCH_RATIO
            ratio = difflib.SequenceMatcher(None, okey, skey).ratio()
            if ratio < threshold and is_japanese:
                # Voicing is the one difference a ratio of 0 hides:
                # か heard as が is a near-miss, not a different word.
                voiced = difflib.SequenceMatcher(
                    None, _strip_dakuten(okey), _strip_dakuten(skey)
                ).ratio()
                if voiced >= threshold:
                    ratio = voiced
            elif ratio < threshold and sound_of is not None:
                # A Chinese misread is a different character, so the
                # surface forms share nothing; re-judge the pair on the
                # romanization before calling it a skip.  Pairs are
                # judged on their most similar readings: the dictionary
                # pick can be the wrong polyphone sense (阿 o1 vs the
                # name-prefix aa3).
                best = 0.0
                for oread in sound_of(original_tokens[scored[oi + k]]):
                    for sread in sound_of(spoken[si + k]):
                        voiced = difflib.SequenceMatcher(None, oread, sread).ratio()
                        if voiced > best:
                            best = voiced
                if best >= threshold:
                    ratio = best
            if ratio >= threshold:
                orig_index = scored[oi + k]
                statuses[orig_index] = STATUS_FUZZY
                result["spoken_for_fuzzy"][orig_index] = spoken[si + k]
            # else: too far apart -- stays a miss.

        # A last pass for the words the 1:1 pairing left behind.  The
        # spoken side can cover several sentence words with one chunk
        # that is not the exact join of their keys -- the drift runs
        # above need every piece to agree -- because SenseVoice glues
        # adjacent words into one token: 侍應話 heard as the name 史英華,
        # or 拍手 + 大聲笑 as the non-word 拍笑大聲笑.  A left-behind word
        # whose key occurs verbatim inside one of the block's spoken
        # tokens was said as-is; one whose syllables occur there was at
        # least attempted.  Verbatim claims are tracked per spoken token
        # so two left-behind words cannot share the same characters.
        claimed = {j: [] for j in range(j1, j2)}
        for k in range(i1, i2):
            orig_index = scored[k]
            if statuses[orig_index] != STATUS_MISS:
                continue
            okey = keyed[k]
            oreads = sound_of(original_tokens[orig_index]) if sound_of else []
            for j in range(j1, j2):
                skey = spoken_keys[j]
                if not skey:
                    continue
                hit = False
                if len(okey) > 1:
                    pos = skey.find(okey)
                    while pos != -1:
                        span = (pos, pos + len(okey))
                        if all(
                            pos >= end or pos + len(okey) <= start
                            for start, end in claimed[j]
                        ):
                            claimed[j].append(span)
                            hit = True
                            break
                        pos = skey.find(okey, pos + 1)
                if hit:
                    statuses[orig_index] = STATUS_MATCH
                    break
                sreads = sound_of(spoken[j]) if sound_of else []
                for oread in oreads:
                    osyls = _reading_syllables(oread)
                    if not osyls:
                        continue
                    if any(
                        _syllables_contained(osyls, _reading_syllables(sread))
                        for sread in sreads
                    ):
                        statuses[orig_index] = STATUS_FUZZY
                        result["spoken_for_fuzzy"][orig_index] = spoken[j]
                        hit = True
                        break
                if hit:
                    break

        # Spoken tokens without an original counterpart are extras.
        result["extras"].extend(spoken[si + n : j2])

    matcher = difflib.SequenceMatcher(a=keyed, b=spoken_keys, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                statuses[scored[k]] = STATUS_MATCH
        elif tag == "replace":
            align_replace(i1, i2, j1, j2)
        elif tag == "insert":
            result["extras"].extend(spoken[j1:j2])
        # "delete": original tokens never spoken -- stay misses.

    result["matched"] = sum(1 for s in statuses if s == STATUS_MATCH)
    result["fuzzy"] = sum(1 for s in statuses if s == STATUS_FUZZY)
    # Near-misses get half credit: the word was recognisably attempted.
    if result["total"] > 0:
        result["score"] = round(
            100 * (result["matched"] + 0.5 * result["fuzzy"]) / result["total"]
        )
    return result


# ---------------------------------------------------------------------------
# Background task machinery.
#
# Scoring runs on a daemon thread instead of in the request: the first
# transcription loads the whisper model (hundreds of MB, possibly
# downloaded on the spot), which is far beyond any reverse proxy's
# timeout.  The POST returns a task_id immediately and the reading page
# polls task_status.
# ---------------------------------------------------------------------------

_TASKS = {}
_TASKS_LOCK = threading.Lock()
_TASK_TTL_SECONDS = 10 * 60  # terminal tasks stay queryable for 10 minutes


def _set_task(task_id, state, result=None, error=None):
    with _TASKS_LOCK:
        _TASKS[task_id] = {
            "state": state,
            "result": result,
            "error": error,
            "ts": time.time(),
        }


def purge_finished_tasks():
    "Drop terminal tasks past the TTL so the registry stays small."
    cutoff = time.time() - _TASK_TTL_SECONDS
    with _TASKS_LOCK:
        stale = [
            tid
            for tid, t in _TASKS.items()
            if t["state"] in ("finished", "error") and t["ts"] < cutoff
        ]
        for tid in stale:
            del _TASKS[tid]


def start_task(
    app,
    audio_path,
    language_id,
    tokens,
    model_size,
    username=None,
    full_text=None,
):
    """
    Register and launch a background scoring task.

    audio_path is a temp file (already on disk); the task deletes it.
    username is the requesting user (multi-user mode); the task thread
    re-enters that user's scope so its db access lands on the user's
    own sqlite file.  full_text is the sentence the tokens belong to
    (Japanese reads its keys in context -- see compare_tokens).
    Returns the task_id.
    """
    purge_finished_tasks()
    task_id = uuid.uuid4().hex
    _set_task(task_id, "queued")
    thread = threading.Thread(
        target=_run_task,
        args=(
            app,
            task_id,
            audio_path,
            language_id,
            tokens,
            model_size,
            username,
            full_text,
        ),
        daemon=True,
    )
    thread.start()
    return task_id


def _run_task(
    app,
    task_id,
    audio_path,
    language_id,
    tokens,
    model_size,
    username,
    full_text=None,
):
    "Thread body: transcribe, diff, store the result. Cleans its temp file."
    try:
        # user_scope is a no-op in single-user mode (username=None); in
        # multi-user mode the ContextVar does not cross threads, so the
        # scope must be re-set here or the db creator raises.
        with app.app_context(), mu_context.user_scope(username):
            lang = LanguageRepository(db.session).find(language_id)
            if lang is None:
                raise RuntimeError("language not found")
            # Two states, because they mean different waits: a cold
            # model load (possibly a several-hundred-MB download) can
            # dwarf the transcription itself, and the reading page says
            # which one it is waiting on.  On a warm cache this is a
            # dictionary lookup and the state flips immediately.
            #
            # Engine choice: SenseVoice-Small is the primary engine for
            # zh/yue/en/ja/ko (faster than whisper, and the only engine
            # trained on Cantonese); whisper covers the other languages
            # and any SenseVoice outage.
            sv_lang = sensevoice.lang_code_for(lang)
            if sv_lang is not None and sensevoice.available():
                engine = "sensevoice"
                _set_task(task_id, "loading_model")
                try:
                    sensevoice.ensure_model_downloaded()
                except Exception as e:  # pylint: disable=broad-exception-caught
                    raise RuntimeError(
                        f"could not download the SenseVoice model: {e}"
                    ) from e
                _set_task(task_id, "transcribing")
                text, duration = sensevoice.transcribe_clip(audio_path, sv_lang)
            else:
                engine = "whisper"
                _set_task(task_id, "loading_model")
                try:
                    _load_model(model_size)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    raise RuntimeError(
                        f"could not load the whisper model '{model_size}': {e}  "
                        "(If this was a network timeout downloading the model, set "
                        "HF_ENDPOINT=https://hf-mirror.com in the server environment "
                        "and retry.)"
                    ) from e
                _set_task(task_id, "transcribing")
                text, duration = transcribe_clip(
                    audio_path, whisper_lang_code(lang), model_size
                )
            if not (text or "").strip():
                raise RuntimeError("no speech detected in the recording")
            # Display-side script restore: a traditional book sentence
            # gets its transcription back in traditional Han (SenseVoice
            # emits simplified even for Cantonese).  Scoring is
            # unaffected -- the diff keys fold to simplified regardless.
            text = _match_book_script(text, tokens, lang)
            spoken_tokens = _spoken_tokens(text, lang)
            comparison = compare_tokens(
                tokens,
                text,
                lang,
                spoken_tokens=spoken_tokens,
                original_full_text=full_text,
            )
            rate = None
            if duration and duration > 0:
                rate = round(comparison["spoken_count"] / (duration / 60.0), 1)
            # Display-side kana restore, the Japanese counterpart of the
            # Han one above: the panel shows the word in the script the
            # book used for it.  Applied to the display fields only --
            # scoring folds kana on both sides regardless, so the
            # verdicts are the same either way.
            restore_kana = _kana_script_restorer(tokens, lang)
            result = {
                "transcription": text,
                # Which engine produced the transcription ("sensevoice"
                # or "whisper") -- diagnostic, not scored.
                "engine": engine,
                # Client-displayable warning for a language the chosen
                # engine transcribes poorly (Cantonese via whisper comes
                # back as Mandarin); None most of the time.
                "language_note": (
                    whisper_language_note(lang) if engine == "whisper" else None
                ),
                # The "heard" sentence, tokenised and annotated with
                # furigana readings so the panel can render and pronounce
                # it word by word (same as the original sentence).  The
                # transcription itself is the reading context.
                "transcription_tokens": annotate_tokens(
                    [restore_kana(t) for t in spoken_tokens], lang, full_text=text
                ),
                "statuses": comparison["statuses"],
                "spoken_for_fuzzy": {
                    str(k): restore_kana(v)
                    for k, v in comparison["spoken_for_fuzzy"].items()
                },
                "extras": [restore_kana(t) for t in comparison["extras"]],
                "score": comparison["score"],
                "matched": comparison["matched"],
                "fuzzy": comparison["fuzzy"],
                "total": comparison["total"],
                "duration": round(duration or 0.0, 2),
                "tokens_per_minute": rate,
                "token_kind": "morpheme" if is_japanese_language(lang) else "word",
            }
            _set_task(task_id, "finished", result=result)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logging.getLogger(__name__).warning(
            "shadowing transcription task failed: %s", e
        )
        _set_task(task_id, "error", error=str(e))
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass


def task_status(task_id):
    "Poller payload: {state, result, error} or state=unknown for lost ids."
    with _TASKS_LOCK:
        t = _TASKS.get(task_id)
        if t is None:
            return {"state": "unknown", "result": None, "error": None}
        return {
            "state": t["state"],
            "result": t["result"],
            "error": t["error"],
        }
