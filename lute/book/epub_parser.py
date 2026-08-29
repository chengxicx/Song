"""
Chapter-aware EPUB parsing for the EPUB import.

An EPUB file is a zip archive containing:
  META-INF/container.xml -- points to the .opf package document
  <package.opf>          -- metadata (title, author), a manifest of all
                            files, and the spine (the reading order)
  *.xhtml                -- the actual content

One spine item is treated as one chapter.  Chapter titles come from
the EPUB 3 nav document or the EPUB 2 toc.ncx when available, falling
back to the first h1-h3 heading in the file, then "Chapter N".

The package structure is parsed with the standard library (zipfile +
ElementTree); BeautifulSoup (already a dependency) extracts the xhtml
text.  No new dependencies are introduced.
"""

import posixpath
import re
import zipfile
from io import BytesIO
from urllib.parse import quote, unquote
from xml.etree import ElementTree

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from lute.book.service import BookImportException

# Tags rendered as one paragraph/line of output; containers recurse
# into; everything else (b, i, span, a, ruby, ...) is inline text glued
# to the surrounding stray text.
_TEXT_TAGS = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "figcaption", "pre"]
_CONTAINER_TAGS = [
    "html",
    "body",
    "main",
    "article",
    "section",
    "div",
    "blockquote",
    "td",
    "th",
    "dt",
    "dd",
    "aside",
    "header",
    "footer",
    "figure",
    "table",
    "tr",
    "ul",
    "ol",
    "dl",
]
_SKIP_TAGS = ["script", "style", "head", "template", "svg", "iframe", "noscript"]

_CONTENT_MEDIA_TYPES = ("application/xhtml+xml", "text/html")
_NCX_MEDIA_TYPE = "application/x-dtbncx+xml"

# Runs of ASCII whitespace collapse to a single space, like browser
# rendering.  The ideographic space (U+3000) is real Japanese text and
# is left alone.
_ASCII_WS_RE = re.compile(r"[ \t\r\n\f\v]+")


def _flattened_text(el):
    """
    The rendered text of an element: strings concatenated as browsers
    render them, with no separator injected between inline elements --
    ruby splits a word into several text nodes, and a space there
    would corrupt the word (「彩葉」 must not become 「彩 葉」).
    """
    return _ASCII_WS_RE.sub(" ", el.get_text("")).strip()


class EpubChapter:
    "One chapter: 0-based position, its title, and its text."

    def __init__(self, index, title, text):
        self.index = index
        self.title = title
        self.text = text


class EpubBookData:
    "Parsed EPUB metadata and chapters."

    def __init__(self, title, author, chapters):
        self.title = title
        self.author = author
        self.chapters = chapters


def chapter_book_title(book_title, position, chapter_title, total_chapters):
    """
    Title for a chapter's book, e.g. 'My Book 03 - The Beginning'.

    The zero-padded position keeps the series page (which sorts by
    title) in chapter order.
    """
    width = max(2, len(str(total_chapters)))
    return f"{book_title} {position:0{width}d} - {chapter_title}"


def parse_epub(filestream):
    """
    Parse an uploaded EPUB stream into metadata + chapters.

    Spine items without readable text (covers, blank pages) are
    skipped, so the returned chapters are exactly the importable ones.

    Raises BookImportException for unreadable or content-free files.
    """
    data = filestream.read()
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except Exception as e:
        raise BookImportException(
            f"Could not read EPUB file (error: {e})", cause=e
        ) from e

    with zf:
        try:
            zip_names = {posixpath.normpath(n): n for n in zf.namelist()}
            opf_path = _root_file_path(zf)
            opf_dir = posixpath.dirname(opf_path)
            opf = ElementTree.fromstring(zf.read(opf_path))
            title, author = _metadata(opf)
            manifest, spine_refs, spine_toc = _manifest_and_spine(opf)
            toc_map = _toc_map(zf, manifest, spine_toc, opf_dir, zip_names)
            chapters = _chapters(zf, manifest, spine_refs, opf_dir, zip_names, toc_map)
        except BookImportException:
            raise
        except Exception as e:
            raise BookImportException(
                f"Could not parse EPUB file (error: {e})", cause=e
            ) from e

    if not chapters:
        raise BookImportException("No readable chapters found in the EPUB file.")
    return EpubBookData(title, author, chapters)


