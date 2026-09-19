"""
Tests for mp4faststart: moving the moov atom in front of mdat.

The fixtures are hand-built mp4 containers (no ffmpeg needed): a real
file's layout is just nested length-prefixed boxes, and for a
progressive file the only part that must change when moov moves is the
chunk offset table (stco) inside moov.
"""
import os
import struct

import pytest

from lute.utils.mp4faststart import faststart_in_place, top_level_boxes


def box(typ, payload):
    "A box with a 32-bit size header."
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def stco_box(offsets):
    "A chunk offset table box, version 0."
    payload = struct.pack(">II", 0, len(offsets))
    payload += b"".join(struct.pack(">I", o) for o in offsets)
    return box(b"stco", payload)


def moov_box(offsets, extra=b""):
    """
    moov > trak > mdia > minf > stbl > stco.

    Real files nest deeper; the offsets only have to be found through the
    container chain.
    """
    stbl = box(b"stbl", stco_box(offsets))
    minf = box(b"minf", stbl)
    mdia = box(b"mdia", minf)
    trak = box(b"trak", mdia + extra)
    return box(b"moov", trak)


def make_file(path, *, tail=True, offsets=None, mdat_payload=b"\x00" * 64, extra=b""):
    """
    Write a minimal mp4.  With tail=True the order is ftyp, mdat, moov
    (what MediaRecorder produces); otherwise ftyp, moov, mdat.
    """
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2")
    mdat = box(b"mdat", mdat_payload)
    if offsets is None:
        # Point at the start of mdat's payload, as a real file would.
        offsets = [len(ftyp) + 8]
    moov = moov_box(offsets, extra=extra)
    order = (ftyp, mdat, moov) if tail else (ftyp, moov, mdat)
    path.write_bytes(b"".join(order))
    return len(moov)


def chunk_offsets(path):
    "Read the stco entries back out of a file."
    data = path.read_bytes()
    for typ, start, size, hdr in top_level_boxes(path):
        if typ != b"moov":
            continue
        body = data[start + hdr : start + size]
        i = body.index(b"stco")
        count = struct.unpack(">I", body[i + 8 : i + 12])[0]
        return [
            struct.unpack(">I", body[i + 12 + 4 * k : i + 16 + 4 * k])[0]
            for k in range(count)
        ]
    raise AssertionError("no stco found")


def test_moves_moov_in_front_of_mdat(tmp_path):
    f = tmp_path / "audio.m4a"
    moov_size = make_file(f, tail=True)
    original_size = f.stat().st_size
    original_mdat = f.read_bytes()[
        len(box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2")) :
    ][: 8 + 64]

    assert faststart_in_place(str(f)) == "rewritten"

    types = [t for t, _s, _z, _h in top_level_boxes(str(f))]
    assert types == [b"ftyp", b"moov", b"mdat"]

    # Lossless: same size, and the payload bytes are unchanged.
    assert f.stat().st_size == original_size
    assert original_mdat in f.read_bytes()

    # The chunk offsets moved with the data they point at.
    moved = chunk_offsets(f)
    assert moved == [
        len(box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2"))
        + moov_size
        + 8
    ]


def test_second_run_is_a_no_op(tmp_path):
    f = tmp_path / "audio.m4a"
    make_file(f, tail=True)
    assert faststart_in_place(str(f)) == "rewritten"
    after = f.read_bytes()
    assert faststart_in_place(str(f)) == "already"
    assert f.read_bytes() == after


def test_file_with_moov_first_is_untouched(tmp_path):
    f = tmp_path / "audio.m4a"
    make_file(f, tail=False)
    before = f.read_bytes()
    assert faststart_in_place(str(f)) == "already"
    assert f.read_bytes() == before


@pytest.mark.parametrize("name", ["song.mp3", "clip.webm", "notes.txt"])
def test_other_file_types_are_ignored(tmp_path, name):
    f = tmp_path / name
    f.write_bytes(b"\x00" * 128)  # not even a valid container
    assert faststart_in_place(str(f)) == "not-mp4"
    assert f.stat().st_size == 128


def test_garbage_with_mp4_extension_is_left_alone(tmp_path):
    f = tmp_path / "broken.mp4"
    f.write_bytes(b"this is not an mp4 file at all, but it has the name")
    before = f.read_bytes()
    assert faststart_in_place(str(f)) == "not-mp4"
    assert f.read_bytes() == before


def test_fragmented_file_is_left_alone(tmp_path):
    # mvex in moov marks a fragmented mp4: no chunk table to fix up.
    f = tmp_path / "frag.mp4"
    make_file(f, tail=True, extra=box(b"mvex", box(b"trex", b"\x00" * 20)))
    before = f.read_bytes()
    assert faststart_in_place(str(f)) == "unsupported"
    assert f.read_bytes() == before


def test_two_mdat_boxes_are_left_alone(tmp_path):
    f = tmp_path / "two.mp4"
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2")
    mdat = box(b"mdat", b"\x00" * 16)
    f.write_bytes(ftyp + mdat + mdat + moov_box([len(ftyp) + 8]))
    before = f.read_bytes()
    assert faststart_in_place(str(f)) == "unsupported"
    assert f.read_bytes() == before


def test_offsets_pointing_outside_mdat_are_refused(tmp_path):
    f = tmp_path / "weird.m4a"
    # Offset 0 is before mdat: not a layout this tool understands.
    make_file(f, tail=True, offsets=[0])
    before = f.read_bytes()
    assert faststart_in_place(str(f)) == "failed"
    assert f.read_bytes() == before
    assert not os.path.exists(str(f) + ".faststart.tmp")


def test_empty_chunk_table_is_left_alone(tmp_path):
    f = tmp_path / "empty.m4a"
    make_file(f, tail=True, offsets=[])
    before = f.read_bytes()
    assert faststart_in_place(str(f)) == "unsupported"
    assert f.read_bytes() == before


def test_bytes_before_mdat_keep_their_place(tmp_path):
    """
    Anything ahead of mdat must not move: only the chunks shift, by
    exactly the size of moov.
    """
    f = tmp_path / "audio.m4a"
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2")
    free = box(b"free", b"\x01" * 24)
    mdat = box(b"mdat", b"\x02" * 40)
    moov = moov_box([len(ftyp) + len(free) + 8])
    f.write_bytes(ftyp + free + mdat + moov)

    assert faststart_in_place(str(f)) == "rewritten"
    data = f.read_bytes()
    types = [t for t, _s, _z, _h in top_level_boxes(str(f))]
    assert types == [b"ftyp", b"free", b"moov", b"mdat"]
    assert data.startswith(ftyp + free)
    assert chunk_offsets(f) == [len(ftyp) + len(free) + len(moov) + 8]
