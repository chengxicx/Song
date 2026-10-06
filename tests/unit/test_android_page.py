"""
The About > Android App page lists apk builds published to the static folder.

Only top-level *.apk files are shown, newest first; anything else, and
an empty folder, must not break the page.
"""

import os

from lute.app_factory import _published_apks


def _make_apk(folder, name, mtime):
    "A fake apk with an explicit modification time."
    path = folder / name
    path.write_bytes(b"fake apk bytes")
    os.utime(path, (mtime, mtime))
    return path


def test_published_apks_only_apks_newest_first(tmp_path):
    _make_apk(tmp_path, "old.apk", 1000)
    _make_apk(tmp_path, "new.apk", 2000)
    (tmp_path / "readme.txt").write_text("not an apk")
    (tmp_path / "sub").mkdir()

    apks = _published_apks(str(tmp_path))

    assert [a["name"] for a in apks] == ["new.apk", "old.apk"]
    assert "size_mb" in apks[0]


def test_published_apks_missing_folder_is_empty():
    assert _published_apks(str("/nonexistent/lute/static")) == []
    assert _published_apks(None) == []


def test_android_page_lists_builds(app, app_context, monkeypatch, tmp_path):
    monkeypatch.setattr(app, "static_folder", str(tmp_path))
    _make_apk(tmp_path, "_lute_handoff_20260927-224843.apk", 2000)
    (tmp_path / "readme.txt").write_text("not an apk")

    resp = app.test_client().get("/android")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "_lute_handoff_20260927-224843.apk" in body
    assert "readme.txt" not in body


def test_android_page_empty_state(app, app_context, monkeypatch, tmp_path):
    monkeypatch.setattr(app, "static_folder", str(tmp_path))

    resp = app.test_client().get("/android")

    assert resp.status_code == 200
    assert "No Android build has been published yet." in resp.get_data(as_text=True)