def _root_file_path(zf):
    "Get the OPF path from META-INF/container.xml."
    try:
        container = ElementTree.fromstring(zf.read("META-INF/container.xml"))
    except KeyError as e:
        raise BookImportException(
            "Not a valid EPUB file: missing META-INF/container.xml.", cause=e
        ) from e
    rootfile = container.find(".//{*}rootfile")
    path = rootfile.get("full-path") if rootfile is not None else None
    if not path:
        raise BookImportException(
            "Not a valid EPUB file: no root file in container.xml."
        )
    return path


def _metadata(opf):
    "Get (title, author) from the OPF metadata; empty strings when absent."
    md = opf.find("{*}metadata")
    title = _first_text(md, "{http://purl.org/dc/elements/1.1/}title")
    author = _first_text(md, "{http://purl.org/dc/elements/1.1/}creator")
    return (title or "", author or "")


def _first_text(el, path):
    "First matching element's text, stripped."
    if el is None:
        return ""
    found = el.find(path)
    if found is None:
        return ""
    return "".join(found.itertext()).strip()


def _manifest_and_spine(opf):
    """
    Get the manifest (id -> item dict), the spine idrefs in order, and
    the spine's toc attribute (EPUB 2 ncx id).
    """
    manifest = {}
    mel = opf.find("{*}manifest")
    if mel is not None:
        for item in mel.findall("{*}item"):
            item_id = item.get("id")
            if item_id:
                manifest[item_id] = {
                    "href": item.get("href") or "",
                    "media_type": item.get("media-type") or "",
                    "properties": (item.get("properties") or "").split(),
                }

    spine = opf.find("{*}spine")
    refs = []
    toc = None
    if spine is not None:
        refs = [i.get("idref") for i in spine.findall("{*}itemref") if i.get("idref")]
        toc = spine.get("toc")
    return manifest, refs, toc


def _toc_map(
    zf, manifest, spine_toc, opf_dir, zip_names
):  # pylint: disable=too-many-locals
    """
    Map zip-relative content path -> [(fragment, title), ...] in TOC
    order.  Prefers the EPUB 3 nav document; falls back to the EPUB 2
    toc.ncx.
    """
    raw = None
    nav_item = next((it for it in manifest.values() if "nav" in it["properties"]), None)
    if nav_item is not None:
        nav_path = _resolve_href(nav_item["href"], opf_dir)
        nav_dir = posixpath.dirname(nav_path)
        content = _read_zip(zf, nav_path, zip_names)
        if content is not None:
            entries = _nav_entries(content)
            if entries is not None:
                raw = [(href, title, nav_dir) for href, title in entries]
    if raw is None:
        ncx_item = manifest.get(spine_toc) or next(
            (it for it in manifest.values() if it["media_type"] == _NCX_MEDIA_TYPE),
            None,
        )
        if ncx_item is not None:
            ncx_path = _resolve_href(ncx_item["href"], opf_dir)
            ncx_dir = posixpath.dirname(ncx_path)
            content = _read_zip(zf, ncx_path, zip_names)
            if content is not None:
                entries = _ncx_entries(content)
                if entries is not None:
                    raw = [(href, title, ncx_dir) for href, title in entries]

    toc_map = {}
    if not raw:
        return toc_map
    for href, title, base_dir in raw:
        href = (href or "").strip()
        if not href or href.startswith(("http:", "https:", "mailto:")):
            continue
        path, _, frag = href.partition("#")
        if not path:
            continue
        key = posixpath.normpath(_resolve_href(path, base_dir))
        toc_map.setdefault(key, []).append((frag, title.strip()))
    return toc_map


def _nav_entries(content):
    "EPUB 3 nav document -> [(href, title)]; None when no links."
    soup = BeautifulSoup(content, "html.parser")
    _prep_soup(soup)
    ret = []
    for a in soup.find_all("a"):
        href = a.get("href")
        text = _flattened_text(a)
        if href and text:
            ret.append((href, text))
    return ret or None


