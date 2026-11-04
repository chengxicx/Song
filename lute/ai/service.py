"""
AI provider configuration and the sentence-translation adapters.

Only Qwen's web session is usable from the server today (see
PROVIDERS).  ChatGPT and DeepSeek are still listed so the settings page
can explain why, and so the reader's fallback links point somewhere
useful, but their web endpoints are deliberately not called:

- ChatGPT puts every message behind Cloudflare Turnstile plus a
  per-message proof-of-work and browser fingerprint checks; a cookie
  replayed from another machine/IP is challenged instead of answered.
- DeepSeek requires a per-message ``DeepSeekHashV1`` proof-of-work
  (a 23-round Keccak variant implemented in WASM).  Reproducing it
  server-side without the WASM is both slow and fragile.

Trying either would just turn "no translation" into a confusing 403, so
they are marked unsupported and the UI offers the top-level web link.

Credential values are dynamic user settings (the same mechanism the
NetEase login uses) and are stored encrypted (lute/ai/crypto.py).
Nothing here ever returns or logs the raw token.
"""

import json
import re
import uuid

import requests

from lute.db import db
from lute.models.repositories import UserSettingRepository
from lute.ai.crypto import decrypt, encrypt

PROVIDER_SETTING_KEY = "ai_sentence_provider"

PROVIDERS = {
    "qwen": {
        "id": "qwen",
        "label": "Qwen (chat.qwen.ai)",
        "supported": True,
        "login_url": "https://chat.qwen.ai/",
        "web_url": "https://chat.qwen.ai/?q={q}",
        "credential_label": "Authorization token",
        "credential_hint": "eyJ... (paste only the token, without 'Bearer')",
        "default_model": "qwen3.6-plus",
        "model_hint": "e.g. qwen3.6-plus",
        "steps": [
            "Open chat.qwen.ai in a new tab and log in.",
            "Open the browser developer tools (F12), then the Network tab.",
            "Send one message, click the request named 'completions', and open its Headers.",
            "Copy the value of the 'Authorization' header, without the leading 'Bearer '.",
            "Paste it below.  The token stays on this server, encrypted, and expires when Qwen logs you out.",
        ],
    },
    "deepseek": {
        "id": "deepseek",
        "label": "DeepSeek (chat.deepseek.com)",
        "supported": False,
        "login_url": "https://chat.deepseek.com/",
        "web_url": "https://chat.deepseek.com/",
        "credential_label": "userToken",
        "credential_hint": "",
        "default_model": "",
        "model_hint": "",
        "unsupported_reason": (
            "DeepSeek's web chat now requires a per-message proof-of-work "
            "(DeepSeekHashV1) that only its in-browser WASM can solve "
            "quickly.  Server-side replay of the userToken therefore fails, "
            "so Lute does not offer it.  Use the web link below instead."
        ),
        "steps": [],
    },
    "chatgpt": {
        "id": "chatgpt",
        "label": "ChatGPT (chatgpt.com)",
        "supported": False,
        "login_url": "https://chatgpt.com/",
        "web_url": "https://chatgpt.com/?q={q}",
        "credential_label": "session cookie",
        "credential_hint": "",
        "default_model": "",
        "model_hint": "",
        "unsupported_reason": (
            "ChatGPT protects every message behind Cloudflare Turnstile, a "
            "per-message proof-of-work, and a browser fingerprint.  A session "
            "cookie copied from your browser is replayed from this server's "
            "IP and user agent, which Cloudflare challenges (403), so it "
            "cannot be used reliably.  Use the web link below instead."
        ),
        "steps": [],
    },
}

# The only web endpoint we call today.
QWEN_ENDPOINT = "https://chat.qwen.ai/api/chat/completions"
QWEN_MODEL_FALLBACK = "qwen3.6-plus"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0 Safari/537.36"
)


