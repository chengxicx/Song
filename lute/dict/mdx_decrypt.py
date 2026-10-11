"""
Decryption for MDX dictionaries saved with ``Encrypted="2"``.

MDict's export dialog has an "Allow export to text" checkbox; leaving it
unchecked stamps ``Encrypted="2"`` on the file.  That flag transforms
one section -- the *key block info*, the table that tells a reader where
each key block starts and how big it is -- and nothing else: the header,
the key blocks and the record blocks stay in the clear.  So an
"export-locked" file is one fixed transform away from readable, and this
module is that transform.  (``Encrypted="1"``/``"Yes"``/``"3"`` is the
genuinely keyed scheme -- the record blocks are locked behind a per-user
registration code -- and MdxReader still refuses those.)

The transform is MDict's "fast decryption", the same routine the
reference writer (writemdict) and readers (readmdict, mdict-utils) use:

    key   = ripemd128(adler32 || b"\\x95\\x36\\x00\\x00")
    plain = transform(cipher, key)

``adler32`` is the block's own 4-byte checksum (bytes 4:8), which is why
the block's 8-byte header -- compression word plus checksum -- passes
through the transform untouched and is copied verbatim.  The 4-byte
tail of the key is ``struct.pack("<L", 0x3695)``.

The key derivation needs RIPEMD-128, which hashlib stopped offering when
OpenSSL 3 moved it to the legacy provider, so it is implemented here in
pure Python.  It runs once per dictionary, over the key block info only
(a few kilobytes), so speed is a non-issue.  The algorithm is the
published one (Dobbertin, Bosselaers, Preneel -- the rmd128.txt
specification) and the tables below are its tables.
"""


def _rol(count, value):
    return ((value << count) | (value >> (32 - count))) & 0xFFFFFFFF


def _f(round_index, x, y, z):
    "The four boolean functions, selected by the round the step is in."
    if round_index < 16:
        return x ^ y ^ z
    if round_index < 32:
        return (x & y) | (~x & z)
    if round_index < 48:
        return (x | ~y) ^ z
    return (x & z) | (y & ~z)


def _k(round_index):
    "Left-line round constants (the right line's are _kp)."
    if round_index < 16:
        return 0x00000000
    if round_index < 32:
        return 0x5A827999
    if round_index < 48:
        return 0x6ED9EBA1
    return 0x8F1BBCDC


def _kp(round_index):
    if round_index < 16:
        return 0x50A28BE6
    if round_index < 32:
        return 0x5C4DD124
    if round_index < 48:
        return 0x6D703EF3
    return 0x00000000


# Message-word order and rotation amounts for the left and right lines,
# 64 steps each.  From the rmd128.txt specification.
_R = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
    7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8,
    3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12,
    1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2,
]
_RP = [
    5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12,
    6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2,
    15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13,
    8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14,
]
_S = [
    11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8,
    7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12,
    11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5,
    11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12,
]
_SP = [
    8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6,
    9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11,
    9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5,
    15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8,
]


def ripemd128(message):  # pylint: disable=too-many-locals
    """
    The 128-bit RIPEMD digest of ``message``, as 16 raw bytes.

    Little-endian throughout, as the specification defines it: the
    length is appended as a 64-bit little-endian bit count and the
    chaining values are packed the same way.
    """
    original_length = len(message)
    # Pad to 56 mod 64: one 0x80 byte, zeros, then the bit length.
    padded = message + b"\x80"
    padded += b"\x00" * ((56 - (original_length + 1) % 64) % 64)
    padded += (original_length * 8).to_bytes(8, "little")

    h0, h1, h2, h3 = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476
    for block_start in range(0, len(padded), 64):
        words = [
            int.from_bytes(padded[block_start + i : block_start + i + 4], "little")
            for i in range(0, 64, 4)
        ]
        a, b, c, d = h0, h1, h2, h3
        ap, bp, cp, dp = h0, h1, h2, h3
        for j in range(64):
            t = _rol(_S[j], (a + _f(j, b, c, d) + words[_R[j]] + _k(j)) & 0xFFFFFFFF)
            a, d, c, b = d, c, b, t
            t = _rol(
                _SP[j], (ap + _f(63 - j, bp, cp, dp) + words[_RP[j]] + _kp(j)) & 0xFFFFFFFF
            )
            ap, dp, cp, bp = dp, cp, bp, t
        t = (h1 + c + dp) & 0xFFFFFFFF
        h1 = (h2 + d + ap) & 0xFFFFFFFF
        h2 = (h3 + a + bp) & 0xFFFFFFFF
        h3 = (h0 + b + cp) & 0xFFFFFFFF
        h0 = t

    return b"".join(h.to_bytes(4, "little") for h in (h0, h1, h2, h3))


def fast_decrypt(data, key):
    """
    MDict's "fast decryption" over ``data`` with a repeating ``key``.

    Each output byte is the nibble-swapped input byte, XORed with the
    previous *input* byte, the byte's index and the key byte.  Chaining
    the input (not the output) is what makes this the exact inverse of
    the writer's transform, which is why one routine serves both
    directions in the reference implementations.
    """
    out = bytearray(data)
    key = bytearray(key)
    previous = 0x36
    for i, byte in enumerate(out):
        t = ((byte >> 4) | (byte << 4)) & 0xFF
        t = t ^ previous ^ (i & 0xFF) ^ key[i % len(key)]
        previous = byte
        out[i] = t
    return bytes(out)


def decrypt_key_info_block(block):
    """
    Undo ``Encrypted="2"`` on one key block info block.

    The 8-byte header (compression word + adler32) is in the clear and
    is copied through; everything after it is the transformed zlib
    stream.
    """
    key = ripemd128(block[4:8] + b"\x95\x36\x00\x00")
    return block[:8] + fast_decrypt(block[8:], key)