def _ncx_entries(content):
    "EPUB 2 toc.ncx -> [(src, title)]; None when unparsable or empty."
    try:
        ncx = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return None
    ret = []
    for navpoint in ncx.findall(".//{*}navPoint"):
        label = navpoint.find("{*}navLabel/{*}text")
        content_el = navpoint.find("{*}content")
        if label is None or content_el is None:
            continue
        text = "".join(label.itertext()).strip()
        src = content_el.get("src")
        if text and src:
            ret.append((src, text))
    return ret or None


def _chapters(
    zf, manifest, spine_refs, opf_dir, zip_names, toc_map
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    "Extract a chapter per readable spine content document."
    chapters = []
    for idref in spine_refs:
        item = manifest.get(idref)
        if item is None or "nav" in item["properties"]:
            continue
        if item["media_type"] not in _CONTENT_MEDIA_TYPES:
            continue
        zip_path = posixpath.normpath(_resolve_href(item["href"], opf_dir))
        content = _read_zip(zf, zip_path, zip_names)
        if content is None:
            continue
        soup = BeautifulSoup(content, "html.parser")
        _prep_soup(soup)
        text = _extract_text(soup)
        if not text:
            # Covers and blank pages produce no text; they cannot be
            # imported (the parser needs content), so skip them.
            continue
        frag = (item["href"] or "").partition("#")[2]
        title = _chapter_title(soup, zip_path, frag, toc_map, len(chapters))
        chapters.append(EpubChapter(len(chapters), title, text))
    return chapters


def _prep_soup(soup):
    """
    Remove nodes that never contribute to the extracted text: scripts,
    styles, and ruby annotations (rt = furigana reading, rp = fallback
    parentheses).  Dropping rt/rp here keeps the readings out of the
    text regardless of the bs4 version.
    """
    for tag in soup(_SKIP_TAGS + ["rt", "rp"]):
        tag.decompose()


def _chapter_title(soup, zip_path, frag, toc_map, position):
    """
    Chapter title: the matching TOC entry (the fragment-specific one
    when the spine href carries one), else the first h1-h3, else
    'Chapter N'.
    """
    entries = toc_map.get(zip_path) or []
    if frag:
        for entry_frag, title in entries:
            if entry_frag == frag and title:
                return title
    if entries and entries[0][1]:
        return entries[0][1]
    for tag in ("h1", "h2", "h3"):
        el = soup.find(tag)
        if el is not None:
            text = _flattened_text(el)
            if text:
                return text
    return f"Chapter {position + 1}"


def _resolve_href(href, base_dir):
    "Resolve a manifest/TOC href to a zip-root-relative path."
    path = unquote(href or "")
    if base_dir:
        path = posixpath.join(base_dir, path)
    return posixpath.normpath(path)


def _read_zip(zf, path, zip_names):
    """
    Read a zip member, tolerating hrefs that are (or are not) url-
    encoded.  Returns None when not found.
    """
    unquoted = unquote(path)
    for candidate in (path, unquoted, quote(unquoted)):
        actual = zip_names.get(posixpath.normpath(candidate))
        if actual is not None:
            try:
                return zf.read(actual)
            except Exception:  # pylint: disable=broad-except
                return None
    return None


def _extract_text(soup):
    """
    Extract the text of an xhtml document, one line per block element
    (paragraph structure), with inline content concatenated as it
    would render.
    """
    root = soup.body or soup
    lines = []
    _collect_lines(root, lines)
    lines = [line for line in (ln.strip() for ln in lines) if line]
    return "\n".join(lines)


def _collect_lines(el, lines):
    "Walk the tree, appending one line per block-level text element."
    pending = []

    def flush():
        if pending:
            text = _ASCII_WS_RE.sub(" ", "".join(pending)).strip()
            if text:
                lines.append(text)
            pending.clear()

    for child in el.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            # Kept as-is: any whitespace the source carries between
            # inline elements is meaningful (word gaps in western
            # text) and gets collapsed only once, at flush time.
            pending.append(str(child))
        elif isinstance(child, Tag):
            name = (child.name or "").lower()
            if name in _SKIP_TAGS:
                continue
            if name in _TEXT_TAGS:
                flush()
                text = _flattened_text(child)
                if text:
                    lines.append(text)
            elif name in _CONTAINER_TAGS:
                flush()
                _collect_lines(child, lines)
            else:
                if name == "br":
                    pending.append("\n")
                    continue
                text = child.get_text("")
                if text:
                    pending.append(text)
    flush()
