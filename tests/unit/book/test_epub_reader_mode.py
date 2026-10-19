"""
Reader-mode EPUB import and render tests.

Reader mode keeps each chapter's original markup instead of flattening
it to plain text, so it has two properties worth pinning down:

- the stored book is one continuously scrolling page per chapter
  (book_type "epub"), with the chapter HTML and a scoped stylesheet
  extracted under static/epub/<uuid>/;
- the token stream the page ships and the chapter's rendered text
  describe the same character sequence, because the front end aligns
  them by plain stream matching (static/js/lute-epub-reader.js).  If
  those two ever drift, the chapter silently loses its word spans.
"""

import io
import json
import os

from bs4 import BeautifulSoup

from lute.book.epub_css import scope_css
from lute.db import db
from lute.models.book import Book as DBBook
from tests.utils import make_epub

CHAPTER = """<html><head>
<link rel="stylesheet" href="styles/book.css"/>
<style>p { color: red; }</style>
<script>alert("x")</script>
</head><body class="novel">
<h2>Chapter</h2>
<p onclick="alert(1)">Hello <em>world</em>, again.</p>
<p>Second paragraph.</p>
<p><ruby>漢字<rt>かんじ</rt></ruby>です。</p>
<p><img src="images/pic.png" alt=""/></p>
</body></html>"""

BOOK_CSS = """@page { margin: 1cm; }
html body { margin: 0; }
body.novel p { text-indent: 1em; }
p { background: url(bg.png); }
@media (min-width: 600px) { body.dark p { color: #fff; background: #222; } }
"""


def _epub_bytes(title="Reader Book"):
    return make_epub(
        {
            "OEBPS/ch1.xhtml": CHAPTER,
            "OEBPS/ch2.xhtml": "<html><body><p>Second chapter.</p></body></html>",
            "OEBPS/styles/book.css": BOOK_CSS,
            "OEBPS/images/pic.png": "not-a-real-png",
        },
        [("ch1", "ch1.xhtml"), ("ch2", "ch2.xhtml")],
        title=title,
        nav_href="nav.xhtml",
        nav_entries=[("ch1.xhtml", "First"), ("ch2.xhtml", "Second")],
    )


def _post(client, epub, **fields):
    data = {"epub_file": (io.BytesIO(epub), "reader.epub")}
    data.update(fields)
    return client.post("/book/import/epub", data=data, content_type="multipart/form-data")


def _import_reader_book(app, client, english, tmp_path):
    "Import in reader mode with the static folder isolated to tmp_path."
    app.static_folder = str(tmp_path)
    resp = _post(
        client,
        _epub_bytes(),
        language_id=str(english.id),
        epub_title="Reader Book",
        chapters=json.dumps([0]),
        epub_reader_mode="1",
    )
    assert resp.get_json()["success"] is True, resp.get_json()
    book = db.session.query(DBBook).filter(DBBook.title.like("Reader Book%")).one()
    return book


def test_reader_mode_import_is_one_page_with_chapter_markup(
    app, app_context, english, client, tmp_path
):
    "The chapter keeps its markup, one page, and its assets are extracted."
    book = _import_reader_book(app, client, english, tmp_path)

    assert book.book_type == "epub"
    assert len(book.texts) == 1, "one page per chapter"
    page_text = book.texts[0].text
    assert "Hello world, again." in page_text
    assert "かんじ" not in page_text, "rt readings are not text"

    html_path = os.path.join(str(tmp_path), book.epub_path)
    assert os.path.isfile(html_path)
    html = open(html_path, encoding="utf-8").read()
    assert 'class="epub-body novel"' in html
    assert "<script" not in html, "active content is stripped"
    assert "onclick" not in html
    assert "<rt" not in html, "ruby readings are stripped for token alignment"
    assert "漢字" in html
    # Relative asset URLs point at the extracted copy.
    assert '/static/epub/' in html and "/OEBPS/images/pic.png" in html

    css_path = os.path.join(str(tmp_path), "epub", book.epub_path.split("/")[1], "lute.css")
    assert os.path.isfile(css_path)
    css = open(css_path, encoding="utf-8").read()
    assert ".epub-body" in css
    assert "@page" not in css, "print-only rules are dropped"


def test_reader_mode_reading_page_renders_chapter_container(
    app, app_context, english, client, tmp_path
):
    "The reading screen uses the epub container and loads the reader script."
    book = _import_reader_book(app, client, english, tmp_path)
    body = client.get(f"/read/{book.id}/page/1").get_data(as_text=True)
    assert "epub-text-container" in body
    assert "lute-epub-reader.js" in body
    assert "pdf-reader.js" not in body


