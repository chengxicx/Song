"""
MOBI / AZW / AZW3 (KF8) reading, converted to an EPUB in memory.

Lute's ebook import (chapter splitting, preview, reader mode) is built
around EPUB, so Kindle formats are handled by *converting* them rather
than by growing a second import path: `kindle_to_epub` reads the
Palm Database container, decompresses the text, rebuilds one HTML
document per book section, embeds the images, and returns a valid
EPUB 2 file.  Everything downstream (parse_epub, extract_epub_package,
the import routes) then runs unchanged.

What is parsed here:

  PDB         Palm Database container: record table.
  PalmDOC     header at the start of record 0 (compression, record count).
  MOBI        header (encoding, version, EXTH flag, resource start) and
              the EXTH metadata block (title, author, KF8 boundary).
  HUFF/CDIC   Amazon's Huffman compression (used by many commercial
              files); PalmDOC LZ77 and uncompressed are the others.
  MOBI 6      one HTML stream split into sections at <mbp:pagebreak>;
              images referenced by the `recindex` attribute.
  KF8/AZW3    skeleton + fragment indexes (INDX/TAGX/CNCX) spliced back
              into one XHTML per spine file; images referenced by
              `kindle:embed:` URIs, stylesheets by `kindle:flow:`.

The byte layout follows the published MOBI notes on MobileRead and the
same reconstruction steps as foliate-js's mobi.js (MIT licensed), which
is the clearest low-level description of the KF8 indexes available.

Not supported: DRM-encrypted books (rejected with a clear message) and
fixed-layout/comic KF8 files, which have no reflowable text.
"""

import html as html_module
import re
import zipfile
from io import BytesIO

from bs4 import BeautifulSoup

from lute.book.service import BookImportException

# Extensions routed here.  .prc is MOBI's older extension; PalmDOC-only
# .prc files have a different container magic and are rejected.
KINDLE_EXTENSIONS = (".mobi", ".azw", ".azw3", ".prc")

_MOBI_MAGIC = b"BOOKMOBI"
_UNSET = 0xFFFFFFFF

# MOBI 6 section break element, split in the raw (still encoded) stream
# so that section offsets match the byte offsets used by filepos links.
_PAGEBREAK_RE = re.compile(rb"<\s*(?:mbp:)?pagebreak[^>]*>", re.IGNORECASE)

# KF8 resource URIs.
_EMBED_RE = re.compile(r"kindle:embed:([0-9A-Va-v]+)(?:\?mime=([-+.\w/]+))?")
_FLOW_RE = re.compile(r"kindle:flow:([0-9A-Va-v]+)(?:\?mime=([-+.\w/]+))?")

_BODY_RE = re.compile(r"<body[^>]*>(.*?)(?:</body\s*>|\Z)", re.IGNORECASE | re.DOTALL)

# Image containers we can name for the EPUB manifest; anything else is
# dropped (Kindle audio/video/embedded fonts have no place in a text book).
_IMAGE_TYPES = (
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
    (b"BM", "bmp", "image/bmp"),
    (b"RIFF", "webp", "image/webp"),
)


def is_kindle_ebook(filename, data=b""):
    """
    True when the upload should go through the Kindle reader: either the
    extension says so or the container magic does.
    """
    name = (filename or "").lower()
    if name.endswith(KINDLE_EXTENSIONS):
        return True
    return bytes(data[60:68]) == _MOBI_MAGIC


def kindle_to_epub(data):
    """
    Convert MOBI / AZW / AZW3 bytes to EPUB bytes.

    Raises BookImportException with a user-facing message when the file
    is encrypted, truncated, or in a variant that cannot be rebuilt.
    """
    book = _KindleBook(data)
    return book.to_epub()


# --------------------------------------------------------------------
# small binary helpers
# --------------------------------------------------------------------


def _u16(buf, off):
    "Big-endian unsigned 16-bit, 0 past the end."
    return int.from_bytes(buf[off : off + 2], "big") if len(buf) >= off + 2 else 0


