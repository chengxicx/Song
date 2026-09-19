"""Move an MP4/M4A 'moov' atom to the front of the file ("faststart").

Why this exists
---------------
A player can only report a media file's duration after it has read the
moov atom.  When moov sits at the *end* of the file -- which is what
MediaRecorder produces (browser audio recording), and what a few
downloaders hand us -- the browser has to fetch the whole file before it
can show a length or seek, and a weak device (e-reader WebView) may well
do exactly that.  On a 64 MB recording that turned "press play" into a
64 MB download.

What it does
------------
A lossless re-mux: no re-encode, no ffmpeg, standard library only.  Only
the chunk offset tables (stco/co64) inside moov change, and only by the
size of moov, because every box before mdat keeps its place and moov is
inserted immediately before mdat.

Safety
------
Everything is validated before the original file is touched:

* only known mp4-family extensions are considered;
* the layout must be one moov + one mdat, with moov after mdat;
* fragmented files (mvex) are left alone;
* every patched offset must fall inside mdat's payload;
* the rewrite goes to a temp file which must come out the same size as
  the original, and only then replaces it.

Anything unexpected returns a non-"rewritten" status and leaves the file
byte-for-byte untouched, so callers can treat this as best-effort.
"""
import os
import struct

FASTSTART_EXTENSIONS = (".m4a", ".m4b", ".mp4", ".mov", ".m4v")

# Boxes whose payload is a list of child boxes; only these are descended
# into when looking for the chunk offset tables.
CONTAINER_BOXES = {
    b"moov",
    b"trak",
    b"mdia",
    b"minf",
    b"stbl",
    b"edts",
    b"udta",
    b"dinf",
}

MAX_MOOV_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024 * 1024
_COPY_CHUNK = 1024 * 1024


def _read_boxes(handle, start, end):
    """
    Yield (type, start, size, header_size) for boxes in [start, end).

    Stops at the first box that doesn't fit, so a truncated or
    non-mp4 file simply yields fewer boxes instead of raising.
    """
    offset = start
    while offset + 8 <= end:
        handle.seek(offset)
        header = handle.read(8)
        if len(header) < 8:
            return
        size = struct.unpack(">I", header[:4])[0]
        typ = header[4:8]
        header_size = 8
        if size == 1:
            extended = handle.read(8)
            if len(extended) < 8:
                return
            size = struct.unpack(">Q", extended)[0]
            header_size = 16
        elif size == 0:
            size = end - offset
        if size < header_size or offset + size > end:
            return
        yield typ, offset, size, header_size
        offset += size


def top_level_boxes(path):
    "Return [(type, start, size, header_size), ...] for the file's top level."
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        return list(_read_boxes(f, 0, size))


def _patch_chunk_offsets(buf, start, end, shift):
    """
    Add shift to every stco/co64 entry in the box tree at [start, end).

    Returns (entry_count, min_original, max_original).  Raises ValueError
    on a malformed table.
    """
    count = 0
    lowest = None
    highest = None
    for typ, box_start, box_size, header_size in _read_boxes_bytearray(buf, start, end):
        if typ in (b"stco", b"co64"):
            wide = typ == b"co64"
            entry_size = 8 if wide else 4
            n = struct.unpack(
                ">I", buf[box_start + header_size + 4 : box_start + header_size + 8]
            )[0]
            table = box_start + header_size + 8
            if table + n * entry_size > box_start + box_size:
                raise ValueError("chunk offset table overruns its box")
            for i in range(n):
                pos = table + i * entry_size
                fmt = ">Q" if wide else ">I"
                value = struct.unpack(fmt, buf[pos : pos + entry_size])[0]
                lowest = value if lowest is None else min(lowest, value)
                highest = value if highest is None else max(highest, value)
                struct.pack_into(fmt, buf, pos, value + shift)
            count += n
        elif typ in CONTAINER_BOXES:
            sub_count, sub_low, sub_high = _patch_chunk_offsets(
                buf, box_start + header_size, box_start + box_size, shift
            )
            count += sub_count
            if sub_low is not None:
                lowest = sub_low if lowest is None else min(lowest, sub_low)
                highest = sub_high if highest is None else max(highest, sub_high)
    return count, lowest, highest


