"""
The MDX fixture must match the spec, not just our reader.

``build_mdx`` is hand-assembled, so it is only as good as the reading of
the format behind it -- and both implementations that consume it
(``lute/dict/mdx.py`` and the Dart ``MdxReader`` in the phone repo) were
ported from the same source.  A wrong assumption in the fixture therefore
cancels out against the same wrong assumption in the reader, and the tests
stay green while neither can read a real file.

That is not hypothetical.  The first version of the fixture computed the
key-section preamble checksum over the key block info instead of over the
preamble itself, and wrote it little-endian instead of big-endian; no test
in this repo noticed, because neither reader verifies it.  These tests pin
that field and the block-header checksum, and -- when ``mdict-utils`` is
installed -- hand the whole file to an independent implementation that
asserts them for us.
"""

import os
import shutil
import tempfile
import zlib

import pytest

from tests.unit.dict.mdx_fixture import build_mdx

ENTRIES = [("あ", "<div>a</div>"), ("い", "<div>b</div>")]

V2_HEADER = b"\x02\x00\x00\x00"  # little-endian block word: zlib, unencrypted


def _width(version):
    return 8 if float(version) >= 2 else 4


def _numbers(data, offset, count, version):
    "``count`` big-endian numbers of the version's width."
    width = _width(version)
    return [
        int.from_bytes(data[offset + i * width : offset + (i + 1) * width], "big")
        for i in range(count)
    ]


def _key_section_offset(data):
    "The key section starts after the header and its 4-byte adler32."
    header_len = int.from_bytes(data[:4], "big")
    return 4 + header_len + 4


def _record_block(data, version="2.0"):
    """
    The single record block, as stored (header + compressed body).

    Walked forward rather than searched for: every section size needed to
    skip the key section is in its header.
    """
    width = _width(version)
    offset = _key_section_offset(data)
    if float(version) >= 2:
        # 5 numbers then the preamble checksum.
        _, _, _, key_block_info_size, key_block_size = _numbers(
            data, offset, 5, version
        )
        offset += width * 5 + 4
    else:
        # 4 numbers, no checksum.
        _, _, key_block_info_size, key_block_size = _numbers(data, offset, 4, version)
        offset += width * 4
    offset += key_block_info_size + key_block_size

    # Record section header: 4 numbers, and -- unlike the key section --
    # no checksum in either version.
    num_record_blocks = _numbers(data, offset, 1, version)[0]
    offset += width * 4
    comp_size = _numbers(data, offset, 1, version)[0]
    offset += width * 2 * num_record_blocks

    assert num_record_blocks == 1, "the fixture writes a single record block"
    return data[offset : offset + comp_size]


def test_the_header_is_followed_by_four_bytes_of_adler32():
    """
    v1.2 writes the header checksum too, not just v2.0 -- a reader that
    skipped it only for v2 would be four bytes out on every old
    dictionary, and every real dictionary in circulation is v1.2.
    """
    for version in ("1.2", "2.0"):
        data = build_mdx(ENTRIES, version=version)
        header_len = int.from_bytes(data[:4], "big")
        header_bytes = data[4 : 4 + header_len]

        assert header_bytes[-2:] == b"\x00\x00", "header ends with a UTF-16 NUL"
        checksum = int.from_bytes(data[4 + header_len : 4 + header_len + 4], "little")
        assert checksum == zlib.adler32(header_bytes) & 0xFFFFFFFF


def test_the_v2_key_section_preamble_is_five_numbers_plus_a_big_endian_checksum():
    """
    v2.0 writes 5 big-endian numbers (40 bytes) and then ``>L`` adler32
    *of those five numbers*.  That is 44 bytes before the key block info,
    and the offset is the whole point: reading from 40 makes the key block
    info start inside the checksum and zlib fails.
    """
    data = build_mdx(ENTRIES, version="2.0")
    offset = _key_section_offset(data)

    preamble = data[offset : offset + 40]
    checksum = int.from_bytes(data[offset + 40 : offset + 44], "big")

    assert checksum == zlib.adler32(preamble) & 0xFFFFFFFF
    # ...and the key block info follows immediately, opening with the
    # block header word for zlib.
    assert data[offset + 44 : offset + 48] == V2_HEADER


