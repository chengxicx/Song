"""
The /ai endpoints and the reader tab wiring.

Settings now exposes only the official OpenAI and Anthropic API cards;
the reader Sentence and Grammar tabs both call those APIs.  An
unconfigured account must get setup guidance, never a dead iframe or a
third-party web-chat link.
"""

import io
import json
import re

import pytest

from lute.ai import service
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


def test_translate_unparsable_reply_sends_only_readable_text(
    monkeypatch, app_context, client
):
    "A broken JSON reply never reaches the panel as raw JSON."
    _configure(client)
    broken = '{"segments":[{"part": Hello], "translation": "Hello there."'
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            lines=_sse({"choices": [{"delta": {"content": broken}}]})
        ),
    )
    body = client.post("/ai/translate", json={"q": "Hello"}).get_data(as_text=True)
    result_part = body.split("event: result", 1)[1]
    assert '"fallback": true' in result_part
    assert '"unparsable": true' in result_part
    assert "Hello there." in result_part
    assert '"segments"' not in result_part
    assert '"state": "fallback"' in body


def test_translate_fallback_is_not_cached(monkeypatch, app_context, client):
    "The next request after a fallback must hit the provider again."
    _configure(client)
    broken = '{"segments":[{"part": Hello], "translation": "Broken."'
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            lines=_sse({"choices": [{"delta": {"content": broken}}]})
        ),
    )
    first = client.post("/ai/translate", json={"q": "Hello"}).get_data(as_text=True)
    assert '"fallback": true' in first

    good = json.dumps({"translation": "Good.", "segments": []})
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            lines=_sse({"choices": [{"delta": {"content": good}}]})
        ),
    )
    second = client.post("/ai/translate", json={"q": "Hello"}).get_data(as_text=True)
    assert '"result"' in second
    assert "Good." in second
    assert '"fallback": true' not in second


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


def test_settings_page_lists_provider_entries(app_context, client):
    "The section shows the saved entries, their masks and the add button."
    _configure(client)
    resp = client.get("/settings/index")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "AI provider (API key)" in body
    assert 'id="ai_provider_rows"' in body
    assert "Use as default" in body
    assert "Add provider" in body
    assert 'data-id="openai"' in body
    assert body.count('class="ai-provider-row"') == 1
    assert "ai-provider-test" in body
    assert "ai-provider-delete" in body
    for removed in ("qwen", "deepseek", "chatgpt", "chat.qwen.ai"):
        assert removed not in body.lower()
    assert TOKEN not in body
    assert "••••••7890" in body


def test_provider_row_card_uses_theme_tokens(app_context, client):
    "The row card must not paint a literal light background on a dark theme."
    _configure(client)
    body = client.get("/settings/index").get_data(as_text=True)
    block = body.split('id="ai_provider_box"', 1)[1].split("</style>", 1)[0]
    rule = re.search(
        r"#ai_provider_rows \.ai-provider-row \{(.*?)\}", block, re.DOTALL
    )
    assert rule is not None, "the provider card rule is gone"
    body_of_rule = rule.group(1)
    assert "var(--panel-bg" in body_of_rule or "color-mix" in body_of_rule
    assert "var(--form-border-color" in body_of_rule
    for light in ("#fafafa", "#ffffff", "white"):
        assert light not in body_of_rule.lower()


def test_settings_page_migrates_legacy_dual_cards(app_context, client):
    "Two old enabled cards become two rows; grammar's choice is the default."
    from lute.models.repositories import UserSettingRepository

    repository = UserSettingRepository(db.session)
    repository.set_dynamic_value("ai_sentence_provider", "openai")
    repository.set_dynamic_value("ai_grammar_provider", "anthropic")
    repository.set_dynamic_value("ai_api_openai_token", service.encrypt(TOKEN))
    repository.set_dynamic_value(
        "ai_api_anthropic_token", service.encrypt("sk-ant-legacy")
    )
    db.session.commit()
    body = client.get("/settings/index").get_data(as_text=True)
    assert body.count('class="ai-provider-row"') == 2
    assert "••••••7890" in body
    # The legacy keys survive for older code to read.
    assert repository.get_dynamic_value("ai_api_openai_token") is not None
    assert repository.get_dynamic_value("ai_sentence_provider") == "openai"


