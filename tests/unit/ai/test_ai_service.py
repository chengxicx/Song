"""
AI sentence translation: credential storage, adapters, and leak checks.

No test touches a real provider.  Qwen's HTTP call is replaced by a
fake response object so the SSE parsing, status mapping, and the
"token never leaves the server" rules can be checked deterministically.
"""

import json

import pytest
import requests

from lute.ai import crypto, service
from lute.ai.service import AiError
from lute.models.repositories import UserSettingRepository
from lute.db import db

TOKEN = "qwen-session-token-abcdef123456"


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
    lines = []
    for p in payloads:
        lines.append(b"data: " + json.dumps(p).encode("utf-8"))
    lines.append(b"data: [DONE]")
    return lines


@pytest.fixture(name="qwen_configured")
def fixture_qwen_configured(app_context):  # pylint: disable=unused-argument
    service.save_config("qwen", TOKEN, "qwen3.6-plus")
    service.set_selected_provider("qwen")
    yield
    service.clear_config("qwen")
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


def test_token_is_encrypted_at_rest_and_masked(app_context, qwen_configured):
    "The raw token is not in the settings row and only a mask is exposed."
    repository = UserSettingRepository(db.session)
    stored = repository.get_dynamic_value("ai_qwen_token")
    assert stored.startswith("v1:")
    assert TOKEN not in stored

    state = service.provider_status("qwen")
    assert state["configured"] is True
    assert state["masked"] == "••••••3456"
    assert TOKEN not in json.dumps(state)
    assert state["model"] == "qwen3.6-plus"


def test_bearer_prefix_is_stripped(app_context):
    "A pasted 'Bearer ...' header value is normalized."
    service.save_config("qwen", "Bearer " + TOKEN)
    assert service._stored_token("qwen") == TOKEN  # pylint: disable=protected-access


def test_unconfigured_provider_raises_a_clear_error(app_context):
    "No binding means a guidance error, not a silent failure."
    service.set_selected_provider("")
    with pytest.raises(AiError) as exc:
        list(service.translate_stream("", "Hello"))
    assert exc.value.kind == "unconfigured"


def test_unsupported_providers_are_refused(app_context):
    "ChatGPT and DeepSeek cannot be selected for server-side use."
    for provider in ("chatgpt", "deepseek"):
        with pytest.raises(AiError) as exc:
            service.set_selected_provider(provider)
        assert exc.value.kind == "unsupported"
        with pytest.raises(AiError) as exc:
            service.save_config(provider, "a-token")
        assert exc.value.kind == "unsupported"


def test_qwen_stream_parses_sse(monkeypatch, app_context, qwen_configured):
    "Delta chunks from Qwen's SSE are yielded in order."
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
    chunks = list(service.translate_stream("qwen", "Bonjour", target_lang="English"))
    assert "".join(chunks) == "Hello world"
    assert captured["url"] == service.QWEN_ENDPOINT
    assert captured["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert captured["json"]["messages"][0]["content"].endswith("Bonjour")


def test_qwen_401_maps_to_session_error(monkeypatch, app_context, qwen_configured):
    "An expired cookie/token is reported as a session problem."
    monkeypatch.setattr(
        service.requests,
        "post",
        lambda *a, **k: FakeResponse(status_code=401, body="unauthorized"),
    )
    with pytest.raises(AiError) as exc:
        list(service.translate_stream("qwen", "Bonjour"))
    assert exc.value.kind == "session"


def test_qwen_429_maps_to_rate_error(monkeypatch, app_context, qwen_configured):
    "Rate limiting has its own message."
    monkeypatch.setattr(
        service.requests,
        "post",
        lambda *a, **k: FakeResponse(status_code=429, body="slow down"),
    )
    with pytest.raises(AiError) as exc:
        list(service.translate_stream("qwen", "Bonjour"))
    assert exc.value.kind == "rate"


def test_qwen_timeout_maps_to_timeout(monkeypatch, app_context, qwen_configured):
    "A hanging upstream is a timeout, not a generic crash."

    def raise_timeout(*args, **kwargs):
        raise requests.exceptions.Timeout()

    monkeypatch.setattr(service.requests, "post", raise_timeout)
    with pytest.raises(AiError) as exc:
        list(service.translate_stream("qwen", "Bonjour"))
    assert exc.value.kind == "timeout"


def test_non_stream_json_response_is_accepted(
    monkeypatch, app_context, qwen_configured
):
    "Some deployments answer JSON despite stream=true."
    monkeypatch.setattr(
        service.requests,
        "post",
        lambda *a, **k: FakeResponse(
            ctype="application/json",
            payload={"choices": [{"message": {"content": "Bonjour"}}]},
        ),
    )
    assert list(service.translate_stream("qwen", "Hello")) == ["Bonjour"]


def test_test_connection_does_not_return_the_reply(
    monkeypatch, app_context, qwen_configured
):
    "The probe reports success without echoing provider text."
    monkeypatch.setattr(
        service.requests,
        "post",
        lambda *a, **k: FakeResponse(
            lines=_sse({"choices": [{"delta": {"content": "secret-reply"}}]})
        ),
    )
    result = service.test_connection("qwen")
    assert result["ok"] is True
    assert "secret-reply" not in json.dumps(result)
    assert TOKEN not in json.dumps(result)
