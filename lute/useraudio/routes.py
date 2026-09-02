"""
User audio routes.

User audio files are stored in the database in books table.
"""

import hashlib
import os
import re
import mimetypes

from flask import Blueprint, current_app, request, Response
from lute.db import db
from lute.models.repositories import BookRepository

bp = Blueprint("useraudio", __name__, url_prefix="/useraudio")

CHUNK_SIZE = 65536


@bp.route("/stream/<int:bookid>", methods=["GET"])
def stream(bookid):
    "Serve the audio with HTTP Range support (needed for seeking)."
    dirname = current_app.env_config.useraudiopath
    br = BookRepository(db.session)
    book = br.find(bookid)
    if book is None or not book.audio_filename:
        return Response("No audio for this book.", status=404, mimetype="text/plain")
    fname = os.path.join(dirname, book.audio_filename)
    if not os.path.isfile(fname):
        return Response("Audio file missing.", status=404, mimetype="text/plain")
    return _send_audio_range_aware(fname)


def _audio_etag(fname, size, mtime):
    """
    Validator for the audio representation, derived from the file
    identity rather than its bytes, so re-uploading a book's audio
    (new size/mtime) changes the ETag without hashing megabytes.
    """
    return '"%s"' % hashlib.md5(
        f"{os.path.basename(fname)}:{size}:{int(mtime)}".encode()
    ).hexdigest()


def _send_audio_range_aware(fname):
    """
    Serve an audio file, honoring HTTP Range requests.

    The HTML5 <audio> player needs partial-content responses (206) so
    the browser can seek to an arbitrary position.  Plain send_file()
    (as_attachment) returns the whole file with status 200 and no
    Accept-Ranges header, which makes seekable() empty and breaks the
    prev/next-cue jump.

    Caching: the reading page links the audio with a versioned URL
    (?v=<mtime>), so the browser may keep it (private, max-age) and
    seek/reopen without re-downloading; a replaced audio file changes
    the mtime, hence the URL, hence the cache key.  ETag + If-None-Match
    /If-Range keep conditional requests correct even for unversioned
    URLs.
    """
    size = os.path.getsize(fname)
    mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
    etag = _audio_etag(fname, size, os.stat(fname).st_mtime)
    range_header = request.headers.get("Range")
    if_range = request.headers.get("If-Range")

    base_headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        # private: user content behind auth, so only the browser may
        # store it.  Cloudflare skips private responses, and this path
        # has no cacheable file extension anyway.
        "Cache-Control": "private, max-age=86400",
    }

    if request.headers.get("If-None-Match") in (etag, "*"):
        return Response(status=304, headers=base_headers)

    # If-Range carries the validator of the client's cached copy: honor
    # the Range only when it matches, else serve the whole (current)
    # file so the client can replace its stale copy.
    range_matches = bool(range_header) and (
        if_range is None or if_range.strip() == etag
    )

    if range_matches:
        m = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
        if m:
            start_str, end_str = m.group(1), m.group(2)
            if start_str == "":
                # Suffix range: last N bytes.
                suffix = int(end_str)
                start = max(0, size - suffix)
                end = size - 1
            else:
                start = int(start_str)
                end = size - 1 if end_str == "" else min(int(end_str), size - 1)
            if start >= size or start > end:
                return Response(
                    status=416,
                    mimetype="text/plain",
                    headers={"Content-Range": f"bytes */{size}"},
                )
            length = end - start + 1

            def _stream():
                with open(fname, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(CHUNK_SIZE, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk

            resp = Response(
                _stream(),
                status=206,
                mimetype=mime,
                direct_passthrough=True,
            )
            resp.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            resp.headers["Content-Length"] = str(length)
            resp.headers.update(base_headers)
            return resp
        # Multi-range requests (bytes=0-9,20-29) land here: the regex
        # grabs only the first range, which browsers never send for
        # media playback, so serving it alone is fine.

    # No (usable) Range header: send the whole file, inline -- this is
    # a stream for the <audio> element, not a download.
    def _stream_all():
        with open(fname, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    resp = Response(
        _stream_all(),
        status=200,
        mimetype=mime,
        direct_passthrough=True,
    )
    resp.headers["Content-Length"] = str(size)
    resp.headers.update(base_headers)
    return resp
