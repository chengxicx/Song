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
import uuid
from urllib.parse import urlparse

import requests

from lute.db import db
from lute.models.repositories import UserSettingRepository
from lute.ai.crypto import decrypt, encrypt

# One choice serves both the reader Sentence tab and the Grammar panel.
# The old per-feature keys are still read as a fallback so existing
# accounts keep working without touching their stored values.
AI_PROVIDER_SETTING_KEY = "ai_provider"
PROVIDER_SETTING_KEY = "ai_sentence_provider"
GRAMMAR_PROVIDER_SETTING_KEY = "ai_grammar_provider"

# The provider list: one JSON array in the per-user settings table.  The
# old single-provider keys above are never deleted; they are read once by
# the migration below and then kept as a read-only fallback for old
# accounts.  Entries look like:
#   {"id", "name", "format", "base_url", "model", "api_key", "is_default"}
# ``api_key`` holds the same ``v1:`` encrypted blob as before.
AI_PROVIDERS_SETTING_KEY = "ai_providers"
PROVIDER_FORMATS = ("openai", "anthropic")
_FORMAT_LABELS = {"openai": "OpenAI", "anthropic": "Anthropic"}


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
    "The display block for one provider entry, or None."
    state = provider_status(provider)
    if state is None:
        return None
    return {
        "id": state["provider"],
        "label": state["label"],
        "format": state["format"],
        "model": state["model"],
    }


def providers_fallback_label():
    "The tab/page label when nothing is selected yet."
    return "AI"


def _raw_setting(key):
    "The stored value, or None when the key was never saved."
    return _repo().get_dynamic_value(key)


def _legacy_provider(key):
    "A provider format still valid from the per-feature keys, or ''."
    p = (_raw_setting(key) or "").strip()
    return p if p in PROVIDER_FORMATS else ""


def _legacy_selected_provider():
    """
    The old single choice, exactly as the pre-list code resolved it.

    ``ai_provider`` is authoritative once saved; otherwise the old
    per-feature keys apply and grammar wins when they disagree.
    """
    canonical = _raw_setting(AI_PROVIDER_SETTING_KEY)
    if canonical is not None:
        p = canonical.strip()
        return p if p in PROVIDER_FORMATS else ""
    sentence = _legacy_provider(PROVIDER_SETTING_KEY)
    grammar = _legacy_provider(GRAMMAR_PROVIDER_SETTING_KEY)
    if grammar and sentence and grammar != sentence:
        return grammar
    return grammar or sentence


def _legacy_enabled_providers():
    "Every old card that was switched on, one or two of them."
    canonical = _raw_setting(AI_PROVIDER_SETTING_KEY)
    if canonical is not None:
        p = canonical.strip()
        return {p} if p in PROVIDER_FORMATS else set()
    return {
        p
        for p in (
            _legacy_provider(PROVIDER_SETTING_KEY),
            _legacy_provider(GRAMMAR_PROVIDER_SETTING_KEY),
        )
        if p
    }


def _new_entry_id(existing_ids):
    "A stable short id that is not in the list yet."
    for _ in range(20):
        candidate = uuid.uuid4().hex[:8]
        if candidate not in existing_ids:
            return candidate
    return uuid.uuid4().hex[:12]


def _save_entries(entries):
    "Persist the whole provider list; commits the session."
    _set(AI_PROVIDERS_SETTING_KEY, json.dumps(entries, ensure_ascii=False))


def ensure_provider_migration():
    """
    Convert the old OpenAI/Anthropic cards into list entries once.

    The old keys are never deleted.  The new ``ai_providers`` key itself
    is the migration marker: even an empty list is written, so this
    never runs twice.  Cards without a key, or that were not switched
    on, produce no entry; the old enabled choice becomes the default
    (grammar wins when the old per-feature keys disagree).
    """
    if _raw_setting(AI_PROVIDERS_SETTING_KEY) is not None:
        return
    chosen = _legacy_selected_provider()
    enabled = _legacy_enabled_providers()
    if chosen:
        enabled.add(chosen)
    entries = []
    for fmt in PROVIDER_FORMATS:
        if fmt not in enabled:
            continue
        token_blob = _get(_api_key_key(fmt))
        if not token_blob or not decrypt(token_blob):
            continue
        p = _API_PROVIDERS[fmt]
        model = _get(_api_model_key(fmt)) or p["default_model"]
        raw_base = _get(_api_base_url_key(fmt)) or p["default_base_url"]
        try:
            base_url = normalize_base_url(raw_base, p["default_base_url"])
        except AiError:
            base_url = p["default_base_url"]
        entries.append(
            {
                "id": fmt,
                "name": model,
                "format": fmt,
                "base_url": base_url,
                "model": model,
                "api_key": token_blob,
                "is_default": fmt == chosen,
            }
        )
    if entries and not any(e["is_default"] for e in entries):
        entries[0]["is_default"] = True
    _save_entries(entries)


