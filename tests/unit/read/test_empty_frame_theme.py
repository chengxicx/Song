"""
The "nothing selected" frames must not paint a white block.

The right pane's word frame starts on /read/empty and falls back to it when
the term form closes (clicking a word twice).  An empty response body leaves
the iframe canvas at the browser default, which is white: in the dark theme
that painted the whole top of the right pane white.  The page now renders a
themed (otherwise empty) document, and the reader no longer points the frame
at about:blank, which is white for the same reason.
"""

import os
import re

import lute

_TEMPLATES = os.path.join(os.path.dirname(lute.__file__), "templates")
_CSS = os.path.join(os.path.dirname(lute.__file__), "static", "css", "styles.css")


def test_empty_page_paints_the_current_theme(client):
    body = client.get("/read/empty").get_data(as_text=True)
    assert body.lstrip().lower().startswith("<!doctype html")
    # The theme stylesheet paints html/body with --background-color; without
    # it the frame falls back to the browser's white canvas.
    assert "/theme/current" in body


def test_reading_wordframe_does_not_start_on_about_blank():
    path = os.path.join(_TEMPLATES, "read", "index.html")
    with open(path, encoding="utf-8") as handle:
        html = handle.read()
    assert "about:blank" not in html
    assert "url_for('read.empty')" in html


def test_right_pane_background_is_theme_aware():
    "The narrow-layout pane must not hard-code a white card."
    with open(_CSS, encoding="utf-8") as handle:
        css = handle.read()
    rules = re.findall(r"div#read_pane_right\s*\{[^}]*\}", css)
    assert rules
    for rule in rules:
        compact = rule.replace(" ", "")
        assert "background-color:white" not in compact
        assert "background-color:#fff;" not in compact
        assert "background-color:#ffffff;" not in compact