def test_the_v1_key_section_is_four_numbers_and_no_checksum():
    """
    v1.2 has no preamble checksum, so its key block info starts at +16 --
    and unlike v2 it is stored raw, with no 8-byte block header, because
    it is not compressed.
    """
    data = build_mdx(ENTRIES, version="1.2")
    offset = _key_section_offset(data)

    num_key_blocks, num_entries, info_size, key_block_size = _numbers(
        data, offset, 4, "1.2"
    )
    assert (num_key_blocks, num_entries) == (1, len(ENTRIES))
    assert info_size > 0
    assert key_block_size > 0

    # The info payload opens with this block's entry count, big-endian --
    # not with a block header word, which only v2 has.
    assert data[offset + 16 : offset + 20] == len(ENTRIES).to_bytes(4, "big")


def test_the_block_checksum_is_big_endian_over_the_uncompressed_payload():
    """
    A block is a little-endian compression word, then a *big-endian*
    adler32 of the *uncompressed* payload, then the payload.  The mixed
    endianness is the format's, not a typo here -- writemdict packs the
    word with ``<L`` and the checksum with ``>L``.
    """
    definition = "<div>x</div>"
    data = build_mdx([("key", definition)], version="2.0", compress=2)
    block = _record_block(data)

    assert block[:4] == V2_HEADER
    assert (
        int.from_bytes(block[4:8], "big")
        == zlib.adler32(definition.encode("utf-8")) & 0xFFFFFFFF
    )
    # The body really is compressed, so the checksum above cannot have
    # been computed over what is stored.
    assert block[8:] == zlib.compress(definition.encode("utf-8"))


def _mdict_utils():
    "The independent reader, or None when it is not installed."
    try:
        from mdict_utils.base import (
            readmdict,
        )  # pylint: disable=import-outside-toplevel
    except ImportError:
        return None
    return readmdict.MDX


def test_an_independent_implementation_reads_every_fixture_variant():
    """
    The strongest available check: mdict-utils verifies the key-section
    preamble checksum, every block checksum and the key-block-info
    decompressed size -- none of which our reader looks at.  It rejects a
    fixture that is merely self-consistent with our own parser, which is
    exactly how the bad preamble checksum was found.

    ``pip install mdict-utils`` to run this; it is deliberately not a
    declared dependency, since only this cross-check needs it.
    """
    mdx_class = _mdict_utils()
    if mdx_class is None:
        pytest.skip("cross-check needs mdict-utils (pip install mdict-utils)")

    entries = [
        ('<a href="entry://たのしい">たのしい</a>', "<div>楽しい<br/>愉快</div>"),
        ("ひさしぶり", "<div>久し振り</div>"),
        ("箱", "はこ"),
    ]
    variants = [
        ("2.0", "UTF-8", 0),
        ("2.0", "UTF-8", 2),
        ("2.0", "UTF-16", 0),
        ("1.2", "UTF-8", 0),
        ("1.2", "UTF-16", 0),
    ]

    # Not pytest's tmp_path: the sandbox this repo is developed in cannot
    # create directories under the system temp root.
    scratch = tempfile.mkdtemp(dir=os.path.dirname(__file__))
    try:
        for version, encoding, compress in variants:
            name = f"{version}_{encoding}_{compress}.mdx"
            path = os.path.join(scratch, name)
            with open(path, "wb") as f:
                f.write(
                    build_mdx(
                        entries,
                        version=version,
                        encoding=encoding,
                        compress=compress,
                    )
                )

            mdx = mdx_class(path)
            assert mdx._version == float(
                version
            ), name  # pylint: disable=protected-access
            assert mdx._num_entries == len(
                entries
            ), name  # pylint: disable=protected-access
            assert [k.decode("utf-8") for k in mdx.keys()] == [
                k for k, _ in entries
            ], name
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
