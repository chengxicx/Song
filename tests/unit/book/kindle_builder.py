"""
Builders for MOBI / AZW3 test files.

The Kindle import is all byte offsets, so the fixtures are assembled
here from the published container layout rather than checked in as
binaries: each helper writes the smallest file that exercises one part
of the reader (PDB record table, PalmDOC text records, EXTH metadata,
MOBI 6 page breaks, KF8 skeleton/fragment indexes, FDST flows).
"""

import struct

UNSET = 0xFFFFFFFF


def _u16(value):
    return struct.pack(">H", value)


def _u32(value):
    return struct.pack(">I", value)


def _pdb(records, name=b"Test Book"):
    "Wrap records in a Palm Database container."
    header = bytearray(78)
    header[0:32] = name.ljust(32, b"\0")[:32]
    header[60:64] = b"BOOK"
    header[64:68] = b"MOBI"
    header[76:78] = _u16(len(records))
    info = bytearray()
    offset = 78 + 8 * len(records)
    for record in records:
        info += _u32(offset) + b"\0\0\0\0"
        offset += len(record)
    return bytes(header) + bytes(info) + b"".join(records)


def _record0(
    title,
    author,
    version=6,
    encoding=65001,
    compression=1,
    num_text_records=1,
    resource_start=UNSET,
    extra_exth=(),
    encryption=0,
    trailing_flags=0,
    indx=UNSET,
    kf8=None,
):
    "Record 0: PalmDOC header + MOBI header + EXTH + full title."
    # A KF8 header carries fields out to offset 264, so it must be at
    # least that long for the EXTH block not to overwrite them.
    mobi_length = 264 if kf8 else 232
    exth_records = [(100, author.encode()), (503, title.encode())] + list(extra_exth)
    exth_body = b"".join(
        _u32(rtype) + _u32(len(value) + 8) + value for rtype, value in exth_records
    )
    exth = b"EXTH" + _u32(12 + len(exth_body)) + _u32(len(exth_records)) + exth_body
    exth += b"\0" * ((4 - len(exth) % 4) % 4)
    title_bytes = title.encode()
    title_offset = 16 + mobi_length + len(exth)

    record = bytearray(title_offset + len(title_bytes) + 4)
    record[0:2] = _u16(compression)
    record[8:10] = _u16(num_text_records)
    record[10:12] = _u16(4096)
    record[12:14] = _u16(encryption)
    record[16:20] = b"MOBI"
    record[20:24] = _u32(mobi_length)
    record[24:28] = _u32(2)
    record[28:32] = _u32(encoding)
    record[32:36] = _u32(1234)
    record[36:40] = _u32(version)
    record[84:88] = _u32(title_offset)
    record[88:92] = _u32(len(title_bytes))
    record[108:112] = _u32(resource_start)
    record[128:132] = _u32(0x40)
    record[240:244] = _u32(trailing_flags)
    record[244:248] = _u32(indx)
    if kf8:
        record[192:196] = _u32(kf8["fdst"])
        record[196:200] = _u32(kf8.get("num_fdst", 2))
        record[248:252] = _u32(kf8["frag"])
        record[252:256] = _u32(kf8["skel"])
        record[260:264] = _u32(UNSET)
    record[16 + mobi_length : 16 + mobi_length + len(exth)] = exth
    record[title_offset : title_offset + len(title_bytes)] = title_bytes
    return bytes(record)


def _palmdoc_literal_encode(data):
    "A valid literal-only PalmDOC stream (no back references)."
    out = bytearray()
    i = 0
    while i < len(data):
        byte = data[i]
        if byte == 0 or 9 <= byte <= 0x7F:
            out.append(byte)
            i += 1
        else:
            run = data[i : i + 8]
            out.append(len(run))
            out += run
            i += len(run)
    return bytes(out)


def _text_records(raw, compression):
    chunks = [raw[i : i + 4096] for i in range(0, len(raw), 4096)] or [b""]
    if compression == 2:
        return [_palmdoc_literal_encode(c) for c in chunks]
    return chunks


FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"not-a-real-jpeg" + b"\xff\xd9"


def build_mobi6(
    title="Test Book",
    author="Test Author",
    sections=None,
    image=None,
    compression=1,
    encryption=0,
):
    """
    A MOBI 6 file: one HTML stream, sections joined by <mbp:pagebreak/>.

    `sections` are full HTML documents; `image` is embedded as a
    resource, referenced from markup with recindex="1".
    """
    sections = sections or ["<html><body><p>one</p></body></html>"]
    raw = b"<mbp:pagebreak/>".join(s.encode() for s in sections)
    records = [
        b"",  # record 0 placeholder
    ]
    text_records = _text_records(raw, compression)
    records += text_records
    resource_start = UNSET
    if image is not None:
        resource_start = len(records)
        records.append(image)
    records[0] = _record0(
        title,
        author,
        version=6,
        compression=compression,
        num_text_records=len(text_records),
        resource_start=resource_start,
        encryption=encryption,
    )
    return _pdb(records)


def _varlen(value):
    "MOBI variable-width integer, terminated by the high bit."
    if value < 0x80:
        return bytes([value | 0x80])
    chunks = [value & 0x7F]
    value >>= 7
    while value:
        chunks.append(value & 0x7F)
        value >>= 7
    out = bytearray()
    for chunk in reversed(chunks[1:]):
        out.append(chunk)
    out.append(chunks[0] | 0x80)
    return bytes(out)


def _tagx(tags):
    "TAGX section: one control byte, then (tag, count, mask, end) rows."
    body = b"".join(bytes((tag, count, mask, end)) for tag, count, mask, end in tags)
    return b"TAGX" + _u32(12 + len(body)) + _u32(1) + body


