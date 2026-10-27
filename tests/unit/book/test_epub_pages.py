"""
A reader-mode chapter is split into pages at import, and the split has
to stay invisible except in the size of a page.

A page is loaded and tokenised whole -- the markup, one span per token,
plus the JSON token stream the front end aligns against the DOM -- so a
chapter file that holds a whole novel (some conversions put the entire
book in one spine item) is one enormous page: slow to open, and part of
every reflow on the reading screen.  So `epub_extract` writes such a
chapter as several pages, and each page becomes a DBText row of its own.

Two things can go wrong quietly, and both are pinned here:

  * a page's stored text drifts from its own markup.  The front end
    aligns the token stream to the DOM character by character
    (lute-epub-reader.js `compareStreams`), and the match is
    all-or-nothing per page: a drift costs the page every word span,
    with nothing but a console warning.
  * the split loses or duplicates content, or drops the wrapper the
    book's own CSS is written against (`div.calibre` and friends).

The book-level contract is unchanged: one chapter is still one book, one
series entry, one title.
"""

import io
import json
import os

from bs4 import BeautifulSoup

from lute.book import epub_paths
from lute.book.epub_extract import PAGE_CHAR_BUDGET, _page_groups
from lute.book.epub_parser import _extract_text
from lute.db import db
from lute.models.book import Book as DBBook
from tests.utils import make_epub

# Comfortably over the budget: 40 paragraphs of ~120 characters.
PARAGRAPH = ("This is a paragraph of a chapter that goes on for a while. " * 2).strip()
PARAGRAPHS = [f"{i:03d} {PARAGRAPH}" for i in range(40)]


def _chapter_html(paragraphs, extra=""):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        "<html><head><title>chapter</title></head>"
        f'<body class="novel"><div class="calibre"><div class="chapter">'
        f"{extra}{body}</div></div></body></html>"
    )


def _epub_bytes(paragraphs=None, extra=""):
    paragraphs = PARAGRAPHS if paragraphs is None else paragraphs
    return make_epub(
        {"OEBPS/ch1.xhtml": _chapter_html(paragraphs, extra)},
        [("ch1", "ch1.xhtml")],
        title="Long Chapter",
    )


def _import(app, client, english, tmp_path, paragraphs=None, extra=""):
    app.config["EPUB_STATIC_ROOT"] = str(tmp_path / "static")
    data = {
        "epub_file": (io.BytesIO(_epub_bytes(paragraphs, extra)), "long.epub"),
        "language_id": str(english.id),
        "epub_title": "Long Chapter",
        "chapters": json.dumps([0]),
        "epub_reader_mode": "1",
    }
    resp = client.post(
        "/book/import/epub", data=data, content_type="multipart/form-data"
    )
    assert resp.get_json()["success"] is True, resp.get_json()
    return db.session.query(DBBook).filter(DBBook.title.like("Long Chapter%")).one()


def _read(app, path):
    with open(
        os.path.join(app.config["EPUB_STATIC_ROOT"], path), encoding="utf-8"
    ) as f:
        return f.read()


def test_a_long_chapter_becomes_several_pages(
    app, app_context, english, client, tmp_path
):
    book = _import(app, client, english, tmp_path)
    assert len(book.texts) > 1, "a chapter over the budget is split"
    assert len(book.texts) == book.page_count


def test_page_one_keeps_the_stored_path_and_the_rest_are_siblings(
    app, app_context, english, client, tmp_path
):
    """
    BkEpubPath stays the path of page 1, so nothing about the book row,
    the stylesheet lookup (dirname twice) or the old static-location
    fallback changes.
    """
    book = _import(app, client, english, tmp_path)
    assert book.epub_path.endswith("/ch/000.html")
    assert os.path.isfile(epub_paths.located(app, book.epub_path))
    for pagenum in range(2, book.page_count + 1):
        path = epub_paths.chapter_file(app, book.epub_path, pagenum)
        assert path is not None, f"page {pagenum} has no file"
        assert os.path.basename(path) == f"000-{pagenum:03d}.html"


def test_every_page_text_is_its_own_markup_text(
    app, app_context, english, client, tmp_path
):
    """
    The invariant the word spans depend on: the server tokenises the
    page's stored text, and the front end aligns those tokens to the
    page's DOM.  Same characters, or the page loses every span.
    """
    book = _import(app, client, english, tmp_path)
    for page in book.texts:
        html = _read(app, epub_paths.chapter_file(app, book.epub_path, page.order))
        rendered = _extract_text(BeautifulSoup(html, "html.parser"))
        assert page.text == rendered