def _stored_entries():
    "The raw entries (encrypted keys included), after the migration."
    ensure_provider_migration()
    raw = _raw_setting(AI_PROVIDERS_SETTING_KEY) or "[]"
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        data = []
    entries = []
    seen_ids = set()
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        fmt = (item.get("format") or "").strip().lower()
        if fmt not in PROVIDER_FORMATS:
            continue
        entry_id = (item.get("id") or "").strip()
        if not entry_id or entry_id in seen_ids:
            entry_id = _new_entry_id(seen_ids)
        seen_ids.add(entry_id)
        model = (item.get("model") or "").strip()
        entries.append(
            {
                "id": entry_id,
                "name": (item.get("name") or "").strip() or model,
                "format": fmt,
                "base_url": (item.get("base_url") or "").strip(),
                "model": model,
                "api_key": item.get("api_key") or "",
                "is_default": bool(item.get("is_default")),
            }
        )
    return entries


def _public_entry(entry):
    "One entry as the UI sees it; the key itself never leaves the server."
    token = decrypt(entry.get("api_key") or "")
    return {
        "id": entry["id"],
        "name": entry["name"] or entry["model"],
        "format": entry["format"],
        "format_label": _FORMAT_LABELS[entry["format"]],
        "base_url": entry["base_url"],
        "model": entry["model"],
        "configured": bool(token),
        "masked": mask_token(token),
        "is_default": bool(entry["is_default"]),
    }


def provider_entries():
    "Every provider entry in display form, in list order."
    default = default_provider_entry()
    default_id = default["id"] if default else ""
    result = []
    for entry in _stored_entries():
        public = _public_entry(entry)
        public["is_default"] = entry["id"] == default_id
        result.append(public)
    return result


def _configured(entry):
    return bool(entry and decrypt(entry.get("api_key") or ""))


def default_provider_entry():
    "The default entry, or the first configured one if the flag is stale."
    entries = _stored_entries()
    for entry in entries:
        if entry["is_default"] and _configured(entry):
            return entry
    for entry in entries:
        if _configured(entry):
            return entry
    return None


def resolve_provider_entry(value):
    """
    An entry id, a legacy ``openai``/``anthropic`` literal, or Default.

    Old language pins used the literal format names; they now map to
    the first entry of that format so existing rows keep working.
    """
    value = (value or "").strip()
    entries = _stored_entries()
    if not value:
        return default_provider_entry()
    for entry in entries:
        if entry["id"] == value:
            return entry
    if value in PROVIDER_FORMATS:
        for entry in entries:
            if entry["format"] == value:
                return entry
    return None


def provider_choice():
    "The default provider's id, or '' when there is no usable entry."
    entry = default_provider_entry()
    return entry["id"] if entry else ""


def provider_conflict():
    "The old keys no longer disagree: the migration resolved them once."
    return None


def selected_provider():
    "The provider the reader should use, or '' when none is selected."
    return provider_choice()


def set_selected_provider(provider):
    "Make one entry the default; unknown ids and formats are refused."
    entries = _stored_entries()
    target = resolve_provider_entry(provider)
    if (provider or "").strip() and target is None:
        raise AiError(f"Unknown AI provider: {provider}", "unconfigured")
    for entry in entries:
        entry["is_default"] = bool(target and entry["id"] == target["id"])
    if not any(e["is_default"] for e in entries):
        configured = [e for e in entries if _configured(e)]
        if configured:
            configured[0]["is_default"] = True
    _save_entries(entries)


