"""
EPUB parser tests.
"""

import io
import os
import zipfile

import pytest

from lute.book.epub_parser import parse_epub, chapter_book_title
from lute.book.service import BookImportException
from tests.utils import make_epub, make_epub_xhtml


def test_parses_metadata_toc_titles_and_text():
    "Metadata, TOC titles, and paragraph text are extracted."
    chapter_files = {
        "OEBPS/ch1.xhtml": make_epub_xhtml(["First paragraph.", "Second paragraph."]),
        "OEBPS/ch2.xhtml": make_epub_xhtml(["More text."], heading="Ignored Heading"),
    }
    epub = make_epub(
        chapter_files,
        [("ch1", "ch1.xhtml"), ("ch2", "ch2.xhtml")],
        nav_href="nav.xhtml",
        nav_entries=[("ch1.xhtml", "The Beginning"), ("ch2.xhtml", "The End")],
    )
    data = parse_epub(io.BytesIO(epub))
    assert data.title == "Test Book"
    assert data.author == "Test Author"
    assert [c.title for c in data.chapters] == ["The Beginning", "The End"]
    assert data.chapters[0].text == "First paragraph.\nSecond paragraph."
    assert "More text." in data.chapters[1].text


def test_titles_fall_back_to_first_heading():
    "Without a TOC, the first h1-h3 is the title."
    epub = make_epub(
        {"OEBPS/ch1.xhtml": make_epub_xhtml(["text"], heading="A Heading")},
        [("ch1", "ch1.xhtml")],
    )
    data = parse_epub(io.BytesIO(epub))
    assert [c.title for c in data.chapters] == ["A Heading"]


def test_titles_fall_back_to_chapter_n():
    "Without a TOC or headings, 'Chapter N' is used."
    content = "<html><body><p>no headings here</p></body></html>"
    epub = make_epub({"OEBPS/ch1.xhtml": content}, [("ch1", "ch1.xhtml")])
    data = parse_epub(io.BytesIO(epub))
    assert [c.title for c in data.chapters] == ["Chapter 1"]


def test_epub2_ncx_toc_used_when_no_nav():
    "EPUB 2 books get their titles from toc.ncx."
    epub = make_epub(
        {"OEBPS/ch1.xhtml": make_epub_xhtml(["hello"])},
        [("ch1", "ch1.xhtml")],
        ncx_href="toc.ncx",
        ncx_entries=[("ch1.xhtml", "NCX Title")],
    )
    data = parse_epub(io.BytesIO(epub))
    assert [c.title for c in data.chapters] == ["NCX Title"]


def test_empty_spine_items_are_skipped():
    "Covers / blank pages produce no text and are not chapters."
    chapter_files = {
        "OEBPS/cover.xhtml": '<html><body><img src="cover.jpg"/></body></html>',
        "OEBPS/ch1.xhtml": make_epub_xhtml(["real text"]),
    }
    epub = make_epub(chapter_files, [("cover", "cover.xhtml"), ("ch1", "ch1.xhtml")])
    data = parse_epub(io.BytesIO(epub))
    assert len(data.chapters) == 1
    assert data.chapters[0].title == "Chapter 1"
    assert data.chapters[0].text == "real text"


def test_nav_document_in_spine_is_not_a_chapter():
    "The EPUB 3 nav document is metadata, even when in the spine."
    epub = make_epub(
        {"OEBPS/ch1.xhtml": make_epub_xhtml(["hello"])},
        [("ch1", "ch1.xhtml"), ("nav", "nav.xhtml")],
        nav_href="nav.xhtml",
        nav_entries=[("ch1.xhtml", "Chapter One")],
    )
    data = parse_epub(io.BytesIO(epub))
    assert len(data.chapters) == 1
    assert data.chapters[0].title == "Chapter One"


def test_corrupt_file_raises_import_exception():
    "Non-zip content raises a BookImportException."
    with pytest.raises(BookImportException):
        parse_epub(io.BytesIO(b"this is not a zip file"))


