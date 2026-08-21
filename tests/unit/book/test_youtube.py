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


def test_parse_vtt_with_cue_settings_and_metadata():
    "Real YouTube VTT (align settings + X-TIMESTAMP-MAP) parses fine."
    yt_vtt = """WEBVTT
Kind: captions
Language: en

X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0

1
00:00:01.000 --> 00:00:04.200 align:start position:0%
Hello world.

2
00:00:05.000 --> 00:00:08.500 align:start position:0%
This is a test subtitle.
"""
    text, cues_json = parse_subtitle_file(
        "sub.vtt", io.BytesIO(yt_vtt.encode())
    )
    assert text == "Hello world.\nThis is a test subtitle."
    cues = json.loads(cues_json)
    assert len(cues) == 2
    assert cues[0]["start"] == 1.0
    assert cues[0]["end"] == 4.2
    assert cues[1]["text"] == "This is a test subtitle."


def test_cues_to_srt_text_round_trip():
    "cues -> SRT text -> parse yields the same cues."
    from lute.book.service import cues_to_srt_text, parse_subtitle_content

    cues = [
        {"start": 1.0, "end": 4.2, "text": "Hello world."},
        {"start": 5.5, "end": 65.75, "text": "This is a test subtitle."},
    ]
    srt_text = cues_to_srt_text(cues)
    assert "00:00:01,000 --> 00:00:04,200" in srt_text
    assert "00:01:05,750" in srt_text

    text, cues_json = parse_subtitle_content(srt_text, ext=".srt")
    assert text == "Hello world.\nThis is a test subtitle."
    reparsed = json.loads(cues_json)
    assert reparsed == cues


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


def test_subtitle_words_html_rebuilds_when_term_status_changes(
    app, app_context, english
):
    "A cached subtitle render must not keep serving a stale status."
    import re

    from lute.book.model import Book
    from lute.models.term import Term
    from lute.read.routes import _subtitle_words_html

    b = Book()
    b.title = "YT_STALE"
    b.language_id = english.id
    b.text = "Hello world."
    b.book_type = "youtube"
    b.srt_data = json.dumps([{"start": 1.0, "end": 4.0, "text": "Hello world."}])

    svc = BookService()
    dbbook = svc.import_book(b, db.session)

    # Build the cache while the term is unknown (status 0).
    first = _subtitle_words_html(dbbook)
    assert "status0" in first[0]

    # Simulate a status change applied directly to the shared DB by
    # another worker (i.e. this worker's in-memory cache was NOT
    # invalidated by the status-update POST).
    wid = int(re.search(r'data-wid="(\d+)"', first[0]).group(1))
    term = db.session.get(Term, wid)
    assert term is not None
    term.status = 2
    db.session.add(term)
    db.session.commit()

    # The stale cache entry must be detected and rebuilt with the new
    # status instead of serving the old status0 HTML.
    second = _subtitle_words_html(dbbook)
    hello_span = re.search(r"<span[^>]*>Hello</span>", second[0]).group(0)
    assert 'data-status-class="status2"' in hello_span
    assert 'data-status-class="status0"' not in hello_span


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


