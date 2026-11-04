"""
AI provider configuration and translation/grammar adapters.

Sentence translation and grammar analysis both use official OpenAI or
Anthropic API keys, configured per user in Settings.  Browser-session
providers (Qwen, ChatGPT, DeepSeek) are deliberately not used: their
proof-of-work and Cloudflare challenges cannot be replayed from a server.
Existing ``ai_qwen_token`` values in a user's database are simply ignored.

Credential values are dynamic user settings (the same mechanism the
NetEase login uses) and are stored encrypted (lute/ai/crypto.py).
Nothing here ever returns or logs the raw API key.
"""

import hashlib
import json
import re
from urllib.parse import urlparse

import requests

from lute.db import db
from lute.models.repositories import UserSettingRepository
from lute.ai.crypto import decrypt, encrypt

PROVIDER_SETTING_KEY = "ai_sentence_provider"


class AiError(Exception):
    """
    An AI failure the UI should show.

    ``kind`` is one of unconfigured, unsupported, auth, config, rate,
    timeout, upstream, so the page can phrase the message precisely.
    """

    def __init__(self, message, kind="upstream"):
        super().__init__(message)
        self.message = message
        self.kind = kind


# ---------------------------------------------------------------------
# Settings storage
# ---------------------------------------------------------------------


def _repo():
    return UserSettingRepository(db.session)


def _get(key):
    return _repo().get_dynamic_value(key) or ""


def _set(key, value):
    _repo().set_dynamic_value(key, value or "")
    db.session.commit()


def provider_def(provider):
    return _API_PROVIDERS.get(provider)


def providers_fallback_label():
    "The tab/page label when nothing is selected yet."
    return "AI"


def selected_provider():
    "The provider the reader should use, or '' when none is selected."
    p = _get(PROVIDER_SETTING_KEY).strip()
    if p in _API_PROVIDERS:
        return p
    return ""


def set_selected_provider(provider):
    "Persist the reader's provider choice; unknown ones are refused."
    provider = (provider or "").strip()
    if provider and provider not in _API_PROVIDERS:
        raise AiError(f"Unknown AI provider: {provider}", "unconfigured")
    _set(PROVIDER_SETTING_KEY, provider)


def mask_token(token):
    "A display-only mask; never the credential itself."
    if not token:
        return ""
    if len(token) <= 8:
        return "••••••"
    return "••••••" + token[-4:]


def provider_status(provider):
    "Everything the settings page may display about one provider."
    state = grammar_provider_status(provider)
    if state is None:
        return None
    state.update(
        {
            "kind": "api",
            "supported": True,
            "steps": [],
            "unsupported_reason": "",
        }
    )
    return state


def all_status():
    "Status for the settings page, plus the current provider choice."
    return {
        "selected": selected_provider(),
        "providers": [provider_status(p) for p in _API_PROVIDERS],
    }


# ---------------------------------------------------------------------
# Sentence text and prompt
# ---------------------------------------------------------------------


def clean_sentence(text):
    """
    The same cleanup the dictionary tabs apply before replacing [LUTE]:
    drop the zero-width spaces Lute puts between tokens, collapse
    whitespace, trim.
    """
    text = (text or "").replace("\u200b", "")
    return re.sub(r"\s+", " ", text).strip()


def build_translation_system_prompt(source_lang="", target_lang=""):
    "The chat-system half of the translation prompt."
    source = (source_lang or "").strip()
    target = (target_lang or "").strip() or "English"
    origin = f"{source} " if source else ""
    return (
        f"You are a translation engine. Translate the following {origin}"
        f"sentence into {target}. Reply with the translation only, "
        f"without explanations, quotes, or the original text."
    )


# ---------------------------------------------------------------------
# Sentence translation via official APIs
# ---------------------------------------------------------------------


