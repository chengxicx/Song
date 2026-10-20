"""
MOBI / AZW3 (KF8) import tests.

The fixtures are assembled by tests/unit/book/kindle_builder.py, which
writes real container bytes (PDB records, PalmDOC text, EXTH, MOBI 6
page breaks, KF8 skeleton/fragment indexes) so the reader is exercised
on the format itself rather than on a stub.
"""

import io
import zipfile

import pytest

from lute.book.epub_extract import extract_epub_package
from lute.book.epub_parser import parse_epub
from lute.book.kindle_parser import (
    _decompress_palmdoc,
    is_kindle_ebook,
    kindle_to_epub,
)
from lute.book.service import BookImportException
from tests.unit.book.kindle_builder import (
    FAKE_JPEG,
    build_combo,
    build_kf8,
    build_mobi6,
)


def _chapters(data):
    return parse_epub(io.BytesIO(data)).chapters


def test_detects_kindle_files_by_extension_and_magic():
    "Either signal is enough, whatever the upload's filename is."
    assert is_kindle_ebook("book.mobi")
    assert is_kindle_ebook("book.AZW3")
    assert is_kindle_ebook("upload", build_mobi6())
    assert not is_kindle_ebook("book.epub")
    assert not is_kindle_ebook("book.txt", b"plain text")


def test_mobi6_sections_metadata_and_text():
    "Each pagebreak becomes a chapter with its own heading and text."
    data = build_mobi6(
        title="Mobi Title",
        author="Mobi Author",
        sections=[
            "<html><body><h2>One</h2><p>first paragraph</p></body></html>",
            "<html><body><h2>Two</h2><p>second paragraph</p></body></html>",
        ],
    )
    book = parse_epub(io.BytesIO(kindle_to_epub(data)))
    assert book.title == "Mobi Title"
    assert book.author == "Mobi Author"
    assert [c.title for c in book.chapters] == ["One", "Two"]
    assert book.chapters[0].text == "One\nfirst paragraph"
    assert book.chapters[1].text == "Two\nsecond paragraph"


def test_mobi6_palmdoc_compressed_text():
    "PalmDOC-compressed records decompress to the same text."
    data = build_mobi6(
        sections=["<html><body><h2>Compressed</h2><p>hello there</p></body></html>"],
        compression=2,
    )
    chapters = _chapters(kindle_to_epub(data))
    assert chapters[0].title == "Compressed"
    assert "hello there" in chapters[0].text


def test_mobi6_image_becomes_epub_resource():
    "A recindex image is copied into the converted EPUB."
    data = build_mobi6(
        sections=[
            '<html><body><h2>Picture</h2><img recindex="1"/><p>text</p></body></html>'
        ],
        image=FAKE_JPEG,
    )
    epub = kindle_to_epub(data)
    with zipfile.ZipFile(io.BytesIO(epub)) as zf:
        names = zf.namelist()
        assert "OEBPS/images/res1.jpg" in names
        document = zf.read("OEBPS/ch0001.xhtml").decode()
    assert 'src="images/res1.jpg"' in document
    assert "recindex" not in document


def test_drm_protected_file_is_rejected():
    "Encrypted books cannot be imported and say so."
    data = build_mobi6(encryption=2)
    with pytest.raises(BookImportException) as error:
        kindle_to_epub(data)
    assert "DRM" in error.value.message


def test_non_mobi_file_is_rejected():
    "A file with the right extension but the wrong container is refused."
    with pytest.raises(BookImportException):
        kindle_to_epub(b"not a palm database at all" * 10)


def test_kf8_chapters_text_css_and_image():
    "KF8 skeletons are refilled with their fragments and keep the CSS."
    data = build_kf8(
        title="Kindle Format 8",
        author="Author 8",
        chapters=[
            ("First", ["alpha", "beta"]),
            ("Second", ["gamma"]),
        ],
        image=FAKE_JPEG,
    )
    epub = kindle_to_epub(data)
    book = parse_epub(io.BytesIO(epub))
    assert book.title == "Kindle Format 8"
    assert book.author == "Author 8"
    assert [c.title for c in book.chapters] == ["First", "Second"]
    assert book.chapters[0].text == "First\nalpha\nbeta"
    assert book.chapters[1].text == "Second\ngamma"

    with zipfile.ZipFile(io.BytesIO(epub)) as zf:
        assert "OEBPS/book.css" in zf.namelist()
        assert "OEBPS/images/res1.jpg" in zf.namelist()
        assert "margin: 0" in zf.read("OEBPS/book.css").decode()
        first = zf.read("OEBPS/ch0001.xhtml").decode()
    assert 'src="images/res1.jpg"' in first
    assert "kindle:embed" not in first


def test_combo_file_prefers_the_kf8_part():
    "A file carrying both parts is read from KF8, not the MOBI 6 text."
    data = build_combo(
        "<html><body><h2>Old</h2><p>obsolete mobi text</p></body></html>",
        [("New", ["fresh kf8 text"])],
    )
    chapters = _chapters(kindle_to_epub(data))
    assert [c.title for c in chapters] == ["New"]
    assert "fresh kf8 text" in chapters[0].text
    assert "obsolete" not in chapters[0].text


def test_palmdoc_decompresses_back_references():
    "A length/distance pair copies earlier output."
    assert _decompress_palmdoc(b"abc\x80\x18") == b"abcabc"


def test_plain_text_import_reads_kindle_files():
    "The single-book 'Text file' path accepts MOBI and AZW3 too."
    from lute.book.service import FileTextExtraction

    mobi = build_mobi6(
        sections=["<html><body><h2>T</h2><p>plain path text</p></body></html>"]
    )
    kf8 = build_kf8(chapters=[("C1", ["kf8 plain text"])])
    extractor = FileTextExtraction()
    assert "plain path text" in extractor.get_file_content(
        "book.mobi", io.BytesIO(mobi)
    )
    assert "kf8 plain text" in extractor.get_file_content("book.azw3", io.BytesIO(kf8))


def test_reader_mode_package_can_be_extracted(tmp_path):
    "The converted EPUB works with the reader-mode extractor too."
    data = build_kf8(chapters=[("Only", ["body text"])], image=FAKE_JPEG)
    epub = kindle_to_epub(data)
    book = parse_epub(io.BytesIO(epub))
    extracted = extract_epub_package(epub, book.chapters, str(tmp_path))
    chapter = extracted.chapters[0]
    assert "body text" in chapter["text"]
    written = tmp_path / chapter["html"]
    assert written.exists()
    assert "body text" in written.read_text(encoding="utf-8")