def mask_token(token):
    "A display-only mask; never the credential itself."
    if not token:
        return ""
    if len(token) <= 8:
        return "••••••"
    return "••••••" + token[-4:]


def provider_status(provider):
    "Everything the settings page may display about one provider."
    entry = resolve_provider_entry(provider)
    if entry is None:
        return None
    p = _API_PROVIDERS[entry["format"]]
    token = decrypt(entry["api_key"])
    return {
        "provider": entry["id"],
        "id": entry["id"],
        "name": entry["name"],
        "format": entry["format"],
        "format_label": _FORMAT_LABELS[entry["format"]],
        "label": p["label"],
        "configured": bool(token),
        "masked": mask_token(token),
        "enabled": bool(entry["is_default"]),
        "base_url": entry["base_url"],
        "default_base_url": p["default_base_url"],
        "base_url_hint": p["base_url_hint"],
        "model": entry["model"],
        "default_model": p["default_model"],
        "model_hint": p["model_hint"],
        "credential_label": p["credential_label"],
        "credential_hint": p["credential_hint"],
        "login_url": p["login_url"],
        "kind": "api",
        "supported": True,
        "steps": [],
        "unsupported_reason": "",
    }


def all_status():
    "Status for the settings page, plus the current provider choice."
    return {
        "selected": selected_provider(),
        "providers": [provider_status(e["id"]) for e in _stored_entries()],
        "entries": provider_entries(),
        "conflict": None,
    }


def _entry_menu_label(entry):
    """
    ``Name · Model``, or ``Format · Model`` when the name is the model.

    The name defaults to the model, so the format is the useful prefix
    until the user renames the entry.
    """
    if entry["name"] and entry["name"] != entry["model"]:
        return f"{entry['name']} · {entry['model']}"
    return f"{entry['format_label']} · {entry['model']}"


def ai_provider_choices():
    """
    The per-language sentence provider menu.

    "" is Default -- follow the default entry in Settings, exactly the
    behaviour every existing language keeps -- and every entry follows,
    in list order.  A keyless entry is marked so the user can see why
    saving it as a pin is refused.
    """
    choices = [("", "Default (use the enabled provider in Settings)")]
    for entry in provider_entries():
        label = _entry_menu_label(entry)
        if not entry["configured"]:
            label += " (no API key)"
        choices.append((entry["id"], label))
    return choices


def resolve_language_pin(pin):
    """
    Normalize a stored/legacy language pin to an entry id.

    ``openai``/``anthropic`` literals map to the first configured entry
    of that format; anything unknown or unusable becomes Default ("").
    Used when the Language form is rendered and submitted, so old rows
    keep their intent instead of silently falling back.
    """
    pin = (pin or "").strip()
    if not pin:
        return ""
    entry = resolve_provider_entry(pin)
    if entry is not None and _configured(entry):
        return entry["id"]
    return ""


def sentence_provider(language):
    """
    The provider a language's sentence translation should use.

    A language may pin one on its AI dictionary row.  The pin is only
    honoured while that entry still has a key: clearing a key in
    Settings must never break the reader, so the default entry takes
    over again.
    """
    pinned = (getattr(language, "ai_sentence_provider", "") or "").strip()
    entry = resolve_provider_entry(pinned) if pinned else None
    if entry is not None and _configured(entry):
        return entry["id"]
    return provider_choice()


