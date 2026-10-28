"""
The reading page offers the mobile reader's MP3 <-> TTS player switch.

A book that has audio -- an mp3 / netease book, or a text book with an
audio file attached -- renders both player bars, and each one carries a
button that hands the book over to the other (yt-tts-mode-btn on the
media bar, tts-mp3-mode-btn on the TTS bar).  Only one bar is on screen
at a time; the switch is session-only and lives in tts-ui.js.

Books with a single player must not show a dead switch: video books
(youtube / video / bilibili) have no TTS bar, and text books without
audio have nothing to switch to.
"""

import json
import os

import lute
from lute.book.model import Book
from lute.book.service import Service as BookService
from lute.db import db

_SHADOWING_JS = os.path.join(
    os.path.dirname(lute.__file__), "static", "js", "lute-shadowing.js"
)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _function_body(js, signature):
    "The source of `signature`, up to the next top-level function."
    start = js.index(signature)
    rest = js[start + len(signature) :]
    end = rest.find("\nfunction ")
    return rest if end < 0 else rest[:end]


def _import_book(english, book_type="", audio_filename=None, source_uri=None):
    b = Book()
    b.title = "Switch book"
    b.language_id = english.id
    b.text = "Hello world.\nGoodbye!"
    b.book_type = book_type
    b.audio_filename = audio_filename
    b.source_uri = source_uri
    if book_type in ("mp3", "netease", "video", "youtube"):
        b.srt_data = json.dumps(
            [
                {"start": 1.0, "end": 4.2, "text": "Hello world."},
                {"start": 5.0, "end": 8.5, "text": "Goodbye!"},
            ]
        )
    return BookService().import_book(b, db.session)


def _content(client, book):
    resp = client.get(f"/read/{book.id}/page/1")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_mp3_book_renders_both_players_and_switches(app, app_context, client, english):
    "An audio book can be handed over to TTS and back."
    content = _content(client, _import_book(english, "mp3", audio_filename="song.mp3"))

    assert 'id="yt-player-container"' in content
    assert 'id="tts-player-container"' in content
    assert 'id="yt-tts-mode-btn"' in content
    assert 'id="tts-mp3-mode-btn"' in content
    # The media player's script must be loaded for the bar to work.
    assert "youtube-player.js" in content


def test_text_book_with_audio_renders_both_players(app, app_context, client, english):
    "A legacy text book with an audio file gets the same two bars."
    content = _content(client, _import_book(english, "", audio_filename="song.mp3"))

    assert 'id="yt-player-container"' in content
    assert 'id="tts-player-container"' in content
    assert 'id="yt-tts-mode-btn"' in content
    assert 'id="tts-mp3-mode-btn"' in content
    # This branch never loaded the media player's script before; without it
    # the rendered bar would sit there inert.
    assert "youtube-player.js" in content


def test_text_book_without_audio_has_no_switch(app, app_context, client, english):
    "Text-only book: the TTS bar is the only player, so no switch buttons."
    content = _content(client, _import_book(english, ""))

    assert 'id="tts-player-container"' in content
    assert 'id="yt-player-container"' not in content
    assert 'id="yt-tts-mode-btn"' not in content
    assert 'id="tts-mp3-mode-btn"' not in content


def test_reading_menu_switch_is_a_player_toggle(app, app_context, client, english):
    """
    The sidebar switch shows / hides *every* player, so it is labelled
    "Player" and carries a player-agnostic id.  A TTS-only id would
    suggest (and with the old code, behave as if) the audio player and
    the video transport were unaffected.
    """
    content = _content(client, _import_book(english, ""))

    assert 'id="player-toggle-container"' in content
    assert 'id="player-toggle"' in content
    assert ">Player<" in content
    assert "tts-player-toggle" not in content


def test_each_bar_carries_its_own_shadow_toggle(app, app_context, client, english):
    """
    Shadowing has a button on both bars, because either one can be the
    visible one.  This is the shape the JS below has to handle.
    """
    content = _content(client, _import_book(english, "mp3", audio_filename="song.mp3"))
    assert 'id="yt-shadowing-btn"' in content
    assert 'id="tts-shadowing-btn"' in content


def test_the_shadow_toggle_works_on_the_visible_bar():
    """
    Binding "the first id that exists" left the click listener on the
    hidden bar: getElementById(tts-shadowing-btn) always wins, and once
    mp3 books started rendering a TTS bar too, that is the hidden one --
    so the Shadow button on the visible MP3 bar did nothing at all.
    Every copy has to be bound, and every copy has to show the mode.
    """
    js = _read(_SHADOWING_JS)
    boot = _function_body(js, "function shadowingBoot()")
    assert (
        "shadowingButtons().forEach" in boot
    ), "every bar's Shadow button must get the click listener"
    setmode = _function_body(js, "function shadowingSetMode(on)")
    assert (
        "shadowingButtons().forEach" in setmode
    ), "turning shadowing on/off must light up every bar's button"
    # The single-element pattern is what broke it.
    assert 'getElementById("tts-shadowing-btn") ||' not in boot
    assert 'getElementById("tts-shadowing-btn") ||' not in setmode


def test_youtube_book_has_no_tts_bar(app, app_context, client, english):
    "Video books keep their own transport -- no TTS bar, no switch."
    content = _content(
        client,
        _import_book(
            english,
            "youtube",
            source_uri="https://www.youtube.com/watch?v=J7BXhKSqH6o",
        ),
    )

    assert 'id="yt-player-container"' in content
    assert 'id="tts-player-container"' not in content
    assert 'id="yt-tts-mode-btn"' not in content
    # ...but it still gets the player on/off switch: that one hides the
    # video transport too.
    assert 'id="player-toggle"' in content
