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
                      the `.epub-body` scope element (plus
                      data-epub-viewport on fixed-layout chapters).  A
                      reflowing chapter over PAGE_CHAR_BUDGET is written
                      as several pages instead: ch/003.html,
                      ch/003-002.html, ch/003-003.html, ...  (page 1 keeps
                      the plain name, which is what BkEpubPath stores)
    lute.css          every stylesheet of the book, scoped to `.epub-body`,
                      text colours on unpainted blocks dropped (dark-theme
                      safety) -- the theme-following variant
    lute-orig.css     the same styles with every colour kept, for the
                      paper-surface "original look" / night modes
    chapters.json     each chapter's archive path + fixed-layout viewport,
                      so lute-orig.css can be regenerated from the
                      extracted originals later (ensure_orig_css)
    <assets...>       images / fonts / everything else, at their
                      original archive paths so relative references from
                      the chapter markup still resolve

The stored plain text is re-extracted from the *sanitised* markup, so
the server token stream and the browser DOM describe the same character
sequence -- that is what lets the front end align tokens to text nodes
by plain stream matching.
"""

import json
import os
import posixpath
import re
import shutil
import tempfile
import uuid
import zipfile
from io import BytesIO
from urllib.parse import quote, unquote

from bs4 import BeautifulSoup, NavigableString

from lute.book.epub_css import scope_css
from lute.book.epub_parser import _extract_text, _prep_soup  # internal reuse
from lute.book import epub_paths
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

# Characters per reader-mode page.
#
# A page is loaded and tokenised whole: the chapter's markup, one span
# per token, plus the JSON token stream the front end aligns against the
# DOM.  So a page's *size* is what the browser pays for -- open cost,
# layout cost, and the cost of every reflow it takes part in.  Some
# conversions put an entire book in one spine item, which used to mean
# one page of tens of thousands of spans.
#
# Characters are a decent proxy for that cost whatever the script: a
# CJK page ends up with about this many spans, a Latin page with fewer
# (same characters, fewer words).  2000 is roughly a screenful of
# Japanese and two of English.
PAGE_CHAR_BUDGET = 2000

# How many single-child wrappers a page split will look through before it
# gives up and keeps the oversized node whole.  Real books nest two or
# three (`div.calibre > div.chapter > div.section`).
_MAX_UNWRAP_DEPTH = 8

# What one picture counts as, in the same units as PAGE_CHAR_BUDGET.
_PICTURE_WEIGHT = 200

# Elements whose content is only ever other blocks, so a page boundary
# inside them is invisible to the book's own CSS.  Anything else -- a
# <p> of running text, a <figure> with its caption, a table -- is a
# single unit: cutting it would change how the book renders, so an
# oversized one keeps its page to itself.
_PAGE_CONTAINERS = frozenset(
    (
        "body",
        "div",
        "section",
        "article",
        "aside",
        "header",
        "footer",
        "main",
        "nav",
        "ul",
        "ol",
        "dl",
        "blockquote",
    )
)

# Inline content: a container holding these is a paragraph in disguise
# (converters do wrap a run of text in a div), not a group of blocks.
# Cutting such a div would cut a text run in half, so it keeps its page.
# The consequence for pictures is deliberate: an image counts towards a
# page only when it sits inside a block of its own (`<p><img/></p>`,
# `div.page > img`, which is how converters emit them).  A bare <img>
# child of a wrapper is left alone with its neighbours.
_INLINE_NAMES = frozenset(
    (
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
        "br",
        "cite",
        "code",
        "data",
        "dfn",
        "em",
        "i",
        "img",
        "kbd",
        "mark",
        "q",
        "rp",
        "rt",
        "ruby",
        "s",
        "samp",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "time",
        "u",
        "var",
        "wbr",
    )
)


class EpubExtract:
    "The extracted package: where it lives and what each chapter became."

    def __init__(self, root, chapters, css_path, orig_css_path=None):
        # All paths are relative to EPUB_STATIC_ROOT (the data
        # directory's static root), which is what BkEpubPath stores.
        self.root = root
        self.css_path = css_path
        # The keep-colours variant of css_path, for the "original look"
        # render modes (None when it could not be produced).
        self.orig_css_path = orig_css_path
        # chapter index -> {"html": page 1's rel path, "text": sanitised
        # chapter text, "viewport": ..., "pages": [{"html", "text"}]}
        self.chapters = chapters


def css_path_for(chapter_html_path, filename="lute.css"):
    """
    The scoped stylesheet that belongs to an extracted chapter
    ("epub/<uuid>/ch/003.html" -> "epub/<uuid>/lute.css").
    """
    return posixpath.join(
        posixpath.dirname(posixpath.dirname(chapter_html_path)), filename
    )


def orig_css_path_for(chapter_html_path):
    """
    The keep-colours variant of css_path_for
    ("epub/<uuid>/ch/003.html" -> "epub/<uuid>/lute-orig.css").
    """
    return css_path_for(chapter_html_path, "lute-orig.css")


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

    Two stylesheets are produced: lute.css (theme-following, text
    colours on unpainted blocks dropped) and lute-orig.css (every
    colour kept, for the paper-surface "original look" modes).
    chapters.json records each chapter's archive path and fixed-layout
    viewport so the original-look variant can be regenerated later
    without the .epub file (see ensure_orig_css).
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

            def read_member_text(path):
                return _decode(_read_member(zf, name_map, path))

            chapter_paths = [c.path for c in chapters]
            css_text = _collect_css(read_member_text, chapter_paths, root_rel, False)
            orig_text = _collect_css(read_member_text, chapter_paths, root_rel, True)

            extracted = {}
            manifest = []
            for chapter in chapters:
                result = _write_chapter(zf, name_map, chapter, root_abs, root_rel)
                extracted[chapter.index] = result
                manifest.append(
                    {
                        "index": chapter.index,
                        "path": chapter.path,
                        "viewport": result["viewport"],
                    }
                )
            css_rel = f"{root_rel}/lute.css"
            _write_text(root_abs, "lute.css", css_text)
            orig_rel = None
            if orig_text.strip():
                orig_rel = f"{root_rel}/lute-orig.css"
                _write_text(root_abs, "lute-orig.css", orig_text)
            _write_text(root_abs, "chapters.json", json.dumps(manifest, indent=2))
        except Exception:
            shutil.rmtree(root_abs, ignore_errors=True)
            raise

    return EpubExtract(root_rel, extracted, css_rel, orig_rel)


def _write_text(root_abs, name, text):
    path = os.path.join(root_abs, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    _make_readable(path)


# The extracted package is served straight off the data directory by
# nginx, as a different user, so its files have to be world-readable.
# Files created the normal way get this from the umask; the atomic
# writers (tempfile.mkstemp) create 0600 and would 403 in the browser.
_DATA_FILE_MODE = 0o644


def _make_readable(path):
    """
    Give an extracted file `_DATA_FILE_MODE`, unless it already has it.

    A 0600 stylesheet is a 403 through nginx: the chapter then renders
    with none of the book's styling, which looks like a broken import.
    """
    try:
        if os.stat(path).st_mode & 0o444 != 0o444:
            os.chmod(path, _DATA_FILE_MODE)
    except OSError:
        pass


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


def _collect_css(read_text, chapter_paths, root_rel, keep_colors):
    """
    Gather every stylesheet the chapters reference, in document order,
    and scope the lot into one text block.

    `read_text(archive_rel_path)` returns the file's text or None --
    from the zip at import time, from the extracted package on disk for
    the lazy regeneration path.  Linked stylesheets win over inline
    <style> in the order they appear in each document.
    """
    parts = []
    seen = set()
    for chapter_path in chapter_paths:
        raw = read_text(chapter_path)
        if raw is None:
            continue
        soup = BeautifulSoup(raw, "html.parser")
        for node in soup.find_all(["link", "style"]):
            if node.name == "style":
                text = node.get_text("") or ""
                if text.strip():
                    parts.append(
                        scope_css(
                            text,
                            posixpath.dirname(chapter_path),
                            f"/static/{root_rel}",
                            keep_colors=keep_colors,
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
                posixpath.join(posixpath.dirname(chapter_path), unquote(href))
            )
            if css_path in seen:
                continue
            seen.add(css_path)
            css_raw = read_text(css_path)
            if css_raw is None:
                continue
            parts.append(
                scope_css(
                    css_raw,
                    posixpath.dirname(css_path),
                    f"/static/{root_rel}",
                    keep_colors=keep_colors,
                )
            )
    return "\n\n".join(p for p in parts if p.strip())


def _write_chapter(zf, name_map, chapter, root_abs, root_rel):
    """
    Sanitise one chapter, write it under ch/, and return

        {"html": first page's path, "text": whole chapter's text,
         "viewport": fixed-layout viewport or None,
         "pages": [{"html": path, "text": text}, ...]}

    A reflowing chapter over PAGE_CHAR_BUDGET is written as several
    pages (ch/003.html, ch/003-002.html, ...) so the reading screen never
    has to build a chapter-sized DOM; `html` is page 1's file, which is
    what BkEpubPath stores, and the reader derives the rest from it (see
    epub_paths.chapter_file).  Everything else -- a short chapter, a
    fixed-layout page box -- is written exactly as before, one page.
    """
    raw = _read_member(zf, name_map, chapter.path)
    if raw is None:
        raise BookImportException(
            f"Chapter {chapter.index + 1} is missing from the EPUB file."
        )
    soup = BeautifulSoup(_decode(raw), "html.parser")
    # The viewport has to be read before _prep_soup drops <head>, where
    # the meta lives.
    viewport = _fixed_viewport(soup)
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
    classes = "epub-body" + ((" " + body_classes) if body_classes else "")
    if viewport:
        classes += " epub-fixed"
    wrapper = (
        f'<div class="{classes}"'
        + (f' data-epub-viewport="{viewport}"' if viewport else "")
        + ">"
    )

    layout = [((), _significant_children(body))]
    if not viewport:
        layout = _page_layout(body, PAGE_CHAR_BUDGET)

    if len(layout) <= 1:
        html = wrapper + body.decode_contents() + "</div>"
        rel = f"ch/{chapter.index:03d}.html"
        _write_text(root_abs, rel, html)
        return {
            "html": f"{root_rel}/{rel}",
            "text": text,
            "viewport": viewport,
            "pages": [{"html": f"{root_rel}/{rel}", "text": text}],
        }

    pages = []
    for number, (wrappers, nodes) in enumerate(layout, start=1):
        html = _page_html(classes, viewport, wrappers, nodes)
        rel = (
            f"ch/{chapter.index:03d}.html"
            if number == 1
            else f"ch/{chapter.index:03d}-{number:03d}.html"
        )
        _write_text(root_abs, rel, html)
        # The page's own text, extracted from its own markup: the server
        # tokenises *this* text for the page, and the front end aligns
        # those tokens against this markup.  Anything else (slicing the
        # chapter's text, reusing the whole chapter's) would desync the
        # two and cost the page its word spans.
        page_soup = BeautifulSoup(html, "html.parser")
        pages.append({"html": f"{root_rel}/{rel}", "text": _extract_text(page_soup)})

    return {
        "html": pages[0]["html"],
        "text": text,
        "viewport": viewport,
        "pages": pages,
    }


def _significant_children(container):
    "The container's child nodes that carry content: elements and non-blank text."
    out = []
    for child in container.children:
        if isinstance(child, NavigableString):
            if str(child).strip():
                out.append(child)
        else:
            out.append(child)
    return out


def _node_len(node):
    "Characters the node contributes, which is what PAGE_CHAR_BUDGET counts."
    if isinstance(node, NavigableString):
        return len(str(node))
    # A picture costs a page about what a paragraph does, even though it
    # contributes no characters -- a reflowing chapter can be nothing but
    # images (a converted comic), and counting only text would leave one
    # page holding all of them.
    pictures = len(node.find_all(("img", "svg", "video")))
    return len(node.get_text()) + _PICTURE_WEIGHT * pictures


def _can_unwrap(node, budget):
    """
    True when `node` only groups blocks, so it can be split.

    A wrapper of blocks (the `div.calibre` / `div.chapter` every
    converter adds) is exactly what has to go; a paragraph, a table or a
    figure is one unit and must keep its page.  A container with no
    block-level child is a paragraph in disguise (a text run, a line of
    spans and anchors): cutting between those would cut text in half, so
    it keeps its page.
    """
    if node.name not in _PAGE_CONTAINERS:
        return False
    if _node_len(node) <= budget:
        return False
    return any(
        not isinstance(child, NavigableString) and child.name not in _INLINE_NAMES
        for child in _significant_children(node)
    )


def _page_layout(container, budget, wrappers=(), depth=0):
    """
    Lay `container`'s content out as pages.

    Each page is (wrappers, nodes): the chain of grouping elements it has
    to be re-created inside, and the nodes it holds.  An oversized
    wrapper is descended into *wherever it sits*, so a body of
    `div.hidden + div.main > p*` splits at the paragraphs inside
    `div.main` instead of putting the whole chapter on one page.
    """
    pages = []
    current = []
    size = 0

    def flush():
        nonlocal current, size
        if current:
            pages.append((wrappers, list(current)))
        current = []
        size = 0

    for node in _significant_children(container):
        if depth < _MAX_UNWRAP_DEPTH and _can_unwrap(node, budget):
            flush()
            pages.extend(_page_layout(node, budget, wrappers + (node,), depth + 1))
            continue
        length = _node_len(node)
        if current and size + length > budget:
            flush()
        current.append(node)
        size += length
    flush()
    return _merge_pages(pages, budget)


def _merge_pages(pages, budget):
    """
    Join neighbouring pages that can share one.

    A descent leaves a partly-filled page behind and, more importantly,
    leaves the converters' empty furniture (`<div style="display:none">`
    anchors, stray `<br/>` blocks) as pages of their own -- a blank
    screen the reader has to turn past.  Those are folded into a
    neighbour; two pages with the same wrapper chain are joined when they
    still fit together.
    """
    out = []
    for page_wrappers, nodes in pages:
        if not out:
            out.append((page_wrappers, list(nodes)))
            continue
        prev_wrappers, prev_nodes = out[-1]
        if not _has_content(prev_nodes):
            # The furniture before a wrapper: it has no page of its own,
            # and it has to go *inside* the wrapper that follows, so the
            # merged page takes that wrapper chain.
            out[-1] = (page_wrappers, prev_nodes + list(nodes))
            continue
        if not _has_content(nodes):
            prev_nodes.extend(nodes)
            continue
        same_chain = prev_wrappers == page_wrappers
        if same_chain and _nodes_len(prev_nodes) + _nodes_len(nodes) <= budget:
            prev_nodes.extend(nodes)
            continue
        out.append((page_wrappers, list(nodes)))
    return out


def _nodes_len(nodes):
    "What a page's nodes cost, in PAGE_CHAR_BUDGET units."
    return sum(_node_len(n) for n in nodes)


def _has_content(nodes):
    "True if a page made of these nodes shows something: text or a picture."
    for node in nodes:
        if isinstance(node, NavigableString):
            if str(node).strip():
                return True
        elif node.get_text().strip() or node.find(("img", "svg", "video")):
            return True
    return False


def _page_html(classes, viewport, wrappers, nodes):
    "One page: the .epub-body wrapper, the grouping chain, then its nodes."
    soup = BeautifulSoup("", "html.parser")
    root = soup.new_tag("div")
    root["class"] = classes.split(" ")
    if viewport:
        root["data-epub-viewport"] = viewport
    soup.append(root)

    target = root
    for wrapper in wrappers:
        clone = soup.new_tag(wrapper.name)
        for key, value in wrapper.attrs.items():
            clone[key] = " ".join(value) if isinstance(value, list) else value
        target.append(clone)
        target = clone
    for node in nodes:
        target.append(node.extract())
    return str(root)


def split_page_file(html, budget=None):
    """
    Split an already-extracted chapter file into page fragments.

    Returns a list of (html, text) in reading order: the file itself when
    it fits on one page, otherwise one entry per page.  The import path
    splits before writing (see _write_chapter); this is for the files
    that were written before that existed -- an old chapter is one file
    holding the whole thing, and this is what the epub_resplit backfill
    turns into pages (it has the extracted markup, not the .epub).

    A fixed-layout chapter is never split: it is one pixel-sized page
    box, and the reading screen fits the whole thing.
    """
    budget = PAGE_CHAR_BUDGET if budget is None else budget
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find(class_="epub-body")
    if root is None:
        return [(html, _extract_text(soup))]
    if root.get("data-epub-viewport"):
        return [(html, _extract_text(soup))]

    classes = " ".join(root.get("class") or ["epub-body"])
    layout = _page_layout(root, budget)
    if len(layout) <= 1:
        return [(html, _extract_text(soup))]
    pages = []
    for wrappers, nodes in layout:
        page_html = _page_html(classes, None, wrappers, nodes)
        pages.append(
            (page_html, _extract_text(BeautifulSoup(page_html, "html.parser")))
        )
    return pages


def write_page_files(root_abs, stored_path, pages, start=1):
    """
    Write `pages` (from split_page_file) beside `stored_path`.

    `stored_path` is the chapter's stored path ("epub/<uuid>/ch/003.html");
    page 1 overwrites it, so BkEpubPath keeps pointing at the chapter,
    and the rest become siblings ("ch/003-002.html", ...).  `start` is
    the number of the first entry, for writing a slice of the pages
    (the backfill writes the extra pages first and page 1 last, so an
    interrupted run cannot leave page 1 holding only its first split).
    Returns the file names written, in order.
    """
    name = posixpath.basename((stored_path or "").strip("/"))
    stem, ext = posixpath.splitext(name)
    if not stem:
        raise ValueError(f"Cannot derive page names from '{stored_path}'.")
    # "epub/<uuid>/ch/003.html" -> "ch": the pages go beside the chapter.
    parts = (stored_path or "").strip("/").split("/")
    rel_dir = "/".join(parts[2:-1]) or "ch"
    written = []
    for offset, (page_html, _text) in enumerate(pages):
        number = start + offset
        page_name = name if number == 1 else f"{stem}-{number:03d}{ext}"
        _write_text(root_abs, posixpath.join(rel_dir, page_name), page_html)
        written.append(page_name)
    return written


# A viewport meta with both dimensions as plain pixel numbers marks a
# fixed-layout page (EPUB FXL); `device-width` / percentages are the
# reflowable spellings and never match.
_VIEWPORT_RE = re.compile(
    r"width\s*=\s*(\d+)\s*,\s*height\s*=\s*(\d+)\s*(?:,|$)"
    r"|height\s*=\s*(\d+)\s*,\s*width\s*=\s*(\d+)\s*(?:,|$)",
    re.I,
)


def _fixed_viewport(soup):
    '"WxH" for a fixed-layout chapter document, else None.'
    for meta in soup.find_all("meta"):
        if (meta.get("name") or "").strip().lower() != "viewport":
            continue
        content = meta.get("content") or ""
        m = _VIEWPORT_RE.search(content)
        if not m:
            continue
        w = m.group(1) or m.group(4)
        h = m.group(2) or m.group(3)
        if int(w) > 0 and int(h) > 0:
            return f"{w}x{h}"
    return None


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


# ---------------------------------------------------------------------------
# Lazy regeneration of lute-orig.css for packages extracted before the
# original-look modes existed: the whole archive (original stylesheets
# included) is on disk, so the variant can be rebuilt from it without
# the .epub file.
# ---------------------------------------------------------------------------

_CONTENT_EXTS = (".xhtml", ".html", ".htm")


def ensure_orig_css(app, stored_epub_path):
    """
    Return the static-relative path of the package's keep-colours
    stylesheet ("epub/<uuid>/lute-orig.css"), creating it from the
    extracted originals when the package only has lute.css.

    Returns None when the package cannot be found; a failed rebuild
    (unreadable manifest, no content files) still writes an empty
    lute-orig.css so the attempt is not repeated on every page render.
    """
    chapter_file = epub_paths.located(app, stored_epub_path)
    if chapter_file is None:
        return None
    root_abs = os.path.dirname(os.path.dirname(chapter_file))
    orig_abs = os.path.join(root_abs, "lute-orig.css")
    if os.path.isfile(orig_abs):
        # Repairs 0600 files left by an earlier build of this function.
        _make_readable(orig_abs)
        return posixpath.join("epub", os.path.basename(root_abs), "lute-orig.css")

    root_rel = f"epub/{os.path.basename(root_abs)}"
    chapter_paths = _manifest_chapter_paths(root_abs)
    if chapter_paths is None:
        chapter_paths = _scanned_chapter_paths(root_abs)

    orig_text = ""
    if chapter_paths:
        orig_text = _collect_css(
            lambda p: _read_extracted(root_abs, p), chapter_paths, root_rel, True
        )

    # Atomic: a concurrent page render may be doing the same rebuild.
    # mkstemp makes the file 0600, and nginx (another user) has to serve
    # it -- hence the chmod before it goes live.
    fd, tmp = tempfile.mkstemp(dir=root_abs, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(orig_text)
        os.chmod(tmp, _DATA_FILE_MODE)
        os.replace(tmp, orig_abs)
    except Exception:
        os.unlink(tmp)
        raise
    return posixpath.join("epub", os.path.basename(root_abs), "lute-orig.css")


def _read_extracted(root_abs, path):
    "Text of one file of the extracted package, by archive-relative path."
    rel = _safe_path(path)
    if rel is None:
        return None
    target = os.path.join(root_abs, *rel.split("/"))
    if not os.path.isfile(target):
        return None
    try:
        with open(target, "rb") as f:
            return _decode(f.read())
    except OSError:
        return None


def _manifest_chapter_paths(root_abs):
    "Source paths recorded by chapters.json, or None when there is none."
    manifest = os.path.join(root_abs, "chapters.json")
    if not os.path.isfile(manifest):
        return None
    try:
        with open(manifest, encoding="utf-8") as f:
            entries = json.load(f)
        return [e["path"] for e in entries if isinstance(e, dict) and e.get("path")]
    except (ValueError, KeyError, TypeError, OSError):
        return None


def _scanned_chapter_paths(root_abs):
    """
    Every original content document of the package, for the oldest
    packages that predate chapters.json.  The sanitised copies under
    ch/ have rewritten URLs and must not be used as collection roots.
    """
    out = []
    for dirpath, dirnames, filenames in os.walk(root_abs):
        if os.path.basename(dirpath) == "ch":
            dirnames[:] = []
            continue
        for name in filenames:
            if name.lower().endswith(_CONTENT_EXTS):
                out.append(
                    os.path.relpath(os.path.join(dirpath, name), root_abs).replace(
                        os.sep, "/"
                    )
                )
    return sorted(out)
