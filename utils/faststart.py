"""Move an MP4/M4A 'moov' atom to the front of the file (faststart).

Pure stdlib re-mux: no ffmpeg needed, no re-encode (lossless).

Usage:
  python3 faststart.py <file> [--apply] [--backup]

Without --apply it only reports the atom layout and what it would do.
"""
import os
import shutil
import struct
import sys

CONTAINERS = {
    b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"udta", b"dinf",
}


def iter_boxes(buf, start, end):
    "Yield (type, box_start, box_size, header_size) for boxes in [start, end)."
    off = start
    while off + 8 <= end:
        size = struct.unpack(">I", buf[off : off + 4])[0]
        typ = bytes(buf[off + 4 : off + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", buf[off + 8 : off + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - off
        if size < hdr or off + size > end:
            break
        yield typ, off, size, hdr
        off += size


def patch_offsets(buf, start, end, shift, stats):
    "Add `shift` to every stco/co64 entry found in the tree at [start, end)."
    for typ, bstart, bsize, hdr in iter_boxes(buf, start, end):
        if typ in (b"stco", b"co64"):
            ver_flags = buf[bstart + hdr : bstart + hdr + 4]
            n = struct.unpack(">I", buf[bstart + hdr + 4 : bstart + hdr + 8])[0]
            wide = typ == b"co64"
            esize = 8 if wide else 4
            tbl = bstart + hdr + 8
            stats[typ.decode("latin1")] = stats.get(typ.decode("latin1"), 0) + n
            for i in range(n):
                p = tbl + i * esize
                if p + esize > bstart + bsize:
                    break
                if wide:
                    v = struct.unpack(">Q", buf[p : p + 8])[0]
                    struct.pack_into(">Q", buf, p, v + shift)
                else:
                    v = struct.unpack(">I", buf[p : p + 4])[0]
                    struct.pack_into(">I", buf, p, v + shift)
        elif typ in CONTAINERS:
            patch_offsets(buf, bstart + hdr, bstart + bsize, shift, stats)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply_ = "--apply" in sys.argv
    backup = "--backup" in sys.argv
    if not args:
        print(__doc__)
        return 1
    path = args[0]
    size = os.path.getsize(path)

    with open(path, "rb") as f:
        head = f.read(64)
        top = list(iter_boxes(head, 0, len(head)))  # only enough for layout probe
        # Read the whole file: moov is small compared to typical audio, and
        # we need it in memory anyway to patch the offset tables.
        f.seek(0)
        data = bytearray(f.read())

    top = list(iter_boxes(data, 0, size))
    print("top-level atoms:")
    for typ, bstart, bsize, hdr in top:
        print(f"  {typ.decode('latin1'):<6} size={bsize:>10} offset={bstart}")

    types = [t for t, _, _, _ in top]
    moov_idx = types.index(b"moov") if b"moov" in types else -1
    mdat_idx = types.index(b"mdat") if b"mdat" in types else -1
    if moov_idx < 0 or mdat_idx < 0:
        print("no moov/mdat pair; nothing to do")
        return 0
    if moov_idx < mdat_idx:
        print("moov already before mdat; nothing to do")
        return 0

    moov_t, moov_off, moov_size, moov_hdr = top[moov_idx]
    # Everything that sits before mdat keeps its place; moov is inserted
    # right there, pushing mdat (and its chunk data) later in the file.
    shift = moov_size
    stats = {}
    patch_offsets(data, moov_off + moov_hdr, moov_off + moov_size, shift, stats)
    print(f"patched offset tables: {stats}  shift=+{shift}")

    if not apply_:
        print("dry run: pass --apply to rewrite the file")
        return 0

    if backup:
        shutil.copy2(path, path + ".bak")
        print("backup:", path + ".bak")

    tmp = path + ".faststart.tmp"
    with open(tmp, "wb") as out:
        for typ, bstart, bsize, hdr in top:
            if typ == b"moov":
                continue
            if typ == b"mdat":
                out.write(data[moov_off : moov_off + moov_size])
            out.write(data[bstart : bstart + bsize])
    os.replace(tmp, path)
    print("rewrote", path, os.path.getsize(path), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
