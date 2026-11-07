"""
/ai routes: per-user API credentials for sentence translation and grammar
analysis.

Everything here is per user (each multi-user account has its own db),
so these endpoints are not server-level and carry no admin gate.  The
responses only ever contain booleans and a masked suffix; the raw API
key is never serialized back to the browser.
"""

import json
import time

from flask import (
    Blueprint,
    Response,
    jsonify,
    render_template,
    request,
    stream_with_context,
)

from lute.ai import library, service
from lute.ai.limiter import INTERACTIVE, LIMITER, normalize_rpm
from lute.ai.service import AiError
from lute.db import db
from lute.i18n import current_language
from lute.models.language import Language

bp = Blueprint("ai", __name__, url_prefix="/ai")


# ---------------------------------------------------------------------
# Settings page helpers
# ---------------------------------------------------------------------


@bp.route("/status", methods=["GET"])
def status():
    "Provider choice, configuration state, and display masks."
    service.ensure_results_migration()
    return jsonify(service.all_status())


@bp.route("/queue", methods=["GET"])
def queue():
    "Per-entry queue length, cooldown and rpm, for the panel and Settings."
    return jsonify({"entries": service.queue_status()})


@bp.route("/library/books", methods=["GET"])
def library_books():
    "The books a user can export explanations for."
    from lute.models.book import Book  # pylint: disable=import-outside-toplevel

    rows = db.session.query(Book).order_by(Book.title).all()
    return jsonify(
        {
            "books": [
                {"id": book.id, "title": book.title, "lang_id": book.language_id}
                for book in rows
            ]
        }
    )


class _LimitedStream:
    "Read an upload without ever holding more than the cap in memory."

    def __init__(self, stream, limit):
        self.stream = stream
        self.limit = limit
        self.total = 0

    def read(self, size=-1):
        chunk = self.stream.read(size)
        self.total += len(chunk)
        if self.total > self.limit:
            raise library.LibraryError("The file is larger than 50 MB.")
        return chunk

    def __iter__(self):
        while True:
            chunk = self.read(65536)
            if not chunk:
                return
            yield chunk

    def seek(self, position):
        self.total = 0
        return self.stream.seek(position)


@bp.route("/library/import", methods=["POST"])
def library_import():
    """
    Import an AI explanation file.  No provider call, no rate limit.

    One bad item is reported and skipped; the rest of the file still
    imports.  The upload lives only in memory (SpooledTemporaryFile) and
    is discarded with the request.
    """
    upload = request.files.get("file")
    if upload is None or not (upload.filename or "").strip():
        return jsonify({"ok": False, "message": "Choose a .json or .jsonl file."}), 400
    stream = _LimitedStream(upload.stream, library.MAX_IMPORT_BYTES)
    overwrite = str(request.form.get("overwrite", "")).lower() in (
        "1", "true", "on", "yes",
    )
    try:
        report = library.import_file(
            stream,
            upload.filename,
            target_lang=request.form.get("target_lang", ""),
            overwrite=overwrite,
        )
    except library.LibraryError as e:
        return jsonify({"ok": False, "message": e.message}), 400
    return jsonify({"ok": True, **report})


@bp.route("/library/export", methods=["GET"])
def library_export():
    "Download one book's stored explanations in the importable format."
    book_id = request.args.get("book_id", "")
    lang_id = request.args.get("lang_id", "")
    target_lang = request.args.get("target_lang", "")
    if not str(book_id).isdigit():
        return jsonify({"ok": False, "message": "book_id is required."}), 400
    try:
        payload = library.export_book(
            int(book_id),
            lang_id=int(lang_id) if str(lang_id).isdigit() else None,
            target_lang=target_lang,
        )
    except (library.LibraryError, ValueError) as e:
        message = getattr(e, "message", str(e))
        return jsonify({"ok": False, "message": message}), 400
    body = json.dumps(payload, ensure_ascii=False)
    return Response(
        body,
        mimetype="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="ai-explanations-book-{book_id}.json"'
            ),
        },
    )