def _settings_post(ai_entries):
    "A complete settings POST, using the stored values for the selects."
    from lute.models.repositories import UserSettingRepository  # pylint: disable=import-outside-toplevel

    repo = UserSettingRepository(db.session)
    data = {
        key: repo.get_value(key)
        for key in (
            "current_theme",
            "ui_language",
            "word_image_max_edge",
            "japanese_dict",
            "japanese_reading",
            "japanese_sudachi_dict",
            "japanese_sudachi_mode",
        )
    }
    data.update(
        {
            "backup_count": "5",
            "stats_calc_sample_size": "10",
            "ankiconnect_url": "http://127.0.0.1:8765",
            "ai_providers_json": json.dumps(ai_entries),
        }
    )
    return data


def test_settings_save_replaces_the_whole_provider_list(app_context, client):
    "The hidden JSON field is the list; invalid rows abort the save."
    entries = [
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
            "api_key": "",
        },
    ]
    resp = client.post("/settings/index", data=_settings_post(entries))
    assert resp.status_code == 302
    saved = service.provider_entries()
    assert len(saved) == 2
    assert saved[0]["name"] == "Main"
    assert saved[1]["configured"] is False

    # A keyless row cannot be the default; nothing is written.
    bad = [
        {
            "format": "anthropic",
            "base_url": "https://api.anthropic.com",
            "model": "claude-sonnet-4-5",
            "api_key": "",
            "is_default": True,
        }
    ]
    resp = client.post("/settings/index", data=_settings_post(bad))
    assert resp.status_code == 200
    assert "cannot be the default provider" in resp.get_data(as_text=True)
    assert len(service.provider_entries()) == 2


def test_provider_test_endpoint_uses_unsaved_row(monkeypatch, app_context, client):
    "Test connection works before saving and never echoes the key."
    calls = []

    def fake_post(url, headers=None, **kwargs):  # pylint: disable=unused-argument
        calls.append(headers)
        return FakeResponse(lines=_sse({"choices": [{"delta": {"content": "OK"}}]}))

    monkeypatch.setattr("lute.ai.service.requests.post", fake_post)
    resp = client.post(
        "/ai/providers/test",
        json={
            "id": "",
            "format": "openai",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key": TOKEN,
        },
    )
    data = resp.get_json()
    assert data["ok"] is True
    assert TOKEN not in resp.get_data(as_text=True)
    assert calls

    resp = client.post(
        "/ai/providers/test",
        json={
            "format": "openai",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key": "",
        },
    )
    assert resp.get_json()["ok"] is False
    assert "Add an API key" in resp.get_json()["message"]


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


def test_sentence_provider_pin_falls_back_to_the_settings_choice(
    app_context, client, english
):
    """
    A language's AI row may pin a provider for sentence translation.
    The pin only wins while that provider has a key; clearing it, or a
    stale/unknown value, falls back to the shared Settings choice so the
    reader never breaks.
    """
    from lute.ai import service
    from lute.models.language import AI_DICT_TYPE

    _configure(client, "openai")

    def _pin(provider):
        ai = next(d for d in english.dictionaries if d.dicttype == AI_DICT_TYPE)
        ai.ai_provider = provider
        db.session.commit()

    _pin("anthropic")  # configured? no key yet
    assert service.sentence_provider(english) == "openai"

    service.save_grammar_config("anthropic", token="sk-ant-test")
    assert service.sentence_provider(english) == "anthropic"

    service.clear_grammar_config("anthropic")
    assert service.sentence_provider(english) == "openai"

    _pin("")
    assert service.sentence_provider(english) == "openai"

    _pin("gemini")
    assert service.sentence_provider(english) == "openai"


