"""
AI sentence translation and grammar analysis: storage, adapters, leaks.

No test touches a real provider.  ``requests.post`` is replaced by a fake
response object so request bodies, SSE parsing, status mapping and cache
behaviour can be checked deterministically.
"""

import json

import pytest
import requests

from lute.ai import crypto, service
from lute.ai.service import AiError
from lute.models.repositories import UserSettingRepository
from lute.db import db

TOKEN = "sk-test-abcdef1234567890"


class FakeResponse:
    "Minimal stand-in for requests.Response with streaming lines."

    def __init__(self, status_code=200, lines=None, body="", payload=None, ctype=None):
        self.status_code = status_code
        self._lines = lines or []
        self.text = body
        self._payload = payload
        self.headers = {"Content-Type": ctype or "text/event-stream"}

    def iter_lines(self, decode_unicode=False):  # pylint: disable=unused-argument
        for line in self._lines:
            yield line

    def json(self):
        return self._payload

    def close(self):
        pass


def _sse(*payloads):
    lines = [b"data: " + json.dumps(p).encode("utf-8") for p in payloads]
    lines.append(b"data: [DONE]")
    return lines


@pytest.fixture(name="openai_configured")
def fixture_openai_configured(app_context):  # pylint: disable=unused-argument
    service.save_grammar_config(
        "openai", token=TOKEN, base_url="https://api.openai.com/v1", model="gpt-4o-mini"
    )
    service.set_selected_provider("openai")
    yield
    service.clear_grammar_config("openai")
    service.set_selected_provider("")


def test_crypto_roundtrip_and_tamper(app_context):
    "The credential wrapper round-trips and rejects a modified value."
    blob = crypto.encrypt("hello-secret")
    assert blob.startswith("v1:")
    assert "hello-secret" not in blob
    assert crypto.decrypt(blob) == "hello-secret"

    tampered = blob[:-2] + ("00" if blob[-2:] != "00" else "11")
    assert crypto.decrypt(tampered) == ""
    assert crypto.decrypt("not-a-blob") == ""
    assert crypto.encrypt("") == ""


def test_api_key_is_encrypted_at_rest_and_masked(app_context, openai_configured):
    "The raw key is not in the settings row and only a mask is exposed."
    repository = UserSettingRepository(db.session)
    stored = repository.get_dynamic_value("ai_api_openai_token")
    assert stored.startswith("v1:")
    assert TOKEN not in stored

    state = service.provider_status("openai")
    assert state["configured"] is True
    assert state["masked"] == "••••••7890"
    assert TOKEN not in json.dumps(state)
    assert state["model"] == "gpt-4o-mini"
    assert state["base_url"] == "https://api.openai.com/v1"


def test_bearer_prefix_is_stripped(app_context):
    "A pasted 'Bearer ...' header value is normalized."
    service.save_grammar_config("openai", token="Bearer " + TOKEN)
    ready = service.grammar_ready("openai")
    assert ready["token"] == TOKEN


def test_unconfigured_provider_raises_a_clear_error(app_context):
    "No key means a guidance error, not a silent failure."
    service.set_selected_provider("")
    with pytest.raises(AiError) as exc:
        list(service.translate_stream("", "Hello"))
    assert exc.value.kind == "unconfigured"


def test_unknown_provider_is_refused(app_context):
    "Only OpenAI and Anthropic exist; old web providers are refused."
    for provider in ("qwen", "chatgpt", "deepseek"):
        with pytest.raises(AiError) as exc:
            service.set_selected_provider(provider)
        assert exc.value.kind == "unconfigured"


def test_base_url_must_be_http_without_credentials(app_context):
    "A proxy URL is allowed, credentials and bad schemes are not."
    with pytest.raises(AiError) as exc:
        service.save_grammar_config("openai", base_url="ftp://example.com")
    assert exc.value.kind == "config"
    with pytest.raises(AiError) as exc:
        service.save_grammar_config("openai", base_url="https://u:p@example.com")
    assert exc.value.kind == "config"

    state = service.save_grammar_config(
        "openai", base_url="https://gateway.example.com/v1/"
    )
    assert state["base_url"] == "https://gateway.example.com/v1"


def test_openai_request_body_headers_and_sse(monkeypatch, app_context):
    "OpenAI gets /chat/completions, Bearer auth, stream=true, and deltas."
    service.save_grammar_config("openai", token=TOKEN)
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        captured["json"] = kwargs["json"]
        return FakeResponse(
            lines=_sse(
                {"choices": [{"delta": {"content": "Hello"}}]},
                {"choices": [{"delta": {"content": " world"}}]},
            )
        )

    monkeypatch.setattr(service.requests, "post", fake_post)
    chunks = list(service.translate_stream("openai", "Bonjour", target_lang="English"))
    assert "".join(chunks) == "Hello world"
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert captured["json"]["model"] == "gpt-4o-mini"
    assert captured["json"]["stream"] is True
    assert captured["json"]["messages"][0]["role"] == "system"
    assert captured["json"]["messages"][1]["content"] == "Bonjour"
    assert TOKEN not in json.dumps(captured["json"])


