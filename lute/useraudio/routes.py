"""
User audio routes.

User audio files are stored in the database in books table.
"""

import os
import re
import mimetypes

from flask import Blueprint, send_file, current_app, request, Response
from lute.db import db
from lute.models.repositories import BookRepository

bp = Blueprint("useraudio", __name__, url_prefix="/useraudio")


@bp.route("/stream/<int:bookid>", methods=["GET"])
def stream(bookid):
    "Serve the audio with HTTP Range support (needed for seeking)."
    dirname = current_app.env_config.useraudiopath
    br = BookRepository(db.session)
    book = br.find(bookid)
    fname = os.path.join(dirname, book.audio_filename)
    return _send_audio_range_aware(fname)


def _send_audio_range_aware(fname):
    """
    Serve an audio file, honoring HTTP Range requests.

    The HTML5 <audio> player needs partial-content responses (206) so
    the browser can seek to an arbitrary position.  Plain send_file()
    (as_attachment) returns the whole file with status 200 and no
    Accept-Ranges header, which makes seekable() empty and breaks the
    prev/next-cue jump.
    """
    size = os.path.getsize(fname)
    mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
    range_header = request.headers.get("Range")

    if range_header:
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
                        chunk = f.read(min(65536, remaining))
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
            resp.headers["Accept-Ranges"] = "bytes"
            resp.headers["Cache-Control"] = "no-store"
            return resp

    # No Range header: send the whole file.  Accept-Ranges is set so
    # the player knows seeking is possible; no-store keeps Cloudflare
    # from caching the stream (which would defeat Range handling).
    resp = send_file(fname, as_attachment=True, max_age=0)
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Cache-Control"] = "no-store"
    return resp
