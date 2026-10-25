"""
Book service tests.
"""

import io
import os
import shutil
from contextlib import ExitStack
from bs4 import BeautifulSoup
from lute.db import db
from lute.models.repositories import BookRepository
from lute.book.model import Book
from lute.book.service import Service, webpage_block_text
from tests.dbasserts import assert_sql_result
from tests.utils import make_pdf_bytes


def get_test_files():
    "Return test files pair."
    thisdir = os.path.dirname(os.path.realpath(__file__))
    sample_files = os.path.join(thisdir, "..", "..", "acceptance", "sample_files")
    text_path = os.path.join(sample_files, "hola.txt")
    mp3_path = os.path.join(sample_files, "fake.mp3")
    with open(text_path, "r", encoding="utf-8") as fp:
        assert fp.read().strip() == "Tengo un amigo.", "Sanity check only."
    with open(mp3_path, "r", encoding="utf-8") as fp:
        assert fp.read().strip() == "fake mp3 file", "Sanity check only."
    return (text_path, mp3_path)


def test_create_book_from_file_paths(app, app_context, spanish):
    "Create a book using the DTO, to be populated by the form."
    text_path, mp3_path = get_test_files()

    b = Book()
    b.title = "Hola"
    b.language_id = spanish.id
    b.text_source_path = text_path
    b.audio_source_path = mp3_path

    svc = Service()
    svc.import_book(b, db.session)

    repo = BookRepository(db.session)
    book = repo.find_by_title("Hola", spanish.id)
    assert book.title == "Hola", "title"
    assert book.texts[0].text == "Tengo un amigo.", "Got content"

    assert book.audio_filename is not None, "Have audio file"
    assert book.audio_filename.endswith("mp3"), "still an mp3"
    useraudiopath = app.env_config.useraudiopath
    full_audio_path = os.path.join(useraudiopath, book.audio_filename)
    assert os.path.exists(full_audio_path), "file saved"

    with open(full_audio_path, "r", encoding="utf-8") as fp:
        assert fp.read().strip() == "fake mp3 file", "correct content copied."


def test_create_book_from_streams(app, app_context, spanish):
    "Create a book using streams, as given by the form."
    text_path, mp3_path = get_test_files()

    b = Book()
    b.title = "Hola"
    b.language_id = spanish.id
    with ExitStack() as stack:
        b.text_stream = stack.enter_context(open(text_path, mode="rb"))
        b.text_stream_filename = "blah.txt"
        b.audio_stream = stack.enter_context(open(mp3_path, mode="rb"))
        b.audio_stream_filename = "blah.mp3"
        svc = Service()
        svc.import_book(b, db.session)

    repo = BookRepository(db.session)
    book = repo.find_by_title("Hola", spanish.id)
    assert book.title == "Hola", "title"
    assert book.texts[0].text == "Tengo un amigo.", "Got content"

    assert book.audio_filename is not None, "Have audio file"
    assert book.audio_filename.endswith("mp3"), "still an mp3"
    useraudiopath = app.env_config.useraudiopath
    full_audio_path = os.path.join(useraudiopath, book.audio_filename)
    assert os.path.exists(full_audio_path), "file saved"
    with open(full_audio_path, "r", encoding="utf-8") as fp:
        assert fp.read().strip() == "fake mp3 file", "correct content copied."


def _import_pdf_book(app, spanish, page_texts):
    "Import a pdf book built from page_texts; return (dbbook, pdf_dir)."
    b = Book()
    b.title = "PDF book"
    b.language_id = spanish.id
    b.book_type = "pdf"
    b.threshold_page_tokens = 250
    b.split_by = "paragraphs"
    b.pdf_stream = io.BytesIO(make_pdf_bytes(page_texts))
    b.pdf_stream_filename = "test.pdf"
    book = Service().import_book(b, db.session)
    pdf_dir = os.path.join(app.static_folder, os.path.dirname(book.pdf_path.strip("/")))
    return book, pdf_dir


def test_import_pdf_book_sets_page_word_counts(app, app_context, spanish):
    "Pdf books' empty page texts get word counts from the PDF text."
    _book, pdf_dir = _import_pdf_book(app, spanish, ["Hello cat dog", "one two"])
    try:
        sql = "select TxWordCount from texts order by TxOrder"
        assert_sql_result(sql, ["3", "2"], "pdf page counts")
    finally:
        shutil.rmtree(pdf_dir, ignore_errors=True)


