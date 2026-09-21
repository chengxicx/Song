"""
The reading page underlines the line the player is currently reading in
the page text itself (static/js/lute-playing-line.js).

The line is resolved two ways, and the difference matters:

- The media players (youtube / bilibili / mp3) know the absolute cue
  index, and the server sends the cue index of each page line as
  window.LUTE_PAGE_CUE_MAP.  That map is positional, so it can name the
  wrong line when the page text has drifted out of step with the cues
  (the text is editable), and underlining the wrong line is worse than
  underlining none.

- The TTS player builds its cues from the .textsentence spans, so it
  passes the span itself.

Everything is driven against a stub #thetext by playing_line_js_harness.js,
because what needs pinning is which <p> gets the class, and a real browser
cannot be asked that question cheaply.

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
_HARNESS = os.path.join(_HERE, "playing_line_js_harness.js")
_MODULE = os.path.join(_ROOT, "lute", "static", "js", "lute-playing-line.js")

# Three page lines, one cue each -- the shape a media book's page has.
PARAGRAPHS = [["Hola."], ["Adios", "amigo."], ["Tengo", "un", "gato."]]


def _run(payload):
    "Feed a scenario to the node harness and return its results."
    if NODE is None:
        pytest.skip("node is not on PATH")
    if not os.path.exists(_HARNESS) or not os.path.exists(_MODULE):
        pytest.skip("playing-line JS not found")
    proc = subprocess.run(
        [NODE, _HARNESS, _MODULE],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["results"]


def _run_steps(steps, paragraphs=None, cue_map=None):
    return _run(
        {
            "paragraphs": PARAGRAPHS if paragraphs is None else paragraphs,
            "cue_map": cue_map,
            "steps": steps,
        }
    )


def test_the_cue_line_is_marked():
    """
    The line holding the playing cue is marked -- and it is found even
    though the page injects a 🔊 button into it, which is markup and
    would otherwise make every line miss its cue.
    """
    results = _run_steps([{"set_cue_index": [1, "Adios amigo."]}], cue_map=[0, 1, 2])
    assert results[-1]["marked"] == [
        {"para": 1, "sentence": None, "text": "Adiosamigo."}
    ]


def test_the_mark_moves_with_the_cue():
    "Only the line being read is marked, never the one before it."
    results = _run_steps(
        [
            {"set_cue_index": [0, "Hola."]},
            {"set_cue_index": [1, "Adios amigo."]},
            {"set_cue_index": [2, "Tengo un gato."]},
        ],
        cue_map=[0, 1, 2],
    )
    assert [r["marked"] for r in results] == [
        [{"para": 0, "sentence": None, "text": "Hola."}],
        [{"para": 1, "sentence": None, "text": "Adiosamigo."}],
        [{"para": 2, "sentence": None, "text": "Tengoungato."}],
    ]


def test_nothing_is_marked_once_playback_stops():
    "The mark is taken off when the player deactivates its cue."
    results = _run_steps(
        [{"set_cue_index": [1, "Adios amigo."]}, {"clear": True}],
        cue_map=[0, 1, 2],
    )
    assert results[0]["marked"] != []
    assert results[-1]["marked"] == []


def test_a_cue_off_this_page_marks_nothing():
    """
    The reader can turn the page while the player keeps going, so the
    playing cue is often not on screen.  Marking nothing is the honest
    answer there.
    """
    results = _run_steps([{"set_cue_index": [7, "Otra cosa."]}], cue_map=[0, 1, 2])
    assert results[-1]["marked"] == []


def test_without_the_map_the_cue_text_is_matched():
    "The map is a shortcut, not a requirement: the text is matched directly."
    results = _run_steps([{"set_cue_index": [1, "Adios amigo."]}], cue_map=None)
    assert results[-1]["marked"] == [
        {"para": 1, "sentence": None, "text": "Adiosamigo."}
    ]


def test_a_drifted_map_falls_back_to_the_text():
    """
    The page text is hand-editable, so the map can point at the wrong
    line.  The text must win over the map's position -- a map that names
    the right cue on the wrong line must not be followed.
    """
    # The map puts cue 1 on line 0, but line 0 holds cue 0's text.
    results = _run_steps([{"set_cue_index": [1, "Adios amigo."]}], cue_map=[1, 0, 0])
    assert results[-1]["marked"] == [
        {"para": 1, "sentence": None, "text": "Adiosamigo."}
    ]


def test_a_drifted_map_with_no_match_marks_nothing():
    """
    When neither the map nor the text agrees there is no right answer,
    and a wrong underline is worse than none.
    """
    results = _run_steps(
        [{"set_cue_index": [1, "Texto que no esta."]}], cue_map=[0, 0, 0]
    )
    assert results[-1]["marked"] == []


def test_a_map_for_another_page_is_ignored():
    """
    A page turn swaps the text under htmx; a map of the wrong length is
    not this page's and must not be used positionally.
    """
    results = _run_steps(
        [{"set_cue_index": [1, "Adios amigo."]}], cue_map=[0, 1, 2, 3, 4]
    )
    assert results[-1]["marked"] == [
        {"para": 1, "sentence": None, "text": "Adiosamigo."}
    ]


def test_the_map_is_not_trusted_when_it_over_claims():
    """
    The map can claim a cue owns lines that do not hold its text (a
    stale map, after the page text was edited).  It must be rejected as
    a whole, not used for the lines that happen to fit.
    """
    results = _run_steps(
        [{"set_cue_index": [0, "Hola."]}],
        paragraphs=[["Hola."], ["Adios."]],
        cue_map=[0, 0],
    )
    # Marked by text, so only the line that really holds "Hola.".
    assert results[-1]["marked"] == [{"para": 0, "sentence": None, "text": "Hola."}]


def test_a_multiline_cue_marks_all_of_its_lines():
    """
    One cue can be a two-line subtitle, which is two page lines.  Both
    are part of the cue being read, so both are marked -- and the map
    must still check out, which means comparing the lines joined.
    """
    results = _run_steps(
        [{"set_cue_index": [0, "Hola.\nAdios amigo."]}],
        paragraphs=[["Hola."], ["Adios", "amigo."], ["Tengo un gato."]],
        cue_map=[0, 0, 1],
    )
    assert results[-1]["marked"] == [
        {"para": 0, "sentence": None, "text": "Hola."},
        {"para": 1, "sentence": None, "text": "Adiosamigo."},
    ]


def test_a_repeated_line_marks_only_the_first():
    """
    A repeated line (a chorus, say) is genuinely ambiguous without the
    map, so the first one wins instead of lighting up the whole page.
    """
    results = _run_steps(
        [{"set_cue_index": [0, "Hola."]}],
        paragraphs=[["Hola."], ["Adios."], ["Hola."]],
        cue_map=None,
    )
    assert results[-1]["marked"] == [{"para": 0, "sentence": None, "text": "Hola."}]


def test_the_tts_player_marks_the_sentence_it_passes():
    """
    The TTS path hands over the span itself, so the mark goes on that
    one sentence -- not on the paragraph around it, which the media
    players mark instead.
    """
    results = _run_steps(
        [{"set_sentence": [1, 1]}],
        cue_map=None,
    )
    # Paragraph 1 is two sentences ("Adios", "amigo."); only the
    # sentence handed over is marked, not the whole paragraph.
    assert results[-1]["marked"] == [{"para": 1, "sentence": 1, "text": "amigo."}]


def test_a_sentence_a_page_turn_dropped_clears_the_mark():
    """
    A page turn replaces #thetext, so a span the TTS player still holds
    is detached.  The mark must come off rather than stay on the line
    that happens to sit where the old one did.
    """
    results = _run_steps(
        [{"set_sentence": [2, 0]}, {"set_sentence": [0, 0], "detached": True}],
        cue_map=None,
    )
    assert results[0]["marked"] == [{"para": 2, "sentence": 0, "text": "Tengo"}]
    assert results[-1]["marked"] == []
