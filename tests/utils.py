"""
Utility methods for tests.
"""

import io
import posixpath
import zipfile

from lute.models.term import Term
from lute.models.book import Book, Text
from lute.read.render.service import Service
from lute.db import db


def add_terms(language, term_array):
    """
    Make and save terms.
    """
    ret = []
    for term in term_array:
        t = Term(language, term)
        db.session.add(t)
        ret.append(t)
    db.session.commit()
    return ret


def make_book(title, content, language):
    """
    Make a book.
    """
    b = Book()
    b.title = title
    b.language = language
    if isinstance(content, str):
        content = [content]
    n = 0
    for c in content:
        n += 1
        _ = Text(b, c, n)
    return b


def make_text(title, content, language):
    """
    Make a single-page book, return the text.
    """
    b = make_book(title, content, language)
    return b.texts[0]


def get_rendered_string(text, imploder="/", overridestringize=None):
    "Get the stringized rendered content after parsing."

    def stringize(ti):
        zws = "\u200B"
        status = ""
        if ti.wo_status not in [None, 0]:
            status = f"({ti.wo_status})"
        return ti.display_text.replace(zws, "") + status

    usestringize = overridestringize or stringize
    ret = []
    service = Service(db.session)
    paras = service.get_paragraphs(text.text, text.book.language)
    for p in paras:
        tis = [t for s in p for t in s]
        ss = [usestringize(ti) for ti in tis]
        ret.append(imploder.join(ss))
    return "/<PARA>/".join(ret)


def assert_rendered_text_equals(text, expected, msg=""):
    "Check that the rendered string matches the expected."
    actual = get_rendered_string(text)
    # This assertion gives details because the module
    # is registered in tests/__init__.py.
    assert actual == "/<PARA>/".join(expected), msg


def make_epub_xhtml(paragraphs, heading=None):
    "Build a minimal xhtml chapter document."
    body = ""
    if heading:
        body += f"<h2>{heading}</h2>"
    for p in paragraphs:
        body += f"<p>{p}</p>"
    return f"<html><head><title>chapter</title></head><body>{body}</body></html>"


def make_epub(
    chapter_files,
    spine,
    title="Test Book",
    author="Test Author",
    nav_href=None,
    nav_entries=None,
    ncx_href=None,
    ncx_entries=None,
):
    """
    Build a minimal EPUB file in memory, returning the bytes.

    chapter_files: dict of zip path -> xhtml content (str).
    spine: list of (manifest id, href) in reading order.
    nav_href / nav_entries: EPUB 3 nav document and [(href, title)].
    ncx_href / ncx_entries: EPUB 2 toc.ncx and [(src, title)].
    """
    manifest_items = []
    for cid, href in spine:
        manifest_items.append(
            f'<item id="{cid}" href="{href}" media-type="application/xhtml+xml"/>'
        )
    if nav_href:
        manifest_items.append(
            f'<item id="nav" href="{nav_href}" '
            'media-type="application/xhtml+xml" properties="nav"/>'
        )
    if ncx_href:
        manifest_items.append(
            f'<item id="ncx" href="{ncx_href}" media-type="application/x-dtbncx+xml"/>'
        )
    spine_attrs = ' toc="ncx"' if ncx_href else ""
    spine_xml = "".join(f'<itemref idref="{cid}"/>' for cid, _ in spine)

    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{title}</dc:title>"
        f"<dc:creator>{author}</dc:creator>"
        "</metadata>"
        f"<manifest>{''.join(manifest_items)}</manifest>"
        f"<spine{spine_attrs}>{spine_xml}</spine>"
        "</package>"
    )
    container = (
        '<?xml version="1.0"?>'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        "<rootfiles>"
        '<rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/>'
        "</rootfiles>"
        "</container>"
    )

    files = {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": container,
        "OEBPS/content.opf": opf,
    }
    files.update(chapter_files)
    if nav_href:
        links = "".join(
            f'<li><a href="{h}">{t}</a></li>' for h, t in (nav_entries or [])
        )
        files[
            posixpath.join("OEBPS", nav_href)
        ] = f"<html><body><nav><ol>{links}</ol></nav></body></html>"
    if ncx_href:
        points = "".join(
            '<navPoint id="np{}"><navLabel><text>{}</text></navLabel>'
            '<content src="{}"/></navPoint>'.format(i, t, s)
            for i, (s, t) in enumerate(ncx_entries or [])
        )
        files[posixpath.join("OEBPS", ncx_href)] = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            f"<head/><docTitle><text>{title}</text></docTitle>"
            f"<navMap>{points}</navMap></ncx>"
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            zf.writestr(name, content)
    return buf.getvalue()


def make_pdf_bytes(page_texts):
    """
    Build a minimal valid PDF (bytes) with one page per string in
    page_texts, each page containing that text in Helvetica.
    """
    font_id = 3 + 2 * len(page_texts)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(page_texts)))
    objs = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {len(page_texts)} >>",
        font_id: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for i, t in enumerate(page_texts):
        page_id = 3 + 2 * i
        esc = t.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream = f"BT /F1 24 Tf 72 720 Td ({esc}) Tj ET".encode("latin-1")
        objs[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {page_id + 1} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        )
        objs[page_id + 1] = (
            b"<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )

    out = b"%PDF-1.4\n"
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        body = objs[num]
        if isinstance(body, str):
            body = body.encode("latin-1")
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    count = max(objs)
    out += f"xref\n0 {count + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for num in range(1, count + 1):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += (
        f"trailer << /Size {count + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return out
