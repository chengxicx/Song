"""
tools/mdx_to_sqlite.py -- the offline way into the dictionary.

The settings page's import is the easy path, but it goes through the
browser and so is capped by the app's 200 MB upload limit; a bigger
dictionary has to be converted on the server with this script.  That
workflow is documented in the settings page and in the tool's own
docstring, and nothing else exercises it, so it is pinned here.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_TOOL = os.path.join(_ROOT, "tools", "mdx_to_sqlite.py")

from tests.unit.dict.mdx_fixture import (
    build_mdx,
)  # pylint: disable=wrong-import-position

ENTRIES = [
    ("たのしい", "<div>楽しい<br/>愉快</div>"),
    ("箱", "<div>はこ</div>"),
]


@pytest.fixture(name="scratch")
def fixture_scratch():
    "A scratch directory; not tmp_path, which the sandbox cannot create."
    folder = tempfile.mkdtemp(dir=_HERE)
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


def _run(args):
    "Run the tool with the interpreter running the tests."
    return subprocess.run(
        [sys.executable, _TOOL] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _write_mdx(folder, name="fixture.mdx"):
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(build_mdx(ENTRIES, title="Fixture"))
    return path


def test_converts_an_mdx_into_the_dictionary_schema(scratch):
    src = _write_mdx(scratch)
    out = os.path.join(scratch, "out.db")

    result = _run([src, out])

    assert result.returncode == 0, result.stderr.decode("utf-8")
    assert "Fixture" in result.stdout.decode("utf-8")

    conn = sqlite3.connect(out)
    try:
        assert conn.execute("SELECT COUNT(*) FROM terms").fetchone()[0] == len(ENTRIES)
        assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == len(
            ENTRIES
        )
        gloss = conn.execute(
            "SELECT entries.def FROM terms JOIN entries ON entries.eid = terms.eid"
            " WHERE terms.word = ?",
            ("たのしい",),
        ).fetchone()[0]
        assert gloss == "楽しい\n愉快"
    finally:
        conn.close()


def test_writes_into_a_folder_that_does_not_exist_yet(scratch):
    src = _write_mdx(scratch)
    out = os.path.join(scratch, "static", "dict", "ja_zh_shogakukan_v2.db")

    result = _run([src, out])

    assert result.returncode == 0, result.stderr.decode("utf-8")
    assert os.path.isfile(out)


def test_prints_usage_without_arguments(scratch):
    result = _run([])

    assert result.returncode == 1
    assert "mdx_to_sqlite.py <file.mdx> <out.db>" in result.stdout.decode("utf-8")


def test_reports_a_missing_source_file(scratch):
    result = _run([os.path.join(scratch, "nope.mdx"), os.path.join(scratch, "out.db")])

    assert result.returncode == 1
    assert "No such file" in result.stdout.decode("utf-8")


def test_reports_a_file_that_is_not_an_mdx(scratch):
    src = os.path.join(scratch, "junk.mdx")
    with open(src, "wb") as f:
        f.write(b"definitely not an mdx file")
    out = os.path.join(scratch, "out.db")

    result = _run([src, out])

    assert result.returncode == 1
    assert "Failed:" in result.stdout.decode("utf-8")
    assert os.path.exists(out) is False