@bp.route("/results/delete", methods=["POST"])
def results_delete():
    "Delete stored results by book and/or language.  No automatic expiry."
    payload = request.get_json(silent=True) or request.form
    book_id = payload.get("book_id")
    lang_id = payload.get("lang_id")
    result_type = (payload.get("result_type") or "").strip() or None
    if book_id in ("", None) and lang_id in ("", None):
        return (
            jsonify({"ok": False, "message": "Pass book_id or lang_id."}),
            400,
        )
    try:
        book_id = int(book_id) if book_id not in ("", None) else None
        lang_id = int(lang_id) if lang_id not in ("", None) else None
    except (TypeError, ValueError):
        return jsonify({"ok": False, "message": "book_id/lang_id must be integers."}), 400
    removed = service.delete_results(book_id=book_id, lang_id=lang_id,
                                     result_type=result_type)
    return jsonify({"ok": True, "removed": removed})


@bp.route("/provider", methods=["POST"])
def set_provider():
    "Select which API provider sentence translation should use."
    payload = request.get_json(silent=True) or request.form
    try:
        service.set_selected_provider(payload.get("provider", ""))
    except AiError as e:
        return jsonify({"ok": False, "message": e.message}), 400
    return jsonify({"ok": True, **service.all_status()})


# ---------------------------------------------------------------------
# Official API providers (keys, models, connection tests)
# ---------------------------------------------------------------------


def _form_bool(value):
    "Checkbox/form values arrive as strings; absence stays None."
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "on", "yes")


@bp.route("/grammar/status", methods=["GET"])
def grammar_status():
    "Grammar provider choice plus masks; never the key."
    return jsonify(service.all_grammar_status())


@bp.route("/grammar/config/<provider>", methods=["POST"])
def grammar_config(provider):
    "Store a Base URL, model and encrypted API key for one provider."
    payload = request.get_json(silent=True) or request.form
    enabled = payload.get("enabled")
    if enabled is not None:
        enabled = _form_bool(enabled)
    try:
        state = service.save_grammar_config(
            provider,
            token=payload.get("token"),
            base_url=payload.get("base_url"),
            model=payload.get("model"),
            enabled=enabled,
        )
    except AiError as e:
        return jsonify({"ok": False, "message": e.message}), 400
    return jsonify({"ok": True, **service.all_grammar_status(), "provider": state})


@bp.route("/grammar/clear/<provider>", methods=["POST"])
def grammar_clear(provider):
    "Forget the stored API key for one grammar provider."
    try:
        state = service.clear_grammar_config(provider)
    except AiError as e:
        return jsonify({"ok": False, "message": e.message}), 400
    return jsonify({"ok": True, **service.all_grammar_status(), "provider": state})


@bp.route("/grammar/test/<provider>", methods=["POST"])
def grammar_test(provider):
    "Round-trip a tiny prompt; never returns the reply or the key."
    result = service.test_grammar_connection(provider)
    return jsonify(result)


@bp.route("/providers/test", methods=["POST"])
def provider_test():
    """
    Test one settings row, saved or not.

    The settings page sends the row's current format/base URL/model/key
    straight from the browser, so a typo can be checked before saving.
    A blank key falls back to the stored one for an existing row; the
    response never carries the key or the model's reply.
    """
    payload = request.get_json(silent=True) or request.form
    result = service.test_provider_connection(
        payload.get("format"),
        payload.get("base_url"),
        payload.get("model"),
        payload.get("api_key") or payload.get("token") or "",
        payload.get("id") or "",
    )
    return jsonify(result)


# ---------------------------------------------------------------------
# Sentence translation
# ---------------------------------------------------------------------


def _sse(event, data):
    "One server-sent event."
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# 429 handling: up to three retries with 2/4/8s backoff, then the entry cools for
# 60s and the request waits in the bucket instead of hammering the API.
_RATE_BACKOFF = (2, 4, 8)
_COOLDOWN_SECONDS = 60
_MAX_COOLDOWNS = 1


def _sleep(seconds):
    "Sleep in short slices so a closed stream stops waiting promptly."
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.5, remaining))


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _acquire_events(provider_id, rpm):
    """
    Yield queued/cooling state events until a token is granted.

    Returns True when the caller may send.  Re-sends the current state
    every 20 seconds so a long wait never looks like a stuck panel.
    """
    ticket = LIMITER.reserve(provider_id, INTERACTIVE)
    last = None
    last_at = 0.0
    try:
        while True:
            status = LIMITER.poll(ticket, rpm)
            if status["state"] == "granted":
                return True
            if status["state"] == "cancelled":
                return False
            now = time.monotonic()
            key = (status["state"], status["ahead"], status["eta"])
            if key != last or (now - last_at) >= 20:
                state = "cooling" if status["state"] == "cooling" else "queued"
                yield _sse(
                    "state",
                    {
                        "state": state,
                        "ahead": status["ahead"],
                        "eta": status["eta"],
                        "cooling": status["cooling"],
                    },
                )
                last, last_at = key, now
            time.sleep(0.35)
    finally:
        LIMITER.cancel(ticket)


