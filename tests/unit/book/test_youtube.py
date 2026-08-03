"""
Tests for the YouTube video book feature.
"""

import io
import json
from unittest.mock import patch
from lute.db import db
from lute.book.service import (
    youtube_video_id,
    parse_subtitle_file,
    Service as BookService,
)
from lute.read.routes import _subtitle_words_html
from lute.models.repositories import BookRepository


SAMPLE_SRT = """1
00:00:01,000 --> 00:00:04,200
Hello world.

2
00:00:05,000 --> 00:00:08,500
This is a test subtitle.

3
00:00:10,000 --> 00:00:13,000
Goodbye!
"""

SAMPLE_VTT = """WEBVTT

Kind: captions
Language: en

00:00:01.000 --> 00:00:04.200
Hello world.

00:00:05.000 --> 00:00:08.500
This is a test subtitle.
"""


# ---------------------------------------------------------------------
# youtube_video_id
# ---------------------------------------------------------------------


def test_youtube_video_id_watch_url():
    assert (
        youtube_video_id("https://www.youtube.com/watch?v=J7BXhKSqH6o")
        == "J7BXhKSqH6o"
    )


def test_youtube_video_id_watch_with_extra_params():
    assert (
        youtube_video_id("https://www.youtube.com/watch?v=J7BXhKSqH6o&t=30s&ab_channel=x")
        == "J7BXhKSqH6o"
    )


def test_youtube_video_id_youtu_be():
    assert youtube_video_id("https://youtu.be/J7BXhKSqH6o") == "J7BXhKSqH6o"


def test_youtube_video_id_embed():
    assert (
        youtube_video_id("https://www.youtube.com/embed/J7BXhKSqH6o")
        == "J7BXhKSqH6o"
    )


def test_youtube_video_id_shorts():
    assert (
        youtube_video_id("https://www.youtube.com/shorts/J7BXhKSqH6o")
        == "J7BXhKSqH6o"
    )


def test_youtube_video_id_invalid():
    assert youtube_video_id("https://example.com/not-youtube") is None
    assert youtube_video_id("") is None
    assert youtube_video_id(None) is None


# ---------------------------------------------------------------------
# parse_subtitle_file
# ---------------------------------------------------------------------


def test_parse_srt_file():
    text, cues_json = parse_subtitle_file("sub.srt", io.BytesIO(SAMPLE_SRT.encode()))
    assert text == "Hello world.\nThis is a test subtitle.\nGoodbye!"
    cues = json.loads(cues_json)
    assert len(cues) == 3
    assert cues[0]["start"] == 1.0
    assert cues[0]["end"] == 4.2
    assert cues[0]["text"] == "Hello world."
    assert cues[1]["start"] == 5.0
    assert cues[2]["text"] == "Goodbye!"


def test_parse_vtt_file_with_youtube_header():
    text, cues_json = parse_subtitle_file("sub.vtt", io.BytesIO(SAMPLE_VTT.encode()))
    assert text == "Hello world.\nThis is a test subtitle."
    cues = json.loads(cues_json)
    assert len(cues) == 2
    assert cues[0]["start"] == 1.0


# ---------------------------------------------------------------------
# parse_subtitle_file -- Japanese cue refinement
# ---------------------------------------------------------------------

# Two cues split mid-sentence (prev ends with て, gap < 400ms).
JP_MERGE_SRT = """1
00:00:01,000 --> 00:00:03,000
昨日は友達に会って

2
00:00:03,200 --> 00:00:06,000
楽しく話しました

3
00:00:07,000 --> 00:00:09,000
今日は晴れています。
"""

# One long cue (>8s) containing two sentences separated by 。
JP_SPLIT_SRT = """1
00:00:01,000 --> 00:00:12,000
今日はいい天気ですね。明日は雨が降るそうです。
"""


def test_parse_srt_japanese_merges_mid_sentence(japanese):
    "Cues ending with a continuative particle (て) are force-merged."
    text, cues_json = parse_subtitle_file(
        "jp.srt", io.BytesIO(JP_MERGE_SRT.encode()), language=japanese
    )
    cues = json.loads(cues_json)
    # Cue 1 (ends with て) and cue 2 merge into one; cue 3 stays separate
    # (ends with 。).
    assert len(cues) == 2
    assert cues[0]["text"] == "昨日は友達に会って楽しく話しました"
    assert cues[1]["text"] == "今日は晴れています。"


