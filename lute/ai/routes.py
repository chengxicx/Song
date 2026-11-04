"""
/ai routes: per-user web-session credentials for sentence translation.

Everything here is per user (each multi-user account has its own db),
so these endpoints are not server-level and carry no admin gate.  The
responses only ever contain booleans and a masked suffix; the raw
session token is never serialized back to the browser.
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
    "Select which provider sentence translation should use."
    payload = request.get_json(silent=True) or request.form
    try:
        service.set_selected_provider(payload.get("provider", ""))
    except AiError as e:
        return jsonify({"ok": False, "message": e.message}), 400
    return jsonify({"ok": True, **service.all_status()})


@bp.route("/config/<provider>", methods=["POST"])
def save_config(provider):
    "Store a provider's session token (encrypted) and model name."
    payload = request.get_json(silent=True) or request.form
    try:
        state = service.save_config(
            provider, payload.get("token"), payload.get("model")
        )
    except AiError as e:
        return jsonify({"ok": False, "message": e.message}), 400
    return jsonify({"ok": True, "provider": state})


@bp.route("/clear/<provider>", methods=["POST"])
def clear_config(provider):
    "Forget the stored web session for one provider."
    try:
        state = service.clear_config(provider)
    except AiError as e:
        return jsonify({"ok": False, "message": e.message}), 400
    return jsonify({"ok": True, "provider": state})


@bp.route("/test/<provider>", methods=["POST"])
def test_connection(provider):
    "Round-trip a tiny prompt; never returns the reply or the token."
    result = service.test_connection(provider)
    return jsonify(result), (200 if result["ok"] else 200)


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

    Rendered by Lute itself (so same-origin and never framed by a
    third party), it either streams a translation from the selected
    provider or explains what to configure.  The provider's own web
    chat stays a top-level new-tab link, never an iframe.
    """
    sentence = service.clean_sentence(request.args.get("q", ""))
    provider = service.selected_provider()
    provider_info = service.provider_def(provider) if provider else None
    state = service.provider_status(provider) if provider else None
    configured = bool(state and state["configured"])

    fallback_provider = provider or "qwen"
    return render_template(
        "ai/sentence.html",
        sentence=sentence,
        lang_id=request.args.get("lang", ""),
        provider=provider,
        provider_label=(provider_info or service.providers_fallback_label()),
        configured=configured,
        fallback_url=service.web_url(fallback_provider, sentence),
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