def test_edit_page_propagates_text_to_cues(app, app_context, english, client):
    "Editing a page of a media book updates the subtitle cues used by the player."
    dbbook = _make_youtube_book(app, app_context, english)
    assert len(dbbook.cues) == 3
    assert dbbook.cues[1]["text"] == "This is a test subtitle."

    new_text = "Hello world.\nThis line was edited.\nGoodbye!"
    resp = client.post(
        f"/read/editpage/{dbbook.id}/1",
        data={"text": new_text},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    repo = BookRepository(db.session)
    book = repo.find(dbbook.id)
    assert len(book.cues) == 3
    assert book.cues[0]["text"] == "Hello world."
    assert book.cues[1]["text"] == "This line was edited."
    assert book.cues[2]["text"] == "Goodbye!"
    # Timing data is untouched.
    assert book.cues[1]["start"] == 5.0
    assert book.cues[2]["end"] == 13.0


def test_sync_media_page_text_crlf_line_endings(app, app_context, english):
    "Page text with CRLF line endings still syncs to the cues (mp3 books)."
    from lute.read.routes import _sync_media_page_text_to_cues

    dbbook = _make_youtube_book(app, app_context, english)
    # Stored page text uses CRLF line endings; cue lines split on \n only.
    original_text = "Hello world.\r\nThis is a test subtitle.\r\nGoodbye!"
    new_text = "Hello world.\r\nThis line was edited.\r\nGoodbye!"
    assert _sync_media_page_text_to_cues(dbbook, original_text, new_text) is True

    book = BookRepository(db.session).find(dbbook.id)
    assert book.cues[0]["text"] == "Hello world."
    assert book.cues[1]["text"] == "This line was edited."
    assert book.cues[2]["text"] == "Goodbye!"


def test_sync_media_page_text_drifted_line(app, app_context, english):
    "A page line that already drifted from the cue is still located via best alignment."
    from lute.read.routes import _sync_media_page_text_to_cues

    dbbook = _make_youtube_book(app, app_context, english)
    # Cue 1 is "This is a test subtitle.", but the page already carries an
    # edited (drifted) version of that line.  Best-position alignment must
    # still find the page block and apply an edit made on another line.
    original_text = "Hello world.\nThis is a drifted line.\nGoodbye!"
    new_text = "Hello world.\nThis is a drifted line.\nGoodbye again!"
    assert _sync_media_page_text_to_cues(dbbook, original_text, new_text) is True

    book = BookRepository(db.session).find(dbbook.id)
    assert book.cues[0]["text"] == "Hello world."
    # The drifted line is synced to match the page, and the new edit landed.
    assert book.cues[1]["text"] == "This is a drifted line."
    assert book.cues[2]["text"] == "Goodbye again!"


def test_sync_media_page_text_no_match_does_nothing(app, app_context, english):
    "When the page shares no lines with the cues, the cues are left alone."
    from lute.read.routes import _sync_media_page_text_to_cues

    dbbook = _make_youtube_book(app, app_context, english)
    before = json.loads(dbbook.srt_data)
    original_text = "Completely\nUnrelated\nLines"
    new_text = "Completely\nUnrelated\nChange"
    assert _sync_media_page_text_to_cues(dbbook, original_text, new_text) is False
    assert json.loads(dbbook.srt_data) == before


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
    # The text field holds the SRT original with timestamps (the ">"
    # is HTML-escaped in the rendered textarea).
    assert "00:00:01,000 --&gt; 00:00:04,200" in content

    # POST the edit form, keeping the same (SRT) data.
    form_data = {
        "title": "Route YouTube Book",
        "text": (
            "1\n"
            "00:00:01,000 --> 00:00:04,200\n"
            "Hello world.\n\n"
            "2\n"
            "00:00:05,000 --> 00:00:08,500\n"
            "This is a test subtitle.\n\n"
            "3\n"
            "00:00:10,000 --> 00:00:13,000\n"
            "Goodbye!"
        ),
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


def test_edit_book_updates_cues_from_srt_text(app, app_context, english, client):
    "Editing the SRT text in the edit page updates the cues on save."
    dbbook = _make_youtube_book(app, app_context, english)

    form_data = {
        "title": "Route YouTube Book",
        "text": (
            "1\n"
            "00:00:01,000 --> 00:00:04,200\n"
            "Hello world.\n\n"
            "2\n"
            "00:00:05,000 --> 00:00:08,500\n"
            "This text was edited.\n\n"
            "3\n"
            "00:00:10,000 --> 00:00:13,000\n"
            "Brand new line."
        ),
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
    assert len(book.cues) == 3
    assert book.cues[1]["text"] == "This text was edited."
    assert book.cues[2]["text"] == "Brand new line."
    assert book.cues[2]["start"] == 10.0