def normalize_provider_entries(raw_entries):
    """
    Validate and normalize submitted provider entries.

    Returns the list ready to store (keys encrypted).  Raises AiError
    with a user-facing message for anything the settings page should
    refuse.  Existing entries keep their key when a row leaves the key
    field blank, so the masked value never has to be retyped.
    """
    if not isinstance(raw_entries, list):
        raise AiError("The AI provider list is not valid.", "config")
    existing = {e["id"]: e for e in _stored_entries()}
    used_ids = set()
    used_names = set()
    entries = []
    for item in raw_entries:
        if not isinstance(item, dict):
            raise AiError("The AI provider list is not valid.", "config")
        fmt = (item.get("format") or "").strip().lower()
        if fmt not in PROVIDER_FORMATS:
            raise AiError(
                "Each AI provider needs a Format (OpenAI or Anthropic).",
                "config",
            )
        label = _FORMAT_LABELS[fmt]
        base_raw = (item.get("base_url") or "").strip()
        if not base_raw:
            raise AiError(f"The {label} Base URL is required.", "config")
        try:
            base_url = normalize_base_url(
                base_raw, _API_PROVIDERS[fmt]["default_base_url"]
            )
        except AiError as e:
            raise AiError(f"{label}: {e.message}", "config") from e
        model = (item.get("model") or "").strip()
        if not model:
            raise AiError(f"The {label} Model is required.", "config")
        entry_id = (item.get("id") or "").strip()
        if entry_id not in existing or entry_id in used_ids:
            entry_id = _new_entry_id(used_ids)
        used_ids.add(entry_id)
        key_text = (item.get("api_key") or "").strip()
        if key_text.lower().startswith("bearer "):
            key_text = key_text[7:].strip()
        old = existing.get(entry_id)
        blob = encrypt(key_text) if key_text else (old or {}).get("api_key", "")
        name = (item.get("name") or "").strip() or model
        unique_name = name
        suffix = 2
        while unique_name.lower() in used_names:
            unique_name = f"{name} {suffix}"
            suffix += 1
        used_names.add(unique_name.lower())
        entries.append(
            {
                "id": entry_id,
                "name": unique_name,
                "format": fmt,
                "base_url": base_url,
                "model": model,
                "api_key": blob,
                "is_default": bool(item.get("is_default")),
            }
        )

    defaults = [e for e in entries if e["is_default"]]
    if len(defaults) > 1:
        raise AiError("Only one provider can be the default.", "config")
    if defaults and not _configured(defaults[0]):
        raise AiError(
            f'"{defaults[0]["name"]}" has no API key, so it cannot be the '
            "default provider.",
            "config",
        )
    if not any(e["is_default"] for e in entries):
        configured = [e for e in entries if _configured(e)]
        if configured:
            configured[0]["is_default"] = True
    return entries


def parse_provider_entries_json(text):
    "Decode the hidden settings field; '' means an empty list."
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as e:
        raise AiError("The AI provider list is not valid.", "config") from e
    if not isinstance(data, list):
        raise AiError("The AI provider list is not valid.", "config")
    return data


def _prune_language_pins(entries):
    """
    Keep language pins pointing at real entries.

    A deleted entry (or a stale legacy literal) becomes Default, so no
    language is left with a dangling reference and the reader falls
    back to the default provider automatically.
    """
    from lute.models.language import (  # pylint: disable=import-outside-toplevel
        AI_DICT_TYPE,
        Language,
    )

    entry_ids = {e["id"] for e in entries}
    first_by_format = {}
    for entry in entries:
        first_by_format.setdefault(entry["format"], entry["id"])
    changed = False
    for lang in db.session.query(Language).all():
        for row in lang.dictionaries:
            if row.dicttype != AI_DICT_TYPE:
                continue
            pin = (row.ai_provider or "").strip()
            if not pin or pin in entry_ids:
                continue
            row.ai_provider = first_by_format.get(pin, "")
            changed = True
    if changed:
        db.session.commit()


def public_entries_from_raw(raw_entries):
    """
    Display rows for an invalid submission, without echoing any key.

    The settings page re-renders after a validation error; this keeps
    the rows the user typed while replacing every credential with a
    mask (or nothing at all for an unsaved new key).
    """
    existing = {e["id"]: e for e in _stored_entries()}
    out = []
    for item in raw_entries if isinstance(raw_entries, list) else []:
        if not isinstance(item, dict):
            continue
        fmt = (item.get("format") or "").strip().lower()
        if fmt not in PROVIDER_FORMATS:
            continue
        key_text = (item.get("api_key") or "").strip()
        stored = decrypt((existing.get((item.get("id") or "").strip()) or {}).get(
            "api_key", ""
        ))
        out.append(
            {
                "id": (item.get("id") or "").strip(),
                "name": (item.get("name") or "").strip()
                or (item.get("model") or "").strip(),
                "format": fmt,
                "format_label": _FORMAT_LABELS[fmt],
                "base_url": (item.get("base_url") or "").strip(),
                "model": (item.get("model") or "").strip(),
                "configured": bool(key_text or stored),
                "masked": "••••" if key_text else mask_token(stored),
                "is_default": bool(item.get("is_default")),
            }
        )
    return out