def test_zip_without_container_raises():
    "A zip without an EPUB container raises a BookImportException."
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("somefile.txt", "hi")
    buf.seek(0)
    with pytest.raises(BookImportException):
        parse_epub(buf)


def test_no_readable_content_raises():
    "An EPUB with no readable text raises a BookImportException."
    content = '<html><body><img src="x.jpg"/></body></html>'
    epub = make_epub({"OEBPS/c.xhtml": content}, [("c", "c.xhtml")])
    with pytest.raises(BookImportException):
        parse_epub(io.BytesIO(epub))


def test_chapter_book_title_pads_position():
    "The position is zero-padded so titles sort in chapter order."
    assert chapter_book_title("My Book", 1, "Intro", 12) == "My Book 01 - Intro"
    assert chapter_book_title("My Book", 12, "Fin", 12) == "My Book 12 - Fin"


def test_ruby_furigana_dropped_and_words_stay_intact():
    """
    Japanese ruby: the base text stays one word, the rt readings are
    dropped, and stray inline anchors do not split words.
    """
    content = (
        "<html><body>"
        "<p>「<ruby>彩<rt>いろ</rt>葉<rt>は</rt></ruby>！」</p>"
        "<p>声が<ruby>鼓<rt>こ</rt>膜<rt>まく</rt></ruby>を打った。</p>"
        '<p>見逃した<a id="p1"/>りはしない。</p>'
        "<p>Hello <b>bold</b> world.</p>"
        "</body></html>"
    )
    epub = make_epub({"OEBPS/ch1.xhtml": content}, [("ch1", "ch1.xhtml")])
    data = parse_epub(io.BytesIO(epub))
    text = data.chapters[0].text
    assert "「彩葉！」" in text
    assert "声が鼓膜を打った。" in text
    assert "見逃したりはしない。" in text
    assert "Hello bold world." in text
    assert "いろ" not in text
    assert "まく" not in text


def test_ruby_in_heading_title():
    "Heading-fallback chapter titles drop furigana and keep base text."
    content = (
        "<html><body>"
        "<h2><ruby>序<rt>じょ</rt>章<rt>しょう</rt></ruby></h2>"
        "<p>text</p>"
        "</body></html>"
    )
    epub = make_epub({"OEBPS/ch1.xhtml": content}, [("ch1", "ch1.xhtml")])
    data = parse_epub(io.BytesIO(epub))
    assert [c.title for c in data.chapters] == ["序章"]


def test_real_sample_file_hola_epub():
    "The checked-in sample EPUB parses and contains the expected text."
    thisdir = os.path.dirname(os.path.realpath(__file__))
    sample = os.path.join(
        thisdir, "..", "..", "acceptance", "sample_files", "Hola.epub"
    )
    with open(sample, "rb") as fp:
        data = parse_epub(fp)
    assert len(data.chapters) >= 1
    all_text = "\n".join(c.text for c in data.chapters)
    assert "amigo" in all_text


# ---------------------------------------------------------------------
# Structural divider pages: a heading-only "PART ONE" page is structure,
# not a chapter, and is folded into the chapter it introduces.
# ---------------------------------------------------------------------


def _divider_epub():
    "EPUB with a title page, a part page, and one real chapter."
    return make_epub(
        {
            "OEBPS/title.xhtml": make_epub_xhtml([], heading="CARRIE"),
            "OEBPS/part.xhtml": make_epub_xhtml([], heading="PART ONE"),
            "OEBPS/ch1.xhtml": make_epub_xhtml(
                ["A real paragraph of text."], heading="Chapter One"
            ),
        },
        [
            ("title", "title.xhtml"),
            ("part", "part.xhtml"),
            ("ch1", "ch1.xhtml"),
        ],
        nav_href="nav.xhtml",
        nav_entries=[
            ("title.xhtml", "CARRIE"),
            ("part.xhtml", "Part One"),
            ("ch1.xhtml", "Chapter One"),
        ],
    )


