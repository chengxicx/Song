"""
Extract an EPUB for the reader-mode import.

A plain-text EPUB import only keeps the flattened chapter text.  Reader
mode keeps the chapter's *markup*: the reading screen loads the chapter
HTML, injects word spans into its text nodes, and the chapter therefore
looks like the book (images, tables, verse, inline styling) while still
getting Lute's lookups, status colours, multi-word selection and TTS.

This module writes everything the reading screen needs under
`<EPUB_STATIC_ROOT>/epub/<uuid>/` -- `<datapath>/static/epub/<uuid>/`
on a real install, i.e. beside the databases rather than inside the
code tree, so a data backup carries it:

    ch/<index>.html   one chapter, sanitised, URLs rewritten, wrapped in
                      the `.epub-body` scope element
    lute.css          every stylesheet of the book, scoped to `.epub-body`
    <assets...>       images / fonts / everything else, at their
                      original archive paths so relative references from
                      the chapter markup still resolve

The stored plain text is re-extracted from the *sanitised* markup, so
the server token stream and the browser DOM describe the same character
sequence -- that is what lets the front end align tokens to text nodes
by plain stream matching.
"""

import os
import posixpath
import re
import shutil
import uuid
import zipfile
from io import BytesIO
from urllib.parse import quote, unquote

from bs4 import BeautifulSoup

from lute.book.epub_css import scope_css
from lute.book.epub_parser import _extract_text, _prep_soup  # internal reuse
from lute.book.service import BookImportException

# Never written to disk: the container description and the mimetype
# stub carry nothing the reader can use.
_SKIP_MEMBERS = ("mimetype", "META-INF/")

# Tags removed from the chapter entirely.  script/style are already
# gone by the time this runs (the parser drops them); the rest are
# active content that would either execute inside the page or need a
# plugin: a book never legitimately needs them, and they are the usual
# carrier for tracking / injection in the wild.
_DROP_TAGS = (
    "script",
    "noscript",
    "iframe",
    "frame",
    "frameset",
    "object",
    "embed",
    "applet",
    "base",
    "form",
    "input",
    "button",
    "textarea",
    "select",
    "link",
    "style",
)

# Attributes whose value is a URL, and the base they resolve against
# (always the chapter document's directory).
_URL_ATTRS = ("src", "href", "poster", "data", "xlink:href")

_MAX_TOTAL_BYTES = 512 * 1024 * 1024  # extracted-content cap (zip bomb guard)
_SAFE_SCHEMES = ("http:", "https:", "mailto:", "tel:", "data:", "javascript:")
_INLINE_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.S | re.I)


class EpubExtract:
    "The extracted package: where it lives and what each chapter became."

    def __init__(self, root, chapters, css_path):
        # All paths are relative to EPUB_STATIC_ROOT (the data
        # directory's static root), which is what BkEpubPath stores.
        self.root = root
        self.css_path = css_path
        # chapter index -> {"html": rel path, "text": sanitised text}
        self.chapters = chapters


def css_path_for(chapter_html_path):
    """
    The scoped stylesheet that belongs to an extracted chapter
    ("epub/<uuid>/ch/003.html" -> "epub/<uuid>/lute.css").
    """
    return posixpath.join(
        posixpath.dirname(posixpath.dirname(chapter_html_path)), "lute.css"
    )


def extract_epub_package(data, chapters, static_root):
    """
    Write `data` (the whole .epub file) into <static_root>/epub/<uuid>/.

    `static_root` is the data directory's static root
    (EPUB_STATIC_ROOT), never the application's own static folder: the
    extracted package is user data and has to sit where a backup looks.
    `chapters` are the EpubChapter objects being imported (they carry
    the archive path of their content document).  Returns an
    EpubExtract.  Raises BookImportException when the archive cannot be
    opened.
    """
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except Exception as e:  # pylint: disable=broad-except
        raise BookImportException(
            f"Could not read EPUB file (error: {e})", cause=e
        ) from e

    with zf:
        root_rel = f"epub/{uuid.uuid4().hex}"
        root_abs = os.path.join(static_root, root_rel)
        os.makedirs(os.path.join(root_abs, "ch"), exist_ok=True)
        try:
            name_map = _extract_members(zf, root_abs)
            css_text, css_name = _collect_css(zf, name_map, chapters, root_rel)
            extracted = {}
            for chapter in chapters:
                html_rel, text = _write_chapter(
                    zf, name_map, chapter, root_abs, root_rel
                )
                extracted[chapter.index] = {"html": html_rel, "text": text}
            css_rel = f"{root_rel}/{css_name}"
            with open(os.path.join(root_abs, css_name), "w", encoding="utf-8") as f:
                f.write(css_text)
        except Exception:
            shutil.rmtree(root_abs, ignore_errors=True)
            raise

    return EpubExtract(root_rel, extracted, css_rel)


