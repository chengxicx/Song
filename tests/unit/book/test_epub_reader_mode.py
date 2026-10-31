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
import logging
import os
import stat

from bs4 import BeautifulSoup

from lute.book.epub_css import scope_css
from lute.book.epub_extract import css_path_for, orig_css_path_for
from lute.book import epub_paths
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
<p>She called it ‘the show’ and left...</p>
<p>&nbsp;&nbsp;Indented with a no-break space.</p>
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
    return client.post(
        "/book/import/epub", data=data, content_type="multipart/form-data"
    )


def _import_reader_book(app, client, english, tmp_path):
    """
    Import in reader mode with the package root isolated to tmp_path.

    The root is a config value, not app.static_folder: extracted
    packages are user data and belong next to the database, where a
    backup and a restore will actually find them.
    """
    app.config["EPUB_STATIC_ROOT"] = str(tmp_path / "static")
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


def _package_path(app, epub_path):
    "Absolute path of an extracted file, from its stored BkEpubPath."
    return os.path.join(app.config["EPUB_STATIC_ROOT"], epub_path)


def _mode(path):
    "Permission bits of an extracted file (0o644 expected for served ones)."
    return stat.S_IMODE(os.stat(path).st_mode)


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

    html_path = _package_path(app, book.epub_path)
    assert os.path.isfile(html_path)
    html = open(html_path, encoding="utf-8").read()
    assert 'class="epub-body novel"' in html
    assert "<script" not in html, "active content is stripped"
    assert "onclick" not in html
    assert "<rt" not in html, "ruby readings are stripped for token alignment"
    assert "漢字" in html
    # Relative asset URLs point at the extracted copy.
    assert "/static/epub/" in html and "/OEBPS/images/pic.png" in html

    css_path = _package_path(app, css_path_for(book.epub_path))
    assert os.path.isfile(css_path)
    css = open(css_path, encoding="utf-8").read()
    assert ".epub-body" in css
    assert "@page" not in css, "print-only rules are dropped"
    # The package is not in the code tree.
    assert not os.path.exists(
        os.path.join(app.static_folder, book.epub_path)
    ), "the extracted chapter must not live under the static folder"


def test_chapter_assets_are_served_from_the_data_directory(
    app, app_context, english, client, tmp_path
):
    """
    The chapter is fetched from /static/epub/..., but the file comes
    from EPUB_STATIC_ROOT.  The URL is kept because the stored chapter
    markup already carries that prefix; what matters is that the path
    resolves into the data directory, which a restore replaces together
    with the database, and not into the code tree.
    """
    book = _import_reader_book(app, client, english, tmp_path)

    chapter = client.get("/static/" + book.epub_path)
    assert chapter.status_code == 200
    assert "epub-body" in chapter.get_data(as_text=True)

    css = client.get("/static/" + css_path_for(book.epub_path))
    assert css.status_code == 200
    assert ".epub-body" in css.get_data(as_text=True)

    # Nothing is left behind in the code tree.
    assert not os.path.exists(os.path.join(app.static_folder, book.epub_path))


def test_missing_package_falls_back_to_the_old_static_location(
    app, app_context, english, client, tmp_path
):
    """
    Books imported before the move keep working between an upgrade and
    the relocation step: the data directory misses, the old location
    answers.
    """
    book = _import_reader_book(app, client, english, tmp_path)
    package = os.path.join(app.config["EPUB_STATIC_ROOT"], book.epub_path)
    # A stand-in for the old code-tree location, so the test never
    # writes into the repository.
    old_static = app.static_folder
    app.static_folder = str(tmp_path / "code_static")
    legacy = os.path.join(app.static_folder, book.epub_path)
    os.makedirs(os.path.dirname(legacy), exist_ok=True)
    os.replace(package, legacy)
    try:
        resp = client.get("/static/" + book.epub_path)
        assert resp.status_code == 200
        assert "epub-body" in resp.get_data(as_text=True)
        assert not os.path.exists(package)
    finally:
        app.static_folder = old_static
        os.replace(legacy, package)