def test_parse_srt_japanese_splits_long_cue(japanese):
    "A merged cue longer than 8s is split at the strong terminator 。"
    text, cues_json = parse_subtitle_file(
        "jp.srt", io.BytesIO(JP_SPLIT_SRT.encode()), language=japanese
    )
    cues = json.loads(cues_json)
    assert len(cues) == 2
    assert cues[0]["text"].endswith("ですね。")
    assert cues[1]["text"].startswith("明日は")
    # Time spans the original range.
    assert cues[0]["start"] == 1.0
    assert cues[1]["end"] == 12.0


def test_parse_srt_non_japanese_unchanged(japanese):
    "Non-Japanese languages get no refinement (3 cues stay 3)."
    # Use the English sample; passing a Japanese language should not
    # change English text, but here we pass language=None to confirm
    # the default path is untouched.
    text, cues_json = parse_subtitle_file("sub.srt", io.BytesIO(SAMPLE_SRT.encode()))
    cues = json.loads(cues_json)
    assert len(cues) == 3
    assert cues[0]["text"] == "Hello world."


# ---------------------------------------------------------------------
# Book creation / import
# ---------------------------------------------------------------------


def test_create_youtube_book(app, app_context, spanish):
    "A Book BO with youtube fields saves with the right data."
    from lute.book.model import Book

    b = Book()
    b.title = "My YouTube Book"
    b.language_id = spanish.id
    b.text = "Hello world.\nThis is a test subtitle.\nGoodbye!"
    b.book_type = "youtube"
    b.srt_data = json.dumps(
        [
            {"start": 1.0, "end": 4.2, "text": "Hello world."},
            {"start": 5.0, "end": 8.5, "text": "This is a test subtitle."},
        ]
    )
    b.source_uri = "https://www.youtube.com/watch?v=J7BXhKSqH6o"
    b.book_tags = ["youtube"]
    b.video_current_pos = 2.5

    svc = BookService()
    dbbook = svc.import_book(b, db.session)

    repo = BookRepository(db.session)
    book = repo.find(dbbook.id)
    assert book.book_type == "youtube"
    assert book.source_uri == "https://www.youtube.com/watch?v=J7BXhKSqH6o"
    assert book.video_current_pos == 2.5
    cues = book.cues
    assert len(cues) == 2
    assert cues[0]["text"] == "Hello world."
    assert [t.text for t in book.book_tags] == ["youtube"]


def test_book_cues_empty_when_no_srt():
    from lute.models.book import Book

    b = Book()
    b.srt_data = None
    assert b.cues == []

    b.srt_data = "not valid json"
    assert b.cues == []


# ---------------------------------------------------------------------
# _subtitle_words_html aligns with cues
# ---------------------------------------------------------------------


def test_subtitle_words_html_aligns_with_cues(app, app_context, english):
    "Each cue gets a rendered word chunk, in order."
    from lute.book.model import Book

    b = Book()
    b.title = "YT"
    b.language_id = english.id
    b.text = "Hello world.\nThis is a test subtitle.\nGoodbye!"
    b.book_type = "youtube"
    b.srt_data = json.dumps(
        [
            {"start": 1.0, "end": 4.2, "text": "Hello world."},
            {"start": 5.0, "end": 8.5, "text": "This is a test subtitle."},
            {"start": 10.0, "end": 13.0, "text": "Goodbye!"},
        ]
    )

    svc = BookService()
    dbbook = svc.import_book(b, db.session)

    words = _subtitle_words_html(dbbook)
    assert len(words) == 3, "one HTML chunk per cue"
    assert "Hello" in words[0] and "world" in words[0]
    assert "This" in words[1] and "subtitle" in words[1]
    assert "Goodbye" in words[2]


def test_subtitle_words_html_pads_missing_chunks(app, app_context, english):
    "If tokenization yields fewer chunks, the list is padded to the cue count."
    from lute.book.model import Book

    b = Book()
    b.title = "YT2"
    b.language_id = english.id
    b.text = "One line only"
    b.book_type = "youtube"
    b.srt_data = json.dumps(
        [
            {"start": 1.0, "end": 2.0, "text": "One"},
            {"start": 2.0, "end": 3.0, "text": "line only"},
        ]
    )

    svc = BookService()
    dbbook = svc.import_book(b, db.session)

    words = _subtitle_words_html(dbbook)
    assert len(words) == 2
    assert words[0] != ""
    assert words[1] != ""


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------


def _make_youtube_book(app, app_context, english):
    from lute.book.model import Book

    b = Book()
    b.title = "Route YouTube Book"
    b.language_id = english.id
    b.text = "Hello world.\nThis is a test subtitle.\nGoodbye!"
    b.book_type = "youtube"
    b.srt_data = json.dumps(
        [
            {"start": 1.0, "end": 4.2, "text": "Hello world."},
            {"start": 5.0, "end": 8.5, "text": "This is a test subtitle."},
            {"start": 10.0, "end": 13.0, "text": "Goodbye!"},
        ]
    )
    b.source_uri = "https://www.youtube.com/watch?v=J7BXhKSqH6o"
    b.book_tags = ["youtube"]
    svc = BookService()
    dbbook = svc.import_book(b, db.session)
    return dbbook


