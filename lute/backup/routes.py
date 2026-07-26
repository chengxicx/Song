"""
Backup routes.

Backup settings form management, and running backups.
"""

import os
import shutil
import tempfile
import traceback
from werkzeug.utils import secure_filename
from flask import (
    Blueprint,
    current_app,
    render_template,
    request,
    jsonify,
    redirect,
    send_file,
    flash,
)
from lute.db import db
from lute.models.repositories import UserSettingRepository
from lute.backup.service import Service


bp = Blueprint("backup", __name__, url_prefix="/backup")


def _get_settings():
    "Get backup settings."
    repo = UserSettingRepository(db.session)
    return repo.get_backup_settings()


@bp.route("/index")
def index():
    """
    List all backups.
    """
    settings = _get_settings()
    service = Service(db.session)
    backups = service.list_backups(settings.backup_dir)
    backups.sort(reverse=True)

    return render_template(
        "backup/index.html", backup_dir=settings.backup_dir, backups=backups
    )


@bp.route("/download/<filename>")
def download_backup(filename):
    "Download the given backup file."
    settings = _get_settings()
    fullpath = os.path.join(settings.backup_dir, filename)
    return send_file(fullpath, as_attachment=True)


@bp.route("/backup", methods=["GET"])
def backup():
    """
    Endpoint called from front page.

    With extra arg 'type' for manual.
    """
    backuptype = "automatic"
    if "type" in request.args:
        backuptype = "manual"

    settings = _get_settings()
    return render_template(
        "backup/backup.html", backup_folder=settings.backup_dir, backuptype=backuptype
    )


@bp.route("/do_backup", methods=["POST"])
def do_backup():
    """
    Ajax endpoint called from backup.html.
    """
    backuptype = "automatic"
    prms = request.form.to_dict()
    if "type" in prms:
        backuptype = prms["type"]

    c = current_app.env_config
    settings = _get_settings()
    service = Service(db.session)
    is_manual = backuptype.lower() == "manual"
    try:
        f = service.create_backup(c, settings, is_manual=is_manual)
        flash(f"Backup created: {f}", "notice")
        return jsonify(f)
    except Exception as e:  # pylint: disable=broad-exception-caught
        tb = traceback.format_exc()
        return jsonify({"errmsg": str(e) + " -- " + tb}), 500


@bp.route("/skip_this_backup", methods=["GET"])
def handle_skip_this_backup():
    "Update last backup date so backup not attempted again."
    service = Service(db.session)
    service.skip_this_backup()
    return redirect("/", 302)


def _do_restore(filepath):
    """
    Common restore logic used by both upload-restore and list-restore.
    """
    c = current_app.env_config
    settings = _get_settings()
    service = Service(db.session)

    pre_restore_backup = service.create_pre_restore_backup(c, settings)

    safety_copy = service.restore_backup(c, filepath)

    flash(
        f"Backup restored successfully! "
        f"A pre-restore backup was created: {pre_restore_backup} "
        f"(and a safety copy of your previous database was saved as: "
        f"{os.path.basename(safety_copy)}). "
        f"Note: The restored database has its own backup settings. "
        f"Please check Settings → Backup directory to make sure it's correct.",
        "notice",
    )
    return redirect("/")


@bp.route("/restore", methods=["POST"])
def restore_backup():
    """
    Restore from an uploaded backup file.
    """

    if "backup_file" not in request.files:
        flash("No file selected", "error")
        return redirect("/backup/index")

    f = request.files["backup_file"]
    if f.filename == "":
        flash("No file selected", "error")
        return redirect("/backup/index")

    # Save uploaded file to temp location
    temp_dir = tempfile.mkdtemp()
    filename = secure_filename(f.filename)
    filepath = os.path.join(temp_dir, filename)
    f.save(filepath)

    try:
        return _do_restore(filepath)
    except Exception as e:  # pylint: disable=broad-except-caught
        flash(f"Restore failed: {str(e)}", "error")
        return redirect("/backup/index")
    finally:
        # Clean up temp file
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)


@bp.route("/restore_file/<filename>", methods=["POST"])
def restore_backup_from_file(filename):
    """
    Restore from a backup file in the backup directory.
    """
    settings = _get_settings()
    filepath = os.path.join(settings.backup_dir, filename)
    if not os.path.exists(filepath):
        flash(f"Backup file not found: {filename}", "error")
        return redirect("/backup/index")

    try:
        return _do_restore(filepath)
    except Exception as e:  # pylint: disable=broad-except-caught
        flash(f"Restore failed: {str(e)}", "error")
        return redirect("/backup/index")