def _indx_data(entries):
    "An INDX record holding entries (name + control byte + var-len values)."
    header = bytearray(192)
    header[0:4] = b"INDX"
    header[4:8] = _u32(192)
    header[24:28] = _u32(len(entries))
    header[28:32] = _u32(65001)
    offsets = []
    position = 192
    for entry in entries:
        offsets.append(position)
        position += len(entry)
    idxt_offset = position
    header[20:24] = _u32(idxt_offset)
    idxt = b"IDXT" + b"".join(_u16(o) for o in offsets)
    return bytes(header) + b"".join(entries) + idxt


def _indx_primary(tagx, num_data_records=1):
    "The INDX record that carries the header and the TAGX tag table."
    header = bytearray(192)
    header[0:4] = b"INDX"
    header[4:8] = _u32(192)
    header[20:24] = _u32(192 + len(tagx))
    header[24:28] = _u32(num_data_records)
    header[28:32] = _u32(65001)
    return bytes(header) + tagx


def _entry(name, control, values):
    "One index entry: name length + name + control bytes + var-len values."
    return (
        bytes([len(name)])
        + name.encode()
        + bytes(control)
        + b"".join(_varlen(v) for v in values)
    )


SKEL_TAGS = [
    # tag 1: fragments per skeleton (presence flag), tag 6: offset+length.
    (1, 1, 0x01, 0),
    (6, 2, 0x0C, 0),
]
FRAG_TAGS = [
    (2, 1, 0x01, 0),  # selector string (unused here)
    (4, 1, 0x02, 0),  # fragment id
    (6, 2, 0x0C, 0),  # offset + length
]


def build_kf8(title="KF8 Book", author="KF8 Author", chapters=None, image=None):
    """
    A KF8 (AZW3) file: each chapter is a skeleton with one fragment
    spliced in before </body>, plus a CSS flow and an FDST table.
    """
    chapters = chapters or [("Chapter One", ["Hello KF8."])]

    flow = bytearray()
    skeletons = []
    fragments = []
    for heading, paragraphs in chapters:
        skeleton = (
            "<html><head><title>t</title>"
            '<link href="kindle:flow:0001?mime=text/css" rel="stylesheet" '
            'type="text/css"/></head><body></body></html>'
        )
        insert_at = skeleton.index("</body>")
        body = f"<h1>{heading}</h1>" + "".join(f"<p>{p}</p>" for p in paragraphs)
        if image is not None and heading == chapters[0][0]:
            body += '<img src="kindle:embed:0001?mime=image/jpeg"/>'
        skel_start = len(flow)
        flow += skeleton.encode()
        frag_start = len(flow)
        flow += body.encode()
        skeletons.append((skel_start, len(skeleton)))
        fragments.append(
            (
                skel_start + insert_at,
                frag_start - (skel_start + len(skeleton)),
                len(body),
            )
        )

    css = b"p { margin: 0; }"
    css_start = len(flow)
    flow += css
    raw = bytes(flow)
    text_records = _text_records(raw, 1)

    records = [b""]
    records += text_records
    resource_start = len(records)
    if image is not None:
        records.append(image)

    fdst_record = (
        b"FDST"
        + b"\0\0\0\0"
        + _u32(2)
        + _u32(0)
        + _u32(css_start)
        + _u32(css_start)
        + _u32(len(raw))
    )
    fdst_index = len(records)
    records.append(fdst_record)

    skel_entries = [
        _entry(f"s{position}", [0xFF], [1, offset, length])
        for position, (offset, length) in enumerate(skeletons)
    ]
    skel_primary = len(records)
    records.append(_indx_primary(_tagx(SKEL_TAGS)))
    records.append(_indx_data(skel_entries))

    frag_entries = [
        _entry(str(insert), [0xFF], [1, offset, length])
        for insert, offset, length in fragments
    ]
    frag_primary = len(records)
    records.append(_indx_primary(_tagx(FRAG_TAGS)))
    records.append(_indx_data(frag_entries))

    records[0] = _record0(
        title,
        author,
        version=8,
        num_text_records=len(text_records),
        resource_start=resource_start,
        kf8={"fdst": fdst_index, "frag": frag_primary, "skel": skel_primary},
    )
    return _pdb(records)


def build_combo(mobi6_html, kf8_chapters, title="Combo Book"):
    "A MOBI 6 part followed by a KF8 part (EXTH 121 points at it)."
    mobi6 = build_mobi6(title=title, sections=[mobi6_html])
    # Rebuild the MOBI 6 records so the KF8 part can be appended.
    kf8 = build_kf8(title=title, chapters=kf8_chapters)

    mobi6_records = _records_of(mobi6)
    kf8_records = _records_of(kf8)
    boundary = len(mobi6_records)
    mobi6_records[0] = _record0(
        title,
        "Combo Author",
        version=6,
        num_text_records=_u16_at(mobi6_records[0], 8),
        resource_start=UNSET,
        extra_exth=[(121, _u32(boundary))],
    )
    return _pdb(mobi6_records + kf8_records)


def _records_of(data):
    "Split a PDB built by _pdb back into its records."
    count = struct.unpack(">H", data[76:78])[0]
    offsets = [
        struct.unpack(">I", data[78 + i * 8 : 82 + i * 8])[0] for i in range(count)
    ]
    offsets.append(len(data))
    return [data[offsets[i] : offsets[i + 1]] for i in range(count)]


def _u16_at(buffer, offset):
    return struct.unpack(">H", buffer[offset : offset + 2])[0]
