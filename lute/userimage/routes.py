"""
User images routes.
"""

import os
from flask import Blueprint, send_from_directory, current_app
from lute.userimage.service import ensure_thumbnail, thumbnail_filename

bp = Blueprint("userimages", __name__, url_prefix="/userimages")

# Filenames are unique (timestamp + hash), so a stored file never changes.
CACHE_CONTROL = "private, max-age=31536000, immutable"
FALLBACK_CACHE_CONTROL = "private, max-age=300"


def _send_cached(directory, filename, mimetype=None, cache_control=CACHE_CONTROL):
    "Send a user image with long-lived private caching."
    response = send_from_directory(directory, filename, mimetype=mimetype)
    response.headers["Cache-Control"] = cache_control
    return response


@bp.route("/<int:lgid>/thumb/<path:f>", methods=["GET"])
def get_thumbnail(lgid, f):
    """
    Serve the WebP thumbnail for image ``f``, generating it on first request.

    If the original cannot be decoded (e.g. AVIF without Pillow support), fall
    back to the original file instead of returning 404.
    """
    # env_config is the user-scoped proxy: in multi-user mode images
    # live under the current user's own userimages directory.
    directory = os.path.join(current_app.env_config.userimagespath, str(lgid))
    original_path = os.path.join(directory, f)
    thumb_name = thumbnail_filename(f)
    thumb_path = os.path.join(directory, thumb_name)

    if not os.path.isfile(thumb_path) and os.path.isfile(original_path):
        ensure_thumbnail(original_path)

    if os.path.isfile(thumb_path):
        return _send_cached(directory, thumb_name, mimetype="image/webp")

    if os.path.isfile(original_path):
        return _send_cached(directory, f, cache_control=FALLBACK_CACHE_CONTROL)

    return ""


@bp.route("/<int:lgid>/<path:f>", methods=["GET"])
def get_image(lgid, f):
    "Serve the image from the data/userimages directory."
    # env_config is the user-scoped proxy: in multi-user mode images
    # live under the current user's own userimages directory.
    directory = os.path.join(current_app.env_config.userimagespath, str(lgid))
    if not os.path.exists(os.path.join(directory, f)):
        return ""
    return _send_cached(directory, f)
