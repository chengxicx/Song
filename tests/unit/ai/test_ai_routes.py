"""
The /ai endpoints and the reader tab wiring.

The sentences tab is now a same-origin Lute page; the old Qwen iframe
URL must be filtered out of the reading page, and an unconfigured
account must get setup guidance instead of a silent dead frame.
"""

import json

from lute.db import db
from lute.models.language import LanguageDictionary
from tests.utils import make_book

TOKEN = "qwen-session-token-abcdef123456"


class FakeResponse:
    def __init__(self, status_code=200, lines=None, body=""):
        self.status_code = status_code
        self._lines = lines or []
        self.text = body
        self.headers = {"Content-Type": "text/event-stream"}

    def iter_lines(self, decode_unicode=False):  # pylint: disable=unused-argument
        yield from self._lines

    def json(self):
        return {}

    def close(self):
        pass


def _configure(client):
    resp = client.post(
        "/ai/config/qwen",
        data={"token": TOKEN, "model": "qwen3.6-plus"},
        content_type="application/x-www-form-urlencoded",
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert TOKEN not in resp.get_data(as_text=True)
    client.post("/ai/provider", data={"provider": "qwen"})


def _sse(*payloads):
    lines = [b"data: " + json.dumps(p).encode("utf-8") for p in payloads]
    lines.append(b"data: [DONE]")
    return lines


def test_status_never_returns_the_token(app_context, client):
    "Status shows a mask, never the credential."
    _configure(client)
    body = client.get("/ai/status").get_data(as_text=True)
    assert TOKEN not in body
    data = client.get("/ai/status").get_json()
    assert data["selected"] == "qwen"
    qwen = [p for p in data["providers"] if p["provider"] == "qwen"][0]
    assert qwen["configured"] is True
    assert qwen["masked"].endswith("3456")


def test_unsupported_provider_is_refused_by_the_route(app_context, client):
    "Selecting ChatGPT/DeepSeek is a 400, not a silent success."
    resp = client.post("/ai/provider", data={"provider": "deepseek"})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_translate_streams_text_events(monkeypatch, app_context, client):
    "A normal upstream reply becomes text events plus done."
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


def test_translate_401_is_an_error_event(monkeypatch, app_context, client):
    "An expired session becomes a readable SSE error, not HTTP 500."
    _configure(client)
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(status_code=401, body="unauthorized"),
    )
    body = client.post("/ai/translate", json={"q": "Hello"}).get_data(as_text=True)
    assert "event: error" in body
    assert "session" in body
    assert TOKEN not in body


def test_sentence_page_without_config_shows_guidance(app_context, client):
    "No provider: guidance and a top-level link, never an iframe."
    body = client.get("/ai/sentence?q=Bonjour&lang=2").get_data(as_text=True)
    assert "Set up an AI provider" in body
    assert "<iframe" not in body
    assert 'target="_top"' in body
    assert 'target="_blank"' in body


def test_sentence_page_with_config_embeds_the_sentence(app_context, client):
    "Once bound, the page carries the sentence for the streaming JS."
    _configure(client)
    body = client.get("/ai/sentence?q=Bonjour%20le%20monde&lang=2").get_data(
        as_text=True
    )
    assert "Bonjour le monde" in body
    assert "qwen" in body
    assert TOKEN not in body


def test_settings_page_lists_three_providers_and_only_masks(app_context, client):
    "The settings section exists, has three cards, and leaks no token."
    _configure(client)
    resp = client.get("/settings/index")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "AI sentence translation" in body
    assert 'data-provider="qwen"' in body
    assert 'data-provider="deepseek"' in body
    assert 'data-provider="chatgpt"' in body
    assert TOKEN not in body
    assert "••••••3456" in body


def test_read_page_uses_the_ai_tab_and_drops_the_qwen_iframe(
    app_context, client, english
):
    "The old chat.qwen.ai sentence dictionary is replaced by /ai/sentence."
    qwen = LanguageDictionary()
    qwen.usefor = "sentences"
    qwen.dicttype = "embeddedhtml"
    qwen.dicturi = "https://chat.qwen.ai/?q=[LUTE]"
    qwen.is_active = True
    qwen.is_hidden = False
    qwen.sort_order = 0
    english.dictionaries.append(qwen)
    db.session.commit()

    book = make_book("AI tab", "This is a sentence.", english)
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/{book.id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "/ai/sentence?lang=" in body
    assert "chat.qwen.ai" not in body