def test_anthropic_request_body_headers_and_sse(monkeypatch, app_context):
    "Anthropic gets /messages, x-api-key, version header, max_tokens."
    service.save_grammar_config("anthropic", token=TOKEN)
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        captured["json"] = kwargs["json"]
        return FakeResponse(
            lines=_sse(
                {"type": "content_block_delta", "delta": {"text": "Bon"}},
                {"type": "content_block_delta", "delta": {"text": "jour"}},
                {"type": "message_stop"},
            )
        )

    monkeypatch.setattr(service.requests, "post", fake_post)
    chunks = list(service.translate_stream("anthropic", "Hello", target_lang="French"))
    assert "".join(chunks) == "Bonjour"
    assert captured["url"] == "https://api.anthropic.com/messages"
    assert captured["headers"]["x-api-key"] == TOKEN
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["json"]["model"] == "claude-sonnet-4-5"
    assert captured["json"]["max_tokens"] >= 1
    assert captured["json"]["stream"] is True
    assert captured["json"]["system"]
    assert captured["json"]["messages"][0]["content"] == "Hello"
    assert TOKEN not in json.dumps(captured["json"])


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (403, "auth"), (429, "rate"), (500, "upstream")],
)
def test_http_status_maps_to_specific_error(
    monkeypatch, app_context, openai_configured, status, kind
):
    "Each failure class gets a readable, key-free message."
    monkeypatch.setattr(
        service.requests,
        "post",
        lambda *a, **k: FakeResponse(status_code=status, body="do not echo"),
    )
    with pytest.raises(AiError) as exc:
        list(service.translate_stream("openai", "Bonjour"))
    assert exc.value.kind == kind
    assert TOKEN not in exc.value.message
    assert "do not echo" not in exc.value.message


def test_timeout_maps_to_timeout(monkeypatch, app_context, openai_configured):
    "A hanging provider is a timeout, not a generic crash."

    def raise_timeout(*args, **kwargs):
        raise requests.exceptions.Timeout()

    monkeypatch.setattr(service.requests, "post", raise_timeout)
    with pytest.raises(AiError) as exc:
        list(service.translate_stream("openai", "Bonjour"))
    assert exc.value.kind == "timeout"
    assert TOKEN not in exc.value.message


def test_non_stream_json_response_is_accepted(
    monkeypatch, app_context, openai_configured
):
    "Some gateways answer JSON despite stream=true."
    monkeypatch.setattr(
        service.requests,
        "post",
        lambda *a, **k: FakeResponse(
            ctype="application/json",
            payload={"choices": [{"message": {"content": "Bonjour"}}]},
        ),
    )
    assert list(service.translate_stream("openai", "Hello")) == ["Bonjour"]


def test_grammar_stream_uses_the_json_prompt(monkeypatch, app_context):
    "The grammar call asks for the card schema and streams raw text."
    service.save_grammar_config("openai", token=TOKEN)
    captured = {}

    def fake_post(url, **kwargs):
        captured["json"] = kwargs["json"]
        return FakeResponse(
            lines=_sse({"choices": [{"delta": {"content": '{"skeleton":'}}]})
        )

    monkeypatch.setattr(service.requests, "post", fake_post)
    chunks = list(
        service.grammar_stream("openai", "This is a test.", "English", "Chinese")
    )
    assert "".join(chunks) == '{"skeleton":'
    system = captured["json"]["messages"][0]["content"]
    assert "JSON" in system
    assert "grammar_points" in system
    assert "This is a test." in captured["json"]["messages"][1]["content"]


def test_parse_grammar_json_tolerates_fences_and_chatter(app_context):
    "Fenced JSON is accepted; plain prose is not."
    data = service.parse_grammar_json(
        'Here you go:\n```json\n{"skeleton": {"subject": "I"}}\n```\nThanks.'
    )
    assert data["skeleton"]["subject"] == "I"
    assert service.parse_grammar_json("This sentence is a simple statement.") is None
    assert service.parse_grammar_json('{"unexpected": 1}') is None


def test_grammar_cache_hit_does_not_call_upstream(monkeypatch, app_context):
    "The second analysis of the same sentence/model/languages is free."
    service.save_grammar_config("openai", token=TOKEN)
    result = {"skeleton": {"subject": "I", "verb": "am", "object": ""}}

    def fake_post(url, **kwargs):
        return FakeResponse(
            lines=_sse({"choices": [{"delta": {"content": json.dumps(result)}}]})
        )

    monkeypatch.setattr(service.requests, "post", fake_post)
    first = "".join(
        service.grammar_stream("openai", "I am here.", "English", "Chinese")
    )
    assert service.parse_grammar_json(first) == result
    service.store_grammar_result(
        "I am here.", "gpt-4o-mini", "English", "Chinese", result
    )

    monkeypatch.setattr(
        service.requests,
        "post",
        lambda *a, **k: pytest.fail("cache hit should not call the provider"),
    )
    cached = service.grammar_cached_result(
        "I am here.", "gpt-4o-mini", "English", "Chinese"
    )
    assert cached == result


def test_grammar_test_connection_does_not_return_the_reply(
    monkeypatch, app_context, openai_configured
):
    "The probe reports success without echoing provider text."
    monkeypatch.setattr(
        service.requests,
        "post",
        lambda *a, **k: FakeResponse(
            lines=_sse({"choices": [{"delta": {"content": "secret-reply"}}]})
        ),
    )
    result = service.test_grammar_connection("openai")
    assert result["ok"] is True
    assert "secret-reply" not in json.dumps(result)
    assert TOKEN not in json.dumps(result)


def test_clear_removes_the_key_and_grammar_selection(app_context):
    "Clearing a key forgets it and deselects it for grammar."
    service.save_grammar_config("openai", token=TOKEN, enabled=True)
    assert service.selected_grammar_provider() == "openai"
    state = service.clear_grammar_config("openai")
    assert state["configured"] is False
    assert service.selected_grammar_provider() == ""
