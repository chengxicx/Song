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


def test_settings_page_has_one_section_and_two_api_cards(app_context, client):
    "The old Sentence-provider selector is gone; only the two cards remain."
    _configure(client)
    resp = client.get("/settings/index")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "AI provider (API key)" in body
    assert "Use this provider for sentence translation and grammar analysis" in body
    assert "id=\"ai_provider_select\"" not in body
    assert "Provider used by the reader Sentence tab" not in body
    assert 'data-provider="openai"' in body
    assert 'data-provider="anthropic"' in body
    assert body.count('class="ai-grammar-provider"') == 2
    for removed in ("qwen", "deepseek", "chatgpt", "chat.qwen.ai"):
        assert removed not in body.lower()
    assert TOKEN not in body
    assert "••••••7890" in body


def test_settings_page_explains_legacy_provider_conflict(app_context, client):
    "Old sentence/grammar choices that disagree are explained, not hidden."
    from lute.models.repositories import UserSettingRepository

    repository = UserSettingRepository(db.session)
    repository.set_dynamic_value("ai_sentence_provider", "openai")
    repository.set_dynamic_value("ai_grammar_provider", "anthropic")
    db.session.commit()

    body = client.get("/settings/index").get_data(as_text=True)
    assert "Your old settings chose" in body
    assert "is now used for both" in body


def test_read_page_uses_the_ai_tab(app_context, client, english):
    "The /ai/sentence frame is present; no legacy provider is injected."
    book = make_book("AI tab", "This is a sentence.", english)
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/{book.id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "/ai/sentence?lang=" in body


def test_sentence_target_language_uses_the_shared_ai_language(app_context, english):
    """
    Sentence translation reads grammar_translate_lang (the Language form's
    "AI analysis & translation language"), falls back to the Google
    Translate target only when that is empty, then to the UI language.
    """
    from lute.ai.routes import _language_context

    english.grammar_translate_lang = "zh"
    english.translate_target_lang = "en"
    db.session.commit()
    assert _language_context(english.id) == ("English", "Simplified Chinese")

    english.grammar_translate_lang = ""
    english.translate_target_lang = "ja"
    db.session.commit()
    assert _language_context(english.id)[1] == "Japanese"

    english.translate_target_lang = ""
    db.session.commit()
    assert _language_context(english.id)[1] == "English"

    english.grammar_translate_lang = "fr"
    db.session.commit()
    assert _language_context(english.id)[1] == "French"

    english.grammar_translate_lang = "ru"
    db.session.commit()
    assert _language_context(english.id)[1] == "Russian"


def test_language_form_offers_japanese_and_other_ai_languages(app_context):
    "The shared AI language select is a fixed list, so prompts stay well-formed."
    from lute.language.forms import LanguageForm

    choices = dict(LanguageForm().grammar_translate_lang.choices)
    assert choices["ja"] == "日本語"
    assert choices["zh"] == "中文"
    assert choices["en"] == "English"
    assert "fr" in choices and "de" in choices and "es" in choices
    assert choices["ru"] == "Русский"
    assert LanguageForm().grammar_translate_lang.label.text == (
        "AI analysis & translation language"
    )


def test_language_edit_form_renders_the_russian_option(app_context, client, english):
    "The fixed list reaches the page the user edits, not just the Form class."
    body = client.get(f"/language/edit/{english.id}").get_data(as_text=True)
    assert 'value="ru">Русский</option>' in body


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


def test_translate_returns_the_structured_result(monkeypatch, app_context, client):
    "The SSE stream carries translation, segments, grammar, context, examples."
    _configure(client)
    result = {
        "translation": "Hello, world.",
        "segments": [
            {"part": "Hello", "reading": "", "meaning": "你好", "role": "greeting"}
        ],
        "grammar": [{"point": "compound", "explanation": "Two clauses.", "fragment": "Hello, world"}],
        "context": "A neutral greeting.",
        "examples": [{"source": "Hello!", "translation": "你好！"}],
    }
    text = json.dumps(result, ensure_ascii=False)
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            lines=_sse({"choices": [{"delta": {"content": text}}]})
        ),
    )
    body = client.post(
        "/ai/translate", json={"q": "Hello, world.", "lang": ""}
    ).get_data(as_text=True)
    assert "event: result" in body
    assert '"segments"' in body
    assert '"examples"' in body
    assert '"cached": false' in body
    assert TOKEN not in body


def test_translate_cache_hit_skips_the_provider_and_shows_no_loading(
    monkeypatch, app_context, client
):
    "A stored structured result is returned without a second API call."
    _configure(client)
    result = {"translation": "Hello.", "segments": [], "examples": []}
    text = json.dumps(result, ensure_ascii=False)
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            lines=_sse({"choices": [{"delta": {"content": text}}]})
        ),
    )
    first = client.post("/ai/translate", json={"q": "Hello.", "lang": ""}).get_data(
        as_text=True
    )
    assert "event: result" in first

    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: pytest.fail("cache hit should not call the provider"),
    )
    body = client.post("/ai/translate", json={"q": "Hello.", "lang": ""}).get_data(
        as_text=True
    )
    assert '"cached": true' in body
    assert '"translation": "Hello."' in body
    assert "event: text" not in body


def test_sentence_translation_can_be_turned_off_for_a_language(
    app_context, client, english
):
    "The backend refuses with the same readable reason the UI shows."
    english.ai_sentence_translation = False
    db.session.commit()

    body = client.post(
        "/ai/translate", json={"q": "Hello", "lang": english.id}
    ).get_data(as_text=True)
    assert "event: error" in body
    assert "unsupported" in body
    assert "Language settings" in body

    page = client.get(f"/ai/sentence?q=Hello&lang={english.id}").get_data(as_text=True)
    assert "turned off for this language" in page


def test_grammar_analysis_can_be_turned_off_for_a_language(
    app_context, client, english
):
    "Off hides the AI section on the read page and rejects the endpoint."
    english.ai_grammar_analysis = False
    db.session.commit()

    body = client.post(
        "/ai/grammar/analyze", json={"q": "Hello.", "lang": english.id}
    ).get_data(as_text=True)
    assert "event: error" in body
    assert "unsupported" in body
    assert "Language settings" in body

    book = make_book("AI off", "This is a sentence.", english)
    db.session.add(book)
    db.session.commit()
    page = client.get(f"/read/{book.id}").get_data(as_text=True)
    assert "LUTE_AI_GRAMMAR_ENABLED = false" in page


def test_sentence_tab_hides_ai_when_the_language_turns_it_off(
    app_context, client, english
):
    "No AI tab is offered for a language that opted out."
    english.ai_sentence_translation = False
    db.session.commit()
    book = make_book("Sentence AI off", "This is a sentence.", english)
    db.session.add(book)
    db.session.commit()

    page = client.get(f"/read/{book.id}").get_data(as_text=True)
    assert "/ai/sentence?lang=" not in page
