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
    stored = repository.get_dynamic_value("ai_providers")
    entry = json.loads(stored)[0]
    assert entry["api_key"].startswith("v1:")
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
    assert "translation" in captured["json"]["messages"][0]["content"]
    assert captured["json"]["messages"][1]["content"].endswith("Bonjour")
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
    assert "translation" in captured["json"]["system"]
    assert captured["json"]["messages"][0]["content"].endswith("Hello")
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


def test_one_provider_serves_sentence_translation_and_grammar(app_context):
    "Saving a provider in one place selects it for both features."
    service.save_grammar_config("openai", token=TOKEN, enabled=True)
    assert service.selected_provider() == "openai"
    assert service.selected_grammar_provider() == "openai"


def test_legacy_single_key_is_still_read(app_context):
    "An account with an old enabled card migrates into one entry."
    repository = UserSettingRepository(db.session)
    repository.set_dynamic_value("ai_sentence_provider", "openai")
    repository.set_dynamic_value("ai_api_openai_token", service.encrypt(TOKEN))
    db.session.commit()
    assert service.selected_provider() == "openai"
    assert service.selected_grammar_provider() == "openai"
    entries = service.provider_entries()
    assert len(entries) == 1
    assert entries[0]["format"] == "openai"
    assert entries[0]["is_default"] is True


def test_conflicting_legacy_keys_prefer_the_enabled_grammar_card(app_context):
    "Both old cards were on: both become entries, grammar is the default."
    repository = UserSettingRepository(db.session)
    repository.set_dynamic_value("ai_sentence_provider", "openai")
    repository.set_dynamic_value("ai_grammar_provider", "anthropic")
    repository.set_dynamic_value("ai_api_openai_token", service.encrypt(TOKEN))
    repository.set_dynamic_value(
        "ai_api_anthropic_token", service.encrypt("sk-ant-legacy")
    )
    db.session.commit()

    assert service.selected_provider() == "anthropic"
    entries = {e["format"]: e for e in service.provider_entries()}
    assert set(entries) == {"openai", "anthropic"}
    assert entries["anthropic"]["is_default"] is True
    assert entries["openai"]["is_default"] is False
    assert service.provider_conflict() is None


def test_saved_canonical_choice_beats_the_legacy_keys(app_context):
    "Once ai_provider is saved, the old per-feature keys are ignored."
    repository = UserSettingRepository(db.session)
    repository.set_dynamic_value("ai_sentence_provider", "openai")
    repository.set_dynamic_value("ai_grammar_provider", "anthropic")
    repository.set_dynamic_value("ai_provider", "")
    repository.set_dynamic_value("ai_api_openai_token", service.encrypt(TOKEN))
    db.session.commit()

    # Nothing was enabled in the old cards, so nothing migrates.
    assert service.provider_entries() == []
    assert service.selected_provider() == ""

    service.save_grammar_config("openai", token=TOKEN, enabled=True)
    assert service.selected_provider() == "openai"


def test_multiple_entries_roundtrip_and_crud(app_context):
    "The list stores several providers and can be edited as a whole."
    saved = service.save_provider_entries(
        [
            {
                "format": "openai",
                "name": "Main",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "api_key": TOKEN,
                "is_default": True,
            },
            {
                "format": "anthropic",
                "base_url": "https://api.anthropic.com",
                "model": "claude-sonnet-4-5",
                "api_key": "sk-ant-test",
            },
        ]
    )
    assert len(saved) == 2
    assert saved[0]["name"] == "Main"
    assert saved[1]["name"] == "claude-sonnet-4-5"
    assert {e["format"] for e in saved} == {"openai", "anthropic"}
    assert saved[0]["is_default"] is True
    assert saved[1]["is_default"] is False
    assert all(e["configured"] for e in saved)
    assert saved[0]["masked"].endswith("7890")

    raw = UserSettingRepository(db.session).get_dynamic_value("ai_providers")
    assert TOKEN not in raw
    assert "sk-ant-test" not in raw

    second = saved[1]
    updated = service.save_provider_entries(
        [
            {
                "id": second["id"],
                "format": "anthropic",
                "name": "Claude",
                "base_url": "https://api.anthropic.com",
                "model": "claude-sonnet-4-5",
                "api_key": "",
            }
        ]
    )
    assert len(updated) == 1
    assert updated[0]["name"] == "Claude"
    assert updated[0]["configured"] is True, "blank key keeps the stored one"
    assert updated[0]["is_default"] is True, "the saved key is preselected"
    ready = service.grammar_ready(updated[0]["id"])
    assert ready["token"] == "sk-ant-test"


