"""
Geometry of the grammar hover rings (lute/static/js/lute-commands.js).

The rings live on a fixed layer over the reading text and no Python test can
see them, so the real module runs in node against a stubbed reading pane
(grammar_rings_harness.js).  The scenario puts two sentences in ONE
.textsentence node and asks the panel to ring only the second one:

* the blue ring must hug the example sentence, not the whole node;
* the amber box must hug the matched characters, not the whole cell (the
  second cell is sized like a saved multi-word term).

Skips when node is not on PATH.
"""

import json
import os
import shutil
import subprocess

import pytest

NODE = shutil.which("node")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_HARNESS = os.path.join(_HERE, "grammar_rings_harness.js")
_MODULE = os.path.join(_ROOT, "lute", "static", "js", "lute-commands.js")


def _rings():
    "Hover one example through the real module and return its ring geometry."
    if NODE is None:
        pytest.skip("node is not on PATH")
    if not os.path.exists(_HARNESS) or not os.path.exists(_MODULE):
        pytest.skip("grammar ring JS not found")
    payload = {
        "data": [
            {
                "key": "zz-ring-test",
                "name": "BB",
                "level": "N4",
                "desc": "ring geometry fixture",
                # Second sentence of a two-sentence node; span covers "BB",
                # the first two characters of a four-character cell.
                "examples": [{"sentence": "BBB。", "matches": [{"start": 0, "end": 2}]}],
            }
        ]
    }
    proc = subprocess.run(
        [NODE, _HARNESS, _MODULE],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["rings"]


def _one(rings, amber):
    matches = [r for r in rings if r["amber"] is amber]
    assert len(matches) == 1, rings
    return matches[0]


def test_blue_ring_hugs_the_example_sentence_not_the_whole_node():
    """
    The example is one sentence inside a multi-sentence .textsentence node.
    Cell1 (x 0..40) belongs to the other sentence; the ring must stay on
    cell2 (x 100..140) plus its 3px padding.
    """
    blue = _one(_rings(), amber=False)
    # Left edge: 100 - 3px outline padding, with a little slack.
    assert 94 <= blue["left"] <= 100, blue
    # Right edge stays on cell2 (140 + 3px padding); a whole-node ring would
    # span roughly x=-3..143.
    assert 140 <= blue["left"] + blue["width"] <= 146, blue
    assert blue["width"] < 60, blue


def test_amber_box_hugs_the_matched_characters_inside_the_cell():
    """
    The span [0, 2) covers "BB" (20px) of the 40px cell; the amber box must
    be range-width (20 + 2px padding), never the whole 40px cell.
    """
    amber = _one(_rings(), amber=True)
    assert 95 <= amber["left"] <= 101, amber
    assert 20 <= amber["width"] <= 26, amber
    # It ends halfway through the cell, not at the cell's right edge (140).
    assert amber["left"] + amber["width"] < 130, amber
