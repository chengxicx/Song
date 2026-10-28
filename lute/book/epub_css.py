"""
Scope an EPUB's CSS to the reading pane.

Reader-mode EPUB books keep the chapter's own markup, so they also keep
its stylesheets -- otherwise the chapter loses the layout that makes it
worth reading in the original form (columns, indents, image floats,
verse spacing ...).  Those stylesheets are written for a whole document
(`html`, `body`, `:root`, bare element selectors), and dropping them
verbatim into Lute's reading page would restyle Lute itself.

So every rule is rewritten once, at import time:

  p { ... }            ->  .epub-body p { ... }
  html body            ->  .epub-body
  :root                ->  .epub-body
  body.dark p          ->  .epub-body.dark p
  @media (...) { ... } ->  recursed
  @font-face           ->  kept, url() rewritten
  @page / @import      ->  dropped
  position: fixed      ->  position: absolute (contained by the scope)
  color: #000          ->  dropped, unless the block paints a background

`url(...)` references are resolved against the stylesheet's own location
inside the archive and rewritten to the extracted copy under
/static/epub/<uuid>/..., so the imports need no other URL mapping.

No third-party CSS parser is used: the transform only needs rule
boundaries and selectors, and a small scanner that is quote- and
paren-aware is enough for real-world EPUB stylesheets.  Anything it
fails to understand is dropped rather than emitted unscoped, because an
unscoped rule is what breaks the reader.
"""

import posixpath
import re

# At-rules whose block body is a list of rules that still need scoping.
_NESTED_AT_RULES = ("@media", "@supports", "@layer", "@container", "@document")

# At-rules kept verbatim (their bodies are declarations, not selectors).
# @font-face is the only one an EPUB actually needs; @keyframes is kept
# so animated decorations still animate.
_KEPT_AT_RULES = ("@font-face", "@keyframes", "-webkit-keyframes", "@-webkit-keyframes")

# At-rules dropped outright: @page describes a printed page, @import
# would load an unscoped stylesheet at runtime, and @charset /
# @namespace have no meaning in the reading pane.
_DROPPED_AT_RULES = ("@page", "@import", "@charset", "@namespace")

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.S | re.I)
_FIXED_RE = re.compile(r"(position\s*:\s*)fixed", re.I)
_WS_RE = re.compile(r"\s+")

# A leading selector run that must be replaced by the scope: html, body,
# :root, optionally with a class/id/attribute suffix (body.dark).
_LEAD_RE = re.compile(r"^(?::root|html|body)((?:\.[\w-]+|#[\w-]+|\[[^\]]*\])*)", re.I)
_COMBINATOR_RE = re.compile(r"^\s*([>+~]?)\s*")


def scope_css(css_text, base_dir, href_prefix, scope=".epub-body", keep_colors=False):
    """
    Scope one stylesheet to `scope`.

    base_dir is the stylesheet's directory inside the archive (e.g.
    "OEBPS/styles"), href_prefix the static URL its file was extracted
    to (e.g. "/static/epub/<uuid>/OEBPS/styles"), so relative url()s can
    be resolved.  Returns CSS text.

    keep_colors=True emits every colour declaration untouched: that is
    the "original look" variant, rendered on the paper surface where the
    book's own palette is readable.  The default (False) drops `color`
    declarations on unpainted blocks (see _drop_unreadable_colors) for
    the theme-following variant.
    """
    css = _COMMENT_RE.sub("", css_text or "")
    out = []
    _emit_rules(
        css, 0, len(css), out, base_dir, href_prefix, scope, keep_colors
    )
    return "\n".join(r for r in out if r.strip())


def _emit_rules(css, start, end, out, base_dir, href_prefix, scope, keep_colors=False):
    "Walk a run of CSS (top level or a nested at-rule body)."
    i = start
    while i < end:
        # Prelude runs to the next '{' or ';' at this level.
        j = _find_rule_end(css, i, end)
        if j is None:
            return
        prelude = css[i:j].strip()
        if not prelude:
            i = j + 1
            continue
        if css[j] == ";":
            # A statement at-rule (@import, @charset, ...).
            if not prelude.lower().startswith(_DROPPED_AT_RULES):
                out.append(_rewrite_urls(prelude, base_dir, href_prefix) + ";")
            i = j + 1
            continue

        block_end = _matching_brace(css, j, end)
        if block_end is None:
            return
        body = css[j + 1 : block_end]
        lowered = prelude.lower()

        if lowered.startswith(_DROPPED_AT_RULES):
            pass
        elif lowered.startswith(_NESTED_AT_RULES):
            inner = []
            _emit_rules(
                body, 0, len(body), inner, base_dir, href_prefix, scope, keep_colors
            )
            if inner:
                out.append(prelude + " {\n" + "\n".join(inner) + "\n}")
        elif lowered.startswith(_KEPT_AT_RULES):
            # Declarations only: rewrite urls (fonts) but keep values.
            out.append(prelude + " {" + _rewrite_declarations(body, base_dir, href_prefix) + "}")
        else:
            selectors = _scope_selectors(prelude, scope)
            decls = (
                _rewrite_declarations(body, base_dir, href_prefix)
                if keep_colors
                else _drop_unreadable_colors(
                    _rewrite_declarations(body, base_dir, href_prefix)
                )
                if selectors
                else None
            )
            # Nothing left after dropping an unreadable colour: emit no
            # rule at all rather than an empty one.
            if selectors and decls and decls.strip():
                out.append(selectors + " {" + decls + "}")
        i = block_end + 1


