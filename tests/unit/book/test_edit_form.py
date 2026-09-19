"""
EditBookForm guards around the text/subtitle fields.

The edit page hides the generic "Text file" upload for subtitle books
(youtube/bilibili/mp3/netease/video), but the hidden input keeps any
file that was picked while the book was still a Text type, so the
server must ignore such uploads itself.
"""

import io

from werkzeug.datastructures import FileStorage

from lute.book.forms import EditBookForm
from lute.book.model import Book

SRT_TEXT = "\n".join(
    [
        "1",
        "00:00:01,000 --> 00:00:04,200",
        "Hello world.",
        "",
        "2",
        "00:00:05,000 --> 00:00:08,500",
        "Goodbye.",
    ]
)


def _stray_txt_file():
    return FileStorage(
        stream=io.BytesIO(b"plain prose that is not srt"),
        filename="stray.txt",
        content_type="text/plain",
    )


def _edit_form(app, book, extra_fields=None):
    "Build an EditBookForm as a POST submission would."
    data = {"title": "B", "text": SRT_TEXT, "book_type": book.book_type}
    data.update(extra_fields or {})
    with app.test_request_context(method="POST", data=data):
        return EditBookForm(obj=book)


def test_subtitle_book_ignores_stray_textfile(app, english):
    "A textfile attached to an mp3 edit must not overwrite the SRT-derived text."
    b = Book()
    b.language_id = english.id
    b.book_type = "mp3"

    form = _edit_form(app, b, {"textfile": _stray_txt_file()})
    form.populate_obj(b)

    assert b.text_stream is None, "stray text file dropped"
    assert b.text_stream_filename is None
    assert b.srt_data is not None, "cues parsed from the SRT text field"
    assert "Hello world." in b.text, "text came from the SRT field"
    assert "plain prose" not in b.text, "text not overwritten by the stray file"


def test_text_book_still_accepts_textfile(app, english):
    "The textfile upload keeps working for plain Text books."
    b = Book()
    b.language_id = english.id
    b.book_type = ""

    form = _edit_form(app, b, {"textfile": _stray_txt_file()})
    form.populate_obj(b)

    assert b.text_stream is not None, "text file kept"
    assert b.text_stream_filename == "stray.txt"


def test_type_change_away_from_subtitles_clears_srt(app, english):
    "Switching a book to a Text type drops the subtitle data."
    b = Book()
    b.language_id = english.id
    b.book_type = "mp3"
    b.srt_data = '[{"start": 1.0, "end": 2.0, "text": "x"}]'
    b.video_current_pos = 1.5

    data = {"title": "B", "text": SRT_TEXT, "book_type": ""}
    with app.test_request_context(method="POST", data=data):
        form = EditBookForm(obj=b)
    form.populate_obj(b)

    assert b.book_type == "", "type switched to Text"
    assert b.srt_data is None
    assert b.video_current_pos is None