def test_only_one_entry_can_be_the_default(app_context):
    "Two default flags are refused, not silently resolved."
    with pytest.raises(AiError) as exc:
        service.save_provider_entries(
            [
                {
                    "format": "openai",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4o-mini",
                    "api_key": TOKEN,
                    "is_default": True,
                },
                {
                    "format": "anthropic",
                    "base_url": "https://api.anthropic.com",
                    "model": "claude-sonnet-4-5",
                    "api_key": "sk-ant-test",
                    "is_default": True,
                },
            ]
        )
    assert exc.value.kind == "config"


def test_deleting_the_default_reselects_the_first_configured(app_context):
    "Removing the default row promotes the first remaining usable entry."
    saved = service.save_provider_entries(
        [
            {
                "format": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "api_key": TOKEN,
                "is_default": True,
            },
            {
                "format": "anthropic",
                "base_url": "https://api.anthropic.com",
                "model": "claude-sonnet-4-5",
                "api_key": "sk-ant-test",
            },
        ]
    )
    remaining = service.save_provider_entries(
        [{"id": saved[1]["id"], "format": "anthropic", "base_url": saved[1]["base_url"], "model": saved[1]["model"], "api_key": ""}]
    )
    assert len(remaining) == 1
    assert remaining[0]["is_default"] is True
    assert service.selected_provider() == remaining[0]["id"]
    assert service.provider_entries()[0]["id"] == remaining[0]["id"]


def test_a_keyless_entry_cannot_be_default_and_falls_back(app_context):
    "Keyless rows are kept, but never selected for calls."
    with pytest.raises(AiError) as exc:
        service.save_provider_entries(
            [
                {
                    "format": "anthropic",
                    "base_url": "https://api.anthropic.com",
                    "model": "claude-sonnet-4-5",
                    "api_key": "",
                    "is_default": True,
                }
            ]
        )
    assert exc.value.kind == "config"

    saved = service.save_provider_entries(
        [
            {
                "format": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "api_key": TOKEN,
                "is_default": True,
            },
            {
                "format": "anthropic",
                "base_url": "https://api.anthropic.com",
                "model": "claude-sonnet-4-5",
                "api_key": "",
            },
        ]
    )
    keyless = [e for e in saved if not e["configured"]][0]
    assert keyless["is_default"] is False
    with pytest.raises(AiError) as exc:
        service.grammar_ready(keyless["id"])
    assert exc.value.kind == "unconfigured"
    assert "no API key" in exc.value.message

    class Pinned:
        ai_sentence_provider = keyless["id"]

    assert service.sentence_provider(Pinned()) == service.selected_provider()


def test_forged_entry_ids_are_replaced_not_hijacked(app_context):
    "A posted id that is not in the saved list becomes a fresh id."
    service.save_provider_entries(
        [
            {
                "format": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "api_key": TOKEN,
                "is_default": True,
            }
        ]
    )
    original = service.provider_entries()[0]["id"]
    forged = service.save_provider_entries(
        [
            {
                "id": "made-up",
                "format": "anthropic",
                "base_url": "https://api.anthropic.com",
                "model": "claude-sonnet-4-5",
                "api_key": "sk-ant-test",
            }
        ]
    )
    assert forged[0]["id"] != "made-up"
    assert forged[0]["id"] != original
    assert service.provider_entries()[0]["format"] == "anthropic"


def test_language_menu_lists_every_entry_id(app_context):
    "The dropdown values are entry ids, labelled Name · Model."
    service.save_provider_entries(
        [
            {
                "format": "openai",
                "name": "Agnes",
                "base_url": "https://api.openai.com/v1",
                "model": "agnes-2.5-flash",
                "api_key": TOKEN,
                "is_default": True,
            },
            {
                "format": "anthropic",
                "base_url": "https://api.anthropic.com",
                "model": "claude-sonnet-4-5",
                "api_key": "",
            },
        ]
    )
    choices = service.ai_provider_choices()
    assert choices[0][0] == ""
    assert "Default" in choices[0][1]
    labels = [label for _, label in choices[1:]]
    assert "Agnes · agnes-2.5-flash" in labels
    assert any(
        "claude-sonnet-4-5" in label and "no API key" in label
        for label in labels
    )
    ids = [value for value, _ in choices[1:]]
    assert service.provider_entries()[0]["id"] in ids
    assert len(ids) == 2


