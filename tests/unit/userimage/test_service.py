"""
Thumbnail service tests.
"""

import logging
from pathlib import Path

from PIL import Image

from lute.userimage.service import (
    DEFAULT_IMAGE_MAX_EDGE,
    THUMBNAIL_MAX_EDGE,
    current_image_max_edge,
    ensure_thumbnail,
    parse_image_max_edge,
    resized_size,
    save_image,
    thumbnail_filename,
    thumbnail_path_for,
    thumbnail_url,
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


## Word card image size tier.


def _image_bytes(size=(3000, 2000), fmt="JPEG"):
    "Encoded test image in memory."
    import io

    buffer = io.BytesIO()
    Image.new("RGB", size, (20, 110, 190)).save(buffer, fmt)
    return buffer.getvalue()


def test_parse_image_max_edge_accepts_the_four_choices():
    "The dropdown values map to a cap, or None for keep-original."
    assert parse_image_max_edge("400") == 400
    assert parse_image_max_edge("800") == 800
    assert parse_image_max_edge("1200") == 1200
    assert parse_image_max_edge("original") is None


def test_parse_image_max_edge_defaults_to_400():
    "Missing or unknown values fall back to the default."
    assert parse_image_max_edge(None) == DEFAULT_IMAGE_MAX_EDGE == 400
    assert parse_image_max_edge("") == 400
    assert parse_image_max_edge("bogus") == 400
    assert parse_image_max_edge("600") == 400


def test_current_image_max_edge_defaults_to_400():
    "A fresh settings dict has no key yet and still means 400."
    from lute.settings.current import current_settings

    current_settings().pop("word_image_max_edge", None)
    assert current_image_max_edge() == 400


def test_resized_size_caps_the_longest_edge():
    "Landscape, portrait and small images all keep their aspect ratio."
    assert resized_size((3000, 2000), 400) == (400, 267)
    assert resized_size((2000, 3000), 400) == (267, 400)
    assert resized_size((3000, 2000), 800) == (800, 533)
    assert resized_size((3000, 2000), 1200) == (1200, 800)


def test_resized_size_never_enlarges():
    "A small image is left alone."
    assert resized_size((300, 200), 400) == (300, 200)
    assert resized_size((300, 200), 1200) == (300, 200)


def test_save_image_tier_writes_one_webp_and_no_thumbnail(tmp_path):
    "A tier save is a single file; the thumbnail path is the file itself."
    big = {"400": (400, 267), "800": (800, 533), "1200": (1200, 800)}
    for tier, expected in big.items():
        directory = tmp_path / tier
        directory.mkdir()
        filename, path = save_image(str(directory), "a", _image_bytes(), int(tier))
        stored = sorted(p.name for p in directory.iterdir())
        assert stored == [filename], f"{tier} must store exactly one file"
        assert filename == "a_wc.webp"
        with Image.open(path) as image:
            assert image.format == "WEBP"
            assert image.size == expected
        assert thumbnail_path_for(path) == path
        assert thumbnail_url(1, filename) == f"/userimages/1/{filename}"


def test_save_image_small_source_is_not_enlarged_or_grown(tmp_path):
    "A 300x200 JPEG keeps its pixels and does not get bigger."
    data = _image_bytes((300, 200))
    for tier in (400, 800, 1200):
        directory = tmp_path / str(tier)
        directory.mkdir()
        filename, path = save_image(str(directory), "a", data, tier)
        assert len(list(directory.iterdir())) == 1
        with Image.open(path) as image:
            assert image.size == (300, 200)
        assert Path(path).stat().st_size <= len(data)
        assert "_wc" in filename


def test_save_image_keep_original_writes_two_files(tmp_path):
    "Keep original keeps the historical original plus thumbnail layout."
    data = _image_bytes((1200, 800))
    filename, path = save_image(str(tmp_path), "a", data, None)
    assert filename == "a.jpeg"
    assert Path(path).read_bytes() == data
    thumb = thumbnail_path_for(path)
    assert thumb != path
    assert thumbnail_filename(filename) == "a_thumb.webp"
    assert ensure_thumbnail(path) == thumb
    assert len(list(tmp_path.iterdir())) == 2


def test_legacy_thumbnail_naming_is_unchanged():
    "Images saved before the setting existed still derive _thumb.webp."
    assert thumbnail_path_for("/tmp/1/a.jpeg") == "/tmp/1/a_thumb.webp"
    assert thumbnail_url(1, "a.jpeg") == "/userimages/1/thumb/a.jpeg"