class AiError(Exception):
    """
    A sentence-translation failure the UI should show.

    ``kind`` is one of unconfigured, unsupported, session, rate, timeout,
    upstream, so the page can phrase the message precisely.
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


def _token_key(provider):
    return f"ai_{provider}_token"


def _model_key(provider):
    return f"ai_{provider}_model"


def _get(key):
    return _repo().get_dynamic_value(key) or ""


def _set(key, value):
    _repo().set_dynamic_value(key, value or "")
    db.session.commit()


def provider_ids():
    return list(PROVIDERS.keys())


def provider_def(provider):
    return PROVIDERS.get(provider)


def providers_fallback_label():
    "The tab/page label when nothing is selected yet."
    return "AI"


def selected_provider():
    "The provider the reader should use, or '' when none is selected."
    p = _get(PROVIDER_SETTING_KEY).strip()
    if p in PROVIDERS and PROVIDERS[p]["supported"]:
        return p
    return ""


def set_selected_provider(provider):
    "Persist the reader's provider choice; unsupported ones are refused."
    provider = (provider or "").strip()
    if provider and provider not in PROVIDERS:
        raise AiError(f"Unknown AI provider: {provider}", "unconfigured")
    if provider and not PROVIDERS[provider]["supported"]:
        raise AiError(
            f"{PROVIDERS[provider]['label']} cannot be used server-side.",
            "unsupported",
        )
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
    p = PROVIDERS.get(provider)
    if p is None:
        return None
    token = decrypt(_get(_token_key(provider)))
    model = _get(_model_key(provider)) or p["default_model"]
    return {
        "provider": provider,
        "label": p["label"],
        "supported": p["supported"],
        "configured": bool(token),
        "masked": mask_token(token),
        "model": model,
        "login_url": p["login_url"],
        "credential_label": p["credential_label"],
        "credential_hint": p["credential_hint"],
        "model_hint": p["model_hint"],
        "steps": p["steps"],
        "unsupported_reason": p.get("unsupported_reason", ""),
    }


def all_status():
    "Status for the settings page, plus the current provider choice."
    return {
        "selected": selected_provider(),
        "providers": [provider_status(p) for p in provider_ids()],
    }


def save_config(provider, token=None, model=None):
    """
    Save a provider's credential and/or model.

    A blank token means "leave the stored one alone" (the input is
    empty on a normal form submit); use clear_config() to forget it.
    """
    p = PROVIDERS.get(provider)
    if p is None:
        raise AiError(f"Unknown AI provider: {provider}", "unconfigured")
    if not p["supported"]:
        raise AiError(
            f"{p['label']} cannot be used server-side; nothing was saved.",
            "unsupported",
        )
    if token is not None:
        token = (token or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token:
            _set(_token_key(provider), encrypt(token))
    if model is not None:
        _set(_model_key(provider), (model or "").strip())
    return provider_status(provider)


def clear_config(provider):
    "Forget a provider's stored credential (logout / re-bind)."
    if provider not in PROVIDERS:
        raise AiError(f"Unknown AI provider: {provider}", "unconfigured")
    _set(_token_key(provider), "")
    return provider_status(provider)


def _stored_token(provider):
    return decrypt(_get(_token_key(provider)))


def _stored_model(provider):
    return _get(_model_key(provider)) or PROVIDERS[provider]["default_model"]


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


def build_prompt(sentence, source_lang="", target_lang=""):
    "The one-shot translation prompt; output-only, no chat context."
    source = (source_lang or "").strip()
    target = (target_lang or "").strip() or "English"
    origin = f"{source} " if source else ""
    return (
        f"You are a translation engine. Translate the following {origin}"
        f"sentence into {target}. Reply with the translation only, "
        f"without explanations, quotes, or the original text.\n\n"
        f"{sentence}"
    )


def web_url(provider, sentence):
    "A top-level (new tab) link to the provider's own web chat."
    p = PROVIDERS.get(provider) or PROVIDERS["qwen"]
    from urllib.parse import quote

    return p["web_url"].replace("{q}", quote(clean_sentence(sentence)))


# ---------------------------------------------------------------------
# Qwen web-session adapter
# ---------------------------------------------------------------------


def translate_stream(provider, sentence, source_lang="", target_lang=""):
    """
    Yield translation text chunks.

    Raises AiError (session/rate/timeout/upstream) on failure; the
    route turns those into an SSE error event.
    """
    provider = provider or selected_provider()
    if not provider:
        raise AiError(
            "No AI provider is configured for sentence translation.",
            "unconfigured",
        )
    p = PROVIDERS.get(provider)
    if p is None or not p["supported"]:
        raise AiError(
            (p or {}).get("unsupported_reason") or "This provider is unsupported.",
            "unsupported",
        )
    token = _stored_token(provider)
    if not token:
        raise AiError(
            f"{p['label']} is not bound yet.  Add its session token in Settings.",
            "unconfigured",
        )

    sentence = clean_sentence(sentence)
    if sentence == "":
        raise AiError("No sentence text was passed to the translator.", "upstream")

    prompt = build_prompt(sentence, source_lang, target_lang)
    if provider == "qwen":
        yield from _qwen_stream(token, _stored_model(provider), prompt)
        return
    raise AiError("This provider has no server-side adapter.", "unsupported")


def test_connection(provider):
    """
    Do a tiny round-trip and report a short, credential-free result.

    Returns {"ok": bool, "message": str}.  The translation text itself
    is not returned, only whether the provider answered.
    """
    try:
        chunks = []
        for chunk in translate_stream(provider, "Hello", target_lang="English"):
            chunks.append(chunk)
            if len("".join(chunks)) >= 24:
                break
        if "".join(chunks).strip():
            return {"ok": True, "message": "The provider answered."}
        return {"ok": False, "message": "The provider returned an empty reply."}
    except AiError as e:
        return {"ok": False, "message": e.message}
    except Exception as e:  # pylint: disable=broad-exception-caught
        return {"ok": False, "message": f"Connection failed ({type(e).__name__})."}


def _qwen_stream(token, model, prompt):
    "POST to Qwen's web completions endpoint and parse its SSE stream."
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": _USER_AGENT,
        "Referer": "https://chat.qwen.ai/",
        "Origin": "https://chat.qwen.ai",
        "source": "web",
        "X-Request-Id": str(uuid.uuid4()),
    }
    payload = {
        "stream": True,
        "incremental_output": True,
        "model": model or QWEN_MODEL_FALLBACK,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        resp = requests.post(
            QWEN_ENDPOINT, json=payload, headers=headers, stream=True, timeout=(10, 90)
        )
    except requests.exceptions.Timeout as e:
        raise AiError("Qwen did not answer in time (timeout).", "timeout") from e
    except requests.exceptions.RequestException as e:
        raise AiError(f"Could not reach Qwen ({type(e).__name__}).", "upstream") from e

    try:
        if resp.status_code != 200:
            raise _response_error(resp)
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "text/event-stream" not in content_type:
            # Some deployments answer a non-streamed JSON body even when
            # stream was requested; accept it instead of failing.
            try:
                data = resp.json()
            except ValueError as e:
                raise AiError(
                    "Qwen returned an unreadable response.", "upstream"
                ) from e
            text = _text_from_payload(data)
            if text:
                yield text
            return

        for raw in resp.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
            except ValueError:
                continue
            text = _text_from_payload(data)
            if text:
                yield text
    finally:
        resp.close()


def _response_error(resp):
    "Map an upstream status to the message the reader should see."
    status = resp.status_code
    if status in (401, 403):
        return AiError(
            "The stored session was rejected (expired, logged out, or risk "
            "control).  Re-bind the token in Settings, or open the provider's "
            "web page from the link below.",
            "session",
        )
    if status == 429:
        return AiError(
            "The provider is rate-limiting this account.  Wait a moment and "
            "try again.",
            "rate",
        )
    body = ""
    try:
        body = (resp.text or "").strip()[:200]
    except Exception:  # pylint: disable=broad-exception-caught
        body = ""
    suffix = f" ({body})" if body else ""
    return AiError(f"The provider returned HTTP {status}{suffix}.", "upstream")


def _text_from_payload(data):
    "Pull text out of the common web-response shapes, leniently."
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