def _safe_path(name):
    """
    Normalise an archive member name for writing under the target dir.

    Returns None for entries that would escape it (absolute paths, `..`,
    drive letters) -- those are how a malicious archive writes outside
    its own directory.
    """
    path = (name or "").replace("\\", "/")
    if not path or path.startswith("/"):
        return None
    path = posixpath.normpath(path)
    if path.startswith("../") or path == ".." or re.match(r"^[A-Za-z]:", path):
        return None
    return path


def _extract_members(zf, root_abs):
    """
    Write every file of the archive to disk, skipping the container
    metadata.  Returns {normalised name: actual zip name} so later
    lookups tolerate encoding differences.
    """
    name_map = {}
    total = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        actual = info.filename
        rel = _safe_path(actual)
        if rel is None:
            continue
        if any(rel == s or rel.startswith(s) for s in _SKIP_MEMBERS):
            continue
        total += info.file_size
        if total > _MAX_TOTAL_BYTES:
            raise BookImportException(
                "This EPUB is too large to import (over 512 MB when unpacked)."
            )
        target = os.path.join(root_abs, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        name_map[posixpath.normpath(rel)] = actual
    return name_map


def _read_member(zf, name_map, path):
    "Read a member by normalised archive path; tolerates url-encoding."
    for candidate in (path, unquote(path), quote(unquote(path))):
        key = posixpath.normpath(candidate)
        actual = name_map.get(key)
        if actual is not None:
            try:
                return zf.read(actual)
            except Exception:  # pylint: disable=broad-except
                return None
    return None


def _decode(raw):
    "Decode chapter / stylesheet bytes (EPUB says UTF-8, real files vary)."
    if raw is None:
        return ""
    for enc in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _collect_css(zf, name_map, chapters, root_rel):
    """
    Gather every stylesheet the chapters reference, in document order,
    and scope the lot into one file.

    Returns (css_text, css_filename).  Linked stylesheets win over
    inline <style> in the order they appear in each document.
    """
    parts = []
    seen = set()
    for chapter in chapters:
        raw = _read_member(zf, name_map, chapter.path)
        if raw is None:
            continue
        soup = BeautifulSoup(_decode(raw), "html.parser")
        for node in soup.find_all(["link", "style"]):
            if node.name == "style":
                text = node.get_text("") or ""
                if text.strip():
                    parts.append(
                        scope_css(
                            text,
                            posixpath.dirname(chapter.path),
                            f"/static/{root_rel}",
                        )
                    )
                continue
            rel = " ".join(node.get("rel") or []).lower()
            href = (node.get("href") or "").strip()
            if "stylesheet" not in rel or not href:
                continue
            if href.startswith(("http:", "https:", "//", "data:")):
                continue
            css_path = posixpath.normpath(
                posixpath.join(posixpath.dirname(chapter.path), unquote(href))
            )
            if css_path in seen:
                continue
            seen.add(css_path)
            css_raw = _read_member(zf, name_map, css_path)
            if css_raw is None:
                continue
            parts.append(
                scope_css(
                    _decode(css_raw),
                    posixpath.dirname(css_path),
                    f"/static/{root_rel}",
                )
            )
    return "\n\n".join(p for p in parts if p.strip()), "lute.css"


def _write_chapter(zf, name_map, chapter, root_abs, root_rel):
    """
    Sanitise one chapter, write it to ch/<index>.html, and return
    (relative path, sanitised text).
    """
    raw = _read_member(zf, name_map, chapter.path)
    if raw is None:
        raise BookImportException(
            f"Chapter {chapter.index + 1} is missing from the EPUB file."
        )
    soup = BeautifulSoup(_decode(raw), "html.parser")
    # Drops script/style/head/template/svg/iframe/noscript and the ruby
    # annotations (rt/rp): readings are not part of the text the parser
    # tokenises, and leaving them in the DOM would desync the token
    # stream from the rendered characters.
    _prep_soup(soup)
    body = soup.body or soup

    for tag in body.find_all(_DROP_TAGS):
        tag.decompose()
    _sanitise_attributes(body, chapter.path, root_rel)

    text = _extract_text(soup)
    body_classes = _body_classes(soup)
    wrapper = f'<div class="epub-body{(" " + body_classes) if body_classes else ""}">'
    html = wrapper + body.decode_contents() + "</div>"

    rel = f"ch/{chapter.index:03d}.html"
    with open(os.path.join(root_abs, *rel.split("/")), "w", encoding="utf-8") as f:
        f.write(html)
    return f"{root_rel}/{rel}", text


def _body_classes(soup):
    "Classes of <html> and <body>, so scoped `body.foo` rules still apply."
    classes = []
    for tag in (soup.find("html"), soup.find("body")):
        if tag is not None:
            classes.extend(tag.get("class") or [])
    seen = set()
    out = []
    for c in classes:
        c = (c or "").strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return " ".join(out)


def _sanitise_attributes(body, chapter_path, root_rel):
    """
    Drop executable attribute values and rewrite archive URLs.

    `on*` handlers, `javascript:` URLs and CSS expressions are removed;
    a book that ships them is either broken or hostile.  URL attributes
    are resolved against the chapter's own directory and pointed at the
    extracted copy under /static, so the inlined markup needs no
    <base> element (which would also capture Lute's own relative URLs).
    """
    base_dir = posixpath.dirname(chapter_path)
    for tag in body.find_all(True):
        for attr in list(tag.attrs):
            value = tag.attrs[attr]
            low_attr = attr.lower()
            if low_attr.startswith("on") or low_attr in ("contenteditable", "srcdoc"):
                del tag.attrs[attr]
                continue
            values = value if isinstance(value, list) else [value]
            cleaned = []
            for v in values:
                if not isinstance(v, str):
                    cleaned.append(v)
                    continue
                lowered = v.strip().lower()
                if lowered.startswith("javascript:") or "expression(" in lowered:
                    v = ""
                if attr == "srcset" and v:
                    v = _rewrite_srcset(v, base_dir, root_rel)
                elif attr in _URL_ATTRS and v:
                    v = _rewrite_url(v, base_dir, root_rel)
                elif attr == "style" and v:
                    v = _INLINE_URL_RE.sub(
                        lambda m: _rewrite_inline_url(m, base_dir, root_rel), v
                    )
                if v != "":
                    cleaned.append(v)
            if not cleaned:
                del tag.attrs[attr]
            elif isinstance(value, list):
                tag.attrs[attr] = cleaned
            else:
                tag.attrs[attr] = cleaned[0]


def _rewrite_url(url, base_dir, root_rel):
    "Point one archive-relative URL at the extracted copy."
    raw = url.strip()
    if not raw or raw.startswith(("#", "/", "//")):
        return raw
    if raw.lower().startswith(_SAFE_SCHEMES):
        return raw
    path, sep, fragment = raw.partition("#")
    if not path:
        return raw
    target = posixpath.normpath(posixpath.join(base_dir, unquote(path)))
    if target.startswith("../"):
        return raw
    return f"/static/{root_rel}/{target}{sep}{fragment}"


def _rewrite_srcset(value, base_dir, root_rel):
    "Rewrite each candidate of a srcset list."
    out = []
    for candidate in value.split(","):
        bits = candidate.strip().split()
        if not bits:
            continue
        bits[0] = _rewrite_url(bits[0], base_dir, root_rel)
        out.append(" ".join(bits))
    return ", ".join(out)


def _rewrite_inline_url(match, base_dir, root_rel):
    "url(...) inside a style attribute."
    raw = (match.group(2) or "").strip()
    if not raw:
        return match.group(0)
    return f'url("{_rewrite_url(raw, base_dir, root_rel)}")'
