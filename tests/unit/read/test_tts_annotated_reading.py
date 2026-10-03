"""
Term TTS prefers the term's annotated reading (kana) over the term text.

Kanji terms whose stored reading -- the pronunciation / romanization
field -- differs from what a TTS engine guesses (names, irregular
readings) should sound like the annotation.  The utterance helpers
speak the annotation only when it contains kana: romaji / pinyin
annotations would be read as Latin text and sound wrong, so those fall
back to the term text.  Every speaking surface goes through the same
helper (window.luteTtsSpeakTerm): the term form's speaker button, the
open-term auto pronunciation, review card playback, and the reading
frame's hover pronunciation (reading fetched with the term popup).
"""

import os

import pytest

from lute.db import db
from lute.term.model import Repository, Term

_JS_DIR = os.path.join(os.path.dirname(__import__("lute").__file__), "static", "js")
_TTS_JS = os.path.join(_JS_DIR, "tts.js")
_TTS_TRANSLATE_JS = os.path.join(_JS_DIR, "tts-translate.js")
_LUTE_TOOLTIP_JS = os.path.join(_JS_DIR, "lute-tooltip.js")
_LUTE_REVIEW_JS = os.path.join(_JS_DIR, "lute-review.js")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(name="repo")
def fixture_repo():
    return Repository(db.session)


def _save_term(repo, language, text, reading=None):
    "Create and commit a term business object, return it reloaded."
    t = Term()
    t.language_id = language.id
    t.text = text
    if reading is not None:
        # Set after .text: for new terms the setter auto-fills a
        # parser-generated reading, and the explicit value is the one
        # under test.
        t.romanization = reading
    t.status = 1
    repo.add(t)
    repo.commit()
    return repo.find(language.id, text)


def test_tts_js_prefers_a_kana_annotation():
    "speakTermText speaks the reading only when it contains kana."
    src = _read(_TTS_JS)
    assert "function speakTermText" in src
    assert "window.luteTtsSpeakTerm = speakTermText" in src
    helper = src.split("function speakTermText")[1].split("\n  }")[0]
    # Kana-only gate: hiragana + katakana ranges.  Romaji and pinyin
    # annotations fail the test and fall through to the term text.
    assert "/[\\u3040-\\u309F\\u30A0-\\u30FF]/.test(reading)" in helper
    assert helper.index(".test(reading)") < helper.index("speakText(reading")
    assert "speakText(term, null, langOverride)" in helper


def test_hover_pronunciation_accepts_a_reading():
    "The shared hover engine speaks the annotation when handed one."
    src = _read(_TTS_JS)
    assert "function luteHoverSpeakStart(rawText, isPlayingFn, readingText)" in src
    hover = src.split("function luteHoverSpeakStart")[1].split("\n  }")[0]
    assert "speakTermText(cleanText, readingText)" in hover
    # The reading comes from the popup cache, keyed by the span's word id.
    assert "hoverReadingFor(wordSpan)" in src


def test_hover_reading_is_served_from_the_popup_cache():
    "lute-tooltip.js caches readings; tts.js looks them up by word id."
    tooltip = _read(_LUTE_TOOLTIP_JS)
    assert "window.LUTE_TERM_READINGS" in tooltip
    assert 'querySelector(".termpopup-reading")' in tooltip
    # Cleared with the popup cache so a just-saved reading re-fetches.
    assert (
        "window.LUTE_TERM_READINGS"
        in tooltip.split("function clear_termpopup_cache")[1].split("\n}", 1)[0]
    )

    reader = _read(_TTS_JS).split("function hoverReadingFor")[1].split("\n  }")[0]
    assert 'getAttribute("data-wid")' in reader
    assert "window.LUTE_TERM_READINGS" in reader


def test_open_term_auto_speak_no_longer_requires_the_top_frame():
    "The top reading page has no #text input, so a top-frame-only guard"
    " left the click pronunciation dead: the frame that owns the form"
    " speaks, and it speaks the annotation."
    src = _read(_TTS_TRANSLATE_JS)
    assert "isTopFrame" not in src
    block = src.split(
        "if (SETTINGS.clickPronunciation && globalCache.lastWord !== word) {"
    )[1].split("\n      }")[0]
    assert 'getElementById("romanization")' in block
    assert "window.luteTtsSpeakTerm(word, reading)" in block


def test_review_cards_speak_the_annotation():
    "Review playback goes through the same annotation-aware helper."
    fn = _read(_LUTE_REVIEW_JS).split("function speak_term(c)")[1].split("\n  }")[0]
    assert "window.luteTtsSpeakTerm(c.term_text, c.romanization" in fn


def test_term_form_speaker_button_speaks_the_annotation(
    app_context, client, english, repo
):
    "The wordframe's 🔊 button hands the #romanization value to the helper."
    _save_term(repo, english, "btnreading", "おとな")
    # The button and its handler live in the shared form template, so a
    # freshly created term's edit form carries the wiring.
    term = repo.find(english.id, "btnreading")
    body = client.get(f"/read/edit_term/{term.id}").get_data(as_text=True)

    assert 'id="term-speak-btn"' in body
    assert 'window.luteTtsSpeakTerm(t.value, r ? r.value : "")' in body
    # The form carries the reading, hidden or not.
    assert 'id="romanization"' in body


def test_termpopup_carries_the_annotated_reading(app_context, client, english, repo):
    "The popup fragment exposes the reading for lute-tooltip.js to cache."
    term = _save_term(repo, english, "speakme", "おとな")
    body = client.get(f"/read/termpopup/{term.id}").get_data(as_text=True)
    assert 'class="termpopup-reading"' in body
    assert "おとな" in body

    # Terms without a reading still render the (empty) holder, so the
    # cache learns the reading is gone rather than serving a stale one.
    bare = _save_term(repo, english, "speakme-bare")
    body = client.get(f"/read/termpopup/{bare.id}").get_data(as_text=True)
    assert 'class="termpopup-reading"' in body