def _u32(buf, off):
    "Big-endian unsigned 32-bit, 0 past the end."
    return int.from_bytes(buf[off : off + 4], "big") if len(buf) >= off + 4 else 0


def _varlen(buf, pos=0):
    """
    MOBI variable-width integer: 7 bits per byte, high bit terminates.
    Returns (value, bytes_consumed).
    """
    value = 0
    length = 0
    for byte in buf[pos : pos + 4]:
        value = (value << 7) | (byte & 0x7F)
        length += 1
        if byte & 0x80:
            break
    return value, length


def _varlen_from_end(buf):
    "Variable-width integer stored backwards at the end of a record."
    value = 0
    for byte in buf[-4:]:
        if byte & 0x80:
            value = 0
        value = (value << 7) | (byte & 0x7F)
    return value


def _popcount(value):
    return bin(value).count("1")


def _trailing_zero_bits(value):
    "How many low bits of value are 0 (the shift used by a TAGX mask)."
    count = 0
    while value and (value & 1) == 0:
        value >>= 1
        count += 1
    return count


def _image_type(data):
    "('jpg', 'image/jpeg') for a known image container, else None."
    if not data:
        return None
    for magic, ext, mime in _IMAGE_TYPES:
        if data.startswith(magic):
            if magic == b"RIFF" and data[8:12] != b"WEBP":
                continue
            return ext, mime
    return None


def _decode(raw, encoding):
    "Decode with the header's declared encoding, tolerating bad bytes."
    codec = "utf-8" if encoding == 65001 else "cp1252"
    try:
        return raw.decode(codec)
    except (UnicodeDecodeError, LookupError):
        return raw.decode(codec if codec != "utf-8" else "utf-8", errors="replace")


def _xml_escape(text):
    "Minimal XML text escaping for the OPF/NCX."
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# --------------------------------------------------------------------
# decompressors
# --------------------------------------------------------------------


def _decompress_palmdoc(data):
    "PalmDOC LZ77 ('length, distance' pairs plus byte-pair spaces)."
    out = bytearray()
    i = 0
    size = len(data)
    while i < size:
        byte = data[i]
        if byte == 0 or 9 <= byte <= 0x7F:
            out.append(byte)
            i += 1
        elif byte <= 8:
            out += data[i + 1 : i + 1 + byte]
            i += 1 + byte
        elif byte <= 0xBF:
            if i + 1 >= size:
                break
            pair = (byte << 8) | data[i + 1]
            i += 2
            distance = (pair & 0x3FFF) >> 3
            length = (pair & 0x07) + 3
            if distance == 0 or distance > len(out):
                continue
            start = len(out) - distance
            for k in range(length):
                out.append(out[start + k])
        else:
            out += bytes((32, byte ^ 0x80))
            i += 1
    return bytes(out)


