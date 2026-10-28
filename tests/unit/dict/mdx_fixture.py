"""
Build tiny synthetic MDX files for the reader tests.

A real dictionary is hundreds of megabytes and cannot be checked in, so
the container format is exercised against hand-assembled files instead.

**These files are validated against an independent implementation.**
`pip install mdict-utils`, then:

    from mdict_utils.base.readmdict import MDX
    m = MDX("/tmp/fixture.mdx")
    print(m._version, m._encoding, m._num_entries, list(m.items()))

mdict-utils asserts the key-section preamble checksum, the block
checksums and the key-block-info decompressed size, so it rejects a
fixture that is merely self-consistent with our own reader.  Do not skip
that check after touching this file: the first version of it got the
preamble checksum wrong (computed over the key block info instead of over
the five numbers, and little-endian instead of big-endian) and no test in
this repo noticed, because neither reader verifies it.

The phone repo has a byte-for-byte copy of two of these files (base64) in
`test/mdx_v2_fixture_test.dart`, because the real dictionary it tests
against is v1.2 and so never exercises the v2 path.

Two engine versions are produced because they differ in every place that
is easy to get wrong:

- v1.2: 4-byte numbers, the key block info is *not* zlib-compressed, and
  its head/tail sizes are unit counts with no terminator.
- v2.0: 8-byte numbers, zlib-compressed key block info, unit counts with
  one terminator unit, and a 4-byte adler32 after the five header
  numbers.

``encrypted_info`` stamps the export lock (``Encrypted="2"``) on a v2
file: the key block info's zlib stream is transformed, everything else
stays in the clear -- exactly what MDict writes with "Allow export to
text" unchecked.
"""

import struct
import zlib

from lute.dict.mdx_decrypt import ripemd128


def _be(value, width):
    return value.to_bytes(width, "big")


def _charwidth(utf16):
    return 2 if utf16 else 1


def _encode(text, utf16):
    if utf16:
        return text.encode("utf-16-le")
    return text.encode("utf-8")


def _block(payload, compression):
    """
    One MDX block: 4-byte little-endian compression word + 4-byte
    *big-endian* adler32 of the *uncompressed* payload, then the
    (optionally compressed) payload.  The block size recorded in the
    block info includes this 8-byte header.

    The mixed endianness is not a typo -- see writemdict's
    ``_mdx_compress``: ``struct.pack(b"<L", compression_type) +
    struct.pack(b">L", zlib.adler32(data))``.  mdict_utils asserts both
    halves on read, so getting either wrong makes the file unreadable by
    any implementation but ours.
    """
    if compression == 0:
        body = payload
    elif compression == 2:
        body = zlib.compress(payload)
    else:
        raise ValueError(f"fixture cannot produce compression {compression}")
    info = compression & 0xF  # low nibble compression, high nibble encryption
    return (
        struct.pack("<I", info)
        + struct.pack(">I", zlib.adler32(payload) & 0xFFFFFFFF)
        + body
    )


# The inverse of lute.dict.mdx_decrypt.fast_decrypt, needed to stamp the
# export lock on a fixture.  Validated against a real file: encrypting
# the New Century Japanese-Chinese dictionary's key block info plaintext
# reproduces its stored ciphertext byte for byte, so a fixture built
# with this is what MDict itself would have written.
def _fast_encrypt(data, key):
    out = bytearray(data)
    key = bytearray(key)
    previous = 0x36
    for i in range(len(out)):
        t = out[i] ^ previous ^ (i & 0xFF) ^ key[i % len(key)]
        t = ((t >> 4) | (t << 4)) & 0xFF
        previous = t
        out[i] = t
    return bytes(out)


def _encrypt_info_block(block):
    "MDict's export lock (Encrypted=2) over one key block info block."
    key = ripemd128(block[4:8] + b"\x95\x36\x00\x00")
    return block[:8] + _fast_encrypt(block[8:], key)


