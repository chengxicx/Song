"""
/ai routes: per-user API credentials for sentence translation and grammar
analysis.

Everything here is per user (each multi-user account has its own db),
so these endpoints are not server-level and carry no admin gate.  The
responses only ever contain booleans and a masked suffix; the raw API
key is never serialized back to the browser.
"""

import json

from flask import (
    Blueprint,
    Response,
    jsonify,
    render_template,
    request,
    stream_with_context,
)

from lute.ai import service
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
    return jsonify(service.all_status())


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


# ---------------------------------------------------------------------
# Sentence translation
# ---------------------------------------------------------------------


def _sse(event, data):
    "One server-sent event."
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _language_context(langid):
    "Source/target names for the prompt, tolerating a missing language."
    try:
        lang = db.session.get(Language, int(langid))
    except (TypeError, ValueError):
        lang = None
    if lang is None:
        return "", ""
    return (lang.name or "", lang.translate_target_lang or "")


@bp.route("/sentence", methods=["GET"])
def sentence_page():
    """
    The reading page's Sentence tab frame.

    Rendered by Lute itself (so same-origin), it either streams a
    translation from the selected API provider or explains what to
    configure.  There is no third-party iframe or web-chat link.
    """
    sentence = service.clean_sentence(request.args.get("q", ""))
    provider = service.selected_provider()
    provider_info = service.provider_def(provider) if provider else None
    state = service.provider_status(provider) if provider else None
    configured = bool(state and state["configured"])

    return render_template(
        "ai/sentence.html",
        sentence=sentence,
        lang_id=request.args.get("lang", ""),
        provider=provider,
        provider_label=(
            provider_info["label"]
            if provider_info
            else service.providers_fallback_label()
        ),
        configured=configured,
        settings_url="/settings/index",
    )


@bp.route("/translate", methods=["POST"])
def translate():
    """
    Stream the translation as SSE: text events, then done, or error.

    Errors are events, not HTTP failures: the frame navigates once and
    the page's JS keeps rendering the same document.
    """
    payload = request.get_json(silent=True) or request.form
    sentence = service.clean_sentence(payload.get("q", ""))
    langid = payload.get("lang", "")
    source_lang, target_lang = _language_context(langid)

    def generate():
        try:
            for chunk in service.translate_stream(
                service.selected_provider(), sentence, source_lang, target_lang
            ):
                yield _sse("text", {"text": chunk})
            yield _sse("done", {})
        except AiError as e:
            yield _sse("error", {"kind": e.kind, "message": e.message})
        except Exception as e:  # pylint: disable=broad-exception-caught
            yield _sse(
                "error",
                {
                    "kind": "upstream",
                    "message": f"Unexpected translation failure ({type(e).__name__}).",
                },
            )

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

_UI_LANGUAGE_NAMES = {
    "en": "English",
    "zh": "Simplified Chinese",
    "ja": "Japanese",
    "ko": "Korean",
}


def _grammar_language_context(langid):
    "Source language name plus the reader's UI language, for the prompt."
    try:
        lang = db.session.get(Language, int(langid))
    except (TypeError, ValueError):
        lang = None
    source = (lang.name or "") if lang is not None else ""
    target = _UI_LANGUAGE_NAMES.get(current_language(), "English")
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
    requested_provider = (payload.get("provider") or "").strip()

    def generate():
        try:
            ready = service.grammar_ready(requested_provider or None)
            provider = ready["provider"]
            model = ready["model"]
            cached = service.grammar_cached_result(
                sentence, model, source_lang, target_lang
            )
            if cached is not None:
                yield _sse(
                    "meta",
                    {"cached": True, "provider": provider, "model": model},
                )
                yield _sse("result", {"result": cached})
                yield _sse("done", {})
                return

            yield _sse("meta", {"cached": False, "provider": provider, "model": model})
            parts = []
            for chunk in service.grammar_stream(
                provider, sentence, source_lang, target_lang
            ):
                parts.append(chunk)
                yield _sse("text", {"text": chunk})

            full = "".join(parts)
            parsed = service.parse_grammar_json(full)
            if parsed is not None:
                service.store_grammar_result(
                    sentence, model, source_lang, target_lang, parsed
                )
                yield _sse("result", {"result": parsed})
            else:
                yield _sse("result", {"fallback": True, "text": full})
            yield _sse("done", {})
        except AiError as e:
            yield _sse("error", {"kind": e.kind, "message": e.message})
        except Exception as e:  # pylint: disable=broad-exception-caught
            yield _sse(
                "error",
                {
                    "kind": "upstream",
                    "message": f"Unexpected grammar analysis failure ({type(e).__name__}).",
                },
            )

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