def test_language_pin_to_a_deleted_entry_becomes_default(app_context, english):
    "Deleting a pinned entry clears the pin instead of leaving a dead id."
    from lute.models.language import AI_DICT_TYPE

    saved = service.save_provider_entries(
        [
            {
                "format": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "api_key": TOKEN,
                "is_default": True,
            },
            {
                "format": "anthropic",
                "base_url": "https://api.anthropic.com",
                "model": "claude-sonnet-4-5",
                "api_key": "sk-ant-test",
            },
        ]
    )
    pinned_id = saved[1]["id"]
    ai_row = next(d for d in english.dictionaries if d.dicttype == AI_DICT_TYPE)
    ai_row.ai_provider = pinned_id
    db.session.commit()

    service.save_provider_entries(
        [
            {
                "id": saved[0]["id"],
                "format": "openai",
                "base_url": saved[0]["base_url"],
                "model": saved[0]["model"],
                "api_key": "",
            }
        ]
    )
    db.session.refresh(ai_row)
    assert ai_row.ai_provider == ""
    assert english.dictionaries == english.dictionaries  # sanity: row still there


def test_a_legacy_literal_pin_maps_to_the_first_matching_entry(app_context, english):
    "Old openai/anthropic pins keep working while an entry exists."
    from lute.models.language import AI_DICT_TYPE

    saved = service.save_provider_entries(
        [
            {
                "format": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "api_key": TOKEN,
                "is_default": True,
            },
            {
                "format": "anthropic",
                "base_url": "https://api.anthropic.com",
                "model": "claude-sonnet-4-5",
                "api_key": "sk-ant-test",
            },
        ]
    )
    ai_row = next(d for d in english.dictionaries if d.dicttype == AI_DICT_TYPE)
    ai_row.ai_provider = "anthropic"
    db.session.commit()
    assert service.resolve_language_pin("anthropic") == saved[1]["id"]
    assert service.sentence_provider(english) == saved[1]["id"]


def test_sentence_prompt_asks_for_the_five_structured_fields():
    "translation is first, then the analysis blocks, all in the target language."
    prompt = service.build_translation_system_prompt("Japanese", "Simplified Chinese")
    order = [
        prompt.index('"translation"'),
        prompt.index('"segments"'),
        prompt.index('"grammar"'),
        prompt.index('"context"'),
        prompt.index('"examples"'),
    ]
    assert order == sorted(order)
    assert "Simplified Chinese" in prompt
    assert "2 to 3" in prompt
    assert "cover the whole sentence" in prompt


def test_parse_sentence_json_tolerates_fences_and_partial_objects():
    "Fenced JSON is unwrapped; a translation-only object still renders."
    fenced = """```json
{"translation":"但房间里只有我一个人。","segments":[{"part":"でも","reading":"","meaning":"但是","role":"接续词"}]}
```"""
    data = service.parse_sentence_json(fenced)
    assert data["translation"] == "但房间里只有我一个人。"
    assert data["segments"][0]["part"] == "でも"

    partial = service.parse_sentence_json('{"translation":"Hello there."}')
    assert partial["translation"] == "Hello there."


def test_parse_sentence_json_returns_none_for_plain_text():
    "Plain prose becomes the single-line fallback in the UI."
    assert service.parse_sentence_json("Bonjour le monde.") is None
    assert service.parse_sentence_json("") is None
    assert service.parse_sentence_json('{"error":"unexpected"}') is None


def test_sentence_cache_uses_a_v2_key_and_round_trips(app_context):
    "Structured results are cached under the versioned key."
    result = {"translation": "Hello.", "segments": [], "examples": []}
    service.store_sentence_result("Bonjour.", "m", "French", "English", result)
    key = service.sentence_cache_key("Bonjour.", "m", "French", "English")
    assert key.startswith("ai_sentence_cache_v2_")
    cached = service.sentence_cached_result("Bonjour.", "m", "French", "English")
    assert cached["translation"] == "Hello."
