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

    def __init__(
        self,
        status_code=200,
        lines=None,
        body="",
        payload=None,
        ctype=None,
        content=None,
    ):
        self.status_code = status_code
        self._lines = lines or []
        self.text = body
        self._payload = payload
        self.headers = {"Content-Type": ctype or "text/event-stream"}
        if content is not None:
            self.content = content
        elif body:
            self.content = body.encode("utf-8")
        elif payload is not None:
            self.content = json.dumps(payload).encode("utf-8")
        else:
            self.content = b""

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


def test_base_url_accepts_a_pasted_endpoint(app_context):
    "A full endpoint in the Base URL field is reduced to its base."
    state = service.save_grammar_config(
        "openai", base_url="https://token.sensenova.cn/v1/chat/completions"
    )
    assert state["base_url"] == "https://token.sensenova.cn/v1"


@pytest.mark.parametrize(
    ("base", "provider", "expected"),
    [
        (
            "https://token.sensenova.cn",
            "openai",
            "https://token.sensenova.cn/v1/chat/completions",
        ),
        (
            "https://token.sensenova.cn/",
            "openai",
            "https://token.sensenova.cn/v1/chat/completions",
        ),
        (
            "https://token.sensenova.cn/v1",
            "openai",
            "https://token.sensenova.cn/v1/chat/completions",
        ),
        (
            "https://token.sensenova.cn/v1/",
            "openai",
            "https://token.sensenova.cn/v1/chat/completions",
        ),
        (
            "https://api.openai.com/v1",
            "openai",
            "https://api.openai.com/v1/chat/completions",
        ),
        (
            "https://proxy.example/openai/v1",
            "openai",
            "https://proxy.example/openai/v1/chat/completions",
        ),
        (
            "https://api.anthropic.com",
            "anthropic",
            "https://api.anthropic.com/v1/messages",
        ),
        (
            "https://api.anthropic.com/v1",
            "anthropic",
            "https://api.anthropic.com/v1/messages",
        ),
        (
            "https://proxy.example/anthropic",
            "anthropic",
            "https://proxy.example/anthropic/messages",
        ),
    ],
)
def test_api_endpoint_adds_v1_exactly_once(base, provider, expected):
    "Base URLs with or without /v1 produce the same, correct endpoint."
    assert service.api_endpoint(base, provider) == expected


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
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == TOKEN
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["json"]["model"] == "claude-sonnet-4-5"
    assert captured["json"]["max_tokens"] >= 1
    assert captured["json"]["stream"] is True
    assert captured["json"]["system"]
    assert captured["json"]["messages"][0]["content"] == "Hello"
    assert TOKEN not in json.dumps(captured["json"])


@pytest.mark.parametrize(
    ("status", "kind", "detail"),
    [
        (401, "auth", "Forbidden"),
        (403, "auth", "Forbidden"),
        (429, "rate", "rate limited"),
        (500, "upstream", "server error"),
    ],
)
def test_http_status_maps_to_specific_error(
    monkeypatch, app_context, openai_configured, status, kind, detail
):
    "Each failure class gets a readable message with the upstream detail."
    body = json.dumps({"error": {"code": 16, "message": detail}})
    monkeypatch.setattr(
        service.requests,
        "post",
        lambda *a, **k: FakeResponse(status_code=status, body=body),
    )
    with pytest.raises(AiError) as exc:
        list(service.translate_stream("openai", "Bonjour"))
    assert exc.value.kind == kind
    assert detail in exc.value.message
    assert TOKEN not in exc.value.message


def test_upstream_message_is_redacted_before_display(
    monkeypatch, app_context, openai_configured
):
    "A gateway that echoes the key must not leak it into our error."
    body = json.dumps({"error": {"message": f"invalid key Bearer {TOKEN}"}})
    monkeypatch.setattr(
        service.requests,
        "post",
        lambda *a, **k: FakeResponse(status_code=401, body=body),
    )
    with pytest.raises(AiError) as exc:
        list(service.translate_stream("openai", "Bonjour"))
    assert "invalid key" in exc.value.message
    assert TOKEN not in exc.value.message


def test_404_names_the_base_url_for_diagnosis(
    monkeypatch, app_context, openai_configured
):
    "A wrong gateway mount shows the URL we actually requested."
    body = json.dumps({"error": {"message": "path not found"}})
    monkeypatch.setattr(
        service.requests,
        "post",
        lambda *a, **k: FakeResponse(status_code=404, body=body),
    )
    with pytest.raises(AiError) as exc:
        list(service.translate_stream("openai", "Bonjour"))
    assert "path not found" in exc.value.message
    assert "https://api.openai.com/v1/chat/completions" in exc.value.message


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


def test_sse_envelope_variants_are_parsed(app_context):
    "``data:{...}``, raw JSON lines and ``event:`` lines all work."

    class Resp:
        headers = {"Content-Type": "text/event-stream"}

        def iter_lines(self, decode_unicode=False):  # pylint: disable=unused-argument
            return iter(
                [
                    b"event: message",
                    b'data:{"choices":[{"delta":{"content":"A"}}]}',
                    b'{"choices":[{"delta":{"content":"B"}}]}',
                    b"data: [DONE]",
                ]
            )

    texts = [service._text_from_payload(d) for d in service._iter_sse_payloads(Resp())]
    assert "".join(texts) == "AB"


def test_stream_refusal_falls_back_to_a_plain_request(
    monkeypatch, app_context, openai_configured
):
    "A gateway that rejects stream:true still answers the plain request."
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"]["stream"])
        if len(calls) == 1:
            body = json.dumps({"error": {"message": "stream is not supported"}})
            return FakeResponse(status_code=400, body=body)
        return FakeResponse(
            ctype="application/json",
            payload={"choices": [{"message": {"content": "Bonjour"}}]},
        )

    monkeypatch.setattr(service.requests, "post", fake_post)
    assert list(service.translate_stream("openai", "Hello")) == ["Bonjour"]
    assert calls == [True, False]


