"""
Utility methods for tests.
"""

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
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
            + stream + b"\nendstream"
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
