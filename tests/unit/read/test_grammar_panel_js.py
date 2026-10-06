"""
The grammar panel's folded reference block (static/js/lute-commands.js).

The backend ships the block as

    "reference": {"sentence": <source sentence>, "text": <translation>,
                  "matches": [{"start": .., "end": ..}]}

and the renderer must put the sentence through the same example renderer the
page examples use, so the point is highlighted there too -- that is the whole
feature.  It is a Python<->JS contract, and the failure mode is silent: read a
field name the backend does not send and the block simply disappears, with no
error and no empty state.

The Japanese engine spells the field `japanese`, so the renderer accepts
either; both spellings are pinned below, because the day someone "tidies up"
the fallback, the Japanese panel loses its reference block and nothing else
would say so.

Everything runs the real lute-commands.js against a stub pane and a stubbed
$.getJSON (grammar_panel_js_harness.js), because the question is what markup
the module produces, which no Python test can see.  Skips when node is not on
PATH.
"""

import json
import os
import shutil
import subprocess

import pytest

NODE = shutil.which("node")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_HARNESS = os.path.join(_HERE, "grammar_panel_js_harness.js")
_MODULE = os.path.join(_ROOT, "lute", "static", "js", "lute-commands.js")


def _entry(**overrides):
    "One panel entry, in the shape the Korean engine emits."
    entry = {
        "key": "kgm_으나머지__282dc4",
        "name": "(으)ㄴ 나머지",
        "level": "TOPIK 3-4",
        "desc": "表示某种行为或状况持续下去，最终导致另一种状态。",
        "examples": [
            {
                "sentence": "무리하게 확장한 나머지 위기를 맞았다.",
                "matches": [{"start": 8, "end": 13}],
            }
        ],
    }
    entry.update(overrides)
    return entry


def _render(entries):
    "Render a panel payload through the real module and return its parts."
    if NODE is None:
        pytest.skip("node is not on PATH")
    if not os.path.exists(_HARNESS) or not os.path.exists(_MODULE):
        pytest.skip("grammar panel JS not found")
    proc = subprocess.run(
        [NODE, _HARNESS, _MODULE],
        input=json.dumps({"data": entries}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_panel_renders_every_entry_the_backend_sends():
    "A two-entry payload produces two rows; the panel is not silently empty."
    out = _render([_entry(), _entry(name="는 통에", key="kgm_는통에__2fe634")])
    assert out["item_count"] == 2, out["html"][:400]


def test_every_rendered_card_carries_its_stable_key():
    """
    Each card carries the backend's `key` as data-grammar-key.  The term
    form's Grammar button marks the card it jumped to by element identity,
    but the anchor is what lets a test (or a future deep link) name a card
    without depending on its position in the level grouping.
    """
    out = _render([_entry(), _entry(name="는 통에", key="kgm_는통에__2fe634")])
    assert out["keys"] == ["kgm_으나머지__282dc4", "kgm_는통에__2fe634"], out["html"][:400]


def test_reference_block_renders_and_highlights_the_korean_sentence():
    """
    The Korean engine's payload names the sentence ``sentence``; the block
    must render it through the example renderer so the point is marked, and
    print the translation under it.
    """
    sentence = "무리하게 확장한 나머지 위기를 맞았다."
    # "나머지" -- the grammar point itself.
    start, end = sentence.index("나머지"), sentence.index("나머지") + 3
    out = _render(
        [
            _entry(
                reference={
                    "sentence": sentence,
                    "text": "过度扩张，结果陷入了危机。",
                    "matches": [{"start": start, "end": end}],
                }
            )
        ]
    )
    assert out["has_refex"], out["html"]
    assert out["marks"] == ["나머지"], out["marks"]
    assert out["reftr"] == "过度扩张，结果陷入了危机。"
    # The reference block rides inside the folded details, with the notes.
    assert out["more_label"] == "参考例句 · 注意点"


def test_reference_block_still_renders_from_the_japanese_field():
    """
    The Japanese engine ships the same block spelled ``japanese``.  Dropping
    that spelling would take the Japanese panel's reference block away
    silently, so it is pinned here rather than left to the Japanese tests
    (which never execute this module).
    """
    sentence = "彼は忙しいあまり、休むのを忘れた。"
    start, end = sentence.index("忙しいあまり"), sentence.index("忙しいあまり") + 6
    out = _render(
        [
            _entry(
                reference={
                    "japanese": sentence,
                    "text": "他因为太忙，忘了休息。",
                    "matches": [{"start": start, "end": end}],
                }
            )
        ]
    )
    assert out["has_refex"], out["html"]
    assert out["marks"] == ["忙しいあまり"], out["marks"]
    assert out["reftr"] == "他因为太忙，忘了休息。"


def test_no_reference_renders_no_block():
    "A row without a reference (a hand-written rule) hides the whole block."
    out = _render([_entry()])
    assert not out["has_refex"], out["html"]
    assert out["marks"] == []


def test_formation_and_notes_render_when_present_and_are_omitted_otherwise():
    """
    The 接续 line and the 注意点 are separate optional fields: a row with
    both shows both, and a row with neither shows no empty scaffolding.
    """
    with_both = _render([_entry(formation="动词·形容词词干 + -(으)ㄴ 나머지", notes="前后分句的主语要一致。")])
    assert with_both["formation"] == "动词·形容词词干 + -(으)ㄴ 나머지"
    assert with_both["notes"] == "前后分句的主语要一致。"

    without = _render([_entry()])
    assert without["formation"] is None
    assert without["notes"] is None


def test_reference_offsets_outside_the_sentence_are_ignored():
    """
    The renderer clamps bad offsets away instead of slicing outside the
    string, so a stale offset shows the sentence unmarked rather than
    dropping or corrupting it.
    """
    sentence = "짧은 문장."
    out = _render(
        [
            _entry(
                reference={
                    "sentence": sentence,
                    "text": "短句。",
                    "matches": [{"start": 0, "end": 99}],
                }
            )
        ]
    )
    assert out["has_refex"], out["html"]
    assert out["marks"] == []
    assert sentence in out["refex"], out["refex"]
