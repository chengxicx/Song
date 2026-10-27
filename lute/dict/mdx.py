"""
Pure-Python MDX (MDict) dictionary reader.

Ported from the Dart parser in LuteForMobile (``lib/core/dict/mdx_reader.dart``),
so the web import and the phone's on-device import agree on what a
dictionary contains.  Supports MDX v1.2 / v2.0 / v3.0, UTF-8 / UTF-16
(and GB18030, which the built-in codec handles and the Dart version
rejects), with uncompressed / zlib / LZO1X blocks.

Dictionaries saved with MDict's "Allow export to text" box unchecked
(Encrypted=2) are supported: that flag transforms only the key block
info, and lute/dict/mdx_decrypt.py undoes the transform.  The keyed
schemes (Encrypted=1/3/"Yes", and the regcode-keyed files that still
claim Encrypted=No) are NOT supported -- the free dictionaries from
mdict.org are almost all unencrypted or export-locked only.

The Encrypted=2 transform is the same one the phone's parser undoes
(``lib/core/dict/mdx_crypto.dart``, ported from the same references), so
the two readers agree on which files open.

Usage::

    with MdxReader.from_file("shogakukan.mdx") as r:
        for key, definition in r.entries():
            ...

``key`` and ``definition`` are the raw MDX strings: both are usually
HTML fragments (keys are often wrapped in a link), so callers run them
through ``clean_key`` / ``clean_definition`` before showing them.
"""

import mmap
import re
import zlib

from lute.dict.lzo1x import lzo1x_decompress
from lute.dict.mdx_decrypt import decrypt_key_info_block


_TAG_RE = re.compile(r"<[^>]+>")
_HEADER_TAG_RE = re.compile(r'([A-Za-z0-9_]+)="([^"]*)"', re.S)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_BLOCK_END_RE = re.compile(r"</(p|div|li|tr|h[1-6])>", re.I)


class MdxError(ValueError):
    "A malformed or unsupported MDX file."


_SENTENCE_BREAK_RE = re.compile(r"[.!?。！？；;…]")


def _truncate_at_boundary(text, limit):
    """
    ``text`` cut near ``limit``, but never inside a word or a sentence.

    Dictionary entries run far past what a card shows and the source
    markup is flattened to lines here, so the cut prefers the last
    complete line, then the last sentence end, and only then a space.
    A single unbroken token still falls back to the hard cut.
    """
    window = text[:limit]
    floor = limit // 3
    cut = window.rfind("\n")
    if cut < floor:
        breaks = list(_SENTENCE_BREAK_RE.finditer(window))
        cut = breaks[-1].end() if breaks else -1
    if cut < floor:
        cut = window.rfind(" ")
    return text[: cut if cut > floor else limit].rstrip()


