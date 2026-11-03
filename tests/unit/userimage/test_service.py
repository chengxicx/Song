"""
Thumbnail service tests.
"""

import logging

from PIL import Image

from lute.userimage.service import (
    THUMBNAIL_MAX_EDGE,
    ensure_thumbnail,
    thumbnail_filename,
    thumbnail_path_for,
)


def _write_image(path, fmt="JPEG", size=(1600, 900)):
    image = Image.new("RGB", size, (30, 120, 200))
    image.save(path, fmt)
    return path


def test_thumbnail_filename_derivation():
    "The thumbnail name is derived from the original, no DB column needed."
    assert thumbnail_filename("20260101_123456789_abcd1234.jpeg") == (
        "20260101_123456789_abcd1234_thumb.webp"
    )
    assert thumbnail_filename("noextension") == "noextension_thumb.webp"
    assert thumbnail_path_for("/tmp/1/a.jpeg") == "/tmp/1/a_thumb.webp"


def test_generates_webp_thumbnail_capped_at_400px(tmp_path):
    "Longest edge is 400 and the output is WebP."
    original = _write_image(tmp_path / "a.jpeg")
    thumb = ensure_thumbnail(str(original))
    assert thumb is not None

    with Image.open(thumb) as image:
        assert image.format == "WEBP"
        assert max(image.size) == THUMBNAIL_MAX_EDGE
        assert image.size == (400, 225)


def test_png_content_in_jpeg_named_file(tmp_path):
    "Pillow decodes by content, not by the .jpeg file name."
    original = _write_image(tmp_path / "a.jpeg", "PNG", (900, 1200))
    thumb = ensure_thumbnail(str(original))
    assert thumb is not None
    with Image.open(thumb) as image:
        assert image.format == "WEBP"
        assert max(image.size) <= THUMBNAIL_MAX_EDGE


def test_webp_content_in_jpeg_named_file(tmp_path):
    "WebP sources are also decoded correctly."
    original = _write_image(tmp_path / "a.jpeg", "WEBP")
    thumb = ensure_thumbnail(str(original))
    assert thumb is not None
    with Image.open(thumb) as image:
        assert image.format == "WEBP"


def test_corrupt_image_returns_none_and_logs(tmp_path, caplog):
    "A bad file must not block the caller; it just has no thumbnail."
    original = tmp_path / "bad.jpeg"
    original.write_bytes(b"not an image")
    caplog.set_level(logging.WARNING, logger="lute.userimage.service")

    thumb = ensure_thumbnail(str(original))
    assert thumb is None
    assert not (tmp_path / "bad_thumb.webp").exists()
    assert "Could not decode image for thumbnail" in caplog.text


def test_missing_original_returns_none(tmp_path):
    "Nothing to generate from."
    assert ensure_thumbnail(str(tmp_path / "missing.jpeg")) is None


def test_existing_thumbnail_is_reused(tmp_path):
    "First-request generation is cached on disk."
    original = _write_image(tmp_path / "a.jpeg")
    thumb = ensure_thumbnail(str(original))
    assert thumb is not None

    first_bytes = thumbnail_path_for(str(original))
    with open(first_bytes, "rb") as f:
        before = f.read()
    again = ensure_thumbnail(str(original))
    assert again == thumb
    with open(first_bytes, "rb") as f:
        assert f.read() == before
