"""
The grammar panel's narrowing must actually hide what it filters out.

`.grammar-analysis-panel .grammar-item--filtered-out` and its group sibling
are the only thing standing between "the panel is narrowed to one sentence"
and "the panel still paints the level headers of the cards it just hid".

A bare `.grammar-group--filtered-out { display: none }` does NOT do that: the
plain `.grammar-group { display: flex }` further down styles.css is a
single-class selector too, so at equal specificity the later declaration wins.
That is not a hypothesis -- it shipped, and the empty state painted three bare
level headers ("A1 1", "A2 1", "B1 1") above its message while the cards
themselves disappeared, because no later rule sets `display` on
`.grammar-item`.

Nothing else in the suite can see this: the JS tests assert the *class* is
toggled (it was), and a class is not a rendering.  So the CSS is checked here,
by specificity rather than by string, so a future reorder cannot quietly
reintroduce it.
"""

import os
import re

import lute

_CSS = os.path.join(os.path.dirname(lute.__file__), "static", "css", "styles.css")

# A rule whose selector list mentions one of these modifiers has to out-rank
# the base class it modifies.
_MODIFIERS = ("--filtered-out",)

_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)


def _rules(css):
    "Every (selector, declarations) pair in the file."
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    return [(sel.strip(), body) for sel, body in _RULE.findall(stripped)]


def _specificity(selector):
    "Class + attribute + pseudo-class count; enough to compare class-only rules."
    return len(re.findall(r"\.[A-Za-z0-9_-]+", selector))


def _css():
    with open(_CSS, encoding="utf-8") as f:
        return f.read()


def test_the_hiding_modifiers_set_display_none():
    css = _css()
    found = 0
    for selector, body in _rules(css):
        if not any(m in selector for m in _MODIFIERS):
            continue
        found += 1
        assert "display: none" in body, f"{selector} does not hide anything"
    assert found, "no --filtered-out rule found; did the modifier get renamed?"


def test_each_hiding_modifier_out_specifies_its_base_class():
    """
    `.x--filtered-out` alone is (0,1,0), the same as `.x`, so it loses to any
    `.x { display: ... }` declared later in the file.  Every hiding modifier
    must therefore carry a scope (or otherwise be at least (0,2,0)).
    """
    for selector, _body in _rules(_css()):
        for part in selector.split(","):
            part = part.strip()
            if not any(m in part for m in _MODIFIERS):
                continue
            assert _specificity(part) >= 2, (
                f"{part!r} is a single-class selector, so a later "
                f"`{part.split('--')[0]} {{ display: ... }}` beats it"
            )