class MdxReader:  # pylint: disable=too-many-instance-attributes
    """
    Random-access reader over an MDX file's bytes.

    ``data`` is any bytes-like object: a bytes blob, or (from
    ``from_file``) an mmap so a 300 MB dictionary does not have to sit in
    the server's heap while it is converted.
    """

    def __init__(self, data, on_close=None):
        self._data = data
        self._on_close = on_close
        self.version = 2.0
        self.encoding = "UTF-8"
        self.title = ""
        self._number_width = 8
        self._utf16 = False
        self._encrypt = 0
        self._key_block_offset = 0
        self._key_list = None
        self._num_entries = 0
        self._record_block_offset = 0
        self._read_header()

    # -- lifecycle --

    def close(self):
        "Release the underlying file handle, if any."
        if self._on_close is not None:
            self._on_close()
            self._on_close = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    @classmethod
    def from_file(cls, path):
        "Open ``path``, memory-mapped when possible."
        fh = open(path, "rb")  # pylint: disable=consider-using-with
        try:
            data = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError):
            # Empty file, or a platform where mmap is unavailable.
            fh.seek(0)
            data = fh.read()

        def _close():
            try:
                closer = getattr(data, "close", None)
                if closer is not None:
                    closer()
            finally:
                fh.close()

        return cls(data, _close)

    # -- primitives --

    @staticmethod
    def _be(buf, offset, width):
        v = 0
        for i in range(width):
            v = (v << 8) | buf[offset + i]
        return v

    def _read_number(self, buf, offset):
        return self._be(buf, offset, self._number_width)

    @staticmethod
    def _le32(buf, offset):
        return (
            buf[offset]
            | (buf[offset + 1] << 8)
            | (buf[offset + 2] << 16)
            | (buf[offset + 3] << 24)
        )

    def _decode_text(self, raw):
        "Decode a key or record's bytes with the file's declared encoding."
        if self._utf16:
            return raw.decode("utf-16-le", errors="replace")
        return raw.decode(self._codec, errors="replace")

    def _decode_block(self, block, decompressed_size):
        info = self._le32(block, 0)
        compression = info & 0xF
        encryption = (info >> 4) & 0xF
        if encryption != 0:
            raise MdxError("MDX: encrypted dictionary is not supported (Encrypted)")
        data = block[8:]
        if compression == 0:
            return data
        if compression == 1:
            return lzo1x_decompress(data, decompressed_size)
        if compression == 2:
            return zlib.decompress(data)
        raise MdxError(f"MDX: unknown block compression {compression}")

    # -- header --

    def _read_header(self):
        data = self._data
        if len(data) < 8:
            raise MdxError("MDX: file is too short to be a dictionary")

        header_len = self._be(data, 0, 4)
        header_bytes = data[4 : 4 + header_len]
        # The 4 bytes after the header XML are an adler32 checksum, written
        # for v1.2 as well as v2.0 (writemdict._write_header writes it
        # unconditionally).  Not verified -- a bad checksum just means a
        # mangled header, which the tag parse below catches anyway.
        self._key_block_offset = 4 + header_len + 4

        # UTF-16LE text, terminated by \x00\x00.
        text_bytes = header_bytes
        if len(text_bytes) >= 2 and text_bytes[-2:] == b"\x00\x00":
            text_bytes = text_bytes[:-2]
        header_text = text_bytes.decode("utf-16-le", errors="replace")

        tags = {}
        for m in _HEADER_TAG_RE.finditer(header_text):
            value = m.group(2) or ""
            for entity, char in (
                ("&lt;", "<"),
                ("&gt;", ">"),
                ("&quot;", '"'),
                ("&amp;", "&"),
            ):
                value = value.replace(entity, char)
            tags[m.group(1)] = value
        self.title = tags.get("Title", "")

        encoding = (tags.get("Encoding") or "utf-8").upper()
        if encoding in ("GBK", "GB2312"):
            encoding = "GB18030"
        if encoding not in ("UTF-8", "UTF-16", "UTF-16LE", "GB18030"):
            raise MdxError(
                f"MDX: unsupported encoding {encoding} "
                "(only UTF-8 / UTF-16 / GB18030)"
            )
        self.encoding = encoding
        self._utf16 = encoding.startswith("UTF-16")
        self._codec = "gb18030" if encoding == "GB18030" else "utf-8"

        encrypted = tags.get("Encrypted") or "No"
        try:
            self._encrypt = 0 if encrypted == "No" else int(encrypted)
        except ValueError:
            # v1 spelled it "Yes"/"No"; "Yes" is the record-block
            # encryption, the same keyed scheme as 1.
            self._encrypt = 1 if encrypted.lower() == "yes" else 0
        if self._encrypt & 1:
            # 1 and 3 lock the record blocks behind a per-user
            # registration code.  2 is the export lock, which only
            # transforms the key block info and is undone at read
            # time (see _decode_key_block_info).
            raise MdxError(
                f"MDX: encrypted dictionary is not supported (Encrypted={encrypted})"
            )

        try:
            self.version = float(tags.get("GeneratedByEngineVersion") or "2.0")
        except ValueError:
            self.version = 2.0
        if self.version >= 3:
            # v3 dropped the Encoding attribute and is always UTF-8.
            self.encoding = "UTF-8"
            self._utf16 = False
            self._codec = "utf-8"
        self._number_width = 4 if self.version < 2 else 8

    # -- keys --

    @property
    def entry_count(self):
        "Number of headwords in the dictionary."
        self._ensure_keys()
        return self._num_entries

    @property
    def record_block_offset(self):
        "Offset of the record section; known once the keys are parsed."
        self._ensure_keys()
        return self._record_block_offset

    def _ensure_keys(self):
        if self._key_list is None:
            if self.version >= 3:
                self._read_keys_v3()
            else:
                self._read_keys_v1_v2()

    def _decode_key_block_info(self, info):
        entries = []
        if self.version >= 2:
            # zlib-compressed; the first 4 bytes are 02 00 00 00, the
            # next 4 an adler32, then the zlib stream.
            if self._encrypt & 2:
                # Encrypted=2 ("allow export to text" left unchecked)
                # transforms this section and only this one: the 8-byte
                # block header stays in the clear, the zlib stream
                # behind it is the ciphertext.
                info = decrypt_key_info_block(info)
            decompressed = zlib.decompress(info[8:])
            byte_width = 2
            text_term = 1
            i = 0
            while i < len(decompressed):
                i += self._number_width  # entry count for the block, unused
                head_size = self._be(decompressed, i, byte_width)
                i += byte_width + (head_size + text_term) * (2 if self._utf16 else 1)
                tail_size = self._be(decompressed, i, byte_width)
                i += byte_width + (tail_size + text_term) * (2 if self._utf16 else 1)
                comp_size = self._read_number(decompressed, i)
                i += self._number_width
                decomp_size = self._read_number(decompressed, i)
                i += self._number_width
                entries.append((comp_size, decomp_size))
            return entries

        byte_width = 1
        text_term = 0
        i = 0
        while i < len(info):
            i += self._number_width
            head_size = self._be(info, i, byte_width)
            # v1 stores head/tail sizes as character counts, so UTF-16
            # needs the x2 (measured on the Shogakukan V2 file: 87
            # blocks, info length 3776, which walks off the end without it).
            i += byte_width + (head_size + text_term) * (2 if self._utf16 else 1)
            tail_size = self._be(info, i, byte_width)
            i += byte_width + (tail_size + text_term) * (2 if self._utf16 else 1)
            comp_size = self._read_number(info, i)
            i += self._number_width
            decomp_size = self._read_number(info, i)
            i += self._number_width
            entries.append((comp_size, decomp_size))
        return entries

    def _split_key_block(self, block):
        keys = []
        start = 0
        width = 2 if self._utf16 else 1
        while start < len(block):
            key_id = self._read_number(block, start)
            i = start + self._number_width
            while i + width <= len(block):
                is_terminator = (
                    block[i] == 0
                    if width == 1
                    else (block[i] == 0 and block[i + 1] == 0)
                )
                if is_terminator:
                    break
                i += width
            text = self._decode_text(block[start + self._number_width : i]).strip()
            if text:
                keys.append((key_id, text))
            start = i + width
        return keys

    def _decode_key_block(self, compressed, info_list):
        keys = []
        i = 0
        for comp_size, decomp_size in info_list:
            block = self._decode_block(compressed[i : i + comp_size], decomp_size)
            keys.extend(self._split_key_block(block))
            i += comp_size
        return keys

    def _read_keys_v1_v2(self):
        data = self._data
        offset = self._key_block_offset
        # v2 writes five big-endian numbers and then the adler32 *of those
        # numbers*; v1 writes four numbers and no checksum.  (The Dart
        # parser this was ported from stops after the fifth number and so
        # reads the checksum as part of the key block info -- correct for
        # the v1.2 dictionary it was validated against, four bytes off for
        # every v2 file.)
        if self.version >= 2:
            header_size = self._number_width * 5 + 4
        else:
            header_size = self._number_width * 4
        block = data[offset : offset + header_size]

        pos = 0
        pos += self._number_width  # key block count (the key list is authoritative)
        self._num_entries = self._read_number(block, pos)
        pos += self._number_width
        if self.version >= 2:
            pos += self._number_width  # key block info decompressed size
        key_block_info_size = self._read_number(block, pos)
        pos += self._number_width
        key_block_size = self._read_number(block, pos)

        offset += header_size
        key_block_info = data[offset : offset + key_block_info_size]
        offset += key_block_info_size
        info_list = self._decode_key_block_info(key_block_info)

        key_block_compressed = data[offset : offset + key_block_size]
        offset += key_block_size
        self._record_block_offset = offset
        self._key_list = self._decode_key_block(key_block_compressed, info_list)

    def _read_keys_v3(self):
        # v3 files are a sequence of [type(uint32 BE) + size(number) + data]
        # blocks; the key data lives in the 0x03000000 one.
        data = self._data
        offset = self._key_block_offset
        key_data_offset = None
        while True:
            block_type = self._be(data, offset, 4)
            offset += 4
            block_size = self._read_number(data, offset)
            offset += self._number_width
            if block_type == 0x03000000:
                key_data_offset = offset
            offset += block_size
            if offset + 4 > len(data):
                break
        if key_data_offset is None:
            raise MdxError("MDX: v3 file has no key data block")

        pos = key_data_offset
        number = self._be(data, pos, 4)
        pos += 4
        pos += self._number_width  # total size
        keys = []
        for _ in range(number):
            decomp_size = self._be(data, pos, 4)
            pos += 4
            comp_size = self._be(data, pos, 4)
            pos += 4
            block = self._decode_block(data[pos : pos + comp_size], decomp_size)
            pos += comp_size
            keys.extend(self._split_key_block(block))
        self._num_entries = len(keys)
        self._key_list = keys

    # -- records --

    def entries(self):
        """
        Yield ``(key, definition)`` for every headword, in file order.

        Keys and definitions are raw MDX strings; see ``clean_key`` and
        ``clean_definition``.
        """
        self._ensure_keys()
        key_list = self._key_list
        data = self._data

        if self.version >= 3:
            # The key blocks are read above, but v3's record section uses a
            # different block framing that no dictionary on hand exercises.
            # Refusing beats emitting half a dictionary.
            raise MdxError("MDX: v3 record section is not supported yet")

        offset = 0
        key_index = 0
        pos = self._record_block_offset
        num_record_blocks = self._read_number(data, pos)
        pos += self._number_width
        pos += self._number_width  # num entries (should equal the key count)
        pos += self._number_width  # record block info size
        pos += self._number_width  # record block total size

        info = []
        for _ in range(num_record_blocks):
            comp_size = self._read_number(data, pos)
            pos += self._number_width
            decomp_size = self._read_number(data, pos)
            pos += self._number_width
            info.append((comp_size, decomp_size))

        for comp_size, decomp_size in info:
            block = self._decode_block(data[pos : pos + comp_size], decomp_size)
            pos += comp_size

            while key_index < len(key_list):
                record_start = key_list[key_index][0]
                if record_start - offset >= len(block):
                    break
                if key_index + 1 < len(key_list):
                    record_end = key_list[key_index + 1][0]
                else:
                    record_end = len(block) + offset
                key_text = key_list[key_index][1]
                key_index += 1
                raw = block[record_start - offset : record_end - offset]
                # The slice runs to the *next* record's start, so it
                # swallows the NUL that terminates this one -- real files
                # end every record with one (writemdict and MdxBuilder
                # both write it), and _split_key_block already skips the
                # same terminator for keys.  Left in, it survives
                # str.strip() (a NUL is not whitespace) and reaches the
                # card as a trailing \x00.  mdict_utils does the same
                # strip in MDX._treat_record_data.
                yield key_text, self._decode_text(raw).strip("\x00")
            offset += len(block)

    # -- text helpers --

    @staticmethod
    def clean_key(key):
        "Strip the HTML wrapper and entities off a key, leaving the headword."
        k = _TAG_RE.sub("", key or "")
        for entity, char in (
            ("&lt;", "<"),
            ("&gt;", ">"),
            ("&quot;", '"'),
            ("&amp;", "&"),
        ):
            k = k.replace(entity, char)
        return k.strip()

    @staticmethod
    def clean_definition(html_text, max_chars=800):
        """
        Flatten a definition's HTML to plain text.

        Line breaks are preserved, tags and entities are stripped, and
        the result is truncated near ``max_chars`` -- the popup and the
        phone's card only ever show a few lines, and dictionaries
        routinely carry 10 KB of markup per entry.  The cut lands on a
        line, sentence or word boundary (see _truncate_at_boundary), so
        the last block never ends with half a word.
        """
        d = _BR_RE.sub("\n", html_text or "")
        d = _BLOCK_END_RE.sub("\n", d)
        d = _TAG_RE.sub("", d)
        for entity, char in (
            ("&lt;", "<"),
            ("&gt;", ">"),
            ("&quot;", '"'),
            ("&nbsp;", " "),
            ("&amp;", "&"),
        ):
            d = d.replace(entity, char)
        lines = [ln.strip() for ln in d.split("\n")]
        text = "\n".join(ln for ln in lines if ln)
        if len(text) <= max_chars:
            return text
        return _truncate_at_boundary(text, max_chars)
