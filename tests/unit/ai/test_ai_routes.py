"""
The /ai endpoints and the reader tab wiring.

Settings now exposes only the official OpenAI and Anthropic API cards;
the reader Sentence and Grammar tabs both call those APIs.  An
unconfigured account must get setup guidance, never a dead iframe or a
third-party web-chat link.
"""

import json

import pytest

from lute.db import db
from tests.utils import make_book

TOKEN = "sk-test-abcdef1234567890"


class FakeResponse:
    def __init__(self, status_code=200, lines=None, body="", payload=None, ctype=None):
        self.status_code = status_code
        self._lines = lines or []
        self.text = body
        self._payload = payload
        self.headers = {"Content-Type": ctype or "text/event-stream"}

    def iter_lines(self, decode_unicode=False):  # pylint: disable=unused-argument
        yield from self._lines

    def json(self):
        return self._payload or {}

    def close(self):
        pass


def _sse(*payloads):
    lines = [b"data: " + json.dumps(p).encode("utf-8") for p in payloads]
    lines.append(b"data: [DONE]")
    return lines


def _configure(client, provider="openai", enabled=False):
    resp = client.post(
        f"/ai/grammar/config/{provider}",
        data={
            "token": TOKEN,
            "base_url": "",
            "model": "",
            "enabled": "1" if enabled else "0",
        },
        content_type="application/x-www-form-urlencoded",
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert TOKEN not in resp.get_data(as_text=True)
    client.post("/ai/provider", data={"provider": provider})


def test_status_never_returns_the_key(app_context, client):
    "Status shows a mask, never the credential."
    _configure(client)
    body = client.get("/ai/status").get_data(as_text=True)
    assert TOKEN not in body
    data = client.get("/ai/status").get_json()
    assert data["selected"] == "openai"
    openai = [p for p in data["providers"] if p["provider"] == "openai"][0]
    assert openai["configured"] is True
    assert openai["masked"].endswith("7890")
    assert openai["base_url"] == "https://api.openai.com/v1"


def test_unknown_provider_is_refused_by_the_route(app_context, client):
    "Old web providers cannot be selected."
    resp = client.post("/ai/provider", data={"provider": "qwen"})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_translate_streams_text_events(monkeypatch, app_context, client):
    "A normal OpenAI reply becomes text events plus done."
    _configure(client)
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            lines=_sse(
                {"choices": [{"delta": {"content": "你好"}}]},
                {"choices": [{"delta": {"content": "世界"}}]},
            )
        ),
    )
    resp = client.post("/ai/translate", json={"q": "Hello world", "lang": ""})
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "event: text" in body
    assert "你好" in body and "世界" in body
    assert "event: done" in body
    assert TOKEN not in body


def test_translate_unconfigured_is_an_error_event(app_context, client):
    "No key means readable guidance inside the SSE stream."
    body = client.post("/ai/translate", json={"q": "Hello"}).get_data(as_text=True)
    assert "event: error" in body
    assert "unconfigured" in body
    assert "Settings" in body


def test_translate_401_is_an_error_event_and_retry_works(
    monkeypatch, app_context, client
):
    "An invalid key is a readable auth error; a later retry still works."
    _configure(client)
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            status_code=401, body=json.dumps({"error": {"message": "invalid api key"}})
        ),
    )
    body = client.post("/ai/translate", json={"q": "Hello"}).get_data(as_text=True)
    assert "event: error" in body
    assert "auth" in body
    assert "invalid api key" in body
    assert TOKEN not in body

    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            lines=_sse({"choices": [{"delta": {"content": "Bonjour"}}]})
        ),
    )
    body = client.post("/ai/translate", json={"q": "Hello"}).get_data(as_text=True)
    assert "Bonjour" in body


def test_sentence_page_without_config_shows_guidance(app_context, client):
    "No provider: guidance, no iframe and no third-party web-chat link."
    body = client.get("/ai/sentence?q=Bonjour&lang=2").get_data(as_text=True)
    assert "Set up an AI provider" in body
    assert "Open AI settings" in body
    assert "<iframe" not in body
    assert 'target="_blank"' not in body
    assert "chat.qwen.ai" not in body


def test_sentence_page_with_config_embeds_the_sentence(app_context, client):
    "Once bound, the page carries the sentence for the streaming JS."
    _configure(client)
    body = client.get("/ai/sentence?q=Bonjour%20le%20monde&lang=2").get_data(
        as_text=True
    )
    assert "Bonjour le monde" in body
    assert "OpenAI (API key)" in body
    assert TOKEN not in body
    assert 'target="_blank"' not in body