def test_token_stream_matches_the_chapter_dom(
    app, app_context, english, client, tmp_path
):
    """
    The invariant the front end depends on: concatenated token text and
    the chapter's rendered text are the same character sequence, once
    ASCII whitespace is ignored.
    """
    book = _import_reader_book(app, client, english, tmp_path)
    fragment = client.get(f"/read/start_reading/{book.id}/1").get_data(as_text=True)

    soup = BeautifulSoup(fragment, "html.parser")
    data = soup.find("script", {"id": "lute-epub-tokens"})
    assert data is not None, "token stream is embedded in the fragment"
    tokens = json.loads(data.string or "[]")
    assert tokens, "chapter tokens were produced"

    dom_text = soup.find(id="epub-chapter").get_text("")
    html_path = os.path.join(str(tmp_path), book.epub_path)
    file_text = BeautifulSoup(
        open(html_path, encoding="utf-8").read(), "html.parser"
    ).get_text("")

    def bare(s):
        return "".join(ch for ch in s if ch not in " \t\n\v\f\r")

    assert bare("".join(t[0] for t in tokens)) == bare(file_text)
    assert bare(dom_text) == bare(file_text), "fragment renders the stored chapter"

    words = [t for t in tokens if t[1]]
    assert words, "words are tokenized"
    assert all(len(t) == 5 for t in tokens), "order/sentence/paragraph are present"
    assert any(t[2] == 0 for t in words), "new words have status 0"


def test_reader_mode_off_keeps_plain_text_import(
    app, app_context, english, client, tmp_path
):
    "Without the switch, the import is unchanged: plain text, no chapter files."
    app.static_folder = str(tmp_path)
    resp = _post(
        client,
        _epub_bytes(),
        language_id=str(english.id),
        epub_title="Plain Book",
        chapters=json.dumps([0]),
    )
    assert resp.get_json()["success"] is True
    book = db.session.query(DBBook).filter(DBBook.title.like("Plain Book%")).one()
    assert book.book_type == ""
    assert book.epub_path is None
    assert not os.path.exists(os.path.join(str(tmp_path), "epub"))


def test_scope_css_rewrites_document_selectors():
    "Document-level selectors become the scope element; @page is dropped."
    css = scope_css(BOOK_CSS, "OEBPS/styles", "/static/epub/x")
    assert ".epub-body { margin: 0" in css
    assert ".epub-body.novel p { text-indent: 1em; }" in css
    assert ".epub-body.dark p { color: #fff; background: #222; }" in css
    assert "@page" not in css
    assert 'url("/static/epub/x/OEBPS/styles/bg.png")' in css


def test_scope_css_keeps_bare_rules_inside_the_scope():
    "A bare element selector must never leak onto the reader page."
    css = scope_css("h1, p > a { margin: 0 }", "OEBPS", "/static/epub/x")
    assert css.strip() == ".epub-body h1, .epub-body p > a { margin: 0 }"


def test_scope_css_pins_fixed_positioning():
    "`position: fixed` would escape the reading pane."
    css = scope_css("body { position: fixed; }", "OEBPS", "/static/epub/x")
    assert "position: absolute" in css


def test_scope_css_drops_a_colour_the_theme_would_hide():
    """
    Lute's default theme is dark, and print-derived books say
    `p { color: #000 }` with no background of their own: kept, the
    chapter would be black on near-black.  The declaration goes, the
    rest of the block stays.
    """
    css = scope_css("p { color: #000; text-indent: 1em; }", "OEBPS", "/static/epub/x")
    assert "color" not in css
    assert "text-indent: 1em" in css

    # A block that was nothing but the colour must vanish, not become an
    # empty rule.
    assert scope_css("p { color: #000; }", "OEBPS", "/static/epub/x").strip() == ""


def test_scope_css_keeps_a_colour_the_block_paints_behind():
    "A block with its own background is the book's surface: leave it whole."
    css = scope_css(
        "p { color: #000; background: #fff; }", "OEBPS", "/static/epub/x"
    )
    assert "color: #000" in css and "background: #fff" in css

    # `background: transparent` paints nothing, so it is not a surface.
    assert "color" not in scope_css(
        "p { color: #000; background: transparent; }", "OEBPS", "/static/epub/x"
    )
    # An image is a surface.
    assert "color: #000" in scope_css(
        "p { color: #000; background-image: url(bg.png); }",
        "OEBPS",
        "/static/epub/x",
    )


def test_scope_css_leaves_other_colour_properties_alone():
    "border-color and friends are not the text colour."
    css = scope_css("p { border-color: red; }", "OEBPS", "/static/epub/x")
    assert "border-color: red" in css
