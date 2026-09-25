"""
The edit page's lyrics panel (static/js/book-edit-cues.js) plays a line
by seeking the audio to that cue's start, and the auto-pause stops the
line at that cue's end.

A seek does not land exactly on the time that was asked for: it lands on
the audio's own frame grid, which can be a hair BEFORE it (a 2 us
overshoot on a wav; a whole MP3 frame, ~26 ms, on an mp3).  The panel
used to attribute such a position to the PREVIOUS cue -- whose end has
already gone by -- so with auto-pause on, clicking that line stopped it
at the instant it started and jumped back to the previous line: the line
looked unclickable (reported on book 285, line 12 of 34; lines 11 and 13
were fine, since only that boundary landed short).

Skips when node isn't on PATH (CI's pytest job doesn't guarantee it).
"""

import json
import os
import shutil
import subprocess

import pytest

NODE = shutil.which("node")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_HARNESS = os.path.join(_HERE, "cue_panel_js_harness.js")
_MODULE = os.path.join(_ROOT, "lute", "static", "js", "book-edit-cues.js")

# Contiguous cues, as subtitles and LRC lyrics are: each cue starts where
# the previous one ends, so a position a hair early is attributed to the
# wrong cue.  The 2 s gap between cue 3 and cue 4 mirrors an instrumental
# break (the reported book has one before line 11).
CUES = [
    {"i": 0, "start": 0.0, "end": 1.0, "text": "one"},
    {"i": 1, "start": 1.0, "end": 5.0, "text": "two"},
    {"i": 2, "start": 5.0, "end": 9.0, "text": "three"},
    {"i": 3, "start": 11.0, "end": 15.0, "text": "four"},
]

# 2 us: what a wav seek overshot by in the reported case.  26 ms: one
# MP3 frame at 44.1 kHz, the coarse end of the range.
EARLY_SEEKS = {"sub_microsecond": 0.000002, "mp3_frame": 0.026}


def _run(payload):
    "Feed a scenario to the node harness and return its results."
    if NODE is None:
        pytest.skip("node is not on PATH")
    if not os.path.exists(_HARNESS) or not os.path.exists(_MODULE):
        pytest.skip("cue panel JS not found")
    proc = subprocess.run(
        [NODE, _HARNESS, _MODULE],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["results"]


def _scenario(click_row, ticks, quantize, auto_pause=True):
    return {
        "cues": CUES,
        "quantize": quantize,
        "auto_pause": auto_pause,
        "steps": [{"click_row": click_row}] + [{"tick": t} for t in ticks],
    }


@pytest.mark.parametrize("quantize", sorted(EARLY_SEEKS.values()))
@pytest.mark.parametrize("click_row", [2, 3, 4])
def test_clicked_line_plays_its_own_cue(click_row, quantize):
    """
    Clicking a line seeks to its cue and keeps playing it -- it must not
    be stopped by the previous cue's (already passed) end.
    """
    cue = CUES[click_row - 1]
    # The browser's first timeupdate after the seek reports the position
    # the seek actually landed on, then normal playback carries on.
    landed = cue["start"] - quantize
    ticks = [landed, landed + 0.3]
    results = _run(_scenario(click_row, ticks=ticks, quantize=quantize))
    after_click, after_first_tick, after_second_tick = results

    assert after_click["play_calls"] == 1
    assert after_click["seeks"] == [cue["start"]]
    assert after_click["paused"] is False
    # The line the user clicked is the one highlighted.
    assert after_click["active_row"] == click_row

    # Still the same line, and still playing, once the playhead has
    # really entered it.
    assert after_second_tick["pause_calls"] == 0, "auto-pause cut the line off"
    assert after_second_tick["paused"] is False
    assert after_second_tick["active_row"] == click_row
    assert after_second_tick["t"] == pytest.approx(ticks[-1], abs=1e-6)
    assert after_first_tick["paused"] is False
    assert after_first_tick["active_row"] == click_row


def test_auto_pause_still_stops_at_the_cue_end():
    "The fix must not break the pause the panel is there to provide."
    cue = CUES[2]  # start 5.0, end 9.0
    results = _run(_scenario(3, ticks=[5.0, 9.2], quantize=0.000002))
    final = results[-1]

    assert final["pause_calls"] == 1
    assert final["paused"] is True
    # Rewound to the line's start, so pressing play replays that line.
    assert final["t"] == pytest.approx(cue["start"], abs=1e-3)


@pytest.mark.parametrize("quantize", sorted(EARLY_SEEKS.values()))
def test_stepping_to_the_next_line_plays_it(quantize):
    """
    Stepping with the next-line button crosses the same boundary: the
    next line starts where the current one ended, so a seek that lands
    short must not be read as the line that just finished.
    """
    nxt = CUES[1]  # start 1.0, end 5.0
    landed = nxt["start"] - quantize
    results = _run(
        {
            "cues": CUES,
            "quantize": quantize,
            "auto_pause": True,
            "steps": [
                {"click_row": 1},
                {"tick": 0.0},
                {"click": "cueNextBtn"},
                {"tick": landed},
                {"tick": landed + 0.3},
            ],
        }
    )
    final = results[-1]
    assert results[2]["seeks"][-1] == nxt["start"]
    assert final["pause_calls"] == 0, "the finished line paused the next one"
    assert final["paused"] is False
    assert final["active_row"] == 2
    assert final["t"] == pytest.approx(landed + 0.3, abs=1e-6)


def test_without_auto_pause_a_line_still_plays():
    "Auto-pause off: clicking a line just plays it, as before."
    cue = CUES[2]
    results = _run(
        _scenario(
            3,
            ticks=[cue["start"] - 0.000002, cue["start"] + 0.3],
            quantize=0.000002,
            auto_pause=False,
        )
    )
    assert results[-1]["paused"] is False
    assert results[-1]["pause_calls"] == 0
    assert results[-1]["active_row"] == 3