def save_provider_entries(raw_entries):
    "Replace the whole list, then repair any language pins."
    entries = normalize_provider_entries(raw_entries)
    _save_entries(entries)
    _prune_language_pins(entries)
    return provider_entries()


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
    "The chat-system half of the structured sentence translation prompt."
    source = (source_lang or "").strip()
    target = (target_lang or "").strip() or "English"
    origin = f"{source} " if source else ""
    return (
        f"You are a translation and language tutor. Analyse the following "
        f"{origin}sentence and explain the result in {target}.\n"
        "Reply with ONE JSON object and nothing else, with these keys in "
        "this order:\n"
        '{"translation": "...", '
        '"segments": [{"part": "...", "reading": "...", "meaning": "...", '
        '"role": "..."}], '
        '"grammar": [{"point": "...", "explanation": "...", '
        '"fragment": "..."}], '
        '"context": "...", '
        '"examples": [{"source": "...", "translation": "..."}]}\n'
        "Rules:\n"
        f"- translation: one natural, fluent translation of the whole "
        f"sentence into {target}; translate the meaning, not word by word, "
        "and do not wrap it in quotation marks.  This must be the first key.\n"
        "- segments: cover the whole sentence, split the way the target "
        "language naturally divides it (words and particles for Japanese, "
        "words or phrases for English); never split single characters.\n"
        "- reading: pronunciation only when the source language needs it "
        "(kana for Japanese, pinyin for Chinese, ...); use an empty string "
        "when it does not apply.\n"
        f"- meaning, role, grammar.explanation, context and "
        f"examples.translation must all be in {target}.\n"
        "- grammar: only the genuinely important points, not every particle; "
        "quote the exact source fragment each point governs.\n"
        "- examples: 2 to 3 short source-language sentences with their "
        f"{target} translation, close to a learner's level.\n"
        "- context: why this translation, the tone/register, and any "
        "ambiguities a learner should know about.\n"
        "- Quote original fragments verbatim.\n"
        "- If a field does not apply, use an empty string or empty array, "
        "but keep every key."
    )


def build_translation_user_prompt(sentence, source_lang=""):
    "The sentence itself, preceded by its source language."
    source = (source_lang or "").strip() or "unknown"
    return f"Sentence ({source}):\n{clean_sentence(sentence)}"


_SENTENCE_JSON_KEYS = ("translation", "segments", "grammar", "context", "examples")

_SENTENCE_CACHE_PREFIX = "ai_sentence_cache_"

# v1 stored a single translated line; structured results live under v2
# so an old entry is never mistaken for a complete analysis.
_SENTENCE_CACHE_VERSION = "v2_"


def _strip_json_fences(text):
    "Remove ```json / ``` markers without touching quotes inside strings."
    raw = (text or "").strip()
    if "```" not in raw:
        return raw
    raw = re.sub(r"```[a-zA-Z0-9_-]*", "", raw)
    return raw.strip()


def _close_json_fragment(raw):
    """
    Close an object that was cut off mid-stream.

    The model is asked to keep ``translation`` first, but a slow or
    truncated stream can stop inside a string, after a colon, or with
    open arrays.  Appending the missing quote and closers usually turns
    that prefix into valid JSON, so the reader still gets the structured
    card instead of a wall of text.
    """
    start = raw.find("{")
    if start < 0:
        return ""
    text = raw[start:]
    out = []
    stack = []
    in_string = False
    escaped = False
    for ch in text:
        out.append(ch)
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if stack:
                    stack.pop()
    if in_string:
        out.append('"')
    repaired = "".join(out).rstrip()
    # A dangling comma or key colon cannot be closed as-is.
    while repaired.endswith(","):
        repaired = repaired[:-1].rstrip()
    if repaired.endswith(":"):
        repaired += " null"
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    return repaired


