"""
Media import must faststart mp4-family files.

A browser recording (MediaRecorder) always puts its moov atom at the end
of the file, and so do some downloads.  A player can only report a
file's duration after it has read moov, so such a file made the browser
fetch the whole thing before it could even show a length.  Both import
paths (upload and download) run the re-mux, best-effort -- an import
must never fail because of it.

The re-mux itself is tested in tests/unit/utils/test_mp4faststart.py;
these tests cover the wiring into the import paths.
"""
import io
import os
import struct

from flask import current_app
from werkzeug.datastructures import FileStorage

from lute.book.service import Service
from lute.utils.mp4faststart import top_level_boxes


def box(typ, payload):
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def tail_moov_m4a():
    """
    A minimal mp4-family file with moov last, as MediaRecorder writes it.

    Layout: ftyp, mdat, moov(stco -> mdat payload).
    """
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2")
    mdat = box(b"mdat", b"\x00" * 64)
    stco = box(b"stco", struct.pack(">II", 0, 1) + struct.pack(">I", len(ftyp) + 8))
    moov = box(
        b"moov",
        box(b"trak", box(b"mdia", box(b"minf", box(b"stbl", stco)))),
    )
    return ftyp + mdat + moov


def saved_path(filename):
    return os.path.join(current_app.env_config.useraudiopath, filename)


def test_save_audio_file_moves_moov_in_front(app_context):  # pylint: disable=unused-argument
    "An uploaded m4a is re-muxed as it is saved."
    storage = FileStorage(
        stream=io.BytesIO(tail_moov_m4a()),
        filename="recording.m4a",
        content_type="audio/mp4",
    )
    filename = Service().save_audio_file(storage)
    path = saved_path(filename)
    assert os.path.exists(path)
    assert filename.endswith(".m4a")
    assert [t for t, _s, _z, _h in top_level_boxes(path)] == [b"ftyp", b"moov", b"mdat"]


def test_save_audio_file_leaves_other_formats_alone(app_context):  # pylint: disable=unused-argument
    "A non-mp4 upload is stored byte for byte."
    payload = b"ID3\x03\x00\x00\x00" + b"\x11" * 128
    storage = FileStorage(
        stream=io.BytesIO(payload), filename="song.mp3", content_type="audio/mpeg"
    )
    filename = Service().save_audio_file(storage)
    with open(saved_path(filename), "rb") as f:
        assert f.read() == payload


def test_save_audio_file_survives_an_unreadable_mp4(app_context):  # pylint: disable=unused-argument
    "A corrupt mp4-family upload is still imported, unmodified."
    payload = b"not really an mp4, just using the name" * 4
    storage = FileStorage(
        stream=io.BytesIO(payload), filename="broken.m4a", content_type="audio/mp4"
    )
    filename = Service().save_audio_file(storage)
    with open(saved_path(filename), "rb") as f:
        assert f.read() == payload
