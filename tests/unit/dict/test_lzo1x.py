"""
LZO1X decompression tests.

The only lzo1x stream available to test against is hand-assembled (see
mdx_fixture.LZO_ABCDBCD): there is no LZO compressor in the test
environment, and no real MDX file to pull a block out of.  That covers
the literal-run, long-match and end-of-stream opcodes; the short-match
opcodes (t < 32 / t < 64) are exercised by real dictionaries only.
"""

import pytest

from lute.dict.lzo1x import lzo1x_decompress

from tests.unit.dict.mdx_fixture import LZO_ABCDBCD, LZO_ABCDBCD_EXPECTED


def test_decompresses_literals_match_and_eof():
    assert (
        lzo1x_decompress(LZO_ABCDBCD, len(LZO_ABCDBCD_EXPECTED)) == LZO_ABCDBCD_EXPECTED
    )


def test_short_input_is_rejected():
    with pytest.raises(ValueError, match="too short"):
        lzo1x_decompress(b"\x01\x02", 4)


def test_input_overrun_is_rejected():
    # First byte promises a four-byte literal run but only two bytes follow.
    with pytest.raises(ValueError, match="overrun"):
        lzo1x_decompress(b"\x15AB", 4)


def test_length_mismatch_is_rejected():
    # A valid stream, but the caller says the result is longer.
    with pytest.raises(ValueError, match="mismatch"):
        lzo1x_decompress(LZO_ABCDBCD, 99)
