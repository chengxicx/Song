"""
Shared image catalog: catalog generation, runtime lookup, packaging.

The shared layer is additive: a missing or stale images.json must fall
back to the legacy flat/<lang> layout so an existing install keeps
working.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("lute_picwords")

from lute_picwords import vocab  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS = REPO_ROOT / "plugins" / "lute-picwords" / "tools"


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _save(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"webp")


def test_catalog_generator_marks_cross_language_images_shared(tmp_path, monkeypatch):
    tool = load_tool("build_image_catalog")
    data = tmp_path / "data"
    images = tmp_path / "img"
    status = tmp_path / "image_status.json"
    data.mkdir()
    status.write_text(
        json.dumps({"version": 1, "status": {"y": "needs_review"}}),
        encoding="utf-8",
    )
    _save(images / "shared" / "x.webp")
    _save(images / "ja" / "y.webp")
    (data / "catalog.json").write_text(
        json.dumps(
            {
                "languages": [
                    {"key": "ja", "dataset": "ja.json"},
                    {"key": "ko", "dataset": "ko.json"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (data / "ja.json").write_text(
        json.dumps(
            {
                "words": [
                    {
                        "text": "かばん",
                        "meaning": "包",
                        "meaning_en": "bag",
                        "concept": "bag",
                        "images": [
                            {
                                "id": "x",
                                "source": "word-atlas",
                                "shared": True,
                                "query": "bag",
                            },
                            {
                                "id": "y",
                                "source": "irasutoya",
                                "shared": False,
                                "query": "かばん",
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (data / "ko.json").write_text(
        json.dumps(
            {
                "words": [
                    {
                        "text": "가방",
                        "meaning": "包",
                        "images": [{"id": "x", "source": "word-atlas"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(tool, "DATA_DIR", data)
    monkeypatch.setattr(tool, "IMAGE_DIR", images)
    monkeypatch.setattr(tool, "STATUS_FILE", status)

    payload, errors = tool.build()
    assert not errors
    assert payload["images"]["x"]["status"] == "shared"
    assert payload["images"]["x"]["file"] == "shared/x.webp"
    assert payload["images"]["x"]["languages"] == ["ja", "ko"]
    assert payload["images"]["x"]["labels"] == {"zh": "包", "en": "bag"}
    assert payload["images"]["y"]["status"] == "needs_review"
    assert payload["images"]["y"]["file"] == "ja/y.webp"


def test_catalog_generator_uses_english_as_the_base(tmp_path, monkeypatch):
    tool = load_tool("build_image_catalog")
    data = tmp_path / "data"
    images = tmp_path / "img"
    atlas = tmp_path / "atlas"
    data.mkdir()
    _save(images / "ja" / "x.webp")
    _save(atlas / "public" / "atlas" / "img" / "x.webp")
    (data / "catalog.json").write_text(
        json.dumps(
            {
                "languages": [
                    {"key": "en", "dataset": "en.json"},
                    {"key": "ja", "dataset": "ja.json"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (data / "en.json").write_text(
        json.dumps(
            {
                "words": [
                    {
                        "text": "library",
                        "meaning": "图书馆",
                        "meaning_en": "a place with books",
                        "concept": "library",
                        "images": [{"id": "x", "source": "word-atlas", "shared": True}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (data / "ja.json").write_text(
        json.dumps(
            {
                "words": [
                    {
                        "text": "図書館",
                        "meaning": "图书馆",
                        "concept": "toshokan",
                        "images": [{"id": "x", "source": "word-atlas", "shared": True}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(tool, "DATA_DIR", data)
    monkeypatch.setattr(tool, "IMAGE_DIR", images)
    monkeypatch.setattr(tool, "STATUS_FILE", tmp_path / "status.json")

    payload, errors = tool.build(atlas)
    assert not errors
    entry = payload["images"]["x"]
    assert entry["status"] == "shared"
    assert entry["languages"] == ["en", "ja"]
    assert entry["labels"]["en"] == "library"
    assert entry["labels"]["zh"] == "图书馆"


def test_shared_image_builder_reuses_bundled_and_encodes_atlas(tmp_path, monkeypatch):
    tool = load_tool("build_shared_images")
    data = tmp_path / "data"
    images = tmp_path / "img"
    shared = images / "shared"
    atlas = tmp_path / "atlas"
    data.mkdir()
    images.mkdir()

    from PIL import Image  # pylint: disable=import-outside-toplevel

    def save_webp(path, color):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), color).save(path, "WEBP")

    save_webp(images / "shared-one.webp", (10, 20, 30))
    save_webp(images / "ja" / "ja-one.webp", (40, 50, 60))
    save_webp(atlas / "public" / "atlas" / "img" / "atlas-one.webp", (70, 80, 90))
    (data / "images.json").write_text(
        json.dumps(
            {
                "images": {
                    "shared-one": {"status": "shared", "languages": ["en", "ja"]},
                    "atlas-one": {"status": "shared", "languages": ["en"]},
                    "ja-one": {"status": "ja_only", "languages": ["ja"]},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(tool, "DATA_DIR", data)
    monkeypatch.setattr(tool, "IMAGES_FILE", data / "images.json")
    monkeypatch.setattr(tool, "IMAGE_DIR", images)
    monkeypatch.setattr(tool, "SHARED_DIR", shared)

    stats = tool.build(atlas_root=atlas)
    assert stats["shared"] == 2
    assert stats["reused"] == 1
    assert stats["encoded"] == 1
    assert stats["missing"] == []
    assert (shared / "shared-one.webp").is_file()
    assert (shared / "atlas-one.webp").is_file()
    assert not (shared / "ja-one.webp").exists()

    # A force rebuild must not fail when dest is already a hard link to
    # the bundled source (SameFileError in shutil.copy2).
    again = tool.build(force=True, atlas_root=atlas)
    assert again["missing"] == []
    assert again["shared"] == 2


def test_catalog_generator_reports_missing_files(tmp_path, monkeypatch):
    tool = load_tool("build_image_catalog")
    data = tmp_path / "data"
    data.mkdir()
    (data / "catalog.json").write_text(
        json.dumps({"languages": [{"key": "ja", "dataset": "ja.json"}]}),
        encoding="utf-8",
    )
    (data / "ja.json").write_text(
        json.dumps(
            {
                "words": [
                    {
                        "text": "かばん",
                        "images": [{"id": "gone", "source": "word-atlas"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(tool, "DATA_DIR", data)
    monkeypatch.setattr(tool, "IMAGE_DIR", tmp_path / "img")
    monkeypatch.setattr(tool, "STATUS_FILE", tmp_path / "status.json")

    _, errors = tool.build()
    assert any("missing image file" in error for error in errors)


@pytest.fixture(name="legacy_image_tree")
def fixture_legacy_image_tree(tmp_path, monkeypatch):
    "A bundled tree with the same id in shared/, ja/, and flat layouts."
    base = tmp_path / "static"
    _save(base / "img" / "shared" / "same.webp")
    _save(base / "img" / "ja" / "same.webp")
    _save(base / "img" / "same.webp")
    _save(base / "img" / "ja" / "only-ja.webp")
    monkeypatch.setattr(vocab, "_STATIC_DIR", base)
    monkeypatch.setattr(vocab, "_config_setting", lambda name: "")
    return base


def test_image_lookup_prefers_catalog_then_language_then_shared(
    legacy_image_tree, monkeypatch
):
    base = legacy_image_tree
    monkeypatch.setattr(
        vocab,
        "load_image_catalog",
        lambda: {
            "images": {
                "same": {"file": "shared/same.webp", "status": "shared"},
                "only-ja": {"file": "ja/only-ja.webp", "status": "ja_only"},
            }
        },
    )
    assert vocab.image_path("ja", "same.webp") == (base / "img/shared/same.webp")
    assert vocab.image_path("ja", "only-ja.webp") == (base / "img/ja/only-ja.webp")
    assert vocab.image_file("same") == base / "img/shared/same.webp"
    assert vocab.image_file("only-ja") == base / "img/ja/only-ja.webp"


def test_image_lookup_falls_back_without_catalog(legacy_image_tree, monkeypatch):
    base = legacy_image_tree
    monkeypatch.setattr(vocab, "load_image_catalog", lambda: {"images": {}})
    assert vocab.image_path("ja", "same.webp") == base / "img/ja/same.webp"
    assert vocab.image_path("ja", "same.webp") != base / "img/same.webp"
    assert vocab.image_path("ja", "../same.webp") is None
    assert vocab.image_path("ja", "same.png") is None


def test_package_image_zip_uses_shared_and_language_dirs(tmp_path, monkeypatch):
    tool = load_tool("package_plugin")
    data = tmp_path / "data"
    images = tmp_path / "img"
    data.mkdir()
    _save(images / "shared" / "shared-one.webp")
    _save(images / "ja" / "ja-one.webp")
    (data / "catalog.json").write_text(
        json.dumps({"languages": [{"key": "ja", "dataset": "ja.json"}]}),
        encoding="utf-8",
    )
    (data / "ja.json").write_text(
        json.dumps(
            {
                "words": [
                    {
                        "text": "a",
                        "images": [{"id": "shared-one"}, {"id": "ja-one"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (data / "images.json").write_text(
        json.dumps(
            {
                "images": {
                    "shared-one": {
                        "file": "shared/shared-one.webp",
                        "status": "shared",
                        "languages": ["ja"],
                    },
                    "ja-one": {
                        "file": "ja/ja-one.webp",
                        "status": "ja_only",
                        "languages": ["ja"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(tool, "DATA_DIR", data)
    monkeypatch.setattr(tool, "CATALOG_FILE", data / "catalog.json")
    monkeypatch.setattr(tool, "IMAGES_FILE", data / "images.json")
    monkeypatch.setattr(tool, "IMAGE_DIR", images)

    ids = tool.referenced_ids("ja")
    assert ids == {"shared-one", "ja-one"}

    import zipfile  # pylint: disable=import-outside-toplevel

    # English is the shared base pack.
    en_zip = tmp_path / "en.zip"
    assert tool.write_image_zip(en_zip, "en", ids) == 1
    with zipfile.ZipFile(en_zip) as archive:
        assert set(archive.namelist()) == {
            "shared/shared-one.webp",
            "manifest-en.json",
        }

    # Japanese only ships its own images; shared/ is installed once.
    ja_zip = tmp_path / "ja.zip"
    assert tool.write_image_zip(ja_zip, "ja", ids) == 1
    with zipfile.ZipFile(ja_zip) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest-ja.json"))
    assert "ja/ja-one.webp" in names
    assert "shared/shared-one.webp" not in names
    assert manifest["count"] == 1