def parse_sentence_json(text):
    """
    Pull the structured translation out of a model reply.

    Tolerates markdown fences, leading/trailing chatter, a truncated tail
    (missing ``}``/``]``/quote), and returns a dict with at least one
    expected key so a partially-filled object still renders.  Returns
    None for prose or a reply that cannot be repaired.
    """
    raw = _strip_json_fences(text)
    if not raw:
        return None
    start = raw.find("{")
    if start < 0:
        return None
    end = raw.rfind("}")
    candidate = raw[start : end + 1] if end > start else raw[start:]
    candidates = [candidate]
    repaired = _close_json_fragment(candidate)
    if repaired and repaired != candidate:
        candidates.append(repaired)
    # Drop a trailing incomplete element and close the rest: covers
    # cut-offs in the middle of a bare token, which the quote fixer
    # cannot rescue.
    last_comma = candidate.rfind(",")
    while last_comma > 0:
        tail = _close_json_fragment(candidate[:last_comma])
        if tail:
            candidates.append(tail)
        last_comma = candidate.rfind(",", 0, last_comma)
        if len(candidates) > 8:
            break
    for item in candidates:
        try:
            data = json.loads(item)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and any(k in data for k in _SENTENCE_JSON_KEYS):
            return data
    return None


def _unescape_json_string(value):
    "Decode the escapes of one quoted JSON string body, even if cut off."
    try:
        return json.loads('"' + value + '"')
    except (TypeError, ValueError):
        pass
    value = value.rstrip("\\")
    try:
        return json.loads('"' + value + '"')
    except (TypeError, ValueError):
        return value


def extract_sentence_translation(text):
    "The ``translation`` string from a raw reply, even if it is cut off."
    raw = _strip_json_fences(text)
    match = re.search(r'"translation"\s*:\s*"((?:[^"\\]|\\.)*)', raw)
    if not match:
        return ""
    return _unescape_json_string(match.group(1)).strip()


def sentence_fallback_text(text):
    """
    Readable text for a reply that did not parse.

    Never returns the raw JSON blob: it prefers the translation field,
    accepts plain prose, and otherwise gives an explicit retry message.
    """
    raw = _strip_json_fences(text)
    translation = extract_sentence_translation(raw)
    if translation:
        return translation
    stripped = raw.strip()
    if not stripped:
        return ""
    looks_structured = (
        stripped[0] in "{["
        or '"translation"' in stripped
        or '"segments"' in stripped
    )
    if looks_structured:
        return (
            "AI returned an unparsable response, please retry. "
            "(The model reply was not valid JSON.)"
        )
    return stripped


def sentence_cache_key(sentence, model, source_lang="", target_lang=""):
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
    return _SENTENCE_CACHE_PREFIX + _SENTENCE_CACHE_VERSION + digest


def sentence_cached_result(sentence, model, source_lang="", target_lang=""):
    "A previously stored structured result, or None."
    value = _get(sentence_cache_key(sentence, model, source_lang, target_lang))
    if not value:
        return None
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not any(key in data for key in _SENTENCE_JSON_KEYS):
        return None
    return data


def store_sentence_result(sentence, model, source_lang, target_lang, result):
    "Persist one parsed result so the same sentence is not billed twice."
    try:
        _set(
            sentence_cache_key(sentence, model, source_lang, target_lang),
            json.dumps(result, ensure_ascii=False),
        )
    except Exception:  # pylint: disable=broad-exception-caught
        # A failed cache write must not break the reader's result.
        db.session.rollback()


# ---------------------------------------------------------------------
# Sentence translation via official APIs
# ---------------------------------------------------------------------


def translate_stream(provider, sentence, source_lang="", target_lang=""):
    """
    Yield translation text chunks from the selected API provider.

    Raises AiError (auth/rate/timeout/upstream) on failure; the route
    turns those into an SSE error event.
    """
    ready = grammar_ready(provider)
    if not ready:
        raise AiError(
            "No AI provider is configured. Add an OpenAI or Anthropic API "
            "key in Settings.",
            "unconfigured",
        )

    sentence = clean_sentence(sentence)
    if sentence == "":
        raise AiError("No sentence text was passed to the translator.", "upstream")

    system_prompt = build_translation_system_prompt(source_lang, target_lang)
    user_prompt = build_translation_user_prompt(sentence, source_lang)
    if ready["format"] == "openai":
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
# Official API providers
#
# One provider serves both sentence translation and grammar analysis, so
# the reader only configures a key/model once.  Which features are
# allowed is decided per language (see Language.ai_sentence_translation /
# ai_grammar_analysis), not here.
# ---------------------------------------------------------------------

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

