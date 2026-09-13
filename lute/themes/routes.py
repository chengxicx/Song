"Theming routes."

import hashlib

from flask import Blueprint, Response, jsonify, request, send_from_directory

from lute.themes.service import Service
from lute.models.repositories import UserSettingRepository
from lute.settings.current import current_settings
from lute.db import db

bp = Blueprint("themes", __name__, url_prefix="/theme")


def _etag_of(content):
    "Strong etag (raw hex) for the given css content."
    return hashlib.sha1(content.encode("utf-8")).hexdigest()


def _revalidate(content):
    """
    Return a 304 when the client's If-None-Match matches, else a 200
    with the content.  Either way the response is marked no-cache so
    the browser revalidates on every load: theme changes show up
    immediately without re-downloading unchanged stylesheets.
    """
    etag = _etag_of(content)
    headers = {
        "Cache-Control": "no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    }
    # Weak comparison: intermediaries (e.g. Cloudflare gzip) downgrade
    # strong etags to W/"..." and the browser echoes that back.
    if request.if_none_match.contains_weak(etag):
        response = Response(status=304)
    else:
        response = Response(content, 200)
        response.content_type = "text/css; charset=utf-8"
    response.headers["ETag"] = f'"{etag}"'
    for k, v in headers.items():
        response.headers[k] = v
    return response


@bp.route("/current", methods=["GET"])
def current_theme():
    "Return current css."
    service = Service(db.session)
    return _revalidate(service.get_current_css())


@bp.route("/custom_styles", methods=["GET"])
def custom_styles():
    """
    Return the custom settings for inclusion in the base.html.
    """
    repo = UserSettingRepository(db.session)
    return _revalidate(repo.get_value("custom_styles"))


@bp.route("/next", methods=["POST"])
def set_next_theme():
    "Go to next theme."
    service = Service(db.session)
    service.next_theme()
    return jsonify("ok")


# Default paired themes used by the dark/light toggle button.
# The dark palette lives in Default.css so it is the initial default;
# Default-Light.css holds the light palette.
LIGHT_THEME = "Default-Light.css"
DARK_THEME = "Default.css"


@bp.route("/toggle_dark", methods=["POST"])
def toggle_dark_theme():
    """
    Toggle between the paired Default (dark) and Default-Light (light) themes.

    - If the current theme is the light Default-Light.css, switch to Default.css (dark).
    - Otherwise (any theme, default, or unknown), switch to Default-Light.css (light).

    Returns JSON with the new theme filename so the client can update
    its icon without a full reload if desired.
    """
    repo = UserSettingRepository(db.session)
    current = repo.get_value("current_theme")
    new_theme = LIGHT_THEME if current == DARK_THEME else DARK_THEME
    repo.set_value("current_theme", new_theme)
    db.session.commit()
    return jsonify({"theme": new_theme, "is_dark": new_theme == DARK_THEME})


@bp.route("/toggle_highlight", methods=["POST"])
def toggle_highlight():
    "Fix the highlight."
    new_setting = not current_settings()["show_highlights"]
    repo = UserSettingRepository(db.session)
    repo.set_value("show_highlights", new_setting)
    db.session.commit()
    current_settings()["show_highlights"] = new_setting
    return jsonify("ok")


@bp.route("/download/<theme_name>", methods=["GET"])
def download_theme(theme_name):
    "Download a theme file."
    service = Service(db.session)
    content, _ = service.download_theme(theme_name)
    if content is None:
        return jsonify({"error": "Theme not found"}), 404
    response = Response(content, 200)
    response.content_type = "text/css; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename={theme_name}"
    return response


@bp.route("/upload", methods=["POST"])
def upload_theme():
    "Upload a theme file."
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    service = Service(db.session)
    content = file.read().decode("utf-8")
    success = service.upload_theme(file.filename, content)
    if success:
        return jsonify({"success": True, "filename": file.filename})
    return jsonify({"error": "Failed to upload theme"}), 500


@bp.route("/delete/<theme_name>", methods=["POST"])
def delete_theme(theme_name):
    "Delete a user-uploaded theme."
    service = Service(db.session)
    success = service.delete_theme(theme_name)
    if success:
        return jsonify({"success": True})
    return jsonify({"error": "Failed to delete theme"}), 404