def _read_boxes_bytearray(buf, start, end):
    "Like _read_boxes, but over an in-memory buffer."
    offset = start
    while offset + 8 <= end:
        size = struct.unpack(">I", buf[offset : offset + 4])[0]
        typ = bytes(buf[offset + 4 : offset + 8])
        header_size = 8
        if size == 1:
            if offset + 16 > end:
                return
            size = struct.unpack(">Q", buf[offset + 8 : offset + 16])[0]
            header_size = 16
        elif size == 0:
            size = end - offset
        if size < header_size or offset + size > end:
            return
        yield typ, offset, size, header_size
        offset += size


def _contains_box(buf, start, end, wanted):
    "True if the box tree at [start, end) holds a box of type `wanted`."
    for typ, box_start, box_size, header_size in _read_boxes_bytearray(buf, start, end):
        if typ == wanted:
            return True
        if typ in CONTAINER_BOXES and _contains_box(
            buf, box_start + header_size, box_start + box_size, wanted
        ):
            return True
    return False


def _copy_range(src, dst, offset, size):
    "Copy size bytes of src, starting at offset, into dst."
    src.seek(offset)
    remaining = size
    while remaining > 0:
        chunk = src.read(min(_COPY_CHUNK, remaining))
        if not chunk:
            raise OSError("short read while rewriting media file")
        dst.write(chunk)
        remaining -= len(chunk)


def faststart_in_place(path):
    """
    Move moov in front of mdat, in place.

    Returns one of:
      "rewritten"   the file was re-muxed
      "already"     moov is already in front of mdat
      "not-mp4"     not a file type this handles (or no moov/mdat)
      "unsupported" layout we don't reason about (fragmented, extra mdat)
      "too-large"   file bigger than MAX_FILE_BYTES
      "failed"      something went wrong; the file was not changed
    """
    if os.path.splitext(path)[1].lower() not in FASTSTART_EXTENSIONS:
        return "not-mp4"
    try:
        file_size = os.path.getsize(path)
        if file_size > MAX_FILE_BYTES:
            return "too-large"
        boxes = top_level_boxes(path)
    except OSError:
        return "failed"

    types = [typ for typ, _, _, _ in boxes]
    if b"moov" not in types or b"mdat" not in types:
        return "not-mp4"
    if types.count(b"moov") != 1 or types.count(b"mdat") != 1:
        return "unsupported"
    moov_index = types.index(b"moov")
    mdat_index = types.index(b"mdat")
    if moov_index < mdat_index:
        return "already"

    moov = boxes[moov_index]
    mdat = boxes[mdat_index]
    moov_size = moov[2]
    if moov_size > MAX_MOOV_BYTES:
        return "unsupported"

    with open(path, "rb") as f:
        f.seek(moov[1])
        moov_bytes = bytearray(f.read(moov_size))
    if len(moov_bytes) != moov_size:
        return "failed"
    if _contains_box(moov_bytes, moov[3], moov_size, b"mvex"):
        # Fragmented mp4: its offsets are relative to each moof, and the
        # moov holds no chunk table, so there is nothing to fix up.
        return "unsupported"

    try:
        count, lowest, highest = _patch_chunk_offsets(
            moov_bytes, moov[3], moov_size, moov_size
        )
    except ValueError:
        return "failed"
    if count == 0 or lowest is None:
        return "unsupported"
    data_start = mdat[1] + mdat[3]
    if lowest < data_start or highest > mdat[1] + mdat[2]:
        # Chunks that don't live in this mdat: a layout this tool should
        # not touch.
        return "failed"

    tmp = path + ".faststart.tmp"
    try:
        with open(path, "rb") as src, open(tmp, "wb") as dst:
            for typ, box_start, box_size, _header_size in boxes:
                if box_start == moov[1]:
                    continue
                if box_start == mdat[1]:
                    dst.write(moov_bytes)
                _copy_range(src, dst, box_start, box_size)
        if os.path.getsize(tmp) != file_size:
            raise OSError("rewrite changed the file size")
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return "failed"
    return "rewritten"


def faststart_quietly(path, logger=None):
    """
    Best-effort faststart for an import path: never raises.

    Logs anything other than the two "nothing to do" outcomes.
    """
    try:
        status = faststart_in_place(path)
    except Exception:  # pylint: disable=broad-except
        if logger is not None:
            logger.exception("faststart failed for %s", path)
        return "failed"
    if logger is not None and status not in ("not-mp4", "already"):
        logger.info("faststart %s: %s", os.path.basename(path), status)
    return status
