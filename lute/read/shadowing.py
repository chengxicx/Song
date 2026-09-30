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

import jaconv

from lute.book.whisper_transcribe import (
    ALLOWED_MODEL_SIZES,
    DEFAULT_MODEL_SIZE,
    _load_model,
    whisper_lang_code,
    whisper_status,
)
from lute.read.render.grammar_analysis import is_japanese_language

# Per-word verdicts, parallel to the sentence's word spans.
STATUS_MISS = 0  # never spoken (漏读)
STATUS_FUZZY = 1  # probably misread: near-match token (错读)
STATUS_MATCH = 2  # spoken as-is

# In a replace block, a near-match pair still counts as an attempt
# (misread) instead of a skip.  Pairs are compared on the diff keys,
# i.e. readings for Japanese.
FUZZY_MATCH_RATIO = 0.6


def transcribe_clip(audio_path, lang_code, model_size=DEFAULT_MODEL_SIZE):
    """
    Transcribe one short recording.

    Returns (text, duration_secs, words) where words is
    [{"word": str, "start": secs, "end": secs}] (may be empty when the
    model produces no word timestamps).
    """
    model = _load_model(model_size)
    segments_iter, info = model.transcribe(
        audio_path,
        language=lang_code,
        # Greedy decoding: on a few seconds of speech the accuracy loss
        # vs beam search is negligible and CPU inference is ~2x faster.
        beam_size=1,
        word_timestamps=True,
        # Same guards as the audiobook transcription (see there).
        vad_filter=True,
        condition_on_previous_text=False,
        # No initial_prompt: hinting the expected sentence would let the
        # model echo it back, inflating the score.
    )

    texts = []
    words = []
    for seg in segments_iter:
        if seg.text:
            texts.append(seg.text)
        for w in seg.words or []:
            token = (w.word or "").strip()
            if token:
                words.append({"word": token, "start": w.start, "end": w.end})
    return "".join(texts).strip(), (info.duration or 0.0), words


def _clean_token(token):
    "Strip display artifacts that must never reach the diff."
    return (token or "").replace("\u200B", "").replace("🔊", "").strip()


def _make_key_fn(language):
    """
    Return token -> comparable diff key, with the parser resolved once.

    Japanese compares kana readings, so 良い read as いい still matches;
    everything else compares lowercased surface forms.  When no reading
    is available (e.g. the japanese_reading setting is unset) the
    surface form is used as-is.
    """
    parser = language.parser

    def key(token):
        token = _clean_token(token)
        if not token:
            return ""
        if is_japanese_language(language):
            try:
                reading = parser.get_reading(token)
            except Exception:  # pylint: disable=broad-exception-caught
                reading = None
            if reading:
                return jaconv.kata2hira(reading.strip())
        try:
            return parser.get_lowercase(token)
        except Exception:  # pylint: disable=broad-exception-caught
            return token.lower()

    return key


def compare_tokens(original_tokens, spoken_text, language):
    """
    Diff the user's transcription against the sentence's word tokens.

    original_tokens: the sentence's word spans' data-text, in DOM order
      (the ground truth the verdict indexes are aligned against).
    spoken_text: raw whisper transcription of the user's recording.

    Returns {
      "statuses": [STATUS_* per original token],
      "spoken_for_fuzzy": {original index: spoken token},
      "extras": [spoken tokens that aren't part of the sentence],
      "matched": int, "fuzzy": int, "total": int,
      "spoken_count": int, "score": 0-100,
    }
    """
    statuses = [STATUS_MISS] * len(original_tokens)
    result = {
        "statuses": statuses,
        "spoken_for_fuzzy": {},
        "extras": [],
        "matched": 0,
        "fuzzy": 0,
        "total": len(original_tokens),
        "spoken_count": 0,
        "score": 0,
    }

    key_of = _make_key_fn(language)
    spoken = [
        s
        for s in (
            _clean_token(pt.token)
            for pt in language.get_parsed_tokens(spoken_text or "")
            if pt.is_word
        )
        if s
    ]
    result["spoken_count"] = len(spoken)

    if not original_tokens:
        return result

    orig_keys = [key_of(t) for t in original_tokens]
    spoken_keys = [key_of(s) for s in spoken]

    # Empty original tokens (rendering artifacts) can never match any
    # spoken token; count them as read so they don't drag the score
    # down, and diff only the real tokens.
    scored = []
    for i, k in enumerate(orig_keys):
        if k:
            scored.append(i)
        else:
            statuses[i] = STATUS_MATCH
    keyed = [orig_keys[i] for i in scored]

    def align_replace(i1, i2, j1, j2):
        "Pair a replace block positionally; near-matches become misreads."
        n = min(i2 - i1, j2 - j1)
        for k in range(n):
            ratio = difflib.SequenceMatcher(
                None, keyed[i1 + k], spoken_keys[j1 + k]
            ).ratio()
            if ratio >= FUZZY_MATCH_RATIO:
                orig_index = scored[i1 + k]
                statuses[orig_index] = STATUS_FUZZY
                result["spoken_for_fuzzy"][orig_index] = spoken[j1 + k]
            # else: too far apart -- stays a miss.
        # Spoken tokens without an original counterpart are extras.
        result["extras"].extend(spoken[j1 + n : j2])

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
    result["score"] = round(
        100 * (result["matched"] + 0.5 * result["fuzzy"]) / len(original_tokens)
    )
    return result