def _stream_events(provider_id, rpm, chunks, collected, usage, mid_state=None):
    """
    Yield text/state events for one outbound request, transparently
    retrying 429s.  ``collected`` receives the full reply; ``usage`` the
    token counts when the upstream sends them.  Returns True on success.
    """
    failures = 0
    cooldowns = 0
    seen_mid = None
    while True:
        granted = yield from _acquire_events(provider_id, rpm)
        if not granted:
            return False
        yield _sse("state", {"state": "streaming"})
        try:
            collected["text"] = ""
            for chunk in chunks():
                collected["text"] += chunk
                yield _sse("text", {"text": chunk})
                if mid_state is not None:
                    name = mid_state(collected["text"])
                    if name and name != seen_mid:
                        seen_mid = name
                        yield _sse("state", {"state": name})
            return True
        except AiError as e:
            if e.kind != "rate":
                raise
            failures += 1
            if failures <= len(_RATE_BACKOFF):
                wait = _RATE_BACKOFF[failures - 1]
                if e.retry_after:
                    wait = max(wait, min(e.retry_after, 30))
                yield _sse(
                    "state",
                    {
                        "state": "cooling",
                        "cooling": wait,
                        "eta": wait,
                        "ahead": 0,
                    },
                )
                _sleep(wait)
                continue
            if cooldowns >= _MAX_COOLDOWNS:
                raise
            cooldowns += 1
            failures = 0
            LIMITER.cool(provider_id, _COOLDOWN_SECONDS)
            # _acquire_events() reports the cooldown while it waits.
            continue


def _language(langid):
    "The language for an id, or None when it cannot be resolved."
    try:
        return db.session.get(Language, int(langid))
    except (TypeError, ValueError):
        return None


def _language_context(langid):
    "Source/target names for the prompt, tolerating a missing language."
    lang = _language(langid)
    if lang is None:
        return "", ""
    return (lang.name or "", _language_target_name(lang))


def _language_target_name(lang):
    """
    The language sentence translations and explanations are written in.

    The Language form's "AI analysis & translation language"
    (grammar_translate_lang) is the single source for both grammar
    analysis and sentence translation.  The Google Translate target is
    only a fallback for languages that never set it, then the reader's
    interface language, then English.  (translate_target_lang keeps its
    original Google Translate meaning; it is not changed or removed.)
    """
    return service.target_language_name(lang, current_language())


def _language_feature_enabled(langid, field_name):
    """
    Whether this language may use one AI feature.

    Returns None when the language cannot be resolved (no langid, or a
    stale id): the caller then keeps its old behaviour instead of
    breaking requests from pages that do not carry a language.
    """
    try:
        lang = db.session.get(Language, int(langid))
    except (TypeError, ValueError):
        lang = None
    if lang is None:
        return None
    value = getattr(lang, field_name, True)
    # NULL in a not-yet-migrated or hand-edited row means "on", the default.
    return True if value is None else bool(value)


@bp.route("/sentence", methods=["GET"])
def sentence_page():
    """
    The reading page's Sentence tab frame.

    Rendered by Lute itself (so same-origin), it either streams a
    translation from the selected API provider or explains what to
    configure.  There is no third-party iframe or web-chat link.
    """
    sentence = service.clean_sentence(request.args.get("q", ""))
    langid = request.args.get("lang", "")
    provider = service.sentence_provider(_language(langid))
    provider_info = service.provider_def(provider) if provider else None
    state = service.provider_status(provider) if provider else None
    configured = bool(state and state["configured"])
    disabled = _language_feature_enabled(langid, "ai_sentence_translation") is False

    return render_template(
        "ai/sentence.html",
        sentence=sentence,
        lang_id=langid,
        provider=provider,
        provider_label=(
            provider_info["label"]
            if provider_info
            else service.providers_fallback_label()
        ),
        configured=configured,
        disabled=disabled,
        disabled_reason=(
            "AI sentence translation is turned off for this language.  "
            "Turn it back on in Language settings."
            if disabled
            else ""
        ),
        settings_url="/settings/index",
    )