def test_translate_uses_the_pinned_provider_and_grammar_keeps_the_global(
    monkeypatch, app_context, client, english
):
    "The pin is for sentence translation only; grammar analysis is unchanged."
    from lute.ai import service
    from lute.models.language import AI_DICT_TYPE

    _configure(client, "openai")
    service.save_grammar_config("anthropic", token="sk-ant-test")
    ai = next(d for d in english.dictionaries if d.dicttype == AI_DICT_TYPE)
    ai.ai_provider = "anthropic"
    db.session.commit()

    monkeypatch.setattr(
        "lute.ai.service.translate_stream",
        lambda provider, sentence, source, target, usage=None: iter(["Bonjour."]),
    )
    body = client.post(
        "/ai/translate", json={"q": "A pinned-provider sentence.", "lang": english.id}
    ).get_data(as_text=True)
    assert '"provider": "anthropic"' in body
    assert "Bonjour." in body
    assert TOKEN not in body

    monkeypatch.setattr(
        "lute.ai.service.grammar_stream",
        lambda provider, sentence, source, target, usage=None: iter(["{}"]),
    )
    body = client.post(
        "/ai/grammar/analyze", json={"q": "A pinned-provider sentence.", "lang": english.id}
    ).get_data(as_text=True)
    assert '"provider": "openai"' in body
    assert '"provider": "anthropic"' not in body


def test_sentence_page_shows_the_pinned_provider(
    app_context, client, english
):
    "The tab's setup label reflects the language's pinned provider."
    from lute.ai import service
    from lute.models.language import AI_DICT_TYPE

    _configure(client, "openai")
    service.save_grammar_config("anthropic", token="sk-ant-test")
    ai = next(d for d in english.dictionaries if d.dicttype == AI_DICT_TYPE)
    ai.ai_provider = "anthropic"
    db.session.commit()

    body = client.get(f"/ai/sentence?q=Bonjour&lang={english.id}").get_data(
        as_text=True
    )
    assert "Anthropic (API key)" in body
    assert "OpenAI (API key)" not in body


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


# ---------------------------------------------------------------------
# Panel state events
# ---------------------------------------------------------------------


def _fake_ticket(state, ahead=0, eta=0, cooling=0):
    return {"state": state, "ahead": ahead, "eta": eta, "cooling": cooling}


class _FakeLimiter:
    "A limiter whose first poll reports the requested state."

    def __init__(self, states):
        self._states = list(states)

    def reserve(self, provider_id, priority):  # pylint: disable=unused-argument
        return "ticket"

    def poll(self, ticket, rpm):  # pylint: disable=unused-argument
        if self._states:
            return self._states.pop(0)
        return _fake_ticket("granted")

    def cancel(self, ticket):  # pylint: disable=unused-argument
        pass

    def cool(self, provider_id, seconds):  # pylint: disable=unused-argument
        pass


def test_repeated_sentence_is_reported_as_cached(monkeypatch, app_context, client):
    "The second identical request sends a cached state, not text."
    _configure(client)
    good = json.dumps({"translation": "Hello.", "segments": []})
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            lines=_sse({"choices": [{"delta": {"content": good}}]})
        ),
    )
    first = client.post("/ai/translate", json={"q": "Hello"}).get_data(as_text=True)
    assert '"result"' in first

    second = client.post("/ai/translate", json={"q": "Hello"}).get_data(as_text=True)
    assert '"state": "cached"' in second
    assert '"saved_from": "live"' in second
    assert "event: text" not in second


def test_waiting_structure_state_precedes_the_result(monkeypatch, app_context, client):
    "Translation closed but JSON still open: the panel waits for structure."
    _configure(client)
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            lines=_sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "content": 'Prose first "translation": "Hello." text '
                            }
                        }
                    ]
                },
                {"choices": [{"delta": {"content": '{"translation": "Hello.", "segments": []}'}}]},
            )
        ),
    )
    body = client.post("/ai/translate", json={"q": "Hello"}).get_data(as_text=True)
    assert '"state": "waiting_structure"' in body
    waiting_at = body.index("waiting_structure")
    result_at = body.index("event: result")
    assert waiting_at < result_at


def test_queued_state_reports_the_number_ahead(monkeypatch, app_context, client):
    "A rate-limited request announces its queue position before streaming."
    _configure(client)
    limiter = _FakeLimiter([_fake_ticket("queued", ahead=2, eta=6)])
    monkeypatch.setattr("lute.ai.routes.LIMITER", limiter)
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            lines=_sse({"choices": [{"delta": {"content": "Bonjour"}}]})
        ),
    )
    body = client.post("/ai/translate", json={"q": "Hello"}).get_data(as_text=True)
    assert '"state": "queued"' in body
    assert '"ahead": 2' in body
    assert "event: text" in body


