"""
MDX reader tests.

The fixture builder lives in mdx_fixture.py -- see its docstring for what
these tests can and cannot prove (there is no real dictionary to check
against in this repo).
"""

import os
import shutil
import tempfile

import pytest

from lute.dict.mdx import MdxError, MdxReader

from tests.unit.dict.mdx_fixture import (
    build_header_only_mdx,
    build_mdx,
)

ENTRIES = [
    ("たのしい", "<div>楽しい<br/>愉快 愉快yúkuài</div>"),
    ("ひさしぶり", "<div>久し振り<br/>好久不见</div>"),
    ("箱", "<div>はこ<br/>箱子</div>"),
]


def _read(entries, **kwargs):
    with MdxReader(build_mdx(entries, **kwargs)) as reader:
        return reader.title, reader.version, reader.entry_count, list(reader.entries())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"version": "2.0", "encoding": "UTF-8", "compress": 0},
        {"version": "2.0", "encoding": "UTF-8", "compress": 2},
        # v1.2 + UTF-16 is the combination the Shogakukan V2 file uses,
        # and the one where the head/tail sizes are character counts.
        {"version": "1.2", "encoding": "UTF-16", "compress": 0},
        {"version": "1.2", "encoding": "UTF-16", "compress": 2},
    ],
)
def test_reads_entries(kwargs):
    title, version, count, entries = _read(ENTRIES, title="Fixture", **kwargs)

    assert title == "Fixture"
    assert version == float(kwargs["version"])
    assert count == len(ENTRIES)
    assert [k for k, _d in entries] == [k for k, _d in ENTRIES]
    assert entries[0][1] == ENTRIES[0][1]


def test_entry_count_matches_parsed_keys():
    with MdxReader(build_mdx(ENTRIES)) as reader:
        assert reader.entry_count == len(list(reader.entries()))


def test_entries_are_streamed_in_file_order():
    entries = [(f"k{i}", f"def{i}") for i in range(50)]
    _title, _version, _count, parsed = _read(entries)
    assert parsed == entries


@pytest.mark.parametrize(
    "kwargs",
    [
        {"version": "2.0", "encoding": "UTF-8", "compress": 0},
        {"version": "2.0", "encoding": "UTF-16", "compress": 2},
        {"version": "1.2", "encoding": "UTF-8", "compress": 0},
        {"version": "1.2", "encoding": "UTF-16", "compress": 2},
    ],
)
def test_the_record_terminator_never_reaches_the_definition(kwargs):
    """
    Every record ends with a NUL terminator, and the record slice runs to
    the *next* record's start -- so the terminator lands inside the text
    the reader decodes.  str.strip() does not remove a NUL, so a reader
    that forgets hands every definition a trailing "\\x00"; that is what
    shipped, and the fixture hid it by not writing terminators at all.
    mdict_utils strips it in MDX._treat_record_data.
    """
    _title, _version, _count, entries = _read(ENTRIES, **kwargs)

    assert entries == ENTRIES
    assert not any("\x00" in definition for _key, definition in entries)


def test_from_file_reads_the_same_thing():
    # Not tmp_path: the sandbox this repo is developed in cannot create
    # directories under the system temp root, so the fixture file goes
    # next to the test and is removed again.
    folder = tempfile.mkdtemp(dir=os.path.dirname(__file__))
    path = os.path.join(folder, "fixture.mdx")
    try:
        with open(path, "wb") as f:
            f.write(build_mdx(ENTRIES))
        with MdxReader.from_file(path) as reader:
            assert reader.entry_count == len(ENTRIES)
            assert [k for k, _d in reader.entries()] == [k for k, _d in ENTRIES]
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_clean_key_strips_the_html_wrapper():
    assert MdxReader.clean_key('<a href="entry://久し振り">久し振り</a>') == "久し振り"
    assert MdxReader.clean_key("  箱 ") == "箱"
    assert MdxReader.clean_key("a &amp; b") == "a & b"


def test_clean_definition_flattens_html_and_keeps_line_breaks():
    text = MdxReader.clean_definition(
        "<div>愉快yúkuài<br/>快乐<div>例：<br/>～クリスマス</div></div>"
    )
    assert "愉快yúkuài\n快乐" in text
    assert "～クリスマス" in text
    assert "<" not in text


def test_clean_definition_truncates():
    assert len(MdxReader.clean_definition("x" * 2000)) == 800
    assert MdxReader.clean_definition("x" * 2000, max_chars=10) == "x" * 10


@pytest.mark.parametrize("encrypted", ["1", "3", "Yes"])
def test_keyed_encryption_is_still_rejected(encrypted):
    """
    Encrypted=1/3 (and v1's "Yes") lock the record blocks behind a
    per-user registration code.  Only the export lock (2) is
    decryptable -- see test_reads_an_export_locked_file.
    """
    data = build_header_only_mdx(
        {"GeneratedByEngineVersion": "2.0", "Encrypted": encrypted, "Encoding": "UTF-8"}
    )
    with pytest.raises(MdxError, match="encrypted"):
        MdxReader(data)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"version": "2.0", "encoding": "UTF-8", "compress": 0},
        {"version": "2.0", "encoding": "UTF-8", "compress": 2},
        {"version": "2.0", "encoding": "UTF-16", "compress": 2},
    ],
)
def test_reads_an_export_locked_file(kwargs):
    """
    Encrypted="2" is MDict's "Allow export to text" checkbox left
    unchecked: only the key block info is transformed, so the file reads
    normally once that section is decrypted.
    """
    with MdxReader(build_mdx(ENTRIES, encrypted_info=True, **kwargs)) as reader:
        assert reader.entry_count == len(ENTRIES)
        assert [k for k, _d in reader.entries()] == [k for k, _d in ENTRIES]
        assert [d for _k, d in reader.entries()] == [d for _k, d in ENTRIES]


def test_unsupported_encoding_is_rejected():
    data = build_header_only_mdx(
        {
            "GeneratedByEngineVersion": "2.0",
            "Encrypted": "No",
            "Encoding": "BIG5",
        }
    )
    with pytest.raises(MdxError, match="encoding"):
        MdxReader(data)


def test_short_file_is_rejected():
    with pytest.raises(MdxError, match="too short"):
        MdxReader(b"abc")