class _HuffCdic:
    "Amazon HUFF/CDIC decompressor (built from the HUFF + CDIC records)."

    def __init__(self, load_record, first_record, num_records):
        huff = load_record(first_record)
        if huff[0:4] != b"HUFF":
            raise BookImportException("This e-book is damaged (missing HUFF table).")
        offset1 = _u32(huff, 8)
        offset2 = _u32(huff, 12)

        # table1 is indexed by the top byte of the code stream.
        self._table1 = []
        for i in range(256):
            x = _u32(huff, offset1 + i * 4)
            self._table1.append((x & 0x80, x & 0x1F, x >> 8))

        # table2 is indexed by code length, 1..32.
        self._table2 = [None]
        for i in range(32):
            self._table2.append(
                (_u32(huff, offset2 + i * 8), _u32(huff, offset2 + i * 8 + 4))
            )

        self._dictionary = []
        for i in range(1, max(1, num_records)):
            cdic = load_record(first_record + i)
            if cdic[0:4] != b"CDIC":
                raise BookImportException(
                    "This e-book is damaged (missing CDIC dictionary)."
                )
            header_length = _u32(cdic, 4)
            num_entries = _u32(cdic, 8)
            code_length = _u32(cdic, 12)
            chunk = cdic[header_length:]
            capacity = 1 << min(code_length, 24)
            count = min(capacity, num_entries - len(self._dictionary))
            for j in range(max(0, count)):
                offset = _u16(chunk, j * 2)
                x = _u16(chunk, offset)
                length = x & 0x7FFF
                plain = bool(x & 0x8000)
                value = chunk[offset + 2 : offset + 2 + length]
                self._dictionary.append([bytes(value), plain])

    def decompress(self, data):
        "Expand one text record."
        out = bytearray()
        bit_length = len(data) * 8
        i = 0
        while i < bit_length:
            bits = self._bits32(data, i)
            found, code_length, value = self._table1[bits >> 24]
            if not found:
                while code_length < 32:
                    limit = self._table2[code_length][0]
                    if (bits >> (32 - code_length)) >= limit:
                        break
                    code_length += 1
                value = self._table2[min(code_length, 32)][1]
            i += code_length
            if i > bit_length:
                break
            code = value - (bits >> (32 - code_length))
            if code < 0 or code >= len(self._dictionary):
                break
            entry = self._dictionary[code]
            if not entry[1]:
                entry[0] = self.decompress(entry[0])
                entry[1] = True
            out += entry[0]
        return bytes(out)

    @staticmethod
    def _bits32(data, from_bit):
        "The 32 bits starting at from_bit, big-endian, zero-padded."
        start_byte = from_bit >> 3
        end_bit = from_bit + 32
        end_byte = end_bit >> 3
        bits = 0
        for i in range(start_byte, end_byte + 1):
            bits = (bits << 8) | (data[i] if i < len(data) else 0)
        return (bits >> (8 - (end_bit & 7))) & 0xFFFFFFFF


# --------------------------------------------------------------------
# the converted book
# --------------------------------------------------------------------