def _record_stream(entries, utf16):
    """
    The decompressed record section: every definition concatenated in the
    file's own encoding, plus the byte offset each one starts at (the key
    block stores those offsets, and they point into this stream).

    Each record ends with a NUL terminator, the way real files do -- both
    writemdict and MdxBuilder write one.  It matters because the record
    slice runs to the *next* record's start, so the terminator lands
    inside the text the reader decodes, and the reader is expected to
    strip it (mdict_utils does, in MDX._treat_record_data).  A fixture
    without terminators makes a reader that leaks a trailing \\x00 into
    every definition look correct.
    """
    terminator = b"\x00\x00" if utf16 else b"\x00"
    parts = [_encode(definition, utf16) + terminator for _key, definition in entries]
    offsets = []
    acc = 0
    for part in parts:
        offsets.append(acc)
        acc += len(part)
    return b"".join(parts), offsets


def _key_payload(entries, offsets, number_width, utf16):
    "Keys, each prefixed by the record offset it starts at."
    payload = b""
    terminator = b"\x00\x00" if utf16 else b"\x00"
    for (key, _definition), offset in zip(entries, offsets):
        payload += _be(offset, number_width)
        payload += _encode(key, utf16) + terminator
    return payload


def build_mdx(
    entries,
    version="2.0",
    encoding="UTF-8",
    compress=0,
    title="Fixture",
    encrypted_info=False,
):
    """
    Assemble a minimal but valid MDX file holding ``entries``, an ordered
    sequence of (key, definition) pairs.

    ``compress`` is the block compression: 0 (none) or 2 (zlib).  LZO is
    not built here -- there is no LZO compressor in the test environment;
    the decompressor is covered directly in test_lzo1x.py.

    ``encrypted_info`` (v2 only) stamps ``Encrypted="2"`` on the header
    and transforms the key block info, the way MDict writes a file with
    "Allow export to text" unchecked.  Everything else -- key blocks,
    record blocks -- stays in the clear, exactly as in a real file.
    """
    entries = list(entries)
    if not entries:
        raise ValueError("an MDX fixture needs at least one entry")
    if encrypted_info and float(version) < 2:
        raise ValueError("the export lock (Encrypted=2) is a v2 feature")

    v2 = float(version) >= 2
    number_width = 8 if v2 else 4
    utf16 = encoding.upper().startswith("UTF-16")

    # ---- header: UTF-16LE XML, then adler32, then the key block ----
    header_text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Dictionary GeneratedByEngineVersion="{version}" '
        f'RequiredEngineVersion="2.0" Encrypted="{"2" if encrypted_info else "No"}" '
        f'Encoding="{encoding}" Title="{title}"/>'
    )
    header_bytes = _encode(header_text, True) + b"\x00\x00"
    out = struct.pack(">I", len(header_bytes)) + header_bytes
    out += struct.pack("<I", zlib.adler32(header_bytes) & 0xFFFFFFFF)

    # ---- key block ----
    records, offsets = _record_stream(entries, utf16)
    key_payload = _key_payload(entries, offsets, number_width, utf16)
    key_block = _block(key_payload, compress)

    head = entries[0][0]
    tail = entries[-1][0]
    info_payload = _be(len(entries), number_width)  # entries in this block
    # Both versions store the head/tail length as a count of *units*, not
    # characters: writemdict computes
    # `len(key.encode(enc)) // encoding_length` -- bytes for UTF-8/GB18030,
    # 16-bit code units for UTF-16 -- and the reader then skips
    # `size + text_term` units (mdict_utils._decode_key_block_info).
    # A character count is only correct for UTF-16, and only for ASCII
    # under UTF-8; non-ASCII keys in a UTF-8 fixture walk the parse off
    # the end of the buffer.
    unit_width = _charwidth(utf16)

    def units(text):
        "The key length as the format counts it: bytes or 16-bit units."
        if utf16:
            return len(text.encode("utf-16-le")) // 2
        return len(text.encode("utf-8"))

    if v2:
        # v2: 2-byte size, text followed by one terminator unit, and the
        # whole info payload zlib-compressed.
        info_payload += _be(units(head), 2)
        info_payload += _encode(head, utf16) + b"\x00" * unit_width
        info_payload += _be(units(tail), 2)
        info_payload += _encode(tail, utf16) + b"\x00" * unit_width
        info_payload += _be(len(key_block), number_width)
        info_payload += _be(len(key_payload), number_width)
        key_block_info = _block(info_payload, 2)
        if encrypted_info:
            key_block_info = _encrypt_info_block(key_block_info)
    else:
        # v1: 1-byte size, no terminator, no compression.
        info_payload += bytes([units(head)])
        info_payload += _encode(head, utf16)
        info_payload += bytes([units(tail)])
        info_payload += _encode(tail, utf16)
        info_payload += _be(len(key_block), number_width)
        info_payload += _be(len(key_payload), number_width)
        key_block_info = info_payload

    if v2:
        preamble = (
            _be(1, number_width)  # key block count
            + _be(len(entries), number_width)
            + _be(len(info_payload), number_width)
            + _be(len(key_block_info), number_width)
            + _be(len(key_block), number_width)
        )
        # The trailing checksum covers the preamble itself -- the five
        # numbers just written -- NOT the key block info, and it is
        # big-endian.  Both the writer (writemdict._write_key_sect:
        # `struct.pack(b">L", zlib.adler32(preamble))`) and an
        # independent reader (mdict_utils.readmdict._read_keys_v1v2:
        # `assert adler32 == zlib.adler32(block)`) agree on that, and the
        # independent reader rejects the file outright if it is wrong --
        # which is how this was caught.  Neither of our own readers
        # verifies it, so getting it wrong here is invisible to them.
        out += preamble
        out += struct.pack(">I", zlib.adler32(preamble) & 0xFFFFFFFF)
    else:
        out += _be(1, number_width)
        out += _be(len(entries), number_width)
        out += _be(len(key_block_info), number_width)
        out += _be(len(key_block), number_width)
    out += key_block_info
    out += key_block

    # ---- record block ----
    record_block = _block(records, compress)
    record_info = _be(len(record_block), number_width) + _be(len(records), number_width)

    out += _be(1, number_width)  # record block count
    out += _be(len(entries), number_width)
    out += _be(len(record_info), number_width)
    out += _be(len(record_block), number_width)
    out += record_info
    out += record_block
    return out


