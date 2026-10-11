"""
Tests for the MDX -> SQLite converter (lute/dict/importer.py).

The interesting behaviour is the ``@@@LINK=`` redirect resolution: a
dictionary that leans on redirects (the New Century Japanese-Chinese
one redirects 62% of its headwords) converts into something a popup can
actually show, and a dictionary without them converts unchanged.
"""

import os
import shutil
import sqlite3
import tempfile

import pytest

from lute.dict.importer import convert

from tests.unit.dict.mdx_fixture import build_mdx


def _convert(entries, **kwargs):
    """
    Convert ``entries`` into a dictionary db and return the open
    connection to it.
    """
    # Not pytest's tmp_path: the sandbox this repo is developed in
    # cannot create directories under the system temp root.
    folder = tempfile.mkdtemp(dir=os.path.dirname(__file__))
    mdx_path = os.path.join(folder, "fixture.mdx")
    db_path = os.path.join(folder, "fixture.db")
    with open(mdx_path, "wb") as f:
        f.write(build_mdx(entries, **kwargs))
    try:
        convert(mdx_path, db_path)
        conn = sqlite3.connect(db_path)
        return folder, conn
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _definitions(conn):
    "Every (word, definition) the dictionary serves, in file order."
    return conn.execute(
        "SELECT terms.word, entries.def FROM terms"
        " JOIN entries ON entries.eid = terms.eid"
        " ORDER BY terms.rowid"
    ).fetchall()


# Already in the flattened form convert() stores (HTML stripped, line
# breaks kept), because the assertions compare against what the
# dictionary serves, not against the raw MDX text.
ENTRIES = [
    ("ひさしぶり", "久し振り\n好久不见"),
    ("みひらき", "見開き"),
]


def test_redirect_resolves_to_the_targets_definition():
    entries = [
        ("みひらき", "@@@LINK=みひらき【見開き】\r\n"),
        ("みひらき【見開き】", "見開き\n左右两页"),
    ]
    folder, conn = _convert(entries)
    try:
        assert _definitions(conn) == [
            ("みひらき", "見開き\n左右两页"),
            ("みひらき【見開き】", "見開き\n左右两页"),
        ]
    finally:
        conn.close()
        shutil.rmtree(folder, ignore_errors=True)


def test_redirect_chain_resolves_through_every_hop():
    # The New Century dictionary has exactly one two-hop redirect; the
    # loop has to follow it rather than stop after one pass.
    entries = [
        ("a", "@@@LINK=b"),
        ("b", "@@@LINK=c"),
        ("c", "the definition"),
    ]
    folder, conn = _convert(entries)
    try:
        assert _definitions(conn) == [
            ("a", "the definition"),
            ("b", "the definition"),
            ("c", "the definition"),
        ]
    finally:
        conn.close()
        shutil.rmtree(folder, ignore_errors=True)


def test_dangling_redirect_keeps_its_text_instead_of_vanishing():
    # A redirect to a key the dictionary does not carry: the headword
    # stays findable, showing the raw pointer rather than nothing.
    entries = [("a", "@@@LINK=nowhere"), ("b", "real")]
    folder, conn = _convert(entries)
    try:
        assert _definitions(conn) == [
            ("a", "@@@LINK=nowhere"),
            ("b", "real"),
        ]
    finally:
        conn.close()
        shutil.rmtree(folder, ignore_errors=True)


def test_a_redirect_cycle_terminates_and_leaves_both_entries_alone():
    entries = [("a", "@@@LINK=b"), ("b", "@@@LINK=a"), ("c", "real")]
    folder, conn = _convert(entries)
    try:
        assert _definitions(conn) == [
            ("a", "@@@LINK=b"),
            ("b", "@@@LINK=a"),
            ("c", "real"),
        ]
    finally:
        conn.close()
        shutil.rmtree(folder, ignore_errors=True)


def test_a_dictionary_without_redirects_converts_unchanged():
    folder, conn = _convert(ENTRIES)
    try:
        assert _definitions(conn) == ENTRIES
    finally:
        conn.close()
        shutil.rmtree(folder, ignore_errors=True)


def test_definitions_are_deduped_across_headwords():
    # Two headwords sharing a definition (including two that redirect to
    # the same target) share one entries row.
    entries = [
        ("a", "same"),
        ("b", "same"),
        ("c", "@@@LINK=a"),
        ("d", "@@@LINK=a"),
    ]
    folder, conn = _convert(entries)
    try:
        rows = _definitions(conn)
        assert rows == [("a", "same"), ("b", "same"), ("c", "same"), ("d", "same")]
        assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1
    finally:
        conn.close()
        shutil.rmtree(folder, ignore_errors=True)


def test_the_metadata_reports_what_was_converted():
    folder = tempfile.mkdtemp(dir=os.path.dirname(__file__))
    mdx_path = os.path.join(folder, "fixture.mdx")
    db_path = os.path.join(folder, "fixture.db")
    with open(mdx_path, "wb") as f:
        f.write(build_mdx(ENTRIES, title="My Dict"))
    try:
        metadata = convert(mdx_path, db_path)
        assert metadata["title"] == "My Dict"
        assert metadata["terms"] == len(ENTRIES)
        assert metadata["entries"] == len(ENTRIES)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_a_file_that_yields_nothing_is_rejected():
    # An MDX whose every entry is empty after cleaning: convert() must
    # refuse rather than publish an empty dictionary.
    folder = tempfile.mkdtemp(dir=os.path.dirname(__file__))
    mdx_path = os.path.join(folder, "fixture.mdx")
    db_path = os.path.join(folder, "fixture.db")
    with open(mdx_path, "wb") as f:
        f.write(build_mdx([("a", "")]))
    try:
        with pytest.raises(ValueError, match="No usable entries"):
            convert(mdx_path, db_path)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