def test_import_pdf_book_no_word_counts_for_non_pdf(app_context, spanish):
    "Text books keep their normal text-derived word counts."
    text_path, _ = get_test_files()
    b = Book()
    b.title = "Hola"
    b.language_id = spanish.id
    b.text_source_path = text_path
    book = Service().import_book(b, db.session)
    assert book.texts[0].text == "Tengo un amigo.", "Got content"
    assert book.texts[0].word_count == 3, "normal count from page text"


def test_webpage_block_text_strips_source_whitespace():
    "Formatted source markup must not leave blank lines in the import text."
    html = """
    <html><body>
      <h1>
        A headline
      </h1>
      <p>
              First paragraph
              continues here.
          </p>
      <p>   </p>
      <p>Second&nbsp;paragraph.</p>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    blocks = webpage_block_text(soup)
    assert blocks == [
        "A headline",
        "First paragraph continues here.",
        "Second paragraph.",
    ], f"blocks = {blocks}"
    # Joined text has exactly one blank line between paragraphs.
    text = "\n\n".join(blocks)
    assert "\n\n\n" not in text, f"extra blank lines: {text!r}"
    assert all(line == line.strip() for line in text.split("\n")), "no indents"


def test_webpage_block_text_skips_nested_matched_tags():
    "A heading nested in a paragraph is only reported once."
    html = "<div><p>outer <h3>inner</h3> tail</p><p>second</p></div>"
    soup = BeautifulSoup(html, "html.parser")
    assert webpage_block_text(soup) == ["outer inner tail", "second"]


def test_webpage_block_text_drops_page_furniture():
    "Site menus, footers and sidebar asides aren't article text."
    html = """
    <html><body>
      <header><nav><p>Home</p><p>World</p><p>Politics</p></nav></header>
      <main>
        <article>
          <header><h1>The real headline</h1></header>
          <p>Body paragraph one, which is long enough to keep.</p>
          <aside><p>Related: something else entirely</p></aside>
          <p>Body paragraph two, also long enough to keep.</p>
          <footer><p>Share on Facebook</p></footer>
        </article>
      </main>
      <footer><p>Copyright 2026 Example News.</p></footer>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    assert webpage_block_text(soup) == [
        "The real headline",
        "Body paragraph one, which is long enough to keep.",
        "Body paragraph two, also long enough to keep.",
    ]


def test_webpage_block_text_keeps_article_header_but_drops_footer():
    "A <header> inside <article> holds the headline, so it's kept."
    html = """
    <div>
      <header><p>Site name</p></header>
      <article>
        <header><h2>Headline in the article header</h2></header>
        <p>Content that should survive the filter intact.</p>
        <footer><p>Tags: politics, elections</p></footer>
      </article>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    assert webpage_block_text(soup) == [
        "Headline in the article header",
        "Content that should survive the filter intact.",
    ]


def test_webpage_block_text_drops_short_link_cards():
    "Related-story cards (anchor inside or around the block) are dropped."
    html = """
    <article>
      <p>A genuine paragraph of article text, long enough to matter.</p>
      <h3><a href="/x">Linked heading inside the heading</a></h3>
      <a href="/y"><div><h3>Card whose anchor wraps the heading</h3></div></a>
      <p>Another genuine paragraph of article text to keep.</p>
    </article>
    """
    soup = BeautifulSoup(html, "html.parser")
    assert webpage_block_text(soup) == [
        "A genuine paragraph of article text, long enough to matter.",
        "Another genuine paragraph of article text to keep.",
    ]


def test_webpage_block_text_falls_back_when_filtering_would_gut_the_page():
    "A page wrapped in a <form> keeps its text rather than importing nothing."
    html = """
    <html><body><form>
      <p>Paragraph one of a page that wraps everything in a form.</p>
      <p>Paragraph two of that same oddly-built page.</p>
    </form></body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    blocks = webpage_block_text(soup)
    assert blocks == [
        "Paragraph one of a page that wraps everything in a form.",
        "Paragraph two of that same oddly-built page.",
    ]
