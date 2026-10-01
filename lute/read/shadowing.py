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
import threading
import time
import uuid

import jaconv

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


def _clean_token(token):
    "Strip display artifacts that must never reach the diff."
    return (token or "").replace("\u200B", "").replace("🔊", "").strip()


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
    """
    return [
        s
        for s in (
            _clean_token(pt.token)
            for pt in language.get_parsed_tokens(spoken_text or "")
            if pt.is_word
        )
        if s and _has_word_chars(s)
    ]


def annotate_tokens(tokens, language):
    """
    Pair each token with its kana reading for the shadowing panel.

    Returns [{"text": surface, "reading": kana-or-None}, ...], parallel to
    the input.  The panel draws the reading above the word (furigana) and
    speaks it when the word is tapped; readings come from the language's
    own parser, the same source term lookups use.  Non-Japanese languages
    (and the japanese_reading setting being off) yield reading=None, so
    the panel simply shows the plain word.
    """
    if not is_japanese_language(language):
        return [{"text": _clean_token(t), "reading": None} for t in tokens]

    parser = language.parser
    out = []
    for t in tokens:
        text = _clean_token(t)
        reading = None
        if text:
            try:
                reading = parser.get_reading(text)
            except Exception:  # pylint: disable=broad-exception-caught
                reading = None
        out.append({"text": text, "reading": (reading or "").strip() or None})
    return out


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


def compare_tokens(original_tokens, spoken_text, language, spoken_tokens=None):
    """
    Diff the user's transcription against the sentence's word tokens.

    original_tokens: the sentence's word spans' data-text, in DOM order
      (the ground truth the verdict indexes are aligned against).
    spoken_text: raw whisper transcription of the user's recording.
    spoken_tokens: optional pre-parsed transcription tokens (from
      _spoken_tokens); the caller may already have them for the furigana
      annotation and re-parsing would only repeat the parser work.

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

    key_of = _make_key_fn(language)
    spoken = (
        spoken_tokens
        if spoken_tokens is not None
        else _spoken_tokens(spoken_text, language)
    )
    result["spoken_count"] = len(spoken)

    if not original_tokens:
        return result

    orig_keys = [key_of(t) for t in original_tokens]
    spoken_keys = [key_of(s) for s in spoken]

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


def start_task(app, audio_path, language_id, tokens, model_size, username=None):
    """
    Register and launch a background scoring task.

    audio_path is a temp file (already on disk); the task deletes it.
    username is the requesting user (multi-user mode); the task thread
    re-enters that user's scope so its db access lands on the user's
    own sqlite file.  Returns the task_id.
    """
    purge_finished_tasks()
    task_id = uuid.uuid4().hex
    _set_task(task_id, "queued")
    thread = threading.Thread(
        target=_run_task,
        args=(app, task_id, audio_path, language_id, tokens, model_size, username),
        daemon=True,
    )
    thread.start()
    return task_id


def _run_task(app, task_id, audio_path, language_id, tokens, model_size, username):
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
                audio_path, whisper_lang_code(lang, model_size), model_size
            )
            if not (text or "").strip():
                raise RuntimeError("no speech detected in the recording")
            spoken_tokens = _spoken_tokens(text, lang)
            comparison = compare_tokens(
                tokens, text, lang, spoken_tokens=spoken_tokens
            )
            rate = None
            if duration and duration > 0:
                rate = round(comparison["spoken_count"] / (duration / 60.0), 1)
            result = {
                "transcription": text,
                # Client-displayable warning for a model/language combo
                # that transcribes poorly (Cantonese on a non-yue model
                # comes back as Mandarin); None most of the time.
                "language_note": whisper_language_note(lang, model_size),
                # The "heard" sentence, tokenised and annotated with
                # furigana readings so the panel can render and pronounce
                # it word by word (same as the original sentence).
                "transcription_tokens": annotate_tokens(spoken_tokens, lang),
                "statuses": comparison["statuses"],
                "spoken_for_fuzzy": {
                    str(k): v
                    for k, v in comparison["spoken_for_fuzzy"].items()
                },
                "extras": comparison["extras"],
                "score": comparison["score"],
                "matched": comparison["matched"],
                "fuzzy": comparison["fuzzy"],
                "total": comparison["total"],
                "duration": round(duration or 0.0, 2),
                "tokens_per_minute": rate,
                "token_kind": "morpheme"
                if is_japanese_language(lang)
                else "word",
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