@bp.route("/translate", methods=["POST"])
def translate():
    """
    Stream one structured sentence analysis as SSE.

    Events: meta (provider/model/cached), text (raw JSON deltas, so the
    page can grow the translation string while the model is still
    writing), result (the parsed object, or a plain-text fallback),
    done, or error.  Errors stay in-band so the iframe keeps its document.
    """
    payload = request.get_json(silent=True) or request.form
    sentence = service.clean_sentence(payload.get("q", ""))
    langid = payload.get("lang", "")
    # The panel's own Regenerate button asks for a fresh provider reply;
    # a plain request still answers from the stored result and never
    # bills the provider twice.  The flag is only honoured for this
    # request, so an ordinary reload goes back to the cache.
    force = str(payload.get("force") or payload.get("regenerate") or "").strip().lower()
    force = force in ("1", "true", "on", "yes")
    source_lang, target_lang = _language_context(langid)
    feature_on = _language_feature_enabled(langid, "ai_sentence_translation")
    book_id = _int_or_none(payload.get("book_id") or payload.get("book"))
    lang_id = _int_or_none(langid)
    # The AI dictionary row may pin a provider for this language; a pin
    # whose key was removed falls back to the shared Settings choice.
    provider = service.sentence_provider(_language(langid))

    def generate():
        try:
            if feature_on is False:
                raise AiError(
                    "AI sentence translation is turned off for this "
                    "language.  Turn it on in Language settings.",
                    "unsupported",
                )
            service.ensure_results_migration()
            ready = service.grammar_ready(provider or None)
            model = ready["model"]
            meta = {}
            cached = None
            if not force:
                # A normal open reuses the stored result (live, prefetch
                # or import).  Regenerate deliberately skips this lookup
                # so the provider is asked again for the same sentence.
                cached = service.sentence_cached_result(
                    sentence,
                    model,
                    source_lang,
                    target_lang,
                    ready["provider"],
                    meta=meta,
                )
            if cached is not None:
                yield _sse(
                    "state",
                    {"state": "cached", "saved_from": meta.get("saved_from", "")},
                )
                yield _sse(
                    "meta",
                    {
                        "provider": ready["provider"],
                        "model": model,
                        "cached": True,
                        "saved_from": meta.get("saved_from", ""),
                    },
                )
                yield _sse("result", {"result": cached})
                yield _sse("done", {})
                return

            yield _sse(
                "meta",
                {
                    "provider": ready["provider"],
                    "model": model,
                    "cached": False,
                    "rpm": ready.get("rpm"),
                    "regenerated": force,
                },
            )
            collected = {"text": ""}
            usage = {}
            delivered = yield from _stream_events(
                ready["provider"],
                ready.get("rpm"),
                lambda: service.translate_stream(
                    ready["provider"],
                    sentence,
                    source_lang,
                    target_lang,
                    usage=usage,
                ),
                collected,
                usage,
                mid_state=lambda text: (
                    "waiting_structure"
                    if service.translation_complete(text)
                    and service.parse_sentence_json(text) is None
                    else None
                ),
            )
            if not delivered:
                return
            parsed = service.parse_sentence_json(collected["text"])
            if parsed is None:
                # Never send the raw reply as body text: prefer its
                # translation field, otherwise a readable retry message.
                readable = service.sentence_fallback_text(collected["text"])
                if not readable:
                    readable = (
                        "AI returned an unparsable response, please retry."
                    )
                yield _sse("state", {"state": "fallback"})
                yield _sse(
                    "result",
                    {"fallback": True, "text": readable, "unparsable": True},
                )
            else:
                # Only a parsed result is stored, so an unparsable reply
                # can never be served again from the cache.
                service.store_sentence_result(
                    sentence,
                    model,
                    source_lang,
                    target_lang,
                    parsed,
                    provider_id=ready["provider"],
                    book_id=book_id,
                    lang_id=lang_id,
                    source="regenerate" if force else "live",
                    token_usage=usage.get("usage"),
                )
                yield _sse("result", {"result": parsed})
            yield _sse("done", {})
        except AiError as e:
            yield _sse(
                "state", {"state": "error", "kind": e.kind, "message": e.message}
            )
            yield _sse("error", {"kind": e.kind, "message": e.message})
        except Exception as e:  # pylint: disable=broad-exception-caught
            message = f"Unexpected translation failure ({type(e).__name__})."
            yield _sse("state", {"state": "error", "kind": "upstream", "message": message})
            yield _sse("error", {"kind": "upstream", "message": message})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------
