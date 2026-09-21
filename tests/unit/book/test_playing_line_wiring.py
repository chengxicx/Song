"""
Wiring for the underline that marks the line the player is reading in the
reading page's text.

tests/unit/book/test_playing_line_js.py pins what the helper
(static/js/lute-playing-line.js) does.  This file pins the two things
around it that are silent when they break:

- The map the media path depends on.  A media book's page text is its cue
  texts joined by newlines, so the cue index of each page line can be
  derived from the book -- and if that derivation is off by a line, the
  player underlines the wrong sentence, or nothing at all, with no error
  anywhere.

- The call sites.  They live inside closures in the player files, so
  nothing can call them from a test; they are checked by reading the
  source, as tests/unit/book/test_bilibili_embed_fallback.py does for the
  Bilibili embed fallback.  A missing call site is a feature that silently
  does nothing -- exactly the kind of break a source guardrail is for.
"""

import os
import re
from types import SimpleNamespace

import lute
from lute.book.types import subtitle_book_types
from lute.read.routes import _page_cue_line_map

_STATIC_JS = os.path.join(os.path.dirname(lute.__file__), "static", "js")
_TEMPLATES = os.path.join(os.path.dirname(lute.__file__), "templates", "read")
_ROUTES = os.path.join(os.path.dirname(lute.__file__), "read", "routes.py")

PLAYING_LINE_JS = os.path.join(_STATIC_JS, "lute-playing-line.js")
MEDIA_JS = os.path.join(_STATIC_JS, "media-player-base.js")
TTS_JS = os.path.join(_STATIC_JS, "tts-player.js")
INDEX_HTML = os.path.join(_TEMPLATES, "index.html")
PAGE_CONTENT_HTML = os.path.join(_TEMPLATES, "page_content.html")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _function_body(js, name):
    """
    The source of `function name(...)`, up to the next function declared
    at the same indentation -- the players declare their functions inside
    a closure, so this is how a call site can be reached.
    """
    marker = f"function {name}("
    start = js.index(marker)
    line_start = js.rindex("\n", 0, start) + 1
    indent = js[line_start:start]
    rest = js[start + len(marker) :]
    end = rest.find("\n" + indent + "function ")
    return rest if end < 0 else rest[:end]


_SUBTITLE_TYPE = (subtitle_book_types() or ("",))[0]


def _book(book_type, cues, pages):
    """
    A book with just what the map needs: its type, its cues, and its pages
    (each a (order, text) pair, as book.texts carries).
    """
    return SimpleNamespace(
        book_type=book_type,
        cues=cues,
        texts=[SimpleNamespace(order=o, text=t) for o, t in pages],
    )


def _cue(text, start=0.0, end=1.0):
    return {"start": start, "end": end, "text": text}


def test_the_map_is_the_cue_index_of_each_line():
    "One line per cue, in order: the map the reading page's lines get."
    book = _book(_SUBTITLE_TYPE, [_cue("a"), _cue("b"), _cue("c")], [(1, "a\nb\nc")])
    assert _page_cue_line_map(book, 1) == [0, 1, 2]


def test_a_multiline_cue_owns_all_of_its_lines():
    "A two-line subtitle is two page lines sharing one cue index."
    book = _book(_SUBTITLE_TYPE, [_cue("a\nb"), _cue("c")], [(1, "a\nb\nc")])
    assert _page_cue_line_map(book, 1) == [0, 0, 1]


def test_the_map_is_the_page_slice_of_the_cue_lines():
    """
    The map is per page, so a later page must be offset by the lines of
    the pages before it -- this is what keeps a page turn from marking a
    line on the wrong page.
    """
    book = _book(
        _SUBTITLE_TYPE,
        [_cue("a"), _cue("b"), _cue("c"), _cue("d")],
        [(1, "a\nb"), (2, "c\nd")],
    )
    assert _page_cue_line_map(book, 1) == [0, 1]
    assert _page_cue_line_map(book, 2) == [2, 3]