def translate_stream(provider, sentence, source_lang="", target_lang=""):
    """
    Yield translation text chunks from the selected API provider.

    Raises AiError (auth/rate/timeout/upstream) on failure; the route
    turns those into an SSE error event.
    """
    provider = provider or selected_provider()
    if not provider:
        raise AiError(
            "No AI provider is configured for sentence translation. Add an "
            "OpenAI or Anthropic API key in Settings and select it there.",
            "unconfigured",
        )

    sentence = clean_sentence(sentence)
    if sentence == "":
        raise AiError("No sentence text was passed to the translator.", "upstream")

    ready = grammar_ready(provider)
    system_prompt = build_translation_system_prompt(source_lang, target_lang)
    if ready["provider"] == "openai":
        yield from _openai_stream(
            ready["base_url"],
            ready["token"],
            ready["model"],
            system_prompt,
            sentence,
        )
        return
    yield from _anthropic_stream(
        ready["base_url"],
        ready["token"],
        ready["model"],
        system_prompt,
        sentence,
    )


def _text_from_payload(data):
    "Pull text out of the common response shapes, leniently."
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        for holder in (first.get("delta"), first.get("message"), first):
            if isinstance(holder, dict):
                text = holder.get("content")
                if isinstance(text, str):
                    return text
                if isinstance(text, list):
                    parts = [
                        p.get("text", "")
                        for p in text
                        if isinstance(p, dict) and isinstance(p.get("text"), str)
                    ]
                    if parts:
                        return "".join(parts)
        if isinstance(first.get("text"), str):
            return first["text"]
    for key in ("content", "text", "response", "answer"):
        if isinstance(data.get(key), str):
            return data[key]
    return ""


# ---------------------------------------------------------------------
# Official API providers for grammar analysis
#
# Official API providers.  These serve both sentence translation (the
# provider chosen in Settings) and grammar analysis (the provider enabled
# for the Grammar panel).  The two choices are separate settings, but the
# encrypted API key and model are shared, so one key serves both features.
# ---------------------------------------------------------------------

GRAMMAR_PROVIDER_SETTING_KEY = "ai_grammar_provider"

_API_PROVIDERS = {
    "openai": {
        "id": "openai",
        "label": "OpenAI (API key)",
        "default_base_url": "https://api.openai.com/v1",
        "base_url_hint": (
            "e.g. https://api.openai.com/v1, or a proxy / gateway "
            "(a bare host gets /v1 added automatically)"
        ),
        "default_model": "gpt-4o-mini",
        "model_hint": "e.g. gpt-4o-mini",
        "credential_label": "API key",
        "credential_hint": "sk-... (stored encrypted; never shown again)",
        "login_url": "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "id": "anthropic",
        "label": "Anthropic (API key)",
        "default_base_url": "https://api.anthropic.com",
        "base_url_hint": (
            "e.g. https://api.anthropic.com, or a proxy / gateway "
            "(a bare host gets /v1/messages automatically)"
        ),
        "default_model": "claude-sonnet-4-5",
        "model_hint": "e.g. claude-sonnet-4-5",
        "credential_label": "API key",
        "credential_hint": "sk-ant-... (stored encrypted; never shown again)",
        "login_url": "https://console.anthropic.com/settings/keys",
    },
}

_GRAMMAR_CACHE_PREFIX = "ai_grammar_cache_"

_GRAMMAR_JSON_KEYS = (
    "skeleton",
    "clauses",
    "grammar_points",
    "tense_voice",
    "pitfalls",
)

_ANTHROPIC_VERSION = "2023-06-01"

_API_TIMEOUT = (10, 120)

_ANTHROPIC_MAX_TOKENS = 2048


def _api_key_key(provider):
    return f"ai_api_{provider}_token"


def _api_base_url_key(provider):
    return f"ai_api_{provider}_base_url"


def _api_model_key(provider):
    return f"ai_api_{provider}_model"


def selected_grammar_provider():
    "The provider the Grammar panel should use, or '' when none is enabled."
    p = _get(GRAMMAR_PROVIDER_SETTING_KEY).strip()
    if p in _API_PROVIDERS:
        return p
    return ""