def test_the_split_keeps_every_paragraph_exactly_once(
    app, app_context, english, client, tmp_path
):
    book = _import(app, client, english, tmp_path)
    joined = "\n".join(p.text for p in book.texts)
    for paragraph in PARAGRAPHS:
        assert joined.count(paragraph) == 1, paragraph[:20]


def test_the_pages_rebuild_the_chapter_in_order(
    app, app_context, english, client, tmp_path
):
    """
    The strongest invariant, and the one the paragraph check cannot see:
    the pages in order *are* the chapter.  A greedy grouping that
    descends through wrappers could reorder or repeat a block without
    losing a paragraph, and a reader would only notice the text jumping.
    """
    book = _import(app, client, english, tmp_path)
    assert "\n".join(p.text for p in book.texts) == "\n".join(PARAGRAPHS)


def test_each_page_keeps_the_wrapper_the_book_styles_against(
    app, app_context, english, client, tmp_path
):
    """
    Page 2 onwards is rebuilt from the grouping chain, not from the
    original body: `div.calibre > div.chapter` has to come with it or the
    book's own CSS (scoped to `.epub-body`) no longer matches.
    """
    book = _import(app, client, english, tmp_path)
    html = _read(app, epub_paths.chapter_file(app, book.epub_path, 2))
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find(class_="epub-body")
    assert body is not None
    assert "novel" in body.get("class")
    assert body.find(class_="calibre") is not None
    assert body.find(class_="chapter") is not None


def test_no_page_is_much_over_the_budget(app, app_context, english, client, tmp_path):
    "One paragraph may overshoot; a page of two must not."
    book = _import(app, client, english, tmp_path)
    lengths = [len(p.text) for p in book.texts]
    assert max(lengths) <= PAGE_CHAR_BUDGET * 2


def test_a_short_chapter_is_still_one_page(app, app_context, english, client, tmp_path):
    book = _import(app, client, english, tmp_path, paragraphs=PARAGRAPHS[:3])
    assert len(book.texts) == 1
    assert book.epub_path.endswith("/ch/000.html")


def test_page_two_serves_its_own_markup_and_its_own_tokens(
    app, app_context, english, client, tmp_path
):
    """
    The reading screen's path, end to end: page 2 gets page 2's markup,
    and the token stream shipped with it describes that same markup --
    which is what lute-epub-reader.js aligns against, character for
    character.
    """
    book = _import(app, client, english, tmp_path)
    fragment = client.get(f"/read/start_reading/{book.id}/2").get_data(as_text=True)

    soup = BeautifulSoup(fragment, "html.parser")
    chapter = soup.find(id="epub-chapter")
    dom_text = chapter.get_text("")
    assert "000 " not in dom_text, "page 2 does not repeat page 1"
    first = dom_text.strip().split(" ")[0]
    assert first.isdigit() and int(first) > 0, first

    tokens = json.loads(soup.find("script", {"id": "lute-epub-tokens"}).string or "[]")
    assert tokens, "the page was tokenised"
    subs = json.loads(
        soup.find("script", {"id": "lute-epub-substitutions"}).string or "[]"
    )

    def normalised(text):
        for frm, to in subs:
            text = text.replace(frm, to)
        return "".join(ch for ch in text if not ch.isspace())

    assert normalised("".join(t[0] for t in tokens)) == normalised(dom_text)


def test_a_fixed_layout_chapter_is_never_split(
    app, app_context, english, client, tmp_path
):
    """
    A fixed-layout chapter is one pixel-sized page box: cutting it would
    break the page, and the fit is computed from the whole thing.
    """
    viewport = '<meta name="viewport" content="width=600,height=800"/>'
    book = _import(app, client, english, tmp_path, extra=viewport)
    assert len(book.texts) == 1
    html = _read(app, book.epub_path)
    assert "data-epub-viewport" in html


def test_pictures_count_towards_a_page():
    """
    A reflowing chapter can be nothing but images (a converted comic).
    Counting characters alone would put every one of them on one page.
    """
    body = BeautifulSoup(
        "<body>" + '<p><img src="a.png"/></p>' * 30 + "</body>", "html.parser"
    )
    _wrappers, groups = _page_groups(body.body, PAGE_CHAR_BUDGET)
    assert len(groups) > 1
