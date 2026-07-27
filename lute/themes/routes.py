"Theming routes."

from flask import Blueprint, Response, jsonify, request, send_from_directory

from lute.themes.service import Service
from lute.models.repositories import UserSettingRepository
from lute.settings.current import current_settings
from lute.db import db

bp = Blueprint("themes", __name__, url_prefix="/theme")


@bp.route("/current", methods=["GET"])
def current_theme():
    "Return current css."
    service = Service(db.session)
    response = Response(service.get_current_css(), 200)
    response.content_type = "text/css; charset=utf-8"
    return response


@bp.route("/custom_styles", methods=["GET"])
def custom_styles():
    """
    Return the custom settings for inclusion in the base.html.
    """
    repo = UserSettingRepository(db.session)
    css = repo.get_value("custom_styles")
    response = Response(css, 200)
    response.content_type = "text/css; charset=utf-8"
    return response


@bp.route("/next", methods=["POST"])
def set_next_theme():
    "Go to next theme."
    service = Service(db.session)
    service.next_theme()
    return jsonify("ok")


@bp.route("/toggle_highlight", methods=["POST"])
def toggle_highlight():
    "Fix the highlight."
    new_setting = not current_settings["show_highlights"]
    repo = UserSettingRepository(db.session)
    repo.set_value("show_highlights", new_setting)
    db.session.commit()
    current_settings["show_highlights"] = new_setting
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