class _KindleBook:
    "One MOBI/KF8 file, parsed into sections ready for EPUB export."

    def __init__(self, data):
        self.data = data
        self.records = self._split_records(data)
        self.headers = self._headers(self.records[0])
        self.start = 0
        self.is_kf8 = self.headers["mobi"]["version"] >= 8

        boundary = self.headers["exth"].get(121)
        if not self.is_kf8 and boundary is not None and boundary < _UNSET:
            # A "combo" file carries both MOBI 6 and KF8; KF8 renders
            # better (real XHTML + CSS), so prefer it when it parses.
            if 0 < boundary < len(self.records):
                try:
                    headers = self._headers(self.records[boundary])
                except BookImportException:
                    headers = None
                if headers is not None and headers["mobi"]["version"] >= 8:
                    self.headers = headers
                    self.start = boundary
                    self.is_kf8 = True

        if self.headers["palmdoc"]["encryption"]:
            raise BookImportException(
                "This e-book is DRM-protected, so it cannot be imported. "
                "Please use a DRM-free copy."
            )

        self._setup()
        self.sections = []
        self.images = {}
        self.css_parts = []
        self._css_flows = set()
        self._build_sections()

    # -- container -------------------------------------------------

    @staticmethod
    def _split_records(data):
        "PDB record table -> list of record byte strings."
        if len(data) < 78:
            raise BookImportException("This file is too small to be a MOBI e-book.")
        if bytes(data[60:68]) != _MOBI_MAGIC:
            raise BookImportException(
                "Not a MOBI / AZW / AZW3 e-book (unexpected container header)."
            )
        num_records = _u16(data, 76)
        if num_records <= 0:
            raise BookImportException("This e-book has no records (file is damaged).")
        offsets = [_u32(data, 78 + i * 8) for i in range(num_records)]
        offsets = [o for o in offsets if 0 < o < len(data)]
        if not offsets:
            raise BookImportException("This e-book's record table is damaged.")
        offsets.append(len(data))
        return [data[offsets[i] : offsets[i + 1]] for i in range(len(offsets) - 1)]

    def _load(self, index):
        "One record of the selected part (MOBI 6 or KF8)."
        actual = self.start + index
        if actual < 0 or actual >= len(self.records):
            return b""
        return self.records[actual]

    def _headers(self, rec0):
        "PalmDOC + MOBI (+ EXTH, + KF8) headers of a part's record 0."
        if len(rec0) < 132 or bytes(rec0[16:20]) != b"MOBI":
            raise BookImportException(
                "Not a MOBI / AZW / AZW3 e-book (missing MOBI header)."
            )
        palmdoc = {
            "compression": _u16(rec0, 0),
            "num_text_records": _u16(rec0, 8),
            "record_size": _u16(rec0, 10),
            "encryption": _u16(rec0, 12),
        }
        mobi = {
            "length": _u32(rec0, 20),
            "type": _u32(rec0, 24),
            "encoding": _u32(rec0, 28),
            "uid": _u32(rec0, 32),
            "version": _u32(rec0, 36),
            "title_offset": _u32(rec0, 84),
            "title_length": _u32(rec0, 88),
            "resource_start": _u32(rec0, 108),
            "huffcdic": _u32(rec0, 112),
            "num_huffcdic": _u32(rec0, 116),
            "exth_flags": _u32(rec0, 128),
            "trailing_flags": _u32(rec0, 240),
            "indx": _u32(rec0, 244),
        }
        exth = {}
        if mobi["exth_flags"] & 0x40:
            try:
                exth = self._exth(rec0[mobi["length"] + 16 :], mobi["encoding"])
            except BookImportException:
                exth = {}
        kf8 = None
        if mobi["version"] >= 8 and len(rec0) >= 264:
            kf8 = {
                "resource_start": _u32(rec0, 108),
                "fdst": _u32(rec0, 192),
                "num_fdst": _u32(rec0, 196),
                "frag": _u32(rec0, 248),
                "skel": _u32(rec0, 252),
                "guide": _u32(rec0, 260),
            }
        return {"palmdoc": palmdoc, "mobi": mobi, "exth": exth, "kf8": kf8}

    @staticmethod
    def _exth(buf, encoding):
        "EXTH metadata block -> {type: value} (types we care about)."
        if len(buf) < 12 or bytes(buf[0:4]) != b"EXTH":
            return {}
        count = _u32(buf, 8)
        wanted = {100, 101, 103, 503, 524, 121}
        ret = {}
        offset = 12
        for _ in range(count):
            if offset + 8 > len(buf):
                break
            rtype = _u32(buf, offset)
            length = _u32(buf, offset + 4)
            if length < 8:
                break
            if rtype in wanted:
                data = buf[offset + 8 : offset + length]
                if rtype == 121:
                    ret[rtype] = _u32(data, 0)
                else:
                    ret[rtype] = _decode(data, encoding).strip()
            offset += length
        return ret

    # -- text records ---------------------------------------------

    def _setup(self):
        "Pick the decompressor and prepare trailing-byte trimming."
        compression = self.headers["palmdoc"]["compression"]
        if compression == 1:
            self._decompress = lambda chunk: chunk
        elif compression == 2:
            self._decompress = _decompress_palmdoc
        elif compression == 17480:
            mobi = self.headers["mobi"]
            self._decompress = _HuffCdic(
                self._load, mobi["huffcdic"], mobi["num_huffcdic"]
            ).decompress
        else:
            raise BookImportException(
                f"Unsupported e-book compression type ({compression})."
            )
        self._trailing_flags = self.headers["mobi"]["trailing_flags"]

    def _strip_trailing(self, chunk):
        """
        Kindle appends bookkeeping to the end of each text record (extra
        multibyte bytes, TBS descriptions, uncrossable breaks); the flags
        say how many.  They must go before decompression.
        """
        flags = self._trailing_flags
        if not flags:
            return chunk
        for _ in range(_popcount(flags >> 1)):
            if not chunk:
                break
            chunk = chunk[: len(chunk) - _varlen_from_end(chunk)]
        if flags & 1 and chunk:
            chunk = chunk[: len(chunk) - ((chunk[-1] & 0x03) + 1)]
        return chunk

    def _raw_text(self):
        "The whole (uncompressed) text stream of this part."
        count = self.headers["palmdoc"]["num_text_records"]
        if count <= 0:
            raise BookImportException("This e-book contains no text records.")
        chunks = []
        for i in range(count):
            record = self._load(i + 1)
            if not record:
                break
            chunks.append(self._decompress(self._strip_trailing(record)))
        raw = b"".join(chunks)
        if not raw:
            raise BookImportException("This e-book contains no readable text.")
        return raw

    # -- sections -------------------------------------------------

    def _build_sections(self):
        raw = self._raw_text()
        if self.is_kf8:
            kf8 = self.headers.get("kf8") or {}
            if kf8.get("skel", _UNSET) >= _UNSET:
                raise BookImportException(
                    "This e-book has no KF8 text index, so it cannot be read."
                )
            try:
                self._build_kf8_sections(raw, kf8)
            except BookImportException:
                raise
            except Exception as e:  # pylint: disable=broad-except
                raise BookImportException(
                    f"Could not read this KF8 e-book (error: {e})", cause=e
                ) from e
        else:
            self._build_mobi6_sections(raw)
        self.sections = [s for s in self.sections if s["html"].strip()]
        if not self.sections:
            raise BookImportException("No readable text was found in this e-book.")

    def _build_mobi6_sections(self, raw):
        "Split the single MOBI 6 HTML stream at <mbp:pagebreak> elements."
        encoding = self.headers["mobi"]["encoding"]
        starts = [0] + [m.start() for m in _PAGEBREAK_RE.finditer(raw)]
        images = {}
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(raw)
            chunk = raw[start:end]
            if not chunk.strip():
                continue
            markup = _strip_pagebreaks(_decode(chunk, encoding))
            markup, found = self._rewrite_mobi6_resources(markup)
            for name, data, mime in found:
                images[name] = (data, mime)
            self.sections.append({"html": markup, "title": ""})
        self.images = images

    def _rewrite_mobi6_resources(self, markup):
        "recindex=N -> images/resN.ext, collecting the image bytes."
        found = {}

        def repl(match):
            recindex = int(match.group(1))
            data = self._resource(recindex - 1)
            ext, mime = _image_type(data) if data else (None, None)
            if ext is None:
                return ""
            name = f"images/res{recindex}.{ext}"
            found[name] = (data, mime)
            return f'src="{name}"'

        markup = re.sub(
            r"recindex=[\"']?(\d+)[\"']?", repl, markup, flags=re.IGNORECASE
        )
        return markup, [(n, d, m) for n, (d, m) in found.items()]

    def _resource(self, index):
        "One resource record's bytes; tries part-relative then absolute."
        if index < 0:
            return None
        starts = [self.headers["mobi"]["resource_start"]]
        if self.start:
            starts.append(self.start + starts[0])
        for start in starts:
            if start >= _UNSET:
                continue
            actual = start + index
            if 0 <= actual < len(self.records):
                record = self.records[actual]
                if record[:4] in (b"FONT", b"RESC", b"PAGE", b"VIDE", b"AUDI"):
                    continue
                if _image_type(record) is not None:
                    return record
        return None

    def _build_kf8_sections(self, raw, kf8):
        "Rebuild each spine file from its skeleton and fragments."
        fdst = self._fdst(kf8)
        skel_index = self._index_data(kf8["skel"])
        frag_index = self._index_data(kf8["frag"])
        frags = []
        for entry in frag_index["table"]:
            tags = entry["tags"]
            geometry = tags.get(6) or [0, 0]
            frags.append(
                {
                    "insert": int(entry["name"] or 0),
                    "fid": (tags.get(4) or [0])[0],
                    "offset": geometry[0],
                    "length": geometry[1] if len(geometry) > 1 else 0,
                }
            )

        cursor = 0
        titles = self._kf8_titles(kf8)
        images = {}
        for position, entry in enumerate(skel_index["table"]):
            tags = entry["tags"]
            geometry = tags.get(6) or [0, 0]
            skel_offset = geometry[0]
            skel_length = geometry[1] if len(geometry) > 1 else 0
            num_frag = (tags.get(1) or [0])[0]
            section_frags = frags[cursor : cursor + num_frag]
            cursor += num_frag
            if not section_frags or skel_length <= 0:
                continue
            total = skel_length + sum(f["length"] for f in section_frags)
            chunk = raw[skel_offset : skel_offset + total]
            if len(chunk) < skel_length:
                continue
            skeleton = chunk[:skel_length]
            for frag in section_frags:
                insert = frag["insert"] - skel_offset
                offset = skel_length + frag["offset"]
                piece = chunk[offset : offset + frag["length"]]
                if not piece or insert < 0 or insert > len(skeleton):
                    continue
                skeleton = skeleton[:insert] + piece + skeleton[insert:]
            markup = _decode(skeleton, self.headers["mobi"]["encoding"])
            markup, found = self._rewrite_kf8_resources(markup, fdst, raw)
            images.update(found)
            self.sections.append({"html": markup, "title": titles.get(position, "")})
        self.images = images

    def _fdst(self, kf8):
        "Flow table: [(start, end)] byte ranges inside the text stream."
        record = self._load(kf8["fdst"])
        if record[0:4] != b"FDST":
            return []
        count = _u32(record, 8)
        return [
            (_u32(record, 12 + i * 8), _u32(record, 16 + i * 8)) for i in range(count)
        ]

    def _rewrite_kf8_resources(self, markup, fdst, raw):
        "kindle:embed / kindle:flow URIs -> real EPUB-relative paths."
        found = {}

        def embed(match):
            index = int(match.group(1), 32) - 1
            data = self._resource(index)
            ext, mime = _image_type(data) if data else (None, None)
            if ext is None:
                return ""
            name = f"images/res{index + 1}.{ext}"
            found[name] = (data, mime)
            return name

        def flow(match):
            if (match.group(2) or "") != "text/css":
                return ""
            number = int(match.group(1), 32)
            if number >= len(fdst):
                return ""
            if number not in self._css_flows:
                self._css_flows.add(number)
                start, end = fdst[number]
                css = _decode(raw[start:end], 65001)
                if css.strip():
                    self.css_parts.append(css)
            return "book.css"

        markup = _EMBED_RE.sub(embed, markup)
        markup = _FLOW_RE.sub(flow, markup)
        # In-book links point at KF8 fragment positions that no longer
        # exist once the book is an EPUB; drop them rather than ship
        # dead `kindle:` URLs into the reading page.
        markup = re.sub(r"kindle:pos:[^\"'\s>]+", "#", markup)
        return markup, found

    def _kf8_titles(self, kf8):
        "Section titles from the NCX, as {skeleton position: label}."
        indx = self.headers["mobi"]["indx"]
        if indx >= _UNSET:
            return {}
        try:
            data = self._index_data(indx)
            skeleton = self._index_data(kf8["skel"])
        except BookImportException:
            return {}
        fid_map = {}
        cursor = 0
        for position, entry in enumerate(skeleton["table"]):
            count = (entry["tags"].get(1) or [0])[0]
            for fid in range(cursor, cursor + count):
                fid_map[fid] = position
            cursor += count

        titles = {}
        for entry in data["table"]:
            tags = entry["tags"]
            if (tags.get(4) or [0])[0] != 0:
                continue  # nested TOC entry, keep the top level only
            pos = tags.get(6) or []
            if not pos:
                continue
            fid, off = pos[0], pos[1] if len(pos) > 1 else 0
            if off != 0:
                continue
            label = data["cncx"].get((tags.get(3) or [0])[0], "")
            position = fid_map.get(fid)
            if position is not None and label and position not in titles:
                titles[position] = label
        return titles

    # -- index records --------------------------------------------

    def _index_data(self, index):
        """
        Parse one INDX record set: the primary record carries the header,
        the TAGX tag table and a CNCX string table; the following records
        carry the entries (one control-byte stream each).
        """
        primary = self._load(index)
        if primary[0:4] != b"INDX":
            raise BookImportException("This e-book's index is damaged.")
        header_length = _u32(primary, 4)
        num_records = _u32(primary, 24)
        encoding = _u32(primary, 28)
        num_cncx = _u32(primary, 52)

        tagx = primary[header_length:]
        if tagx[0:4] != b"TAGX":
            raise BookImportException("This e-book's index table is damaged.")
        tagx_length = _u32(tagx, 4)
        num_control_bytes = _u32(tagx, 8)
        num_tags = max(0, (tagx_length - 12) // 4)
        tag_table = [tuple(tagx[12 + i * 4 : 16 + i * 4]) for i in range(num_tags)]

        cncx = {}
        cncx_offset = 0
        for i in range(num_cncx):
            record = self._load(index + num_records + i + 1)
            pos = 0
            while pos < len(record):
                start = pos
                length, consumed = _varlen(record, pos)
                pos += consumed
                cncx[cncx_offset + start] = _decode(
                    record[pos : pos + length], encoding
                )
                pos += length
            cncx_offset += 0x10000

        table = []
        for i in range(num_records):
            record = self._load(index + 1 + i)
            if record[0:4] != b"INDX":
                raise BookImportException("This e-book's index data is damaged.")
            idxt = _u32(record, 20)
            entries = _u32(record, 24)
            for j in range(entries):
                offset = _u16(record, idxt + 4 + 2 * j)
                if offset <= 0 or offset >= len(record):
                    continue
                name_length = record[offset]
                name = _decode(record[offset + 1 : offset + 1 + name_length], encoding)
                table.append(
                    {
                        "name": name,
                        "tags": self._entry_tags(
                            record,
                            offset + 1 + name_length,
                            tag_table,
                            num_control_bytes,
                        ),
                    }
                )
        return {"table": table, "cncx": cncx}

    @staticmethod
    def _entry_tags(record, start, tag_table, num_control_bytes):
        "Decode one index entry's tag values (control byte + var-len runs)."
        tags = {}
        control_index = 0
        pos = start + num_control_bytes
        for tag, num_values, mask, end in tag_table:
            if end & 1:
                control_index += 1
                continue
            control = (
                record[start + control_index]
                if start + control_index < len(record)
                else 0
            )
            value = control & mask
            if value == mask:
                if _popcount(mask) > 1:
                    count, consumed = _varlen(record, pos)
                    pos += consumed
                    values = []
                    for _ in range(count * max(1, num_values)):
                        item, consumed = _varlen(record, pos)
                        pos += consumed
                        values.append(item)
                    tags[tag] = values
                else:
                    tags[tag] = [1] * max(1, num_values)
            else:
                tags[tag] = [value >> _trailing_zero_bits(mask)] * max(1, num_values)
        return tags

    # -- export ---------------------------------------------------

    def to_epub(self):
        "Write the sections, stylesheet and images into an EPUB 2 zip."
        title = self.headers["exth"].get(503) or self._header_title()
        author = self.headers["exth"].get(100, "") or ""
        language = self.headers["exth"].get(524, "") or "und"
        title = (title or "Kindle import").strip()[:250]

        documents = []
        for position, section in enumerate(self.sections):
            documents.append(
                (
                    f"ch{position + 1:04d}.xhtml",
                    section["title"] or "",
                    _xhtml_document(section["html"], self.css_parts),
                )
            )

        manifest = [
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        ]
        if self.css_parts:
            manifest.append('<item id="css" href="book.css" media-type="text/css"/>')
        spine = []
        for position, (href, _, _) in enumerate(documents):
            item_id = f"ch{position + 1:04d}"
            manifest.append(
                f'<item id="{item_id}" href="{href}" '
                'media-type="application/xhtml+xml"/>'
            )
            spine.append(f'<itemref idref="{item_id}"/>')
        for name, (_, mime) in sorted(self.images.items()):
            item_id = "img" + re.sub(r"\W", "_", name)
            manifest.append(f'<item id="{item_id}" href="{name}" media-type="{mime}"/>')

        opf = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
            'unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:title>{_xml_escape(title)}</dc:title>"
            f"<dc:creator>{_xml_escape(author)}</dc:creator>"
            f"<dc:language>{_xml_escape(language)}</dc:language>"
            f'<dc:identifier id="bookid">kindle-{self.headers["mobi"]["uid"]}</dc:identifier>'
            "</metadata>"
            f"<manifest>{''.join(manifest)}</manifest>"
            f'<spine toc="ncx">{"".join(spine)}</spine>'
            "</package>"
        )

        # Only sections that carry a real TOC label go into the NCX: an
        # unlabeled section must fall through to the EPUB parser's own
        # heading / "Chapter N" fallback.
        navpoints = "".join(
            '<navPoint id="np{0}" playOrder="{0}"><navLabel><text>{1}</text>'
            '</navLabel><content src="{2}"/></navPoint>'.format(
                position + 1, _xml_escape(label), href
            )
            for position, (href, label, _) in enumerate(documents)
            if label
        )
        ncx = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            f"<head/><docTitle><text>{_xml_escape(title)}</text></docTitle>"
            f"<navMap>{navpoints}</navMap></ncx>"
        )

        container = (
            '<?xml version="1.0"?>'
            '<container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            "<rootfiles>"
            '<rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>"
        )

        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # The mimetype entry must come first and be stored verbatim.
            zf.writestr(
                zipfile.ZipInfo("mimetype"),
                "application/epub+zip",
                compress_type=zipfile.ZIP_STORED,
            )
            zf.writestr("META-INF/container.xml", container)
            zf.writestr("OEBPS/content.opf", opf)
            zf.writestr("OEBPS/toc.ncx", ncx)
            for href, _, markup in documents:
                zf.writestr(f"OEBPS/{href}", markup)
            if self.css_parts:
                zf.writestr("OEBPS/book.css", "\n\n".join(self.css_parts))
            for name, (data, _) in self.images.items():
                zf.writestr(f"OEBPS/{name}", data)
        return buffer.getvalue()

    def _header_title(self):
        "The title stored directly in the MOBI header (no EXTH)."
        mobi = self.headers["mobi"]
        raw = self.data[
            mobi["title_offset"] : mobi["title_offset"] + mobi["title_length"]
        ]
        return html_module.unescape(_decode(raw, mobi["encoding"])).strip()


def _strip_pagebreaks(markup):
    "Remove MOBI 6 page-break elements from decoded markup."
    return re.sub(r"<\s*(?:mbp:)?pagebreak[^>]*>", "", markup, flags=re.IGNORECASE)


def _body_inner(markup):
    "The body children of a document, or the whole thing when it has none."
    match = _BODY_RE.search(markup)
    if match:
        return match.group(1)
    return markup


def _xhtml_document(markup, css_parts):
    """
    Wrap a section's HTML in an XHTML document the EPUB parser/scoper can
    work with.  A stylesheet link is only added when the book carries CSS
    (KF8); MOBI 6 books have inline styling only.
    """
    head = '<meta charset="utf-8"/>'
    if css_parts:
        head += '<link rel="stylesheet" type="text/css" href="book.css"/>'
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        f"{head}</head><body>{_body_inner(markup)}</body></html>"
    )