def build_header_only_mdx(attributes):
    """
    An MDX whose header carries exactly ``attributes`` and nothing else,
    for the constructor's rejection paths.
    """
    attrs = " ".join(f'{k}="{v}"' for k, v in attributes.items())
    header_bytes = _encode(f"<Dictionary {attrs}/>", True) + b"\x00\x00"
    out = struct.pack(">I", len(header_bytes)) + header_bytes
    out += struct.pack("<I", zlib.adler32(header_bytes) & 0xFFFFFFFF)
    return out


# A hand-assembled lzo1x stream that decompresses to b"ABCDBCD".  There is
# no LZO compressor available to build one, so this is derived from the
# kernel's decompressor (lib/lzo/lzo1x_decompress_safe.c) one opcode at a
# time.  A real lzo1x stream always opens with a literal run of at least
# four bytes, which is why the first byte is > 17:
#   15            first byte 21 -> literal run of (21 - 17) = 4 bytes
#   "ABCD"        those four literals
#   48 00         control >= 64 -> match of (72 >> 5) - 1 + 2 = 3 bytes
#                 from offset op - 1 - ((72 >> 2) & 7) - (0 << 3) = 1,
#                 i.e. out[1..3] = "BCD"; 0 literals follow
#   11 00 00      control 17 with a zero match offset -> end of stream
LZO_ABCDBCD = b"\x15ABCD\x48\x00\x11\x00\x00"
LZO_ABCDBCD_EXPECTED = b"ABCDBCD"