def test_grammar_analyze_streams_a_structured_result(monkeypatch, app_context, client):
    "The panel gets text deltas plus a parsed result event."
    _configure(client, enabled=True)
    result = {
        "skeleton": {"subject": "I", "verb": "am", "object": "here"},
        "clauses": [{"type": "main", "text": "I am here.", "role": "statement"}],
        "grammar_points": [{"name": "be", "role": "linking verb", "fragment": "am"}],
        "tense_voice": {"tense": "present", "voice": "active", "aspect": "simple"},
        "pitfalls": ["Do not omit the verb."],
    }
    text = json.dumps(result)
    half = len(text) // 2
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            lines=_sse(
                {"choices": [{"delta": {"content": text[:half]}}]},
                {"choices": [{"delta": {"content": text[half:]}}]},
            )
        ),
    )
    body = client.post(
        "/ai/grammar/analyze", json={"q": "I am here.", "lang": ""}
    ).get_data(as_text=True)
    assert "event: meta" in body
    assert "event: text" in body
    assert "event: result" in body
    assert '"subject": "I"' in body
    assert "event: done" in body
    assert TOKEN not in body


def test_grammar_analyze_cache_hit_skips_the_provider(monkeypatch, app_context, client):
    "A repeated sentence/model/language pair is served from the cache."
    _configure(client, enabled=True)
    result = {"skeleton": {"subject": "I", "verb": "am", "object": ""}}
    text = json.dumps(result)
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            lines=_sse({"choices": [{"delta": {"content": text}}]})
        ),
    )
    first = client.post(
        "/ai/grammar/analyze", json={"q": "I am here.", "lang": ""}
    ).get_data(as_text=True)
    assert '"subject": "I"' in first

    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: pytest.fail("cache hit should not call the provider"),
    )
    second = client.post(
        "/ai/grammar/analyze", json={"q": "I am here.", "lang": ""}
    ).get_data(as_text=True)
    assert '"cached": true' in second
    assert '"subject": "I"' in second
    assert "event: text" not in second


def test_grammar_analyze_plain_text_falls_back(monkeypatch, app_context, client):
    "A non-JSON reply is still shown as readable paragraphs."
    _configure(client, enabled=True)
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            lines=_sse(
                {"choices": [{"delta": {"content": "This is a simple "}}]},
                {"choices": [{"delta": {"content": "present-tense sentence."}}]},
            )
        ),
    )
    body = client.post(
        "/ai/grammar/analyze", json={"q": "This is simple.", "lang": ""}
    ).get_data(as_text=True)
    assert '"fallback": true' in body
    assert "This is a simple present-tense sentence." in body


def test_grammar_analyze_unconfigured_and_auth_errors(monkeypatch, app_context, client):
    "Unconfigured and rejected keys are distinct error events."
    body = client.post(
        "/ai/grammar/analyze", json={"q": "Hello.", "lang": ""}
    ).get_data(as_text=True)
    assert "event: error" in body
    assert "unconfigured" in body

    _configure(client, enabled=True)
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            status_code=401, body=json.dumps({"error": {"message": "Forbidden"}})
        ),
    )
    body = client.post(
        "/ai/grammar/analyze", json={"q": "Hello.", "lang": ""}
    ).get_data(as_text=True)
    assert "event: error" in body
    assert "auth" in body
    assert "Forbidden" in body
    assert TOKEN not in body


def test_settings_page_has_exactly_two_api_cards_and_masks(app_context, client):
    "Only OpenAI and Anthropic remain; no web-session cards or keys."
    _configure(client)
    resp = client.get("/settings/index")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "AI sentence translation" in body
    assert 'data-provider="openai"' in body
    assert 'data-provider="anthropic"' in body
    assert body.count('class="ai-grammar-provider"') == 2
    for removed in ("qwen", "deepseek", "chatgpt", "chat.qwen.ai"):
        assert removed not in body.lower()
    assert TOKEN not in body
    assert "••••••7890" in body


def test_read_page_uses_the_ai_tab(app_context, client, english):
    "The /ai/sentence frame is present; no legacy provider is injected."
    book = make_book("AI tab", "This is a sentence.", english)
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/{book.id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "/ai/sentence?lang=" in body


def test_grammar_target_language_follows_the_language_setting(app_context, english):
    "Grammar Analysis Language wins; empty or unknown falls back to the UI."
    from lute.ai.routes import _grammar_language_context

    english.grammar_translate_lang = "zh"
    db.session.commit()
    assert _grammar_language_context(english.id)[1] == "Simplified Chinese"

    english.grammar_translate_lang = "ja"
    db.session.commit()
    assert _grammar_language_context(english.id)[1] == "Japanese"

    english.grammar_translate_lang = ""
    db.session.commit()
    assert _grammar_language_context(english.id)[1] == "English"

    english.grammar_translate_lang = "xx"
    db.session.commit()
    assert _grammar_language_context(english.id)[1] == "English"
