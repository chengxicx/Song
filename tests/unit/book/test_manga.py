"""
Tests for the Mokuro manga book feature:
zip/cbz import, default "Manga" tag, page rendering with overlaid
text blocks, DB <-> filesystem path consistency, and dictionary
lookup of overlaid words.
"""

import base64
import html
import io
import json
import os
import re
import shutil
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
    # A third page, so re-importing a bigger archive can be tested.
    {
        "version": "0.2.1",
        "img_path": "hanabira_manga_03.jpg",
        "img_width": 848,
        "img_height": 1264,
        "blocks": [
            {
                "box": [120, 260, 300, 330],
                "vertical": False,
                "font_size": 22,
                "lines": ["おはよう"],
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
        "manga_tag": extra.get("manga_tag", ""),
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


def test_extract_manga_volume_subdir_layout(app_context):
    """
    Mokuro's default layout puts images inside a volume/ subdirectory
    while the .mokuro JSON records only the basename (e.g. "001.jpg")
    in img_path.  The extractor must rewrite img_path to include the
    volume subdirectory, so the reading screen can find the files.
    """
    from flask import current_app

    volume = "-Zombie-Sagashitemasu-01"
    mokuro = {
        "version": "0.2.0-beta.6",
        "title": "#Zombie Sagashitemasu",
        "volume": volume,
        "pages": [
            {
                "version": "0.1.6",
                "img_path": "001.jpg",
                "img_width": 1080,
                "img_height": 1530,
                "blocks": [{"box": [53, 461, 552, 568], "vertical": False,
                            "font_size": 32, "lines": ["テスト"]}],
            },
            {
                "version": "0.1.6",
                "img_path": "002.jpg",
                "img_width": 1080,
                "img_height": 1530,
                "blocks": [],
            },
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # .mokuro at zip root
        zf.writestr(f"{volume}.mokuro", json.dumps(mokuro, ensure_ascii=False))
        # images inside volume/ subdirectory (mokuro default)
        for p in mokuro["pages"]:
            zf.writestr(f"{volume}/{p['img_path']}", PNG_1PX)
    buf.seek(0)

    manga_path, parsed = BookService().extract_manga("book.zip", buf)
    target = os.path.join(current_app.static_folder, manga_path)

    # Files extracted where expected.
    assert os.path.isdir(os.path.join(target, volume))
    assert os.path.exists(os.path.join(target, volume, "001.jpg"))
    assert os.path.exists(os.path.join(target, volume, "002.jpg"))

    # img_path rewritten to include the volume subdirectory.
    assert parsed["pages"][0]["img_path"] == f"{volume}/001.jpg"
    assert parsed["pages"][1]["img_path"] == f"{volume}/002.jpg"


def test_extract_manga_image_extension_changed(app_context):
    """
    A jpg -> webp conversion renames the image files but leaves the
    .mokuro img_path values as "001.jpg".  The extractor must still
    resolve them and rewrite img_path to the file that actually
    exists, otherwise the reading screen requests a .jpg that 404s and
    the page renders blank.
    """
    from flask import current_app

    volume = "textbook_vol"
    page = {
        "version": "0.2.1",
        "img_width": 1365,
        "img_height": 2048,
        "blocks": [],
    }
    mokuro = {
        "version": "0.2.1",
        "title": "Converted",
        "volume": volume,
        "pages": [
            dict(page, img_path="001.jpg"),
            dict(page, img_path="002.jpg"),
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{volume}.mokuro", json.dumps(mokuro, ensure_ascii=False))
        for n in ("001", "002"):
            zf.writestr(f"{volume}/{n}.webp", PNG_1PX)
    buf.seek(0)

    manga_path, parsed = BookService().extract_manga("book.cbz", buf)
    target = os.path.join(current_app.static_folder, manga_path)

    assert os.path.exists(os.path.join(target, volume, "001.webp"))
    assert parsed["pages"][0]["img_path"] == f"{volume}/001.webp"
    assert parsed["pages"][1]["img_path"] == f"{volume}/002.webp"


def test_read_page_resolves_image_after_extension_change(app_context, japanese):
    """
    Books imported before the fix keep the stale "001.jpg" img_path in
    the DB, so the reading screen must resolve it against the real
    files on disk (001.webp) at render time -- no re-import required.
    """
    from flask import current_app

    from lute.book.model import Book
    from lute.read.service import Service as ReadService

    manga_path = "manga/test-ext-change"
    target = os.path.join(current_app.static_folder, manga_path)
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "001.webp"), "wb") as f:
        f.write(PNG_1PX)

    pages = [
        {
            "version": "0.2.1",
            "img_path": "001.jpg",  # stale: the extracted file is 001.webp
            "img_width": 10,
            "img_height": 10,
            "blocks": [
                {
                    "box": [1, 1, 5, 5],
                    "vertical": False,
                    "font_size": 5,
                    "lines": ["テスト"],
                }
            ],
        }
    ]

    book = Book()
    book.language_id = japanese.id
    book.title = "Extension changed"
    book.book_type = "manga"
    book.manga_path = manga_path
    book.manga_data = json.dumps(
        {"version": "0.2.1", "pages": pages}, ensure_ascii=False
    )
    dbbook = BookService().import_book(book, db.session)

    try:
        ctx = ReadService(db.session).manga_page_context(
            dbbook, 1, track_page_open=False
        )
        assert ctx["img_url"] == f"/static/{manga_path}/001.webp"
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_extract_manga_pages_without_img_path(app_context):
    """
    A .mokuro assembled from the raw _ocr output has no img_path on its
    pages -- the official mokuro CLI is what adds that field when it
    builds the volume file.  Such an archive is still perfectly
    readable, because mokuro pairs pages with images by natural-sorted
    position.  The extractor must write those paths down, otherwise the
    imported book has pages with no image to request.
    """
    from flask import current_app

    volume = "textbook_vol"
    page = {
        "version": "0.2.5",
        "img_width": 1365,
        "img_height": 2048,
        "blocks": [],
    }
    mokuro = {
        "version": "0.2.5",
        "title": "mokuro_project",
        "volume": volume,
        "pages": [dict(page), dict(page), dict(page)],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{volume}.mokuro", json.dumps(mokuro, ensure_ascii=False))
        for n in ("001", "002", "003"):
            zf.writestr(f"{volume}/{n}.webp", PNG_1PX)
    buf.seek(0)

    manga_path, parsed = BookService().extract_manga("book.cbz", buf)
    target = os.path.join(current_app.static_folder, manga_path)

    assert [p["img_path"] for p in parsed["pages"]] == [
        f"{volume}/001.webp",
        f"{volume}/002.webp",
        f"{volume}/003.webp",
    ]
    for page in parsed["pages"]:
        assert os.path.isfile(os.path.join(target, page["img_path"]))


def test_read_page_resolves_image_without_img_path(app_context, japanese):
    """
    Books imported before the fix keep pages with no img_path in the
    DB, so the reading screen must fall back to positional matching at
    render time -- no re-import required.  Before the fix the URL was
    the bare manga directory, which 403s and renders a blank page.
    """
    from flask import current_app

    from lute.book.model import Book
    from lute.read.service import Service as ReadService

    manga_path = "manga/test-no-img-path"
    target = os.path.join(current_app.static_folder, manga_path)
    volume_dir = os.path.join(target, "textbook_vol")
    os.makedirs(volume_dir, exist_ok=True)
    for n in ("001", "002"):
        with open(os.path.join(volume_dir, f"{n}.webp"), "wb") as f:
            f.write(PNG_1PX)

    pages = [
        {
            "version": "0.2.5",
            "img_width": 10,
            "img_height": 10,
            "blocks": [
                {
                    "box": [1, 1, 5, 5],
                    "vertical": False,
                    "font_size": 5,
                    "lines": ["テ"],
                }
            ],
        },
        {
            "version": "0.2.5",
            "img_width": 10,
            "img_height": 10,
            "blocks": [],
        },
    ]

    book = Book()
    book.language_id = japanese.id
    book.title = "No img_path"
    book.book_type = "manga"
    book.manga_path = manga_path
    book.manga_data = json.dumps(
        {"version": "0.2.5", "volume": "textbook_vol", "pages": pages},
        ensure_ascii=False,
    )
    dbbook = BookService().import_book(book, db.session)

    try:
        service = ReadService(db.session)
        ctx = service.manga_page_context(dbbook, 1, track_page_open=False)
        assert ctx["img_url"] == f"/static/{manga_path}/textbook_vol/001.webp"

        ctx = service.manga_page_context(dbbook, 2, track_page_open=False)
        assert ctx["img_url"] == f"/static/{manga_path}/textbook_vol/002.webp"
    finally:
        shutil.rmtree(target, ignore_errors=True)


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
    "POSTing a .zip import creates a manga book with no tag unless given."
    resp, mokuro = _import_manga(client, japanese.id, ".zip")
    assert resp.status_code == 302
    assert "/read/" in resp.headers["Location"]

    repo = BookRepository(db.session)
    book = repo.find_by_title("Test Manga", japanese.id)
    assert book is not None
    assert book.book_type == "manga"
    assert book.manga_path.startswith("manga/")
    assert [t.text for t in book.book_tags] == []
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
    "Tags entered in the form are applied as given."
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


def test_manga_context_filters_paragraph_marker(app, app_context, japanese):
    """
    mokuro lines may join several physical text rows with newlines or
    the "¶" paragraph marker.  Each row must render on its own line in
    the box; the marker itself must not appear as visible text.
    """
    from lute.book.model import Book
    from lute.read.service import Service as ReadService

    pages = [{
        "version": "0.2.1",
        "img_path": "page.jpg",
        "img_width": 848,
        "img_height": 1264,
        "blocks": [
            {
                "box": [10, 10, 100, 100],
                "vertical": False,
                "font_size": 25,
                "lines": ["一行目\n二行目", "まとめ¶あとがき"],
            },
        ],
    }]

    book = Book()
    book.language_id = japanese.id
    book.title = "Pilcrow filter"
    book.book_type = "manga"
    book.manga_path = "manga/test-nonexistent"
    book.manga_data = json.dumps({"version": "0.2.1", "pages": pages}, ensure_ascii=False)
    dbbook = BookService().import_book(book, db.session)

    ctx = ReadService(db.session).manga_page_context(dbbook, 1, track_page_open=False)
    line_items = ctx["blocks"][0]["line_items"]

    all_items = [it for line in line_items for it in line]
    assert all_items, "lines produced items"
    assert not any(it.text == "¶" for it in all_items), "¶ marker is filtered out"
    lines = ["".join(it.text for it in line) for line in line_items]
    assert lines == ["一行目", "二行目", "まとめ", "あとがき"], (
        "each physical row renders on its own line"
    )


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
    "A business-object re-save keeps the manga path and JSON."
    book = _import_and_get_book(app, app_context, japanese, client)

    # Reload into a BO and re-save it (e.g. scripts and data cleanup
    # round-trip books this way); the manga fields are not carried by
    # the BO's text, so they must survive intact.
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


# ---------------------------------------------------------------------
# Edit page: manga books get their own page, and can be re-imported
# ---------------------------------------------------------------------


def _post_manga_edit(client, book_id, **extra):
    """
    POST the manga edit form.

    `archive` is a (stream, mokuro) pair from make_archive; when omitted
    no file is uploaded, i.e. only the title/tags are saved.
    """
    data = {
        "title": extra.get("title", "Test Manga"),
        "book_tags": extra.get("book_tags", ""),
    }
    archive = extra.get("archive")
    if archive is not None:
        filename = extra.get("archive_name", "hanabira_manga_01.cbz")
        data["manga_file"] = (archive[0], filename)
    return client.post(
        f"/book/edit/{book_id}",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=False,
    )


def test_manga_edit_page_is_manga_specific(app, app_context, japanese, client):
    """
    /book/edit/<manga id> shows the manga page: no text editor and no
    way to retype the book, but the archive can be replaced.
    """
    book = _import_and_get_book(app, app_context, japanese, client)

    resp = client.get(f"/book/edit/{book.id}")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)

    assert "Edit manga book" in content
    assert 'name="manga_file"' in content
    assert 'accept=".zip,.cbz"' in content
    assert "hanabira_manga_01.cbz" in content, "the current archive is shown"
    assert "2 pages" in content

    # None of the generic (meaningless) text-editing controls.
    assert 'name="text"' not in content
    assert 'id="book_type"' not in content
    assert 'name="audiofile"' not in content
    assert "cueEditorPanel" not in content

    # Save / Cancel only: there is no "Read" shortcut here, because it
    # would navigate away and silently drop unsaved title / tag edits.
    assert 'class="btn btn-primary">Save</button>' in content
    assert "window.location = '/read/" not in content


def test_text_book_uses_the_generic_edit_page(app, app_context, japanese, client):
    "A plain text book is unaffected: /book/edit still shows the text form."
    b = Book()
    b.language_id = japanese.id
    b.title = "Plain text book"
    b.text = "これは テスト です。"
    dbbook = BookService().import_book(b, db.session)

    resp = client.get(f"/book/edit/{dbbook.id}")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)
    assert 'id="book_type"' in content
    assert 'name="text"' in content
    assert 'name="manga_file"' not in content


def test_manga_edit_saves_title_and_tags(app, app_context, japanese, client):
    "Without an archive, only the title and tags change."
    book = _import_and_get_book(app, app_context, japanese, client)
    old_path = book.manga_path

    resp = _post_manga_edit(
        client,
        book.id,
        title="Renamed Manga",
        book_tags='[{"value":"Manga"},{"value":"jp"}]',
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"

    reloaded = BookRepository(db.session).find(book.id)
    assert reloaded.title == "Renamed Manga"
    assert sorted(t.text for t in reloaded.book_tags) == ["Manga", "jp"]
    assert reloaded.book_type == "manga"
    assert reloaded.manga_path == old_path, "no archive uploaded, images unchanged"
    assert reloaded.page_count == 2


def test_manga_edit_page_prefills_the_tags_it_will_save(
    app, app_context, japanese, client
):
    """
    A tagged manga book renders its tags into the form, so re-saving the
    page without touching the tag field keeps them (the field replaces
    the book's tags, so an empty echo would silently drop them).
    """
    book = _import_and_get_book(app, app_context, japanese, client)
    _post_manga_edit(client, book.id, book_tags='[{"value":"Manga"},{"value":"jp"}]')

    resp = client.get(f"/book/edit/{book.id}")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)
    assert "Manga" in content and "jp" in content, "tags are shown in the form"
    rendered = re.search(r'id="book_tags"[^>]*value="([^"]*)"', content).group(1)

    # Post the untouched field straight back: tags must survive.
    _post_manga_edit(client, book.id, book_tags=html.unescape(rendered))
    reloaded = BookRepository(db.session).find(book.id)
    assert sorted(t.text for t in reloaded.book_tags) == ["Manga", "jp"]


def test_manga_edit_reimports_archive_over_the_book(
    app, app_context, japanese, client
):
    "An uploaded archive replaces the book's pages, images and mokuro data."
    from flask import current_app

    book = _import_and_get_book(app, app_context, japanese, client)  # 2 pages
    old_path = book.manga_path

    resp = _post_manga_edit(
        client,
        book.id,
        archive=make_archive(".cbz", 3),
        archive_name="new_manga.cbz",
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == f"/read/{book.id}/page/1"

    reloaded = BookRepository(db.session).find(book.id)
    assert reloaded.id == book.id, "the same book row is reused"
    assert reloaded.book_type == "manga"
    assert reloaded.source_uri == "new_manga.cbz"
    assert reloaded.manga_path != old_path
    assert reloaded.page_count == 3
    assert len(reloaded.manga["pages"]) == 3

    new_dir = os.path.join(current_app.static_folder, reloaded.manga_path)
    assert os.path.isdir(new_dir)
    assert os.path.exists(os.path.join(new_dir, "hanabira_manga_03.jpg"))
    # The previous folder is deliberately kept on disk, so restoring an
    # older DB backup still finds the images it refers to.
    assert os.path.isdir(os.path.join(current_app.static_folder, old_path))

    # The reading screen serves the new images.
    resp = client.get(f"/read/start_reading/{book.id}/1")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)
    assert f'src="/static/{reloaded.manga_path}/hanabira_manga_01.jpg"' in content


def test_manga_edit_reimport_shrinks_to_the_new_page_count(
    app, app_context, japanese, client
):
    "Re-importing a smaller archive drops the extra pages."
    book = _import_and_get_book(app, app_context, japanese, client)  # 2 pages

    resp = _post_manga_edit(client, book.id, archive=make_archive(".cbz", 1))
    assert resp.status_code == 302

    reloaded = BookRepository(db.session).find(book.id)
    assert reloaded.page_count == 1
    assert len(reloaded.manga["pages"]) == 1


def test_manga_edit_rejects_bad_archive(app, app_context, japanese, client):
    "A non-zip/cbz upload is rejected and nothing is saved."
    book = _import_and_get_book(app, app_context, japanese, client)

    resp = _post_manga_edit(
        client,
        book.id,
        title="Should not be saved",
        archive=(io.BytesIO(b"nope"), None),
        archive_name="book.rar",
    )
    assert resp.status_code == 200
    assert ".zip or .cbz" in resp.get_data(as_text=True)

    reloaded = BookRepository(db.session).find(book.id)
    assert reloaded.title == "Test Manga"
    assert reloaded.manga_path == book.manga_path
    assert reloaded.page_count == 2


def test_manga_edit_requires_a_title(app, app_context, japanese, client):
    "The title can't be blanked out."
    book = _import_and_get_book(app, app_context, japanese, client)

    resp = _post_manga_edit(client, book.id, title="")
    assert resp.status_code == 200
    assert "This field is required" in resp.get_data(as_text=True)
    assert BookRepository(db.session).find(book.id).title == "Test Manga"


import pytest  # noqa: E402  (used by the extract_manga rejection tests)