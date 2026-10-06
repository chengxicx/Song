"""
The Shadow panel starts on the sentence of the word the reader clicked.

The behaviour itself is browser-side (opening a word closes the panel, and
turning the mode back on has to land on that word's sentence rather than
the page's first one), but it hangs on four wiring points that are pure
source facts, and every one of them fails silently -- the panel just opens
on sentence 1 again:

  * lute-tooltip.js's show_term_edit_form has to hand the word over; it is
    the single choke point every "open this word" gesture goes through
    (click, tap, long press, keyboard cursor),
  * lute-shadowing.js has to keep the bridge and the remembered word,
  * shadowingSetMode has to consult the clicked word *before* the player's
    cue fallback, or the cue wins and the click is ignored,
  * index.html has to ship the script at all.

The word is consumed on read, so a later plain toggle goes back to the
player's cue.
"""

import os
import re

import pytest

_LUTE_DIR = os.path.dirname(__import__("lute").__file__)
_SHADOWING_JS = os.path.join(_LUTE_DIR, "static", "js", "lute-shadowing.js")
_TOOLTIP_JS = os.path.join(_LUTE_DIR, "static", "js", "lute-tooltip.js")
_INDEX_HTML = os.path.join(_LUTE_DIR, "templates", "read", "index.html")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _function_body(js, name):
    "The body of a top-level function, up to the next one."
    marker = f"function {name}("
    assert marker in js, f"{name} not found"
    start = js.index(marker)
    rest = js[start + len(marker) :]
    end = rest.find("\nfunction ")
    return rest if end < 0 else rest[:end]


@pytest.fixture(name="shadowing")
def fixture_shadowing():
    return _read(_SHADOWING_JS)


def test_opening_a_word_hands_it_to_the_shadowing_module():
    "show_term_edit_form is where every open-this-word gesture lands."
    body = _function_body(_read(_TOOLTIP_JS), "show_term_edit_form")
    # Assert the call shape, not the bare name: the module defines the
    # function too, and a definition would satisfy a loose check.
    assert 'typeof window.luteShadowingRememberWord === "function"' in body
    assert "window.luteShadowingRememberWord(el)" in body


def test_the_bridge_keeps_the_word_and_rejects_non_words(shadowing):
    "The bridge stores a word span and nothing else."
    assert "window.luteShadowingRememberWord = function (el)" in shadowing
    body = shadowing.split("window.luteShadowingRememberWord = function")[1]
    body = body.split("\n};")[0]
    assert "shadowingClickedWord =" in body
    assert 'classList.contains("word")' in body


def test_the_clicked_word_is_resolved_to_its_sentence(shadowing):
    "The unit is the .textsentence -- the element the arrows and cues use."
    body = _function_body(shadowing, "shadowingSentenceForWord")
    assert 'closest(".textsentence")' in body
    assert "isConnected" in body
    # A word in a player subtitle has no .textsentence parent; it is joined
    # back to #thetext by the data-order both copies carry.
    assert "data-order" in body
    assert 'getElementById("thetext")' in body


def test_the_clicked_word_is_consumed_and_becomes_a_unit(shadowing):
    "Read once: the next toggle without a click falls back to the cue."
    body = _function_body(shadowing, "shadowingTakeClickedWordUnit")
    assert "const el = shadowingClickedWord;" in body
    assert "shadowingClickedWord = null;" in body
    assert "shadowingSentenceForWord(el)" in body
    assert "shadowingUnitFromEl(sentence" in body


def test_the_clicked_word_beats_the_player_cue(shadowing):
    "Order matters: the cue fallback must not pre-empt the click."
    body = _function_body(shadowing, "shadowingSetMode")
    on_branch = body.split("if (on) {")[1].split("} else {")[0]
    assert "shadowingTakeClickedWordUnit()" in on_branch
    assert "shadowingSetUnit(clicked)" in on_branch
    assert on_branch.index("shadowingTakeClickedWordUnit()") < on_branch.index(
        "shadowingApplyCue(shadowingLastCue)"
    )
    # ...and the cue fallback is still there for the no-click case.
    assert "shadowingApplyCue(shadowingLastCue)" in on_branch


def test_the_reading_page_ships_the_shadowing_script():
    "Without the script tag the whole feature is a no-op."
    html = _read(_INDEX_HTML)
    assert re.search(r"vstatic\w*\('js/lute-shadowing\.js'\)", html)