def test_a_book_without_cues_has_no_map():
    "Nothing to map, so the page gets no map rather than a wrong one."
    assert _page_cue_line_map(_book(_SUBTITLE_TYPE, [], [(1, "a")]), 1) == []


def test_a_page_that_runs_past_the_cues_has_no_map():
    """
    The page text is hand-editable, so it can end up with more lines than
    the cues cover.  No map, and the helper falls back to matching text.
    """
    book = _book(_SUBTITLE_TYPE, [_cue("a")], [(1, "a\nb\nc")])
    assert _page_cue_line_map(book, 1) == []


def test_a_page_the_book_does_not_have_has_no_map():
    book = _book(_SUBTITLE_TYPE, [_cue("a")], [(1, "a")])
    assert _page_cue_line_map(book, 2) == []


def test_a_non_subtitle_book_has_no_map():
    "A plain text book has no cues to line its paragraphs up with."
    book = _book("text", [_cue("a")], [(1, "a")])
    assert _page_cue_line_map(book, 1) == []


def test_the_reading_page_loads_the_helper():
    """
    Without this script the whole feature is silent: the players call
    window.LutePlayingLine, and every call is a no-op when it is absent.
    """
    assert "lute-playing-line.js" in _read(INDEX_HTML), (
        "the reading page must load static/js/lute-playing-line.js, or"
        " nothing underlines the line being read"
    )
    assert os.path.exists(PLAYING_LINE_JS)


def test_the_reading_page_ships_the_cue_map():
    "The media path resolves its line through this, and only through this."
    html = _read(PAGE_CONTENT_HTML)
    assert "LUTE_PAGE_CUE_MAP" in html
    # In the page fragment, not in the page shell: htmx swaps the fragment
    # on every page turn, so the map has to come with it.
    assert "page_cue_map" in html

    # Every render of that fragment must hand the map over -- both routes
    # render it, and each also has manga/pdf branches that render it with
    # nothing to map.
    routes = _read(_ROUTES)
    renders = re.findall(
        r'render_template\(\s*"read/page_content\.html".*?\)', routes, re.S
    )
    assert len(renders) == routes.count('"read/page_content.html"'), (
        "a read/page_content.html render was not matched by this test;"
        " the pattern needs updating"
    )
    for render in renders:
        assert "page_cue_map" in render, (
            "every read/page_content.html render must pass page_cue_map,"
            f" or that page has no map: {render}"
        )
    assert any("page_cue_map=_page_cue_line_map(" in r for r in renders), (
        "the text branches must derive the map from the book; the empty"
        " list is for the manga/pdf branches, which have no cues"
    )


def test_the_media_player_marks_and_clears_the_line():
    """
    The unified player (youtube / bilibili / mp3 / video) marks the cue it
    activates and drops the mark when it deactivates one.
    """
    js = _read(MEDIA_JS)
    activate = _function_body(js, "ytActivateCue")
    assert (
        "ytMarkPlayingLine(idx)" in activate
    ), "ytActivateCue must mark the line it is playing"
    assert "LutePlayingLine" in _function_body(js, "ytMarkPlayingLine")
    assert "LutePlayingLine.clear()" in _function_body(
        js, "ytDeactivateCue"
    ), "the mark must come off when playback leaves the cue"


def test_the_tts_player_marks_and_clears_the_sentence():
    "The TTS player marks the sentence span it activates, and clears it."
    js = _read(TTS_JS)
    activate = _function_body(js, "ttsActivateCue")
    assert (
        "ttsMarkPlayingSentence(idx)" in activate
    ), "ttsActivateCue must mark the sentence it is reading"
    assert "LutePlayingLine.setElement(" in _function_body(js, "ttsMarkPlayingSentence")
    assert "LutePlayingLine.clear()" in _function_body(
        js, "ttsDeactivateCue"
    ), "the mark must come off when the TTS player stops"