def test_read_page_passes_youtube_data(app, app_context, english, client):
    "Reading a youtube book renders the player with cues and words."
    dbbook = _make_youtube_book(app, app_context, english)

    resp = client.get(f"/read/{dbbook.id}/page/1")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)

    assert "yt-player-container" in content
    assert "J7BXhKSqH6o" in content
    assert "youtube-player.js" in content
    assert "Hello world." in content
    assert "This is a test subtitle." in content


def test_import_webpage_form_renders_youtube_fields(app, app_context, english, client):
    "The import page has a language selector and a youtube form."
    resp = client.get("/book/import_webpage")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)

    # Language selector (for subtitle parsing).
    assert 'id="webpage-language"' in content
    assert 'id="youtube-language"' in content
    assert "English" in content

    # Youtube type option + fields.
    assert 'value="youtube"' in content
    assert 'id="youtube_url"' in content
    assert 'id="youtube_tag"' in content
    assert 'value="youtube"' in content, "tag defaults to youtube"
    assert 'id="srt_file"' in content


def test_import_youtube_video_route(app, app_context, english, client):
    "POSTing to import_webpage with type=youtube creates a book."
    with patch.object(BookService, "youtube_title", return_value="Route YouTube Book"):
        data = {
            "import_type": "youtube",
            "youtube_url": "https://www.youtube.com/watch?v=J7BXhKSqH6o",
            "youtube_tag": "my-tag",
            "language_id": str(english.id),
            "srt_file": (io.BytesIO(SAMPLE_SRT.encode()), "sub.srt"),
        }
        resp = client.post(
            "/book/import_webpage",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/read/" in resp.headers["Location"]

        repo = BookRepository(db.session)
        book = repo.find_by_title("Route YouTube Book", english.id)
        assert book is not None, "book created with the given title"

        assert book.book_type == "youtube"
        assert book.source_uri == "https://www.youtube.com/watch?v=J7BXhKSqH6o"
        assert len(book.cues) == 3
        assert [t.text for t in book.book_tags] == ["my-tag"]


def test_save_youtube_player_data(app, app_context, english, client):
    "The player position is saved via the ajax route."
    dbbook = _make_youtube_book(app, app_context, english)

    resp = client.post(
        "/read/save_youtube_player_data",
        data=json.dumps({"bookid": dbbook.id, "position": 12.5}),
        content_type="application/json",
    )
    assert resp.status_code == 200

    repo = BookRepository(db.session)
    book = repo.find(dbbook.id)
    assert book.video_current_pos == 12.5


def test_books_datatables_show_youtube_book(app, app_context, english, client):
    "The main book list shows youtube books."
    dbbook = _make_youtube_book(app, app_context, english)

    from lute.book.datatables import get_data_tables_list

    params = {
        "draw": "1",
        "columns": [
            {"data": "0", "name": "BkID", "searchable": False, "orderable": False},
            {"data": "1", "name": "BkTitle", "searchable": True, "orderable": True},
            {"data": "2", "name": "IsCompleted", "searchable": False, "orderable": False},
        ],
        "order": [{"column": "1", "dir": "asc"}],
        "start": "0",
        "length": "10",
        "search": {"value": "", "regex": False},
        "filtLanguage": "0",
    }
    d = get_data_tables_list(params, False, db.session)
    titles = [row["BkTitle"] for row in d["data"]]
    assert "Route YouTube Book" in titles


def test_edit_book_preserves_type(app, app_context, english, client):
    "Editing a youtube book and saving keeps the youtube data."
    dbbook = _make_youtube_book(app, app_context, english)

    resp = client.get(f"/book/edit/{dbbook.id}")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)
    assert "youtube" in content

    # POST the edit form, keeping the same data.
    form_data = {
        "title": "Route YouTube Book",
        "text": "Hello world.\nThis is a test subtitle.\nGoodbye!",
        "split_by": "paragraphs",
        "threshold_page_tokens": "250",
        "source_uri": "https://www.youtube.com/watch?v=J7BXhKSqH6o",
        "book_tags": '[{"value": "youtube"}]',
        "book_type": "youtube",
    }
    resp = client.post(f"/book/edit/{dbbook.id}", data=form_data, follow_redirects=False)
    assert resp.status_code == 302

    repo = BookRepository(db.session)
    book = repo.find(dbbook.id)
    assert book.book_type == "youtube"
    assert len(book.cues) == 3
