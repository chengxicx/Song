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


def _run_steps(steps, paragraphs=None, cue_map=None, cue_page_starts=None):
    return _run(
        {
            "paragraphs": PARAGRAPHS if paragraphs is None else paragraphs,
            "cue_map": cue_map,
            "cue_page_starts": cue_page_starts,
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


# ---------------------------------------------------------------------
# Which page a cue lives on.
#
# The media players turn the page when the playhead leaves the page being
# read, and they resolve "which page" from the first-cue index of every
# page (window.LUTE_CUE_PAGE_STARTS).  An off-by-one here does not fail
# loudly: the reader is simply sent one page short, or one page past.


def _pages(steps, starts):
    return [r.get("page") for r in _run_steps(steps, cue_page_starts=starts)]


def test_a_cue_resolves_to_the_page_holding_it():
    # Page 1 holds cues 0-1, page 2 cues 2-4, page 3 cues 5-5.
    starts = [0, 2, 5]
    assert _pages([{"page_of_cue": i} for i in range(6)], starts) == [1, 1, 2, 2, 2, 3]


def test_the_first_cue_of_a_page_resolves_to_that_page():
    "The boundary belongs to the page it starts, not the one before it."
    assert _pages([{"page_of_cue": 0}], [0, 3]) == [1]
    assert _pages([{"page_of_cue": 3}], [0, 3]) == [2]


def test_a_cue_before_the_first_page_stays_put():
    """
    A cue index the map doesn't cover (a book whose cues were rewritten)
    gives 0, which the caller reads as "no page" -- it stays where it is
    instead of turning to page 1 blind.
    """
    assert _pages([{"page_of_cue": 0}], [4, 9]) == [0]


def test_a_book_without_a_map_never_turns_the_page():
    assert _pages([{"page_of_cue": 0}, {"page_of_cue": 9}], None) == [0, 0]
    assert _pages([{"page_of_cue": 0}], []) == [0]


def test_a_negative_or_missing_cue_has_no_page():
    "Playback with no cue yet (-1) must not page anywhere."
    assert _pages([{"page_of_cue": -1}], [0, 3]) == [0]


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


# A page the furigana has annotated: a word with a reading is rendered
# <ruby>word<rt>reading</rt></ruby> (lute-furigana.js), so the line's
# textContent is the word and its reading run together.
RUBY_PARAGRAPHS = [
    # One sentence, given as a token list: a {base, reading} token is a
    # furigana'd word, a string is plain text.
    [
        [
            {"base": "間", "reading": "あいだ"},
            "は本当",
            {"base": "事", "reading": "こと"},
            "です。",
        ]
    ],
    [["うん？"]],
]


def test_a_furigana_reading_is_not_part_of_the_line():
    """
    The mark must survive furigana.  A bare textContent flattens the
    reading into the sentence ("間" reads "間あいだ"), so the line never
    equals its cue text and the underline is dropped -- on every line
    holding a reading, which is why it survives the first sentence or two
    (no kanji, no ruby) and disappears once the readings land.  Measured
    on production: 10 of 28 lines still matched with readings in, 28 of
    28 without.
    """
    results = _run_steps(
        [{"set_cue_index": [0, "間は本当事です。"]}],
        paragraphs=RUBY_PARAGRAPHS,
        cue_map=[0, 1],
    )
    assert results[-1]["marked"] == [{"para": 0, "sentence": None, "text": "間は本当事です。"}]


def test_the_text_fallback_ignores_furigana_too():
    "Same answer without the map: the cue text is matched against the lines."
    results = _run_steps(
        [{"set_cue_index": [1, "間は本当事です。"]}],
        paragraphs=RUBY_PARAGRAPHS,
        cue_map=None,
    )
    assert results[-1]["marked"] == [{"para": 0, "sentence": None, "text": "間は本当事です。"}]


def test_a_line_without_readings_still_matches_beside_ruby_lines():
    "Kana-only lines carry no ruby; they keep matching their cues."
    results = _run_steps(
        [
            {"set_cue_index": [0, "間は本当事です。"]},
            {"set_cue_index": [1, "うん？"]},
        ],
        paragraphs=RUBY_PARAGRAPHS,
        cue_map=[0, 1],
    )
    assert [r["marked"] for r in results] == [
        [{"para": 0, "sentence": None, "text": "間は本当事です。"}],
        [{"para": 1, "sentence": None, "text": "うん？"}],
    ]
