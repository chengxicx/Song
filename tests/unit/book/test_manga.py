"""
Tests for the Mokuro manga book feature:
zip/cbz import, default "Manga" tag, page rendering with overlaid
text blocks, DB <-> filesystem path consistency, and dictionary
lookup of overlaid words.
"""

import base64
import io
import json
import os
import zipfile

from lute.db import db
from lute.book.model import Book, Repository as BookModelRepository
from lute.book.service import Service as BookService, BookImportException
from lute.models.repositories import BookRepository

# A 1x1 transparent PNG, used as a stand-in for the comic page image.
# The import flow doesn't decode the image, it just stores the file.
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

# Minimal but realistic mokuro JSON, modelled on hanabira_manga_01.mokuro.
SAMPLE_PAGES = [
    {
        "version": "0.2.1",
        "img_path": "hanabira_manga_01.jpg",
        "img_width": 848,
        "img_height": 1264,
        "blocks": [
            {
                "box": [672, 51, 788, 81],
                "vertical": False,
                "font_size": 25,
                "lines": ["京都・祇園"],
            },
            {
                "box": [759, 483, 806, 680],
                "vertical": True,
                "font_size": 19,
                "lines": ["うちが守らなあかん。"],
            },
        ],
    },
    {
        "version": "0.2.1",
        "img_path": "hanabira_manga_02.jpg",
        "img_width": 848,
        "img_height": 1264,
        "blocks": [
            {
                "box": [729, 102, 765, 182],
                "vertical": True,
                "font_size": 30,
                "lines": ["夜——"],
            },
        ],
    },
]


def make_mokuro(num_pages=2):
    "Build a mokuro dict with the given page count."
    return {
        "version": "0.2.1",
        "generator": "lute-tests",
        "title": "Test Manga",
        "pages": SAMPLE_PAGES[:num_pages],
    }