def test_heading_only_divider_pages_merge_into_the_next_chapter():
    "Title page + part page chain into the first real chapter."
    data = parse_epub(io.BytesIO(_divider_epub()))
    assert len(data.chapters) == 1
    chapter = data.chapters[0]
    assert chapter.title == "Chapter One"
    assert chapter.index == 0
    assert chapter.text == "CARRIE\nPART ONE\nChapter One\nA real paragraph of text."
    assert chapter.path == "OEBPS/ch1.xhtml"
    assert chapter.parts == [
        "OEBPS/title.xhtml",
        "OEBPS/part.xhtml",
        "OEBPS/ch1.xhtml",
    ]


def test_short_chapter_with_body_text_is_not_merged():
    "A dedication is short but real: it stays its own chapter."
    epub = make_epub(
        {
            "OEBPS/ded.xhtml": make_epub_xhtml(["For Tabby."], heading="Dedication"),
            "OEBPS/ch1.xhtml": make_epub_xhtml(["Real text."], heading="Chapter One"),
        },
        [("ded", "ded.xhtml"), ("ch1", "ch1.xhtml")],
    )
    data = parse_epub(io.BytesIO(epub))
    assert [c.title for c in data.chapters] == ["Dedication", "Chapter One"]
    assert data.chapters[0].text == "Dedication\nFor Tabby."


def test_long_heading_only_page_is_not_merged():
    "A long all-heading document (a contents page) is not a divider."
    long_heading = "A " + "very " * 20 + "long heading"
    epub = make_epub(
        {
            "OEBPS/contents.xhtml": make_epub_xhtml([], heading=long_heading),
            "OEBPS/ch1.xhtml": make_epub_xhtml(["Real text."]),
        },
        [("contents", "contents.xhtml"), ("ch1", "ch1.xhtml")],
    )
    data = parse_epub(io.BytesIO(epub))
    assert len(data.chapters) == 2


def test_trailing_divider_merges_into_the_previous_chapter():
    "A divider with nothing after it joins the chapter before it."
    epub = make_epub(
        {
            "OEBPS/ch1.xhtml": make_epub_xhtml(["Real text."], heading="Chapter One"),
            "OEBPS/end.xhtml": make_epub_xhtml([], heading="THE END"),
        },
        [("ch1", "ch1.xhtml"), ("end", "end.xhtml")],
    )
    data = parse_epub(io.BytesIO(epub))
    assert len(data.chapters) == 1
    assert data.chapters[0].text == "Chapter One\nReal text.\nTHE END"
    assert data.chapters[0].parts == ["OEBPS/ch1.xhtml", "OEBPS/end.xhtml"]


def test_fixed_layout_heading_page_is_not_merged():
    "A fixed-layout page box is one screen; it keeps its own chapter."
    fixed = (
        '<html><head><meta name="viewport" content="width=600, height=800"/>'
        "</head><body><h2>PART ONE</h2></body></html>"
    )
    epub = make_epub(
        {
            "OEBPS/part.xhtml": fixed,
            "OEBPS/ch1.xhtml": make_epub_xhtml(["Real text."]),
        },
        [("part", "part.xhtml"), ("ch1", "ch1.xhtml")],
    )
    data = parse_epub(io.BytesIO(epub))
    assert len(data.chapters) == 2


def test_divider_before_fixed_layout_chapter_stays_separate():
    "Merging into a pixel-sized page box would break that page."
    fixed = (
        '<html><head><meta name="viewport" content="width=600, height=800"/>'
        '</head><body><div class="page"><img src="p1.jpg"/></div></body></html>'
    )
    epub = make_epub(
        {
            "OEBPS/part.xhtml": make_epub_xhtml([], heading="PART ONE"),
            "OEBPS/fxl.xhtml": (
                '<html><head><meta name="viewport" content="width=600, height=800"/>'
                "</head><body><p>Boxed page text.</p></body></html>"
            ),
        },
        [("part", "part.xhtml"), ("fxl", "fxl.xhtml")],
    )
    data = parse_epub(io.BytesIO(epub))
    assert len(data.chapters) == 2
    assert data.chapters[1].parts == ["OEBPS/fxl.xhtml"]
