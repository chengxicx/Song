"""Tests for lute.utils.static_assets cache-busting helpers."""

import os
import re

import pytest

import lute
from lute.utils.static_assets import file_hash, make_vstatic, make_vstatic_js


@pytest.fixture(name="tmp_static")
def fixture_tmp_static(tmp_path):
    """A fake static folder with one file."""
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "lib.css").write_text("body { color: red; }", encoding="utf-8")
    return tmp_path


def test_file_hash_is_stable_and_content_sensitive(tmp_static):
    h1 = file_hash(str(tmp_static), "vendor/lib.css")
    assert h1 != file_hash(str(tmp_static), "nope/missing.css")

    # Same content -> same hash.
    assert file_hash(str(tmp_static), "vendor/lib.css") == h1

    # Changed content -> changed hash (must clear the lru_cache).
    (tmp_static / "vendor" / "lib.css").write_text(
        "body { color: blue; }", encoding="utf-8"
    )
    file_hash.cache_clear()
    assert file_hash(str(tmp_static), "vendor/lib.css") != h1


def test_file_hash_missing_file_is_safe(tmp_static):
    assert file_hash(str(tmp_static), "nope/missing.css") == "0"
    assert file_hash(None, "vendor/lib.css") == "0"


def test_make_vstatic_builds_versioned_url(tmp_static):
    def fake_url_for(filename):
        return f"/static/{filename}"

    vstatic = make_vstatic(str(tmp_static), fake_url_for)
    url = vstatic("vendor/lib.css")
    assert url.startswith("/static/vendor/lib.css?v=")
    assert (
        url
        == f"/static/vendor/lib.css?v={file_hash(str(tmp_static), 'vendor/lib.css')}"
    )


def test_make_vstatic_js_hashes_the_file_the_route_serves(tmp_static):
    """
    The never_cache route maps /static/js/never_cache/<file> onto
    static/js/<file>, so that is the file whose hash must be used.
    """
    js_dir = tmp_static / "js"
    js_dir.mkdir()
    (js_dir / "lute.js").write_text("// lute", encoding="utf-8")

    def fake_url_for(filename):
        return f"/static/js/never_cache/{filename}"

    vstatic_js = make_vstatic_js(str(tmp_static), fake_url_for)
    url = vstatic_js("lute.js")
    assert (
        url
        == f"/static/js/never_cache/lute.js?v={file_hash(str(tmp_static), 'js/lute.js')}"
    )

    # Editing the file changes the URL, with no version bump anywhere.
    (js_dir / "lute.js").write_text("// lute v2", encoding="utf-8")
    file_hash.cache_clear()
    assert vstatic_js("lute.js") != url


def test_make_vstatic_js_missing_file_is_safe(tmp_static):
    """A typo in a template must not blow up the page render."""
    vstatic_js = make_vstatic_js(
        str(tmp_static), lambda f: f"/static/js/never_cache/{f}"
    )
    assert vstatic_js("nope.js").endswith("?v=0")


# --------------------------------------------------------------------------
# Guardrail: the helpers above are only useful if templates actually use
# them.  These assets are served `Cache-Control: immutable` for a year
# (verified against production), so a hand-written version string -- or one
# tied to the package version -- silently pins clients to the old file.
# --------------------------------------------------------------------------

_TEMPLATES_DIR = os.path.join(os.path.dirname(lute.__file__), "templates")

# Version sources retired in favour of the content hash.  Both used to
# require a human to remember a bump.
_RETIRED_VERSIONS = ("lute_version", "asset_cache_bust")


def _template_files():
    for root, _dirs, files in os.walk(_TEMPLATES_DIR):
        for name in sorted(files):
            if name.endswith(".html"):
                yield os.path.join(root, name)


def _strip_comments(text):
    """
    Blank out HTML and Jinja comments, keeping line/column positions.

    Comments legitimately *describe* ?v= (as the ones in base.html do),
    and they are not asset references.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("<!--", i):
            end = text.find("-->", i)
            end = n if end == -1 else end + 3
        elif text.startswith("{#", i):
            end = text.find("#}", i)
            end = n if end == -1 else end + 2
        else:
            out.append(text[i])
            i += 1
            continue
        out.append(re.sub(r"[^\n]", " ", text[i:end]))
        i = end
    return "".join(out)


def test_templates_do_not_hardcode_asset_versions():
    """
    Every ?v= in a template must come from a dynamic expression; the
    static-asset ones must originate in vstatic()/vstatic_js().
    """
    offenders = []
    for path in _template_files():
        rel = os.path.relpath(path, _TEMPLATES_DIR)
        with open(path, encoding="utf-8") as f:
            source = _strip_comments(f.read())
        for lineno, line in enumerate(source.splitlines(), 1):
            # Only asset references; a YouTube URL in a placeholder
            # attribute is not one.
            if "src=" not in line and "href=" not in line:
                continue
            for match in re.finditer(r"\?v=(\S*)", line):
                value = match.group(1)
                if not value.startswith("{{"):
                    offenders.append(f"{rel}:{lineno}: literal ?v={value}")
                elif any(name in value for name in _RETIRED_VERSIONS):
                    offenders.append(f"{rel}:{lineno}: retired ?v={value}")
    assert not offenders, (
        "Versioned asset URLs must use vstatic() / vstatic_js(), which derive"
        " ?v= from the file's content hash:\n  " + "\n  ".join(offenders)
    )


def test_first_party_js_is_referenced_through_vstatic_js():
    """
    Lute's own JS lives in static/js/ and is served immutable, so it must
    carry a content hash.  Catches a reference with no ?v= at all, which
    is worse than a stale one -- it can never be busted.
    """
    offenders = []
    for path in _template_files():
        rel = os.path.relpath(path, _TEMPLATES_DIR)
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if "src=" in line and re.search(r"\.js[\"']", line):
                    if "vstatic" not in line:
                        offenders.append(f"{rel}:{lineno}: {line.strip()[:90]}")
    assert not offenders, (
        "JS assets must be loaded via vstatic() / vstatic_js() so the URL"
        " carries a content hash:\n  " + "\n  ".join(offenders)
    )
