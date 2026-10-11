"""
Tests for the Encrypted=2 (export lock) decryption.

The transform and its key derivation are pinned against a real file:
the vector below is the first 32 bytes of the New Century
Japanese-Chinese dictionary's key block info, ciphertext and plaintext,
so a change that breaks real-file compatibility fails here instead of
on the next import.  The RIPEMD-128 vectors are the published ones
(ISO/IEC 10118-3:2004, the same set the Linux kernel's tcrypt uses).
"""

import pytest

from lute.dict.mdx import MdxReader
from lute.dict.mdx_decrypt import decrypt_key_info_block, fast_decrypt, ripemd128

from tests.unit.dict.mdx_fixture import build_mdx

# Published RIPEMD-128 test vectors (message, digest).
RIPEMD128_VECTORS = [
    (b"", "cdf26213a150dc3ecb610f18f6b38b46"),
    (b"a", "86be7afa339d0fc7cfc785e72f578d33"),
    (b"abc", "c14a12199c66e4ba84636b0f69144c77"),
    (b"message digest", "9e327b3d6e523062afc1132d7df9d1b8"),
    (
        b"abcdefghijklmnopqrstuvwxyz",
        "fd2aa607f71dc8f510714922b371834e",
    ),
    (
        b"The quick brown fox jumps over the lazy dog",
        "3fa9b57f053c053fbe2735b2380db596",
    ),
]


@pytest.mark.parametrize("message,digest", RIPEMD128_VECTORS)
def test_ripemd128_matches_the_published_vectors(message, digest):
    assert ripemd128(message).hex() == digest


@pytest.mark.parametrize("length", [0, 1, 55, 56, 57, 63, 64, 65, 128, 200])
def test_ripemd128_pads_every_length_without_raising(length):
    # The padding arithmetic is where a pure-Python RIPEMD usually goes
    # wrong: one 0x80 byte, zeros to 56 mod 64, then the bit length.
    digest = ripemd128(b"a" * length)
    assert len(digest) == 16
    assert digest != ripemd128(b"a" * (length + 1))


def _encrypt_like_the_writer(data, key):
    """
    The inverse of ``fast_decrypt`` -- the transform MDict's writer
    applies.  It differs in the two places that are easy to get wrong:
    the nibble swap comes *after* the XOR chain, and the chain follows
    the ciphertext rather than the input.
    """
    out = bytearray(data)
    key = bytearray(key)
    previous = 0x36
    for i in range(len(out)):
        t = out[i] ^ previous ^ (i & 0xFF) ^ key[i % len(key)]
        t = ((t >> 4) | (t << 4)) & 0xFF
        previous = t
        out[i] = t
    return bytes(out)


@pytest.mark.parametrize("length", [1, 15, 16, 17, 64, 65, 1000])
def test_fast_decrypt_undoes_the_writers_transform(length):
    key = ripemd128(b"\x01\x02\x03\x04" + b"\x95\x36\x00\x00")
    blob = bytes((i * 7 + 11) % 256 for i in range(length))

    assert fast_decrypt(_encrypt_like_the_writer(blob, key), key) == blob


# --- the real-file vector ---------------------------------------------
#
# New Century Japanese-Chinese dictionary (xsjrihanshuangjie.mdx,
# Encrypted=2), key block info block: the 8-byte header is in the clear
# (zlib compression word + adler32 of the decompressed info), the rest
# is the transformed zlib stream.  78da at the front of the plaintext is
# the zlib header, which is what a correct key derivation produces.
REAL_INFO_HEADER = bytes.fromhex("02000000bb5e0dd2")
REAL_CIPHERTEXT = bytes.fromhex(
    "460779e5a9e79236d1701d37a0dd760cdf7948b40759245061511c14df235691"
)
REAL_PLAINTEXT = bytes.fromhex(
    "78da9d7c09705cc775e0970101a28522cbb4e048a22599070852b622c52269cb"
)


def test_decrypts_the_real_dictionarys_key_block_info():
    block = REAL_INFO_HEADER + REAL_CIPHERTEXT

    assert decrypt_key_info_block(block) == REAL_INFO_HEADER + REAL_PLAINTEXT


def test_the_block_header_survives_the_transform_untouched():
    block = REAL_INFO_HEADER + REAL_CIPHERTEXT

    assert decrypt_key_info_block(block)[:8] == REAL_INFO_HEADER


def test_an_export_locked_file_reads_end_to_end():
    """
    The fixture builder stamps Encrypted="2" with the writer's
    transform; the reader must undo exactly that and return the
    original entries.
    """
    entries = [("みひらき", "<div>見開き</div>"), ("たべる", "<div>食べる</div>")]

    with MdxReader(build_mdx(entries, encrypted_info=True)) as reader:
        assert reader.entry_count == len(entries)
        assert list(reader.entries()) == entries