def _set_grammar_provider(provider):
    provider = (provider or "").strip()
    if provider and provider not in _API_PROVIDERS:
        raise AiError(f"Unknown grammar AI provider: {provider}", "unsupported")
    _set(GRAMMAR_PROVIDER_SETTING_KEY, provider)


def _api_token(provider):
    return decrypt(_get(_api_key_key(provider)))


def _api_base_url(provider):
    p = _API_PROVIDERS[provider]
    raw = _get(_api_base_url_key(provider)) or p["default_base_url"]
    return normalize_base_url(raw, p["default_base_url"])


def _api_model(provider):
    p = _API_PROVIDERS[provider]
    return _get(_api_model_key(provider)) or p["default_model"]


def normalize_base_url(raw, default=""):
    "A user-editable http(s) base URL; no credentials, no trailing slash."
    text = (raw or "").strip() or (default or "")
    text = text.rstrip("/")
    # Accept a pasted full endpoint as a base, too; the endpoint builder
    # adds the path back exactly once.
    for suffix in ("/chat/completions", "/messages"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    if not text:
        raise AiError("The Base URL cannot be empty.", "config")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AiError("The Base URL must be an http(s) URL.", "config")
    if parsed.username or parsed.password:
        raise AiError("The Base URL must not contain a username or password.", "config")
    return text


def api_endpoint(base_url, provider):
    """
    The request URL for one provider, tolerating ``/v1`` or its absence.

    Gateways publish different bases: ``https://host`` (with the API under
    /v1), ``https://host/v1``, or a self-hosted mount such as
    ``https://host/openai/v1``.  Appending ``/v1`` blindly either repeats
    it (``/v1/v1/chat/completions``) or drops it (``/chat/completions``),
    so the path decides.  Any other prefix is respected as-is.
    """
    base = (base_url or "").rstrip("/")
    path = urlparse(base).path.rstrip("/")
    if provider == "openai":
        endpoint, suffix = "/chat/completions", "/chat/completions"
    else:
        endpoint, suffix = "/messages", "/messages"
    if path.endswith(endpoint):
        return base
    if path.endswith("/v1"):
        return base + suffix
    if path in ("", "/"):
        return base + "/v1" + suffix
    return base + suffix


def grammar_provider_status(provider):
    "Everything the settings page may display; never the raw API key."
    p = _API_PROVIDERS.get(provider)
    if p is None:
        return None
    token = _api_token(provider)
    selected = selected_grammar_provider()
    return {
        "provider": provider,
        "label": p["label"],
        "configured": bool(token),
        "masked": mask_token(token),
        "enabled": selected == provider,
        "base_url": _api_base_url(provider),
        "default_base_url": p["default_base_url"],
        "base_url_hint": p["base_url_hint"],
        "model": _api_model(provider),
        "default_model": p["default_model"],
        "model_hint": p["model_hint"],
        "credential_label": p["credential_label"],
        "credential_hint": p["credential_hint"],
        "login_url": p["login_url"],
    }


def all_grammar_status():
    "The grammar-provider block the settings page renders, with masks only."
    return {
        "selected": selected_grammar_provider(),
        "providers": [grammar_provider_status(p) for p in _API_PROVIDERS],
    }


def save_grammar_config(provider, token=None, base_url=None, model=None, enabled=None):
    """
    Save one API provider's key (encrypted), Base URL and model.

    ``enabled`` is optional: True selects this provider, False deselects it
    (only when it was selected).  The key itself is never echoed back.
    """
    if provider not in _API_PROVIDERS:
        raise AiError(f"Unknown grammar AI provider: {provider}", "unsupported")
    if base_url is not None:
        _set(
            _api_base_url_key(provider),
            normalize_base_url(base_url, _API_PROVIDERS[provider]["default_base_url"]),
        )
    if model is not None:
        model = (model or "").strip()
        if model:
            _set(_api_model_key(provider), model)
    if token is not None:
        token = (token or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token:
            _set(_api_key_key(provider), encrypt(token))

    if enabled:
        if not _api_token(provider):
            raise AiError(
                f"{_API_PROVIDERS[provider]['label']} needs an API key before "
                f"it can be enabled.",
                "config",
            )
        _set_grammar_provider(provider)
    elif enabled is False and selected_grammar_provider() == provider:
        _set_grammar_provider("")

    return grammar_provider_status(provider)


def clear_grammar_config(provider):
    "Forget the stored API key; also deselect the provider if selected."
    if provider not in _API_PROVIDERS:
        raise AiError(f"Unknown grammar AI provider: {provider}", "unsupported")
    _set(_api_key_key(provider), "")
    if selected_grammar_provider() == provider:
        _set_grammar_provider("")
    return grammar_provider_status(provider)


def grammar_ready(provider=None):
    """
    Resolve and validate the provider for one analysis.

    Returns the internal call context (including the decrypted key).  Callers
    must never serialize this dict.
    """
    provider = (provider or "").strip() or selected_grammar_provider()
    if not provider:
        raise AiError(
            "No grammar AI provider is enabled. Add an OpenAI or Anthropic "
            "API key in Settings and enable it for grammar analysis.",
            "unconfigured",
        )
    p = _API_PROVIDERS.get(provider)
    if p is None:
        raise AiError(f"Unknown grammar AI provider: {provider}", "unsupported")
    token = _api_token(provider)
    if not token:
        raise AiError(
            f"{p['label']} has no API key yet. Add one in Settings.",
            "unconfigured",
        )
    return {
        "provider": provider,
        "label": p["label"],
        "token": token,
        "base_url": _api_base_url(provider),
        "model": _api_model(provider),
    }


def build_grammar_system_prompt(source_lang="", target_lang=""):
    "Instruct the model to answer with the panel's JSON card structure."
    source = (source_lang or "").strip() or "the source language"
    target = (target_lang or "").strip() or "English"
    return (
        "You are a precise grammar-analysis engine. "
        f"The sentence is written in {source}. Explain it in {target}.\n"
        "Return ONLY one JSON object, with no markdown fences, no preamble "
        "and no trailing commentary. Use exactly these keys:\n"
        '{"skeleton":{"subject":"","verb":"","object":""},'
        '"clauses":[{"type":"","text":"","role":""}],'
        '"grammar_points":[{"name":"","role":"","fragment":""}],'
        '"tense_voice":{"tense":"","voice":"","aspect":""},'
        '"pitfalls":[""]}\n'
        "Rules:\n"
        "- skeleton: the main subject, predicate verb and object "
        "(empty string when absent).\n"
        "- clauses: split the sentence into main/subordinate/relative/etc. "
        "clauses; quote the original text verbatim.\n"
        "- grammar_points: every key grammar point, with its name, its "
        "function/role, and the exact source fragment it governs.\n"
        "- tense_voice: tense, voice and aspect of the main clause.\n"
        "- pitfalls: learner pitfalls or ambiguities, one per array item.\n"
        f"- All explanatory strings must be in {target}; quote original "
        "fragments verbatim.\n"
        "- If a field does not apply, use an empty string or empty array, "
        "but keep every key."
    )


def build_grammar_user_prompt(sentence, source_lang=""):
    "The sentence itself, preceded by its source language."
    source = (source_lang or "").strip() or "unknown"
    return f"Sentence ({source}):\n{clean_sentence(sentence)}"


def parse_grammar_json(text):
    """
    Pull a structured analysis out of a model reply.

    Tolerates markdown fences and leading/trailing chatter.  Returns None
    when the reply is not a JSON object with at least one expected key,
    so the caller can fall back to showing the plain text.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not any(key in data for key in _GRAMMAR_JSON_KEYS):
        return None
    return data


def grammar_cache_key(sentence, model, source_lang="", target_lang=""):
    "Sentence hash plus model and language pair; one key per stored result."
    raw = "\x1f".join(
        [
            clean_sentence(sentence),
            model or "",
            (source_lang or "").strip(),
            (target_lang or "").strip(),
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return _GRAMMAR_CACHE_PREFIX + digest


def grammar_cached_result(sentence, model, source_lang="", target_lang=""):
    "A previously stored structured result, or None."
    value = _get(grammar_cache_key(sentence, model, source_lang, target_lang))
    if not value:
        return None
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not any(key in data for key in _GRAMMAR_JSON_KEYS):
        return None
    return data


def store_grammar_result(sentence, model, source_lang, target_lang, result):
    "Persist one parsed result so the same sentence is not billed twice."
    try:
        _set(
            grammar_cache_key(sentence, model, source_lang, target_lang),
            json.dumps(result, ensure_ascii=False),
        )
    except Exception:  # pylint: disable=broad-exception-caught
        # A failed cache write must not break the reader's result.
        db.session.rollback()


_UPSTREAM_DETAIL_LIMIT = 200


def _redact(text, secret):
    "Remove the API key, in any spelling, from text we are about to show."
    if not text:
        return ""
    if secret:
        text = text.replace(f"Bearer {secret}", "Bearer ****")
        text = text.replace(secret, "****")
    return text


def _declared_charset(resp):
    "The charset explicitly declared in Content-Type, or ''."
    ctype = getattr(resp, "headers", None) or {}
    content_type = ctype.get("Content-Type") or ""
    for part in content_type.split(";"):
        name, _, value = part.strip().partition("=")
        if name.lower() == "charset":
            return value.strip().strip('"').strip("'")
    return ""


def _decode_utf8(raw, charset=""):
    """
    Decode upstream bytes as UTF-8, whatever the response claims.

    requests falls back to ISO-8859-1 when a Content-Type has no charset,
    which turns Chinese/Japanese text into latin fragments.  A declared
    non-UTF-8 charset is honoured only as a last resort.
    """
    if isinstance(raw, str):
        return raw
    try:
        return raw.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        return raw.decode(charset or "latin-1", "replace")


def _response_text(resp):
    "The full body as text, decoded as UTF-8 rather than via resp.encoding."
    content = getattr(resp, "content", None)
    if isinstance(content, (bytes, bytearray)) and content:
        return _decode_utf8(bytes(content), _declared_charset(resp))
    return getattr(resp, "text", "") or ""


def _upstream_detail(resp, token):
    """
    A short, sanitized message from an upstream error body.

    Only the message field is used -- never the whole body, which may echo
    request headers -- and the key is redacted before it reaches the UI.
    """
    try:
        body = _response_text(resp)[:1000]
    except Exception:  # pylint: disable=broad-exception-caught
        return ""
    message = ""
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("msg") or ""
        elif isinstance(error, str):
            message = error
        if not message:
            message = data.get("message") or data.get("msg") or ""
    if not message and not isinstance(data, dict):
        message = body
    message = " ".join(str(_redact(message, token)).split())
    if not message:
        return ""
    if len(message) > _UPSTREAM_DETAIL_LIMIT:
        message = message[:_UPSTREAM_DETAIL_LIMIT] + "…"
    return message


def _api_response_error(resp, label, token, url=""):
    """
    Map an official-API status to a readable error, with a safe detail.

    The detail is the upstream's own message field (status + text such as
    "401 Forbidden" or "404 model not found"), redacted and truncated; the
    body is never echoed wholesale because gateways sometimes quote the
    request, and the request carries the key.
    """
    status = resp.status_code
    detail = _upstream_detail(resp, token)
    tail = f": {detail}" if detail else ""
    if status in (401, 403):
        return AiError(
            f"{label} rejected the API key (HTTP {status}{tail}). "
            "Check the key, Base URL and model name in Settings.",
            "auth",
        )
    if status == 429:
        return AiError(
            f"{label} is rate-limiting this key (HTTP 429{tail}). "
            "Wait a moment and try again.",
            "rate",
        )
    if status == 404:
        where = f" for {url}" if url else ""
        return AiError(
            f"{label} returned HTTP 404{where}{tail}. Check the Base URL.",
            "upstream",
        )
    return AiError(f"{label} returned HTTP {status}{tail}.", "upstream")


def _api_post(url, headers, payload, label, stream=True):
    "POST; translate transport failures into AiError."
    try:
        return requests.post(
            url,
            json=payload,
            headers=headers,
            stream=stream,
            timeout=_API_TIMEOUT,
            allow_redirects=False,
        )
    except requests.exceptions.Timeout as e:
        raise AiError(f"{label} did not answer in time (timeout).", "timeout") from e
    except requests.exceptions.RequestException as e:
        raise AiError(
            f"Could not reach {label} ({type(e).__name__}).", "upstream"
        ) from e


def _iter_sse_payloads(resp):
    """
    Yield parsed JSON from one SSE response; ignore comments and junk.

    Chat-completion gateways disagree on the envelope: most send
    ``data: {...}``, some omit the space, and a few send one raw JSON object
    per line.  ``event:`` lines only name the type and are skipped here;
    Anthropic's payload carries the same type inside the JSON.

    Lines are read as bytes and decoded here: ``decode_unicode=True`` would
    use ``resp.encoding``, which is ISO-8859-1 for a ``text/event-stream``
    body without a charset and mangles every CJK character.
    """
    charset = _declared_charset(resp)
    for raw in resp.iter_lines(decode_unicode=False):
        if raw is None:
            continue
        line = _decode_utf8(raw, charset).strip()
        if not line:
            continue
        if line.startswith("data:"):
            data_str = line[5:].strip()
        elif line.startswith("{"):
            data_str = line
        else:
            continue
        if data_str == "[DONE]":
            return
        try:
            data = json.loads(data_str)
        except ValueError:
            continue
        if isinstance(data, dict):
            yield data


def _response_json(resp, label):
    "Decode a whole JSON body as UTF-8, or report an unreadable answer."
    try:
        return json.loads(_response_text(resp))
    except ValueError as e:
        raise AiError(f"{label} returned an unreadable response.", "upstream") from e


def _anthropic_text_from_payload(data):
    "Text from a non-streamed Anthropic message payload."
    if not isinstance(data, dict):
        return ""
    content = data.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        return "".join(parts)
    if isinstance(data.get("completion"), str):
        return data["completion"]
    return ""


_STREAM_FALLBACK_KINDS = ("upstream", "config")


def _openai_read(url, headers, payload, label, token):
    "One streamed request; yields text chunks from deltas or a JSON body."
    resp = _api_post(url, headers, payload, label, stream=True)
    try:
        if resp.status_code != 200:
            raise _api_response_error(resp, label, token, url)
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "text/event-stream" not in content_type:
            text = _text_from_payload(_response_json(resp, label))
            if text:
                yield text
            return
        for data in _iter_sse_payloads(resp):
            text = _text_from_payload(data)
            if text:
                yield text
    finally:
        resp.close()


def _openai_plain(url, headers, payload, label, token):
    "One non-streamed request; returns the whole reply."
    resp = _api_post(url, headers, payload, label, stream=False)
    try:
        if resp.status_code != 200:
            raise _api_response_error(resp, label, token, url)
        return _text_from_payload(_response_json(resp, label))
    finally:
        resp.close()


def _openai_stream(base_url, token, model, system_prompt, user_prompt):
    """
    POST /chat/completions and parse choices[0].delta.content.

    If the gateway refuses ``stream: true`` (some proxies answer 400 or
    return no usable event) the same request is retried once without
    streaming, so a non-streaming-compatible gateway still works.  Auth,
    rate-limit and timeout failures are not retried.
    """
    label = _API_PROVIDERS["openai"]["label"]
    url = api_endpoint(base_url, "openai")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    payload = {
        "model": model,
        "stream": True,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    emitted = False
    try:
        for text in _openai_read(url, headers, payload, label, token):
            emitted = True
            yield text
        return
    except AiError as e:
        if emitted or e.kind not in _STREAM_FALLBACK_KINDS:
            raise
    plain = dict(payload)
    plain["stream"] = False
    text = _openai_plain(url, headers, plain, label, token)
    if text:
        yield text


def _anthropic_read(url, headers, payload, label, token):
    "One streamed request; yields content_block_delta text or a JSON body."
    resp = _api_post(url, headers, payload, label, stream=True)
    try:
        if resp.status_code != 200:
            raise _api_response_error(resp, label, token, url)
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "text/event-stream" not in content_type:
            text = _anthropic_text_from_payload(_response_json(resp, label))
            if text:
                yield text
            return
        for data in _iter_sse_payloads(resp):
            event_type = data.get("type")
            if event_type == "error":
                raise AiError(f"{label} reported an upstream error.", "upstream")
            if event_type and event_type != "content_block_delta":
                continue
            delta = data.get("delta")
            if not isinstance(delta, dict):
                continue
            text = delta.get("text")
            if isinstance(text, str) and text:
                yield text
    finally:
        resp.close()


def _anthropic_plain(url, headers, payload, label, token):
    "One non-streamed request; returns the whole reply."
    resp = _api_post(url, headers, payload, label, stream=False)
    try:
        if resp.status_code != 200:
            raise _api_response_error(resp, label, token, url)
        return _anthropic_text_from_payload(_response_json(resp, label))
    finally:
        resp.close()


def _anthropic_stream(
    base_url, token, model, system_prompt, user_prompt, max_tokens=None
):
    """
    POST /messages and parse content_block_delta.delta.text.

    Falls back to a plain request once when the gateway refuses streaming,
    exactly like the OpenAI adapter.
    """
    label = _API_PROVIDERS["anthropic"]["label"]
    url = api_endpoint(base_url, "anthropic")
    headers = {
        "x-api-key": token,
        "anthropic-version": _ANTHROPIC_VERSION,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    payload = {
        "model": model,
        "max_tokens": max_tokens or _ANTHROPIC_MAX_TOKENS,
        "stream": True,
        "temperature": 0.2,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    emitted = False
    try:
        for text in _anthropic_read(url, headers, payload, label, token):
            emitted = True
            yield text
        return
    except AiError as e:
        if emitted or e.kind not in _STREAM_FALLBACK_KINDS:
            raise
    plain = dict(payload)
    plain["stream"] = False
    text = _anthropic_plain(url, headers, plain, label, token)
    if text:
        yield text


def grammar_stream(provider, sentence, source_lang="", target_lang=""):
    "Stream one grammar analysis; yields raw model text chunks."
    ready = grammar_ready(provider)
    sentence = clean_sentence(sentence)
    if not sentence:
        raise AiError("There is no sentence to analyze.", "upstream")
    system_prompt = build_grammar_system_prompt(source_lang, target_lang)
    user_prompt = build_grammar_user_prompt(sentence, source_lang)
    if ready["provider"] == "openai":
        yield from _openai_stream(
            ready["base_url"],
            ready["token"],
            ready["model"],
            system_prompt,
            user_prompt,
        )
        return
    yield from _anthropic_stream(
        ready["base_url"],
        ready["token"],
        ready["model"],
        system_prompt,
        user_prompt,
    )


def test_grammar_connection(provider):
    "Round-trip a tiny prompt; never returns the reply or the key."
    try:
        ready = grammar_ready(provider)
        system_prompt = "Reply with the single word OK."
        user_prompt = "OK"
        if ready["provider"] == "openai":
            chunks = _openai_stream(
                ready["base_url"],
                ready["token"],
                ready["model"],
                system_prompt,
                user_prompt,
            )
        else:
            chunks = _anthropic_stream(
                ready["base_url"],
                ready["token"],
                ready["model"],
                system_prompt,
                user_prompt,
                max_tokens=16,
            )
        seen = ""
        for chunk in chunks:
            seen += chunk
            if len(seen) >= 24:
                break
        if seen.strip():
            return {"ok": True, "message": "The provider answered."}
        return {"ok": False, "message": "The provider returned an empty reply."}
    except AiError as e:
        return {"ok": False, "message": e.message}
    except Exception as e:  # pylint: disable=broad-exception-caught
        return {"ok": False, "message": f"Connection failed ({type(e).__name__})."}
