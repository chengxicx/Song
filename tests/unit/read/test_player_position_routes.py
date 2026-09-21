"""
The reading page's player posts its position back to the server on a loop.

A player can outlive its book -- the reader deletes it from the book list,
or a tab left open keeps posting -- and both endpoints used to look the book
up and then write to it unconditionally, so every save raised
AttributeError: 'NoneType' object has no attribute 'video_current_pos' and
answered 500.  The shared media engine posts to the youtube endpoint for
every backend it drives, audio included, so a single open audio book was
enough to fill the log with tracebacks.

Found by the media-player acceptance scenario, which is the first test to
open a real player against a book that a later scenario wipes.
"""

import pytest

from lute.db import db
from lute.models.book import Book
from lute.models.language import Language


@pytest.fixture(name="book")
def fixture_book(app_context):
    "A book to save a position against."
    language = Language()
    language.name = "English"
    language.lang_code = "eng"
    language.parser_type = "spacedel"
    db.session.add(language)
    db.session.commit()
    book = Book("Player position book", language)
    db.session.add(book)
    db.session.commit()
    return book


@pytest.mark.parametrize(
    "url,attribute",
    [
        ("/read/save_player_data", "audio_current_pos"),
        ("/read/save_youtube_player_data", "video_current_pos"),
    ],
)
def test_a_position_is_saved(app, book, url, attribute):
    "The happy path still saves."
    response = app.test_client().post(url, json={"bookid": book.id, "position": 12.5})
    assert response.status_code == 200
    assert getattr(book, attribute) == 12.5


@pytest.mark.parametrize(
    "url",
    ["/read/save_player_data", "/read/save_youtube_player_data"],
)
def test_a_deleted_book_answers_404_instead_of_raising(app, url):
    """
    A save for a book that is gone is not ours to keep, and must not raise:
    the client fires and forgets, so a 500 here only pollutes the log.
    """
    response = app.test_client().post(url, json={"bookid": 99999, "position": 1.0})
    assert response.status_code == 404
