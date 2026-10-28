"""
The side page arrows must not move when the pointer reaches them.

Both arrows are `position: fixed` and their geometry is written from JS
(`_layout_side_nav`, read/index.html) out of their own offsetWidth /
offsetHeight.  Two things used to break that, and both surface as "the
arrow jitters when I hover it" in scrolling mode -- reader-mode EPUB
chapters, and media books:

  * `.hide` is `display: none`, so a hidden arrow measured 0, and any
    layout pass that landed on it wrote a left/top half an arrow off;
    the next pass snapped it back.  Reaching for the arrow is what
    changes its visibility, which is why it looked like a hover bug;
  * `_updateScrollModeArrows` runs from the scroll listener, re-tested a
    single 60px bottom threshold, and re-laid the arrows out on every
    frame once the reader was at the bottom -- a forced synchronous
    reflow of a whole chapter (one span per token) to write back the
    value the CSS already had.

The CSS half is pinned by specificity, the JS half by reading the two
function bodies out of the template (brace-matched, not offset-based, so
the guards survive the file growing).
"""

import os
import re

import lute

_LUTE = os.path.dirname(lute.__file__)
_CSS = os.path.join(_LUTE, "static", "css", "styles.css")
_TEMPLATE = os.path.join(_LUTE, "templates", "read", "index.html")

_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _rules(css):
    "Every (selector, declarations) pair in the file, comments removed."
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    return [(sel.strip(), body) for sel, body in _RULE.findall(stripped)]


def _specificity(selector):
    "Class + attribute + pseudo-class count; enough for class-only rules."
    return len(re.findall(r"\.[A-Za-z0-9_-]+", selector))


def _function_body(src, name):
    """The source between the braces of `function name(...) { ... }`."""
    marker = "function " + name + "("
    assert marker in src, name + " is gone from the template"
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


def _hidden_rule():
    "The `.side-page-nav.hide` rule: (selector, declarations)."
    found = [
        (sel, body) for sel, body in _rules(_read(_CSS)) if ".side-page-nav.hide" in sel
    ]
    assert found, (
        "no `.side-page-nav.hide` rule: the arrows fall back to the generic "
        "`display: none` and measure 0 while hidden"
    )
    return found[0]


def test_the_hidden_arrow_keeps_its_box_measurable():
    """
    `display: none` makes offsetWidth / offsetHeight read 0, which is the
    value the layout code writes into left / top.
    """
    _selector, body = _hidden_rule()
    assert "display: none" not in body, (
        "hiding the arrow with display:none makes its own offsetWidth 0, "
        "and _layout_side_nav writes that 0 into the arrow's position"
    )
    assert "visibility: hidden" in body
    assert "pointer-events: none" in body, "a hidden arrow must not be hoverable"
    assert "opacity: 0" in body


def test_the_hidden_state_wins_over_hide_and_over_hover():
    """
    `.side-page-nav.hide` is (0,2,0) -- the same as `.side-page-nav:hover`
    -- so it has to come after the hover rule in the file to keep a hidden
    arrow from lighting up.  It out-ranks the generic `.hide` (0,1,0) on
    its own.
    """
    selector, _body = _hidden_rule()
    assert _specificity(selector) >= 2, selector + " loses to `.hide`"
    css = _read(_CSS)
    assert css.index(".side-page-nav:hover") < css.index(".side-page-nav.hide")


def test_the_bottom_threshold_has_hysteresis():
    """
    A single 60px test flickers: an EPUB chapter grows as images and fonts
    arrive, and the last page adds the end-of-book strip.
    """
    body = _function_body(_read(_TEMPLATE), "_updateScrollModeArrows")
    src = _read(_TEMPLATE)
    enter = re.search(r"const ARROWS_ENTER_PX = (\d+);", src)
    leave = re.search(r"const ARROWS_EXIT_PX = (\d+);", src)
    assert enter and leave, "the two thresholds are gone"
    assert int(leave.group(1)) > int(enter.group(1))
    assert "ARROWS_ENTER_PX" in body and "ARROWS_EXIT_PX" in body
    assert "_arrows_at_bottom ? ARROWS_EXIT_PX : ARROWS_ENTER_PX" in body


def test_the_arrows_are_laid_out_only_when_they_appear():
    "Not on every scroll frame: the arrows are viewport-fixed."
    body = _function_body(_read(_TEMPLATE), "_updateScrollModeArrows")
    assert "if (changed && atBottom) _layout_side_nav();" in body
    assert "if (atBottom) _layout_side_nav();" not in body, (
        "an unguarded layout call here runs on every scroll frame at the "
        "bottom of the chapter"
    )


def test_scrolling_mode_leaves_the_vertical_centre_to_css():
    """
    In scrolling mode the arrows belong at the viewport centre, which is
    what `top: 50%` + `translateY(-50%)` already says.  Writing it from JS
    is what forced the per-frame reflow (and, while hidden, wrote a top
    half an arrow too low).
    """
    body = _function_body(_read(_TEMPLATE), "_layout_side_nav")
    assert "innerHeight" not in body, (
        "the vertical centre is the CSS default in scrolling mode; do not "
        "compute it from the viewport height"
    )
    assert 'navSideNext.style.top = "";' in body
    assert 'navSideNext.style.transform = "";' in body
