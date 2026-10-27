"""
The pane split must never be measured on the narrow layout.

`applyInitialPaneSizes` (lute/static/js/resize.js) writes the reader's
remembered split inline, and it derives the default from the *computed*
widths.  On the narrow layout (@media max-width: 980px) the left pane
fills the viewport and the right pane is a full-width bottom sheet, so
`getTextWidthPercentage()` reads 100 there -- writing that back inline
bakes "left 100%, right 0%" into the document.  Inline styles outrank the
stylesheet, so the first time the viewport is wide again (un-maximising
the window, or the reader dropping a flash notice and with it the
document scrollbar, which shifts the 980px breakpoint) the left pane
stays full width and the dictionary pane collapses to zero width at the
right edge: the divider jumps to the far right and the pane is unusable.

So while the reader is narrow the widths belong to the media query, and
crossing the breakpoint has to re-apply them.

The browser-level behaviour is pinned by
tests/js/run_pane_split_harness.py; this is the cheap CI-side guard.
"""

import os
import re

import lute

_JS = os.path.join(os.path.dirname(lute.__file__), "static", "js", "resize.js")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _function_body(src, name):
    "The source between the braces of `function name(...) { ... }`."
    marker = "function " + name + "("
    assert marker in src, name + " is gone from resize.js"
    start = src.index(marker)
    brace = src.index("{", start)
    depth = 0
    for i in range(brace, len(src)):
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[brace + 1 : i]
    raise AssertionError(name + " never closes its body")


def _branches():
    "The narrow and desktop halves of applyInitialPaneSizes."
    body = _function_body(_read(_JS), "applyInitialPaneSizes")
    narrow, sep, desktop = body.partition("} else {")
    assert sep, "applyInitialPaneSizes no longer branches on the layout"
    return narrow, desktop


def test_the_narrow_layout_is_detected():
    narrow, _desktop = _branches()
    msg = "the widths must not be measured while the narrow layout owns them"
    assert "mediaTablet.matches" in narrow, msg


def test_narrow_writes_no_inline_width():
    """
    An inline width survives the breakpoint: the desktop rules can no
    longer win, and the pane stays collapsed.
    """
    narrow, desktop = _branches()
    assert ".style.width =" not in narrow, (
        "writing the split inline on the narrow layout bakes "
        "'left 100% / right 0%' into the document"
    )
    assert "getTextWidthPercentage" not in narrow
    assert ".style.width =" in desktop, "the desktop split must still be written"


def test_narrow_drops_a_split_measured_on_desktop():
    "Entering the narrow layout must clear the desktop inline widths."
    narrow, _desktop = _branches()
    assert re.search(r"removeProperty\(\s*[\"']width[\"']\s*\)", narrow), (
        "a desktop split left inline keeps the panes at 50/50 inside the "
        "narrow layout's !important rules"
    )
    msg = "both panes carry an inline width from the desktop layout"
    assert narrow.count("removeProperty") >= 2, msg


def test_a_remembered_split_cannot_collapse_a_pane():
    "The stored value goes through the same 25..95 clamp resizeCol uses."
    _narrow, desktop = _branches()
    assert "clamp(" in desktop, "an out-of-range textWidth collapses a pane"


def test_crossing_the_breakpoint_reapplies_the_split():
    src = _read(_JS)
    assert re.search(
        r"mediaTablet\.addEventListener\(\s*[\"']change[\"']\s*,\s*"
        r"applyInitialPaneSizes\s*\)",
        src,
    ), "the breakpoint is crossed by resizing the window and by a scrollbar; re-apply"