def _find_rule_end(css, i, end):
    "Index of the first '{' or ';' from i, skipping strings and parens."
    depth = 0
    quote = None
    while i < end:
        ch = css[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and ch in "{;":
            return i
        i += 1
    return None


def _matching_brace(css, open_idx, end):
    "Index of the '}' closing the '{' at open_idx."
    depth = 0
    quote = None
    i = open_idx
    while i < end:
        ch = css[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _scope_selectors(prelude, scope):
    "Scope a selector list; returns None when nothing is left."
    parts = _split_selector_list(prelude)
    scoped = [s for s in (_scope_selector(p, scope) for p in parts) if s]
    return ", ".join(scoped)


def _split_selector_list(text):
    "Split on top-level commas (not inside parens or brackets)."
    parts = []
    buf = []
    depth = 0
    quote = None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _scope_selector(selector, scope):
    """
    Prefix one selector with the scope.

    Leading html/body/:root tokens are *replaced* by the scope (they
    describe the document, which is now the scope element); everything
    else is prefixed as a descendant.
    """
    sel = _WS_RE.sub(" ", selector).strip()
    if not sel:
        return None

    consumed = []
    rest = sel
    while True:
        m = _LEAD_RE.match(rest)
        if not m:
            break
        consumed.append(m.group(1) or "")
        rest = rest[m.end() :]
        comb = _COMBINATOR_RE.match(rest)
        if not comb:
            rest = ""
            break
        rest = rest[comb.end() :]
        if comb.group(1) and comb.group(1) != " ":
            # `html > body` -- the combinator is structural, the
            # remaining selector is now a descendant of the scope.
            pass

    if consumed:
        suffix = "".join(c for c in consumed if c)
        scoped = scope + suffix
        rest = rest.strip()
        if rest:
            scoped += " " + rest
        return scoped

    if sel == scope or sel.startswith(scope + " ") or sel.startswith(scope + "."):
        return sel
    if sel.startswith(scope + ":"):
        return sel
    return f"{scope} {sel}"


def _rewrite_declarations(body, base_dir, href_prefix):
    "Rewrite url()s and pin fixed positioning inside the reading pane."
    out = _rewrite_urls(body, base_dir, href_prefix)
    # `position: fixed` would escape the reading pane and follow the
    # viewport (EPUB readers use it for headers/footers); absolute keeps
    # it inside the scoped container, which is position: relative.
    return _FIXED_RE.sub(r"\1absolute", out)


# Declarations that paint the element's own background.  A block that
# sets one of these is a self-contained surface and keeps its own text
# colour (see _drop_unreadable_colors).
_BG_PROPS = ("background", "background-color", "background-image")
# Values that paint nothing, so they do not make a block self-contained.
_NO_PAINT = ("", "none", "transparent", "initial", "inherit", "unset", "revert")


def _drop_unreadable_colors(body):
    """
    Drop a `color` declaration whose block paints no background.

    Lute's default theme is dark, and print-derived EPUB stylesheets
    routinely say `p { color: #000 }` with the page background left to
    the device -- which here is near-black.  A block that also paints its
    own background is left completely alone: that is the book's own
    surface, and its text colour belongs to it.  Everything else
    inherits the reader's colour, so the chapter stays readable in every
    theme.  The cost is an intentional accent colour (`color: red` on an
    otherwise unpainted element); the alternative is invisible text.

    Only the offending declaration is removed: the rest of the block is
    emitted exactly as written, so the result stays diffable against the
    book's own stylesheet.
    """
    segments = _split_declarations(body)
    if not any(name == "color" for name, _raw in segments):
        return body
    if any(name in _BG_PROPS and _paints(raw) for name, raw in segments):
        return body
    return "".join(raw for name, raw in segments if name != "color").strip()


def _paints(raw):
    "True when this background declaration actually paints something."
    value = raw.partition(":")[2].split("!")[0].rstrip(";").strip().lower()
    return value not in _NO_PAINT


def _split_declarations(body):
    """
    Split a declaration block into (lowercased property, original text).

    Each segment keeps its own whitespace and its trailing semicolon, so
    joining the kept ones reproduces the input byte for byte.
    """
    segments = []
    buf = []
    depth = 0
    quote = None
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == ";" and depth == 0:
            buf.append(ch)
            segments.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    segments.append("".join(buf))
    # A segment without a colon is not a declaration (trailing
    # whitespace, a stray fragment): keep it, under a name no rule
    # matches, so the block is reproduced faithfully.
    return [
        (seg.partition(":")[0].strip().lower() if ":" in seg else "", seg)
        for seg in segments
    ]



def _rewrite_urls(text, base_dir, href_prefix):
    "Resolve relative url() references against the extracted copy."

    def repl(m):
        raw = (m.group(2) or "").strip()
        if not raw or raw.startswith(("data:", "http:", "https:", "//", "/", "#")):
            return m.group(0)
        target = posixpath.normpath(posixpath.join(base_dir, raw))
        if target.startswith("../"):
            return m.group(0)
        return f'url("{href_prefix}/{target}")'

    return _URL_RE.sub(repl, text)
