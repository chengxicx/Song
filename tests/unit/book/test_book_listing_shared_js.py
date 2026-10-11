"""
The home book table's click-filters (tag / new word / type) must survive the
load order of static/js/book-listing-shared.js.

The home page (templates/book/tablelisting.html) loads that shared file with
`defer`, so it runs AFTER the page's inline <script> that defines the real
setTagFilter / setNewWordFilter / setTypeFilter.  A classic script's top-level
function declaration replaces the global binding, so the shared file's
`function setTagFilter(tag) {}` no-op defaults -- there for pages that have no
filter header, i.e. the book-set overview -- silently overwrote the home
page's implementations.  Result: clicking a tag, a new-word badge or a type
chip did nothing at all (no filter pill, no ajax filter), from d586f6a1 on.

The shared file must therefore only *provide* defaults, never install them
unconditionally.  Reproduced here in node, in the real order, against the real
file -- a Python test cannot see script execution order.

Skips when node isn't on PATH (CI's pytest job doesn't guarantee it).
"""

import json
import os
import shutil
import subprocess

import pytest

NODE = shutil.which("node")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_HARNESS = os.path.join(_HERE, "book_listing_js_harness.js")
_MODULE = os.path.join(_ROOT, "lute", "static", "js", "book-listing-shared.js")
_HOME_TEMPLATE = os.path.join(_ROOT, "lute", "templates", "book", "tablelisting.html")

_FILTER_FUNCTIONS = ("setTagFilter", "setNewWordFilter", "setTypeFilter")


def _run(page_defines):
    "Load the shared module after the page's own definitions, and report."
    if NODE is None:
        pytest.skip("node is not on PATH")
    if not os.path.exists(_HARNESS) or not os.path.exists(_MODULE):
        pytest.skip("book-listing JS not found")
    proc = subprocess.run(
        [NODE, _HARNESS, _MODULE],
        input=json.dumps({"page_defines": page_defines}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_page_implementations_survive_the_deferred_shared_script():
    """
    The whole bug in one assertion: with the page's own implementations in
    place, loading the shared file must leave them callable.
    """
    result = _run(page_defines=True)
    assert result["calls"] == [
        ["tag", "probe"],
        ["newword", "HARD"],
        ["type", "text"],
    ], (
        "the shared file replaced the home page's filter setters with its "
        f"no-op defaults: {result}"
    )


def test_the_shared_file_still_provides_defaults():
    """
    The other half: on a page with no filter header (the book-set overview)
    the setters must still exist, or render_new_word's inline
    onclick="setNewWordFilter(...)" throws a ReferenceError on click.
    """
    result = _run(page_defines=False)
    for name in _FILTER_FUNCTIONS:
        assert result["defined"][name] == "function", f"{name} is not callable"
    assert result["error"] is None, result["error"]
    assert result["calls"] == [], "a default must not do anything"


def test_the_home_page_is_still_the_deferred_load_that_needs_the_guard():
    """
    The guard above only matters because of this order.  If the home page
    stops deferring the shared file the test still passes (the guard is
    order-independent), but the two facts belong together, so pin both.
    """
    with open(_HOME_TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    idx = html.index("js/book-listing-shared.js")
    tag = html[html.rindex("<script", 0, idx) : html.index(">", idx)]
    assert "defer" in tag, f"shared listing JS is no longer deferred: {tag!r}"
    assert (
        html.index("function setTagFilter") > idx
    ), "the home page no longer defines setTagFilter after the shared script"
