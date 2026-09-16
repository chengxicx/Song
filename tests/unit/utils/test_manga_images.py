"""
Tests for lute.utils.manga_images, which pairs Mokuro pages with the
image files on disk when a .mokuro carries no img_path.
"""

import os

from lute.utils.manga_images import (
    image_path_for_page,
    natural_key,
    sorted_image_paths,
)


def _touch(root, rel):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x")
    return path


def test_natural_key_orders_numbers_by_value():
    names = ["10.jpg", "2.jpg", "1.jpg", "20.jpg"]
    assert sorted(names, key=natural_key) == [
        "1.jpg",
        "2.jpg",
        "10.jpg",
        "20.jpg",
    ]


def test_sorted_image_paths_ignores_non_images_and_lowercases(tmp_path):
    root = str(tmp_path)
    _touch(root, "vol/002.WEBP")
    _touch(root, "vol/001.webp")
    _touch(root, "vol/vol.mokuro")
    _touch(root, "vol/vol.html")

    assert sorted_image_paths(root) == ["vol/001.webp", "vol/002.WEBP"]


def test_sorted_image_paths_missing_dir_is_empty(tmp_path):
    assert sorted_image_paths(str(tmp_path / "nope")) == []
    assert sorted_image_paths(None) == []


def test_image_path_for_page_matches_by_position(tmp_path):
    "Pages map to images in natural-sorted order, like mokuro does."
    root = str(tmp_path)
    for n in ("1", "2", "10"):
        _touch(root, f"vol/{n}.webp")

    assert image_path_for_page(root, 0) == "vol/1.webp"
    assert image_path_for_page(root, 1) == "vol/2.webp"
    assert image_path_for_page(root, 2) == "vol/10.webp"
    assert image_path_for_page(root, 3) is None
    assert image_path_for_page(root, -1) is None


def test_image_path_for_page_prefers_volume_subdir(tmp_path):
    "A volume/ subdirectory is used when it holds the requested page."
    root = str(tmp_path)
    _touch(root, "vol1/001.webp")
    _touch(root, "vol2/001.webp")
    _touch(root, "vol2/002.webp")

    assert image_path_for_page(root, 0, "vol2") == "vol2/001.webp"
    assert image_path_for_page(root, 1, "vol2") == "vol2/002.webp"


def test_image_path_for_page_falls_back_when_volume_is_short(tmp_path):
    """
    When the volume directory does not reach the requested page, the
    whole tree is searched instead of giving up.
    """
    root = str(tmp_path)
    _touch(root, "vol/001.webp")
    _touch(root, "extra/002.webp")

    result = image_path_for_page(root, 1, "vol")
    assert result is not None
    assert os.path.isfile(os.path.join(root, result))