def test_chapter_stylesheet_rides_on_the_container(
    app, app_context, english, client, tmp_path
):
    """
    The fragment is swapped into #thetext with innerHTML, and a <link>
    at its top does not survive that swap -- the chapter rendered with
    none of the book's styling.  The URL therefore travels on the
    container as data-epub-css and lute-epub-reader.js creates the
    <link> in <head>.
    """
    book = _import_reader_book(app, client, english, tmp_path)
    fragment = client.get(f"/read/start_reading/{book.id}/1").get_data(as_text=True)

    soup = BeautifulSoup(fragment, "html.parser")
    container = soup.find(id="epub-chapter")
    assert container is not None
    href = container.get("data-epub-css")
    assert href and href.endswith("/lute.css")
    assert href.startswith("/static/epub/")
    assert soup.find("link") is None, "a <link> in the fragment would be dropped"

    # And the URL it names really is served.
    assert client.get(href).status_code == 200


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
    the language's character substitutions are applied and whitespace is
    ignored.

    The substitutions matter: the parser normalises the text before
    tokenizing (a curved apostrophe becomes a straight one so the book's
    word matches the dictionary's), while the chapter keeps the book's
    own characters.  The page ships the table so the front end can apply
    it -- this test fails if the two ever drift apart.
    """
    english.character_substitutions = "’='|...=…"
    db.session.add(english)

    book = _import_reader_book(app, client, english, tmp_path)
    fragment = client.get(f"/read/start_reading/{book.id}/1").get_data(as_text=True)

    soup = BeautifulSoup(fragment, "html.parser")
    data = soup.find("script", {"id": "lute-epub-tokens"})
    assert data is not None, "token stream is embedded in the fragment"
    tokens = json.loads(data.string or "[]")
    assert tokens, "chapter tokens were produced"

    subs_data = soup.find("script", {"id": "lute-epub-substitutions"})
    assert subs_data is not None, "the substitution table is shipped too"
    subs = json.loads(subs_data.string or "[]")
    assert ["’", "'"] in subs and ["...", "…"] in subs

    def normalised(s):
        for frm, to in subs:
            s = s.replace(frm, to)
        return s

    def bare(s):
        return "".join(ch for ch in s if not ch.isspace())

    dom_text = soup.find(id="epub-chapter").get_text("")
    html_path = _package_path(app, book.epub_path)
    file_text = BeautifulSoup(
        open(html_path, encoding="utf-8").read(), "html.parser"
    ).get_text("")

    assert bare("".join(t[0] for t in tokens)) == bare(normalised(file_text))
    assert bare(dom_text) == bare(file_text), "fragment renders the stored chapter"
    # The chapter itself keeps the book's typography; only the alignment
    # sees the normalised form.
    assert "‘the show’" in file_text and "..." in file_text

    words = [t for t in tokens if t[1]]
    assert words, "words are tokenized"
    assert all(len(t) == 5 for t in tokens), "order/sentence/paragraph are present"
    assert any(t[2] == 0 for t in words), "new words have status 0"
    assert "the" in [t[0] for t in words], "words on both sides of a curly quote parse"


def _rendered_tokens(client, book):
    """
    The token stream a page ships, and the chapter text it has to align
    to -- normalised with the substitutions the page applies itself.
    """
    fragment = client.get(f"/read/start_reading/{book.id}/1").get_data(as_text=True)
    soup = BeautifulSoup(fragment, "html.parser")
    data = soup.find("script", {"id": "lute-epub-tokens"})
    tokens = json.loads(data.string or "[]")
    subs = json.loads(
        soup.find("script", {"id": "lute-epub-substitutions"}).string or "[]"
    )
    # get_text() leaves out <rt>, which is what the page's SKIP_SELECTOR
    # does too -- this is the character stream the aligner sees.
    dom_text = soup.find(id="epub-chapter").get_text()
    for frm, to in subs:
        dom_text = dom_text.replace(frm, to)
    return tokens, dom_text, fragment


def _bare(s):
    return "".join(ch for ch in s if not ch.isspace())


def test_token_stream_is_realigned_when_the_stored_text_has_drifted(
    app, app_context, english, client, tmp_path, caplog
):
    """
    A chapter whose stored text no longer matches its chapter file still
    renders with a stream the page can align to.

    The tokens come from the page's stored text, the markup from the
    chapter file, and nothing re-checks that the two agree after import
    -- a book re-imported or re-split part-way through can leave them
    describing different characters.  The front end then refuses to
    mount, and the chapter renders as plain text: no word spans, so no
    ruby, no tapping, no status colours.  The render re-parses the
    chapter in that case instead of shipping a stream that cannot work.
    """
    book = _import_reader_book(app, client, english, tmp_path)
    text = book.text_at_page(1)
    text.text = f"{text.text} drifted"
    db.session.add(text)
    db.session.commit()

    caplog.set_level(logging.WARNING)
    tokens, dom_text, _ = _rendered_tokens(client, book)
    assert tokens, "a drifted chapter still gets a token stream"
    assert _bare("".join(t[0] for t in tokens)) == _bare(dom_text)
    assert "token stream realigned" in caplog.text, "the drift was reported"


def test_ruby_readings_in_the_markup_do_not_count_as_drift(
    app, app_context, english, client, tmp_path, caplog
):
    """
    A chapter that still carries its ruby annotations is not "drifted".

    Books imported before the extraction dropped rt/rp have readings in
    both the chapter file and the stored text.  The page skips them when
    it collects the characters to align (SKIP_SELECTOR), so the readings
    must be left out of the comparison here too -- otherwise every such
    chapter would look broken and be re-parsed for nothing.
    """
    book = _import_reader_book(app, client, english, tmp_path)
    path = epub_paths.chapter_file(app, book.epub_path, 1)
    html = open(path, encoding="utf-8").read()
    # A new import keeps the <ruby> shell but drops the reading itself.
    assert "<ruby>" in html and "<rt>" not in html, "annotations are stripped"
    open(path, "w", encoding="utf-8").write(
        html.replace("</ruby>", "<rt>かんじ</rt></ruby>", 1)
    )

    caplog.set_level(logging.WARNING)
    tokens, dom_text, fragment = _rendered_tokens(client, book)
    assert "<rt>かんじ</rt>" in fragment, "the reading is rendered"
    assert _bare("".join(t[0] for t in tokens)) == _bare(
        dom_text
    ), "the reading is not part of the aligned stream"
    assert "token stream realigned" not in caplog.text, "nothing to repair"


def test_parser_reports_the_substitutions_it_applies_to_text(app, app_context, english):
    """
    text_substitutions() has to describe what the parser really does:
    the alignment trusts it, and the parser and the report are written
    in different places.
    """
    english.character_substitutions = "’='|...=…"
    parser = english.parser
    pairs = parser.text_substitutions(english)
    assert pairs == [
        ("\u200b", ""),
        ("’", "'"),
        ("...", "…"),
        ("{", "["),
        ("}", "]"),
    ]

    # Applying them must reproduce the tokenizer's view of the text.
    text = "it’s ‘quoted’ {x} ... \u200bend"
    normalised = text
    for frm, to in pairs:
        normalised = normalised.replace(frm, to)
    strip_ws = lambda s: "".join(ch for ch in s if not ch.isspace())
    tokens = english.get_parsed_tokens(text)
    assert strip_ws("".join(t.token for t in tokens)) == strip_ws(normalised)
    assert "…" in "".join(t.token for t in tokens), "the ellipsis is normalised"


def test_reader_mode_off_keeps_plain_text_import(
    app, app_context, english, client, tmp_path
):
    "Without the switch, the import is unchanged: plain text, no chapter files."
    app.config["EPUB_STATIC_ROOT"] = str(tmp_path / "static")
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
    assert not os.path.exists(os.path.join(str(tmp_path), "static", "epub"))


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
    css = scope_css("p { color: #000; background: #fff; }", "OEBPS", "/static/epub/x")
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


def test_scope_css_keep_colors_variant_preserves_every_colour():
    """
    The original-look stylesheet keeps the book's palette whole: the
    paper surface renders it as designed, so the invisible-text guard
    (dropping unpainted `color`s for dark themes) must not run.
    """
    css = scope_css(
        "p { color: #000; text-indent: 1em; }",
        "OEBPS",
        "/static/epub/x",
        keep_colors=True,
    )
    assert "color: #000" in css
    assert "text-indent: 1em" in css
    # A block that is nothing but the colour survives too.
    assert (
        scope_css(
            "p { color: #000; }", "OEBPS", "/static/epub/x", keep_colors=True
        ).strip()
        == ".epub-body p { color: #000; }"
    )


def test_reader_mode_import_writes_orig_stylesheet_and_manifest(
    app, app_context, english, client, tmp_path
):
    """
    The package carries two scoped stylesheets -- lute.css follows the
    theme (unpainted colours dropped), lute-orig.css keeps them for the
    paper / night looks -- plus chapters.json recording each chapter's
    archive path, so the variant can be rebuilt later without the .epub.
    """
    book = _import_reader_book(app, client, english, tmp_path)

    theme_css = open(
        _package_path(app, css_path_for(book.epub_path)), encoding="utf-8"
    ).read()
    orig_css = open(
        _package_path(app, orig_css_path_for(book.epub_path)), encoding="utf-8"
    ).read()
    # The chapter's inline style is `p { color: red; }` with no
    # background: dropped for the theme, kept for the paper looks.
    assert "color: red" not in theme_css
    assert "color: red" in orig_css

    manifest = json.load(
        open(
            _package_path(
                app, "epub/" + book.epub_path.split("/")[1] + "/chapters.json"
            ),
            encoding="utf-8",
        )
    )
    assert manifest == [{"index": 0, "path": "OEBPS/ch1.xhtml", "viewport": None}]

    # The reading fragment ships both URLs on the container.
    fragment = client.get(f"/read/start_reading/{book.id}/1").get_data(as_text=True)
    soup = BeautifulSoup(fragment, "html.parser")
    container = soup.find(id="epub-chapter")
    assert container.get("data-epub-css", "").endswith("/lute.css")
    orig_href = container.get("data-epub-orig-css", "")
    assert orig_href.endswith("/lute-orig.css")
    assert client.get(orig_href).status_code == 200


FIXED_CHAPTER = """<html><head>
<meta name="viewport" content="width=768, height=1024"/>
</head><body>
<p style="position:absolute; left:10px; top:20px">Fixed page text.</p>
</body></html>"""

REFLOW_WITH_VIEWPORT_META = """<html><head>
<meta name="viewport" content="width=device-width, height=device-height"/>
</head><body><p>Reflows like a normal chapter.</p></body></html>"""


def test_fixed_layout_chapter_gets_a_viewport_page_box(
    app, app_context, english, client, tmp_path
):
    """
    A fixed-layout chapter (pixel viewport meta, e.g. a picture book)
    carries its page size on the wrapper so the reading screen can
    render it as a fixed page box; device-width spellings stay reflow.
    """
    app.config["EPUB_STATIC_ROOT"] = str(tmp_path / "static")
    epub = make_epub(
        {
            "OEBPS/fixed.xhtml": FIXED_CHAPTER,
            "OEBPS/reflow.xhtml": REFLOW_WITH_VIEWPORT_META,
        },
        [("fixed", "fixed.xhtml"), ("reflow", "reflow.xhtml")],
        title="Picture Book",
        nav_href="nav.xhtml",
        nav_entries=[("fixed.xhtml", "One"), ("reflow.xhtml", "Two")],
    )
    resp = _post(
        client,
        epub,
        language_id=str(english.id),
        epub_title="Picture Book",
        epub_reader_mode="1",
    )
    assert resp.get_json()["success"] is True, resp.get_json()
    books = (
        db.session.query(DBBook)
        .filter(DBBook.title.like("Picture Book%"))
        .order_by(DBBook.title)
        .all()
    )
    assert len(books) == 2

    fixed = next(b for b in books if " 01 " in b.title)
    reflow = next(b for b in books if " 02 " in b.title)

    fixed_html = open(_package_path(app, fixed.epub_path), encoding="utf-8").read()
    assert 'data-epub-viewport="768x1024"' in fixed_html
    assert 'class="epub-body epub-fixed"' in fixed_html

    reflow_html = open(_package_path(app, reflow.epub_path), encoding="utf-8").read()
    assert "data-epub-viewport" not in reflow_html
    assert "epub-fixed" not in reflow_html

    manifest = json.load(
        open(
            _package_path(
                app, "epub/" + fixed.epub_path.split("/")[1] + "/chapters.json"
            ),
            encoding="utf-8",
        )
    )
    assert manifest == [
        {"index": 0, "path": "OEBPS/fixed.xhtml", "viewport": "768x1024"},
        {"index": 1, "path": "OEBPS/reflow.xhtml", "viewport": None},
    ]


def test_ensure_orig_css_regenerates_for_old_packages(
    app, app_context, english, client, tmp_path
):
    """
    Packages extracted before lute-orig.css existed get it rebuilt from
    the extracted originals (the whole archive is on disk): with
    chapters.json for exact collection, without it by scanning the
    content documents.  A missing package reports None.
    """
    from lute.book.epub_extract import ensure_orig_css

    book = _import_reader_book(app, client, english, tmp_path)
    rel = orig_css_path_for(book.epub_path)

    # Old package: no variant stylesheet, but the manifest is there.
    os.remove(_package_path(app, rel))
    assert ensure_orig_css(app, book.epub_path) == rel
    regenerated = open(_package_path(app, rel), encoding="utf-8").read()
    assert "color: red" in regenerated
    # nginx serves the package as another user, so the atomic write must
    # not leave the file 0600 (a 403 in the browser, i.e. an unstyled
    # chapter).
    assert _mode(_package_path(app, rel)) == 0o644

    # Idempotent: the existing file is returned untouched.
    assert ensure_orig_css(app, book.epub_path) == rel

    # A 0600 file written by an earlier build is repaired on render.
    os.chmod(_package_path(app, rel), 0o600)
    assert ensure_orig_css(app, book.epub_path) == rel
    assert _mode(_package_path(app, rel)) == 0o644

    # Oldest package: no manifest either -- the scan path finds the
    # chapter documents and produces the same stylesheet.
    os.remove(_package_path(app, rel))
    os.remove(
        _package_path(app, "epub/" + book.epub_path.split("/")[1] + "/chapters.json")
    )
    assert ensure_orig_css(app, book.epub_path) == rel
    assert "color: red" in open(_package_path(app, rel), encoding="utf-8").read()

    # A book pointing at nothing gets None, not a crash.
    assert ensure_orig_css(app, "epub/deadbeef/ch/000.html") is None


def test_epub_render_standalone_page(app, app_context, english, client, tmp_path):
    """/read/epub_render serves a complete document for the mobile
    WebView: the chapter markup, the token JSON, the reader script, and
    the bridge -- none of the reading-page chrome."""
    book = _import_reader_book(app, client, english, tmp_path)
    resp = client.get(f"/read/epub_render/{book.id}/1")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    soup = BeautifulSoup(html, "html.parser")
    assert soup.find(id="epub-chapter") is not None
    assert soup.find(id="lute-epub-tokens") is not None
    assert soup.find(id="lute-epub-substitutions") is not None
    assert "lute-epub-reader.js" in html
    assert "LuteEpubBridge" in html
    thetext = soup.find(id="thetext")
    assert "epub-text-container" in (thetext.get("class") or [])
    # Default look is "original" (the paper surface).
    assert "epub-look-original" in (thetext.get("class") or [])
    # The scoped stylesheet URL rides on the container as data
    # attributes, exactly like the fragment.
    container = soup.find(id="epub-chapter")
    assert (container.get("data-epub-orig-css") or "").endswith("/lute-orig.css")


def test_epub_render_look_and_furigana_params(
    app, app_context, english, client, tmp_path
):
    """?look= pre-selects the look class (no flash of the wrong theme),
    ?furigana= decides whether lute-furigana.js is on the page at all --
    its annotate() ignores the enabled flag, so "off" must mean
    "absent"."""
    book = _import_reader_book(app, client, english, tmp_path)

    night = client.get(f"/read/epub_render/{book.id}/1?look=night").get_data(
        as_text=True
    )
    thetext = BeautifulSoup(night, "html.parser").find(id="thetext")
    assert "epub-look-night" in (thetext.get("class") or [])

    # Unknown look values fall back to original, never a broken page.
    bogus = client.get(f"/read/epub_render/{book.id}/1?look=sparkly").get_data(
        as_text=True
    )
    thetext = BeautifulSoup(bogus, "html.parser").find(id="thetext")
    assert "epub-look-original" in (thetext.get("class") or [])

    # English has no readings source, so by default the script is off;
    # ?furigana=1 forces it on.  (The bridge's FURIGANA_SRC constant
    # always names the URL, so this looks at real <script> srcs.)
    def _has_furigana_script(html):
        soup = BeautifulSoup(html, "html.parser")
        return (
            soup.find("script", src=lambda s: s and "lute-furigana.js" in s) is not None
        )

    default = client.get(f"/read/epub_render/{book.id}/1").get_data(as_text=True)
    assert not _has_furigana_script(default)
    forced = client.get(f"/read/epub_render/{book.id}/1?furigana=1").get_data(
        as_text=True
    )
    assert _has_furigana_script(forced)
    assert "LUTE_FURIGANA" in forced

    # ?triple=1 arms the page-side triple-tap window (mark known);
    # without it the second tap settles immediately.
    plain = client.get(f"/read/epub_render/{book.id}/1").get_data(as_text=True)
    assert "var tripleEnabled = false;" in plain
    triple = client.get(f"/read/epub_render/{book.id}/1?triple=1").get_data(
        as_text=True
    )
    assert "var tripleEnabled = true;" in triple


def test_epub_render_rejects_non_epub_and_missing_pages(
    app, app_context, english, client, tmp_path
):
    """/read/epub_render is for reader-mode EPUB books only: a plain
    text book or an out-of-range page is a 404, not a broken render."""
    from tests.utils import make_book

    epub_book = _import_reader_book(app, client, english, tmp_path)
    assert client.get(f"/read/epub_render/{epub_book.id}/99").status_code == 404
    assert client.get(f"/read/epub_render/{epub_book.id + 1000}/1").status_code == 404

    text_book = make_book("plain", "Some normal text.", english)
    db.session.add(text_book)
    db.session.commit()
    assert client.get(f"/read/epub_render/{text_book.id}/1").status_code == 404
