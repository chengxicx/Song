"""Tests for lute.utils.static_assets cache-busting helpers."""

import os

import pytest

from lute.utils.static_assets import file_hash, make_vstatic


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
    assert url == f"/static/vendor/lib.css?v={file_hash(str(tmp_static), 'vendor/lib.css')}"