# Grammar analysis for the reading panel
# ---------------------------------------------------------------------

_UI_LANGUAGE_NAMES = service.UI_LANGUAGE_NAMES


def _grammar_language_context(langid):
    """
    Source language name, plus the display language chosen for this book's
    language in Settings ("Grammar Analysis Language").

    An empty or unknown setting falls back to the reader's UI language,
    which is the behaviour before that column existed.
    """
    try:
        lang = db.session.get(Language, int(langid))
    except (TypeError, ValueError):
        lang = None
    source = (lang.name or "") if lang is not None else ""
    chosen = (getattr(lang, "grammar_translate_lang", "") or "").strip()
    target = _UI_LANGUAGE_NAMES.get(chosen) or _UI_LANGUAGE_NAMES.get(
        current_language(), "English"
    )
    return source, target


@bp.route("/grammar/analyze", methods=["POST"])
def grammar_analyze():
    """
    Stream one structured grammar analysis as SSE.

    Events: meta (provider/model/cached), text (raw deltas for the
    progressive view), result (structured JSON or a plain-text fallback),
    done, or error.  Errors stay in-band so the panel keeps its document.
    """
    payload = request.get_json(silent=True) or request.form
    sentence = service.clean_sentence(payload.get("q", ""))
    langid = payload.get("lang", "")
    source_lang, target_lang = _grammar_language_context(langid)
    feature_on = _language_feature_enabled(langid, "ai_grammar_analysis")

    def generate():
        try:
            if feature_on is False:
                raise AiError(
                    "AI grammar analysis is turned off for this language.  "
                    "Turn it on in Language settings.",
                    "unsupported",
                )
            # Grammar analysis always follows Settings' default entry;
            # per-language pins are only for sentence translation.
            service.ensure_results_migration()
            ready = service.grammar_ready(None)
            provider = ready["provider"]
            model = ready["model"]
            meta = {}
            cached = service.grammar_cached_result(
                sentence, model, source_lang, target_lang, provider, meta=meta
            )
            if cached is not None:
                yield _sse(
                    "state",
                    {"state": "cached", "saved_from": meta.get("saved_from", "")},
                )
                yield _sse(
                    "meta",
                    {
                        "cached": True,
                        "provider": provider,
                        "model": model,
                        "saved_from": meta.get("saved_from", ""),
                    },
                )
                yield _sse("result", {"result": cached})
                yield _sse("done", {})
                return

            yield _sse("meta", {"cached": False, "provider": provider, "model": model})
            collected = {"text": ""}

            usage = {}
            delivered = yield from _stream_events(
                provider,
                ready.get("rpm"),
                lambda: service.grammar_stream(
                    provider,
                    sentence,
                    source_lang,
                    target_lang,
                    usage=usage,
                ),
                collected,
                usage,
                mid_state=lambda text: (
                    "waiting_structure"
                    if service.translation_complete(text)
                    and service.parse_grammar_json(text) is None
                    else None
                ),
            )
            if not delivered:
                return
            full = collected["text"]
            parsed = service.parse_grammar_json(full)
            if parsed is not None:
                service.store_grammar_result(
                    sentence,
                    model,
                    source_lang,
                    target_lang,
                    parsed,
                    provider_id=provider,
                    lang_id=_int_or_none(langid),
                    source="live",
                    token_usage=usage.get("usage"),
                )
                yield _sse("result", {"result": parsed})
            else:
                yield _sse("state", {"state": "fallback"})
                yield _sse("result", {"fallback": True, "text": full})
            yield _sse("done", {})
        except AiError as e:
            yield _sse(
                "state", {"state": "error", "kind": e.kind, "message": e.message}
            )
            yield _sse("error", {"kind": e.kind, "message": e.message})
        except Exception as e:  # pylint: disable=broad-exception-caught
            message = (
                f"Unexpected grammar analysis failure ({type(e).__name__})."
            )
            yield _sse("state", {"state": "error", "kind": "upstream", "message": message})
            yield _sse("error", {"kind": "upstream", "message": message})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
