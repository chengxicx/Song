"""
User images routes.
"""

import os
from flask import Blueprint, abort, send_from_directory, current_app
from lute.db import db
from lute.models.language import Language
from lute.userimage.service import ensure_thumbnail, thumbnail_path_for

bp = Blueprint("userimages", __name__, url_prefix="/userimages")

# Filenames are unique (timestamp + hash), so a stored file never changes.
CACHE_CONTROL = "private, max-age=31536000, immutable"
FALLBACK_CACHE_CONTROL = "private, max-age=300"


def _send_cached(directory, filename, mimetype=None, cache_control=CACHE_CONTROL):
    "Send a user image with long-lived private caching."
    response = send_from_directory(directory, filename, mimetype=mimetype)
    response.headers["Cache-Control"] = cache_control
    return response


def _is_plain_filename(name):
    "A flat file name with no path separators, traversal or NUL byte."
    return (
        bool(name)
        and name != "."
        and "/" not in name
        and "\\" not in name
        and "\x00" not in name
        and ".." not in name
        and name == os.path.basename(name)
    )


def _image_directory(lgid):
    "The language's image directory, or None if it escapes the userimages root."
    root = os.path.realpath(current_app.env_config.userimagespath)
    directory = os.path.join(root, str(lgid))
    if os.path.commonpath([os.path.realpath(directory), root]) != root:
        return None
    return directory


def _path_in_directory(directory, path):
    "True if path (after resolving symlinks) stays inside directory."
    return os.path.commonpath([os.path.realpath(path), directory]) == directory


def _language_exists(lgid):
    "The language is in the current user's data (single-user instance data otherwise)."
    return db.session.get(Language, lgid) is not None


@bp.route("/<int:lgid>/thumb/<path:f>", methods=["GET"])
def get_thumbnail(lgid, f):
    """
    Serve the WebP thumbnail for image ``f``, generating it on first request.

    If the original cannot be decoded (e.g. AVIF without Pillow support), fall
    back to the original file instead of returning 404.
    """
    if not _is_plain_filename(f):
        abort(404)
    # env_config is the user-scoped proxy: in multi-user mode images
    # live under the current user's own userimages directory.
    directory = _image_directory(lgid)
    if directory is None:
        abort(404)

    original_path = os.path.join(directory, f)
    thumb_path = thumbnail_path_for(original_path)
    if not _path_in_directory(directory, original_path) or not _path_in_directory(
        directory, thumb_path
    ):
        abort(404)

    if not os.path.isfile(thumb_path) and os.path.isfile(original_path):
        # Generating writes into the directory, so the language must
        # belong to the current user's data, not just the path.
        if not _language_exists(lgid):
            abort(404)
        ensure_thumbnail(original_path)

    if os.path.isfile(thumb_path):
        return _send_cached(
            directory, os.path.basename(thumb_path), mimetype="image/webp"
        )

    if os.path.isfile(original_path):
        return _send_cached(directory, f, cache_control=FALLBACK_CACHE_CONTROL)

    abort(404)


@bp.route("/<int:lgid>/<path:f>", methods=["GET"])
def get_image(lgid, f):
    "Serve the image from the data/userimages directory."
    if not _is_plain_filename(f):
        abort(404)
    # env_config is the user-scoped proxy: in multi-user mode images
    # live under the current user's own userimages directory.
    directory = _image_directory(lgid)
    if directory is None:
        abort(404)
    image_path = os.path.join(directory, f)
    if not _path_in_directory(directory, image_path) or not os.path.isfile(image_path):
        abort(404)
    return _send_cached(directory, f)