def make_archive(ext=".cbz", num_pages=2):
    """
    Build an in-memory zip/cbz archive containing a .mokuro file plus
    one image per page, matching the mokuro img_path values.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        mokuro = make_mokuro(num_pages)
        zf.writestr(
            "hanabira_manga_01.mokuro", json.dumps(mokuro, ensure_ascii=False)
        )
        for page in mokuro["pages"]:
            zf.writestr(page["img_path"], PNG_1PX)
    buf.seek(0)
    return buf, mokuro


def _import_manga(client, language_id, ext=".cbz", num_pages=2, **extra):
    "POST the manga import form, return (response, mokuro)."
    stream, mokuro = make_archive(ext, num_pages)
    data = {
        "import_type": "manga",
        "language_id": str(language_id),
        "manga_title": "Test Manga",
        "manga_tag": extra.get("manga_tag", '[{"value":"Manga"}]'),
        "manga_file": (stream, f"hanabira_manga_01{ext}"),
    }
    resp = client.post(
        "/book/import_webpage", data=data, content_type="multipart/form-data",
        follow_redirects=False,
    )
    return resp, mokuro


# ---------------------------------------------------------------------
# extract_manga
# ---------------------------------------------------------------------


def test_extract_manga_zip(app_context):
    "A .zip archive is extracted and the .mokuro JSON parsed."
    from flask import current_app

    stream, mokuro = make_archive(".zip")
    manga_path, parsed = BookService().extract_manga("book.zip", stream)
    assert manga_path.startswith("manga/")
    assert parsed["title"] == "Test Manga"
    assert len(parsed["pages"]) == 2

    target = os.path.join(current_app.static_folder, manga_path)
    assert os.path.isdir(target)
    assert os.path.exists(os.path.join(target, "hanabira_manga_01.jpg"))
    assert os.path.exists(os.path.join(target, "hanabira_manga_02.jpg"))


def test_extract_manga_cbz(app_context):
    "A .cbz archive is extracted like a zip."
    stream, mokuro = make_archive(".cbz")
    manga_path, parsed = BookService().extract_manga("book.cbz", stream)
    assert manga_path.startswith("manga/")
    assert parsed["pages"] == mokuro["pages"]


def test_extract_manga_rejects_bad_extension(app_context):
    "An invalid extension is rejected before any extraction."
    stream, _ = make_archive(".cbz")
    with pytest.raises(BookImportException, match="extension"):
        BookService().extract_manga("book.rar", stream)


def test_extract_manga_rejects_archive_without_mokuro(app_context):
    "An archive with no .mokuro file is rejected."
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("page.jpg", PNG_1PX)
    buf.seek(0)
    with pytest.raises(BookImportException, match="no .mokuro"):
        BookService().extract_manga("book.cbz", buf)


def test_extract_manga_zip_slip_protection(app_context):
    "Archive members that would escape the target dir are rejected."
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil.mokuro", json.dumps(make_mokuro()))
    buf.seek(0)
    with pytest.raises(BookImportException, match="escape"):
        BookService().extract_manga("book.cbz", buf)


def test_real_sample_mokuro_import_and_render(app_context, japanese):
    """
    End-to-end check against the real hanabira_manga_01.mokuro sample:
    extract -> import -> build the reading context for a page.  Covers
    multi-line blocks, vertical text, and real font boxes.  Skipped if
    the sample file has not been downloaded into ~/Documents/lutedev.
    """
    sample = os.path.expanduser("~/Documents/lutedev/hanabira_manga_01.mokuro")
    if not os.path.isfile(sample):
        pytest.skip("hanabira_manga_01.mokuro sample not available")

    with open(sample, "r", encoding="utf-8") as f:
        mokuro = json.load(f)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(os.path.basename(sample), json.dumps(mokuro, ensure_ascii=False))
        for page in mokuro["pages"]:
            zf.writestr(page["img_path"], PNG_1PX)
    buf.seek(0)

    manga_path, parsed = BookService().extract_manga("hanabira.zip", buf)
    assert len(parsed["pages"]) == len(mokuro["pages"])

    # Import through the book model so we can render the context.
    book = Book()
    book.language_id = japanese.id
    book.title = "Real sample"
    book.book_type = "manga"
    book.manga_path = manga_path
    book.manga_data = json.dumps(parsed, ensure_ascii=False)
    dbbook = BookService().import_book(book, db.session)

    from lute.read.service import Service as ReadService

    ctx = ReadService(db.session).manga_page_context(dbbook, 1, track_page_open=False)
    assert ctx["img_url"].startswith("/static/")
    assert ctx["img_width"] == mokuro["pages"][0]["img_width"]
    assert ctx["img_height"] == mokuro["pages"][0]["img_height"]
    blocks = ctx["blocks"]
    assert len(blocks) == len(mokuro["pages"][0]["blocks"])
    assert any(b["vertical"] for b in blocks), "sample has vertical blocks"
    # A multi-line block produced one line of items per source line.
    multiline = mokuro["pages"][0]["blocks"][1]
    assert len(multiline["lines"]) > 1  # sanity on the sample
    got = [b for b in blocks if b["box"] == multiline["box"]][0]
    assert len(got["line_items"]) == len(multiline["lines"])
    # Words are real, clickable TextItems.
    assert any(i.is_word for line in got["line_items"] for i in line)


# ---------------------------------------------------------------------
# Import route
# ---------------------------------------------------------------------


def test_import_page_has_manga_option(app, app_context, client):
    "The import page shows the Mokuro Manga type and its form."
    resp = client.get("/book/import_webpage")
    content = resp.get_data(as_text=True)
    assert 'value="manga"' in content
    assert 'id="manga-form"' in content
    assert 'id="manga_file"' in content
    assert '.zip,.cbz' in content
    assert 'Manga' in content


def test_import_manga_zip_route(app, app_context, japanese, client):
    "POSTing a .zip import creates a manga book with default Manga tag."
    resp, mokuro = _import_manga(client, japanese.id, ".zip")
    assert resp.status_code == 302
    assert "/read/" in resp.headers["Location"]

    repo = BookRepository(db.session)
    book = repo.find_by_title("Test Manga", japanese.id)
    assert book is not None
    assert book.book_type == "manga"
    assert book.manga_path.startswith("manga/")
    assert [t.text for t in book.book_tags] == ["Manga"]
    assert book.manga is not None, "manga JSON is stored"
    assert len(book.manga["pages"]) == len(mokuro["pages"])
    assert book.page_count == len(mokuro["pages"]), "one empty page per mokuro page"


def test_import_manga_cbz_route(app, app_context, japanese, client):
    ".cbz imports exactly like .zip."
    resp, _ = _import_manga(client, japanese.id, ".cbz")
    assert resp.status_code == 302

    repo = BookRepository(db.session)
    book = repo.find_by_title("Test Manga", japanese.id)
    assert book is not None
    assert book.book_type == "manga"
    assert book.manga_path.startswith("manga/")
    assert book.manga_data is not None


def test_import_manga_db_path_matches_filesystem(app, app_context, japanese, client):
    "The DB record and the extracted files point at the same directory."
    from flask import current_app

    resp, _ = _import_manga(client, japanese.id, ".cbz")
    assert resp.status_code == 302

    repo = BookRepository(db.session)
    book = repo.find_by_title("Test Manga", japanese.id)
    target = os.path.join(current_app.static_folder, book.manga_path)
    assert os.path.isdir(target)
    for page in book.manga["pages"]:
        img = os.path.basename(page["img_path"])
        assert os.path.exists(os.path.join(target, img)), f"{img} extracted"


def test_import_manga_custom_tags(app, app_context, japanese, client):
    "Custom tags replace the default Manga tag."
    resp, _ = _import_manga(
        client, japanese.id, ".zip",
        manga_tag='[{"value":"Manga"},{"value":"reading"}]',
    )
    assert resp.status_code == 302
    repo = BookRepository(db.session)
    book = repo.find_by_title("Test Manga", japanese.id)
    assert sorted(t.text for t in book.book_tags) == ["Manga", "reading"]


def test_import_manga_rejects_bad_extension(app, app_context, client):
    "Uploading a non-zip/cbz file is rejected."
    data = {
        "import_type": "manga",
        "language_id": "1",
        "manga_file": (io.BytesIO(b"nope"), "book.rar"),
    }
    resp = client.post(
        "/book/import_webpage", data=data, content_type="multipart/form-data",
        follow_redirects=True,
    )
    content = resp.get_data(as_text=True)
    assert ".zip or .cbz" in content
    assert "Mokuro" in content


# ---------------------------------------------------------------------
# Reading pane
# ---------------------------------------------------------------------


def _import_and_get_book(app, app_context, japanese, client):
    "Import a manga archive and return the DBBook."
    _import_manga(client, japanese.id, ".cbz")
    repo = BookRepository(db.session)
    return repo.find_by_title("Test Manga", japanese.id)


def test_read_page_renders_manga_frame(app, app_context, japanese, client):
    "The reading screen renders the manga container (no TTS player)."
    book = _import_and_get_book(app, app_context, japanese, client)
    resp = client.get(f"/read/{book.id}/page/1")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)
    assert "manga-text-container" in content
    assert "tts_player" not in content, "no TTS player for manga"
    assert "youtube-player.js" not in content


def test_start_reading_renders_manga_page(app, app_context, japanese, client):
    "The AJAX page content overlays text blocks on the page image."
    book = _import_and_get_book(app, app_context, japanese, client)
    resp = client.get(f"/read/start_reading/{book.id}/1")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)

    assert "manga-page" in content
    # Image points into the extracted manga directory.
    assert f'src="/static/{book.manga_path}/hanabira_manga_01.jpg"' in content

    # Blocks are absolutely positioned % of the page image.
    assert "manga-text-block" in content
    assert "style=" in content and "left:" in content and "top:" in content
    # font-size uses container-query units so the text scales.
    assert "cqw" in content
    # Vertical blocks are marked.
    assert "manga-vertical" in content

    # Words were tokenized into clickable spans (reuses textitem.html).
    assert 'class="textitem click word' in content
    assert 'data-status-class="status0"' in content


def test_refresh_page_renders_manga_page(app, app_context, japanese, client):
    "refresh_page (used after term edits) renders the same manga content."
    book = _import_and_get_book(app, app_context, japanese, client)
    resp = client.get(f"/read/refresh_page/{book.id}/1")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)
    assert "manga-page" in content
    assert "manga-text-block" in content


def test_manga_words_get_data_wid_after_page_load(app, app_context, japanese, client):
    "Terms created during tokenization are saved, so words carry data-wid."
    book = _import_and_get_book(app, app_context, japanese, client)
    # First load tokenizes and saves the status-0 terms.
    client.get(f"/read/start_reading/{book.id}/1")
    # Second load finds them in the DB and emits data-wid attributes.
    resp = client.get(f"/read/start_reading/{book.id}/1")
    content = resp.get_data(as_text=True)
    assert "data-wid=" in content, "words link to saved terms for editing"
    assert 'class="textitem click word' in content


def test_page_done_works_for_manga(app, app_context, japanese, client):
    "Marking a manga page read doesn't crash (WordsRead with 0 words)."
    book = _import_and_get_book(app, app_context, japanese, client)
    resp = client.post(
        "/read/page_done",
        data=json.dumps({"bookid": book.id, "pagenum": 1, "restknown": False}),
        content_type="application/json",
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------
# Dictionary lookup from overlaid words
# ---------------------------------------------------------------------


def test_termform_creates_lookup_for_manga_word(app, app_context, japanese, client):
    "Clicking a manga word opens the Lute term form (dictionary lookup)."
    book = _import_and_get_book(app, app_context, japanese, client)
    resp = client.get("/read/termform/{}/{}".format(japanese.id, "京都"))
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)
    assert "京都" in content


def test_termpopup_returns_term_data(app, app_context, japanese, client):
    "Hovering a saved manga word returns popup data for the term."
    from lute.term.model import Repository as TermRepository

    book = _import_and_get_book(app, app_context, japanese, client)
    client.get(f"/read/start_reading/{book.id}/1")

    trepo = TermRepository(db.session)
    term = trepo.find_or_new(japanese.id, "京都")
    if term.id is None:
        trepo.add(term)
        trepo.commit()

    resp = client.get(f"/read/termpopup/{term.id}")
    assert resp.status_code == 200


def test_manga_edit_preserves_manga_data(app, app_context, japanese, client):
    "Re-saving a manga book keeps its manga path and JSON."
    book = _import_and_get_book(app, app_context, japanese, client)

    # Reload into a BO and re-save it (the same path the edit route uses);
    # the manga fields are not exposed via the edit form, so they must
    # survive intact.
    repo = BookRepository(db.session)
    updated = BookModelRepository(db.session)._build_business_book(book)
    updated.title = "Test Manga [edited]"
    BookService().import_book(updated, db.session)

    repo = BookRepository(db.session)
    reloaded = repo.find(book.id)
    assert reloaded.book_type == "manga"
    assert reloaded.manga_path == book.manga_path
    assert reloaded.manga is not None
    assert reloaded.title == "Test Manga [edited]"


import pytest  # noqa: E402  (used by the extract_manga rejection tests)