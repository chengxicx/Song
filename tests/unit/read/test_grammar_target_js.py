"""
Which grammar card the term form's Grammar button jumps to.

The choice is pure logic over the panel payload and the reading page's
sentence nodes -- "the card whose matched span covers the clicked word, else
the first card on the sentence, in panel order" -- and it is invisible to
both a Python test (no DOM) and a browser test (the grammar engine decides
what ends up on screen).  So the real module is run under node against stub
elements (grammar_target_harness.js) and asked directly.  Skips when node is
not on PATH.
"""

import json
import os
import shutil
import subprocess

import pytest

NODE = shutil.which("node")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_HARNESS = os.path.join(_HERE, "grammar_target_harness.js")
_MODULE = os.path.join(_ROOT, "lute", "static", "js", "lute-commands.js")


@pytest.fixture(name="answers", scope="module")
def fixture_answers():
    "Run the harness once; every test reads the same answers."
    if NODE is None:
        pytest.skip("node is not on PATH")
    if not os.path.exists(_HARNESS) or not os.path.exists(_MODULE):
        pytest.skip("grammar target harness not found")
    proc = subprocess.run(
        [NODE, _HARNESS, _MODULE], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_span_test_answers_about_the_clicked_word(answers):
    assert answers["covers_clicked_word"] is True
    assert answers["covers_another_word"] is False
    assert answers["covers_nothing_without_a_word"] is False


def test_the_card_covering_the_clicked_word_wins(answers):
    "Even when an earlier card also matches the sentence."
    assert answers["pick_prefers_covering"] == "covering"


def test_the_first_card_on_the_sentence_is_the_fallback(answers):
    "No card covers the clicked word, so panel order decides."
    assert answers["pick_falls_back_to_first_on_sentence"] == "early"


def test_a_sentence_with_no_grammar_yields_no_target(answers):
    assert answers["pick_none_when_sentence_unmatched"] is None


def test_a_phrase_split_across_sentence_nodes_still_matches(answers):
    assert answers["pick_matches_a_split_sentence"] == "unrelated"