# v1 results have no ``translation`` field, so they cannot be reused for
# the current panel; a new prefix makes every old entry a miss.
_GRAMMAR_CACHE_VERSION = "v2_"

_GRAMMAR_JSON_KEYS = (
    "translation",
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
    "The Grammar panel's provider: the default entry."
    return provider_choice()


def _set_grammar_provider(provider):
    set_selected_provider(provider)


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
    return provider_status(provider)


def all_grammar_status():
    "Backward-compatible alias for the provider list block."
    return all_status()


def _legacy_entry_for_format(fmt, entries=None):
    "The entry a legacy ``openai``/``anthropic`` card points at, or None."
    for entry in entries if entries is not None else _stored_entries():
        if entry["id"] == fmt or entry["format"] == fmt:
            return entry
    return None


def save_grammar_config(provider, token=None, base_url=None, model=None, enabled=None):
    """
    Save one entry's key (encrypted), Base URL and model.

    Kept for the old two-card API: ``provider`` is ``openai`` or
    ``anthropic``, and the first entry of that format is updated (or a
    new one is created).  ``enabled`` True makes it the default; False
    promotes another configured entry when this one was the default.
    The key itself is never echoed back.
    """
    if provider not in _API_PROVIDERS:
        raise AiError(f"Unknown AI provider: {provider}", "unsupported")
    entries = _stored_entries()
    entry = _legacy_entry_for_format(provider, entries)
    if entry is None:
        p = _API_PROVIDERS[provider]
        entry = {
            "id": provider,
            "name": p["default_model"],
            "format": provider,
            "base_url": p["default_base_url"],
            "model": p["default_model"],
            "api_key": "",
            "is_default": False,
        }
        entries.append(entry)
    p = _API_PROVIDERS[entry["format"]]
    if base_url is not None:
        entry["base_url"] = normalize_base_url(base_url, p["default_base_url"])
    if model is not None:
        model = (model or "").strip()
        if model:
            entry["model"] = model
            if entry["name"] == p["default_model"]:
                entry["name"] = model
    if token is not None:
        token = (token or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token:
            entry["api_key"] = encrypt(token)

    if enabled:
        if not _configured(entry):
            raise AiError(
                f"{p['label']} needs an API key before it can be enabled.",
                "config",
            )
        for other in entries:
            other["is_default"] = False
        entry["is_default"] = True
    elif enabled is False and entry["is_default"]:
        entry["is_default"] = False
        for other in entries:
            if _configured(other):
                other["is_default"] = True
                break
    _save_entries(entries)
    return grammar_provider_status(entry["id"])


def clear_grammar_config(provider):
    "Forget one entry's stored API key; move the default if it was this one."
    if provider not in _API_PROVIDERS:
        raise AiError(f"Unknown AI provider: {provider}", "unsupported")
    entries = _stored_entries()
    entry = _legacy_entry_for_format(provider, entries)
    if entry is None:
        return None
    entry["api_key"] = ""
    if entry["is_default"]:
        entry["is_default"] = False
        for other in entries:
            if _configured(other):
                other["is_default"] = True
                break
    _save_entries(entries)
    return grammar_provider_status(entry["id"])


def grammar_ready(provider=None):
    """
    Resolve and validate the provider for one analysis.

    An empty argument means the default entry.  Returns the internal
    call context (including the decrypted key).  Callers must never
    serialize this dict.
    """
    entry = resolve_provider_entry(provider)
    if entry is None:
        raise AiError(
            "No AI provider is configured. Add an OpenAI or Anthropic API "
            "key in Settings.",
            "unconfigured",
        )
    token = decrypt(entry["api_key"])
    if not token:
        raise AiError(
            f"{entry['name']} has no API key yet. Add one in Settings.",
            "unconfigured",
        )
    p = _API_PROVIDERS[entry["format"]]
    return {
        "provider": entry["id"],
        "format": entry["format"],
        "label": p["label"],
        "token": token,
        "base_url": normalize_base_url(entry["base_url"], p["default_base_url"]),
        "model": entry["model"],
    }


def build_grammar_system_prompt(source_lang="", target_lang=""):
    "Instruct the model to answer with the panel's JSON card structure."
    source = (source_lang or "").strip() or "the source language"
    target = (target_lang or "").strip() or "English"
    return (
        "You are a precise grammar-analysis engine. "
        f"The sentence is written in {source}. Explain it in {target}.\n"
        "Return ONLY one JSON object, with no markdown fences, no preamble "
        "and no trailing commentary. Use exactly these keys, in this order:\n"
        '{"translation":"",'
        '"skeleton":{"subject":"","verb":"","object":""},'
        '"clauses":[{"type":"","text":"","role":""}],'
        '"grammar_points":[{"name":"","role":"","fragment":""}],'
        '"tense_voice":{"tense":"","voice":"","aspect":""},'
        '"pitfalls":[""]}\n'
        "Rules:\n"
        f"- translation: one natural, fluent translation of the whole "
        f"sentence into {target}; translate the meaning, not word by word, "
        "and do not wrap it in quotation marks.\n"
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
    return _GRAMMAR_CACHE_PREFIX + _GRAMMAR_CACHE_VERSION + digest


def _legacy_grammar_cache_key(sentence, model, source_lang="", target_lang=""):
    "The pre-v2 key, whose cached result has no whole-sentence translation."
    return grammar_cache_key(sentence, model, source_lang, target_lang).replace(
        _GRAMMAR_CACHE_PREFIX + _GRAMMAR_CACHE_VERSION,
        _GRAMMAR_CACHE_PREFIX,
        1,
    )


def _drop_legacy_grammar_cache(sentence, model, source_lang="", target_lang=""):
    "Best-effort cleanup of the one old entry, after a v2 cache miss."
    try:
        key = _legacy_grammar_cache_key(sentence, model, source_lang, target_lang)
        repository = _repo()
        if repository.get_dynamic_value(key):
            repository.delete_key(key)
            db.session.commit()
    except Exception:  # pylint: disable=broad-exception-caught
        # Cleanup is optional; never break the analysis because of it.
        db.session.rollback()


def grammar_cached_result(sentence, model, source_lang="", target_lang=""):
    "A previously stored structured result, or None."
    value = _get(grammar_cache_key(sentence, model, source_lang, target_lang))
    if not value:
        _drop_legacy_grammar_cache(sentence, model, source_lang, target_lang)
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
    if ready["format"] == "openai":
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


def test_provider_connection(fmt, base_url, model, token="", entry_id=""):
    """
    Round-trip a tiny prompt against unsaved settings.

    Used by the settings page's Test button, which must work before the
    row is saved.  A blank token falls back to the stored key for
    ``entry_id``.  The reply and the key are never returned; failures
    carry the upstream's own redacted message.
    """
    try:
        fmt = (fmt or "").strip().lower()
        if fmt not in _API_PROVIDERS:
            raise AiError("Choose OpenAI or Anthropic first.", "config")
        token = (token or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token and entry_id:
            token = decrypt((_find_raw_entry(entry_id) or {}).get("api_key", ""))
        if not token:
            return {"ok": False, "message": "Add an API key before testing."}
        model = (model or "").strip()
        if not model:
            raise AiError("The Model is required.", "config")
        base_url = normalize_base_url(
            base_url, _API_PROVIDERS[fmt]["default_base_url"]
        )
        system_prompt = "Reply with the single word OK."
        user_prompt = "OK"
        if fmt == "openai":
            chunks = _openai_stream(
                base_url, token, model, system_prompt, user_prompt
            )
        else:
            chunks = _anthropic_stream(
                base_url, token, model, system_prompt, user_prompt, max_tokens=16
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


def _find_raw_entry(entry_id):
    "One stored entry by id, or None."
    for entry in _stored_entries():
        if entry["id"] == (entry_id or "").strip():
            return entry
    return None


def test_grammar_connection(provider):
    "Round-trip a tiny prompt for a saved entry; never returns key or reply."
    entry = resolve_provider_entry(provider)
    if entry is None:
        return {
            "ok": False,
            "message": "No AI provider is configured. Add one in Settings.",
        }
    return test_provider_connection(
        entry["format"],
        entry["base_url"],
        entry["model"],
        token=decrypt(entry["api_key"]),
        entry_id=entry["id"],
    )