def test_cooling_state_is_reported_before_retrying(monkeypatch, app_context, client):
    "An entry in cooldown shows the cooling state, then streams."
    _configure(client)
    limiter = _FakeLimiter([_fake_ticket("cooling", ahead=0, eta=45, cooling=45)])
    monkeypatch.setattr("lute.ai.routes.LIMITER", limiter)
    monkeypatch.setattr(
        "lute.ai.service.requests.post",
        lambda *a, **k: FakeResponse(
            lines=_sse({"choices": [{"delta": {"content": "Bonjour"}}]})
        ),
    )
    body = client.post("/ai/translate", json={"q": "Hello"}).get_data(as_text=True)
    assert '"state": "cooling"' in body
    assert '"cooling": 45' in body
    assert "event: text" in body


def test_429_backoff_emits_cooling_then_succeeds(monkeypatch, app_context, client):
    "A 429 waits (2s), reports cooling, then retries the same request."
    _configure(client)
    responses = [
        FakeResponse(status_code=429, body=json.dumps({"error": "slow down"})),
        FakeResponse(lines=_sse({"choices": [{"delta": {"content": "Bonjour"}}]})),
    ]

    def fake_post(*args, **kwargs):  # pylint: disable=unused-argument
        response = responses.pop(0)
        if response.status_code == 429:
            response.headers["Retry-After"] = "2"
        return response

    monkeypatch.setattr("lute.ai.service.requests.post", fake_post)
    monkeypatch.setattr("lute.ai.routes._sleep", lambda seconds: None)
    body = client.post("/ai/translate", json={"q": "Hello"}).get_data(as_text=True)
    assert '"state": "cooling"' in body
    assert "event: text" in body
    assert "Bonjour" in body


def test_queue_endpoint_exposes_each_entry(app_context, client):
    "The panel can poll queue length and cooldown per provider entry."
    _configure(client)
    data = client.get("/ai/queue").get_json()
    entry = data["entries"][0]
    assert entry["id"] == "openai"
    assert entry["rpm"] == 20
    assert entry["queued"] == 0
    assert entry["cooling"] == 0


def test_results_delete_endpoint(app_context, client):
    "Stored results can be removed by book without automatic expiry."
    service.save_result(
        service.RESULT_TYPE_SENTENCE,
        "Hello.",
        {"translation": "Bonjour."},
        provider_id="p",
        model="m",
        target_lang="fr",
        book_id=7,
    )
    resp = client.post("/ai/results/delete", json={"book_id": 7})
    assert resp.get_json() == {"ok": True, "removed": 1}
    assert client.post("/ai/results/delete", json={}).status_code == 400


def test_library_import_and_export_endpoints(app_context, client, english):
    "Upload a JSON file through the API and export the stored explanations."
    from lute.models.book import Sentence

    book = make_book("Library", "placeholder", english)
    db.session.add(book)
    db.session.commit()
    db.session.add(
        Sentence(tx_id=book.texts[0].id, order=1, text_content="Hello there, friend.")
    )
    db.session.commit()

    payload = {
        "version": 1,
        "book_id": book.id,
        "lang_id": english.id,
        "target_lang": "zh",
        "model": "outside-ai",
        "items": [{"sentence": "Hello there friend", "translation": "你好，朋友。"}],
    }
    data = {
        "file": (io.BytesIO(json.dumps(payload).encode("utf-8")), "explanations.json"),
    }
    resp = client.post(
        "/ai/library/import", data=data, content_type="multipart/form-data"
    )
    assert resp.status_code == 200
    report = resp.get_json()
    assert report["ok"] is True
    assert report["added"] == 1
    assert report["levels"]["punct"] == 1

    export = client.get(f"/ai/library/export?book_id={book.id}")
    assert export.status_code == 200
    exported = json.loads(export.get_data(as_text=True))
    assert exported["items"][0]["translation"] == "你好，朋友。"
    assert client.get("/ai/library/export").status_code == 400
    assert client.post("/ai/library/import", data={}).status_code == 400


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