def test_auth_failure_is_not_retried(monkeypatch, app_context, openai_configured):
    "A bad key is reported once; no second, identical request is sent."
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"]["stream"])
        body = json.dumps({"error": {"message": "Forbidden"}})
        return FakeResponse(status_code=401, body=body)

    monkeypatch.setattr(service.requests, "post", fake_post)
    with pytest.raises(AiError):
        list(service.translate_stream("openai", "Hello"))
    assert calls == [True]


def test_anthropic_delta_without_a_type_field_is_accepted(monkeypatch, app_context):
    "Some gateways drop the SSE type but keep delta.text."
    service.save_grammar_config("anthropic", token=TOKEN)
    monkeypatch.setattr(
        service.requests,
        "post",
        lambda *a, **k: FakeResponse(lines=_sse({"delta": {"text": "Bonjour"}})),
    )
    assert "".join(service.translate_stream("anthropic", "Hello")) == "Bonjour"


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


def test_sse_without_charset_is_decoded_as_utf8(
    monkeypatch, app_context, openai_configured
):
    "A text/event-stream body with no charset still yields real CJK text."
    lines = [
        'data: {"choices":[{"delta":{"content":"一"}}]}'.encode("utf-8"),
        'data: {"choices":[{"delta":{"content":"緒"}}]}'.encode("utf-8"),
        'data: {"choices":[{"delta":{"content":"に"}}]}'.encode("utf-8"),
        b"data: [DONE]",
    ]
    monkeypatch.setattr(
        service.requests,
        "post",
        lambda *a, **k: FakeResponse(lines=lines, ctype="text/event-stream"),
    )
    chunks = list(service.translate_stream("openai", "Hello"))
    assert "".join(chunks) == "一緒に"
    assert "\u00e4" not in "".join(chunks)


def test_plain_json_without_charset_is_decoded_as_utf8(
    monkeypatch, app_context, openai_configured
):
    "A non-streamed JSON body is decoded as UTF-8, not ISO-8859-1."
    body = json.dumps(
        {"choices": [{"message": {"content": "日本語の文法"}}]},
        ensure_ascii=False,
    ).encode("utf-8")
    monkeypatch.setattr(
        service.requests,
        "post",
        lambda *a, **k: FakeResponse(content=body, ctype="application/json"),
    )
    chunks = list(service.translate_stream("openai", "Hello"))
    assert "".join(chunks) == "日本語の文法"


def test_error_body_without_charset_is_decoded_as_utf8(
    monkeypatch, app_context, openai_configured
):
    "The upstream error message stays readable instead of mojibake."
    body = json.dumps(
        {"error": {"message": "您已达到免费用户的 API 速率限制"}},
        ensure_ascii=False,
    ).encode("utf-8")
    monkeypatch.setattr(
        service.requests,
        "post",
        lambda *a, **k: FakeResponse(
            status_code=429, content=body, ctype="application/json"
        ),
    )
    with pytest.raises(AiError) as exc:
        list(service.translate_stream("openai", "Hello"))
    assert exc.value.kind == "rate"
    assert "您已达到免费用户的 API 速率限制" in exc.value.message
    assert "ä" not in exc.value.message


def test_grammar_prompt_asks_for_a_whole_sentence_translation():
    "translation comes first and names the requested display language."
    prompt = service.build_grammar_system_prompt("Japanese", "Simplified Chinese")
    assert '"translation"' in prompt
    assert prompt.index('"translation"') < prompt.index('"skeleton"')
    assert "one natural, fluent translation" in prompt
    assert "not word by word" in prompt
    assert "Simplified Chinese" in prompt


def test_parse_grammar_json_reads_the_translation():
    "A fenced reply still yields the translation plus the structured cards."
    text = (
        "```json\n"
        '{"translation":"但房间里只有我一个人。",'
        '"skeleton":{"subject":"わたし","verb":"でした","object":""},'
        '"clauses":[{"type":"main","text":"でも、へやには、わたしだけがでした。",'
        '"role":"转折"}]}\n'
        "```"
    )
    data = service.parse_grammar_json(text)
    assert data["translation"] == "但房间里只有我一个人。"
    assert data["skeleton"]["subject"] == "わたし"


def test_a_cache_entry_without_translation_is_not_reused(app_context):
    "Old v1 results would render a missing first card, so they are dropped."
    repository = UserSettingRepository(db.session)
    legacy = service._legacy_grammar_cache_key(
        "Hello there.", "gpt-4o-mini", "English", "English"
    )
    repository.set_dynamic_value(
        legacy, json.dumps({"skeleton": {"subject": "I", "verb": "am", "object": ""}})
    )
    db.session.commit()

    cached = service.grammar_cached_result(
        "Hello there.", "gpt-4o-mini", "English", "English"
    )
    assert cached is None
    assert repository.get_dynamic_value(legacy) is None


def test_a_new_cache_entry_round_trips_the_translation(app_context):
    "The v2 key stores and returns the whole-sentence translation."
    result = {
        "translation": "但房间里只有我一个人。",
        "skeleton": {"subject": "わたし", "verb": "でした", "object": ""},
    }
    service.store_grammar_result("sentence one.", "m", "Japanese", "Chinese", result)
    cached = service.grammar_cached_result("sentence one.", "m", "Japanese", "Chinese")
    assert cached["translation"] == "但房间里只有我一个人。"
