"""
Dictionary routes.

Backing the settings page's "Local dictionary" card: import an MDX file,
watch the conversion, and remove the published dictionary.  The read
side (the popup gloss and the reading page's dictionary tab) goes through
``lute.dict.service`` directly.
"""

import os
import uuid

from flask import Blueprint, current_app, jsonify, request

from lute.dict import service
from lute.multiuser.permissions import admin_only_if_multiuser_json


bp = Blueprint("dict", __name__, url_prefix="/dict")


def _failure(message, code=400):
    "The shape the settings page's JS already knows how to display."
    return jsonify({"result": "failure", "message": message}), code


@bp.route("/status", methods=["GET"])
def get_status():
    "Whether a dictionary is installed, and what is in it."
    return jsonify({"result": "success", "status": service.status()})


@bp.route("/import", methods=["POST"])
@admin_only_if_multiuser_json
def import_mdx():
    """
    Convert an uploaded .mdx into the published dictionary.

    The upload is streamed to the temp dir and handed to a worker thread:
    parsing a dictionary takes minutes, so the response carries the
    task_id the settings page polls instead of the result.
    """
    upload = request.files.get("mdx")
    if upload is None or not (upload.filename or "").strip():
        return _failure("Choose an .mdx file to import.")

    source_name = os.path.basename(upload.filename)
    if not source_name.lower().endswith(".mdx"):
        return _failure(f'"{source_name}" is not an .mdx file.')

    temppath = current_app.env_config.temppath
    os.makedirs(temppath, exist_ok=True)
    mdx_path = os.path.join(temppath, f"dict_{uuid.uuid4().hex}.mdx")
    upload.save(mdx_path)
    if os.path.getsize(mdx_path) == 0:
        os.remove(mdx_path)
        return _failure("The uploaded file is empty.")

    task_id = service.start_import(
        current_app._get_current_object(),  # pylint: disable=protected-access
        mdx_path,
        source_name,
    )
    return jsonify(
        {
            "result": "success",
            "task_id": task_id,
            "source": source_name,
        }
    )


@bp.route("/import_status/<task_id>", methods=["GET"])
def import_status(task_id):
    "Progress for an import started by /dict/import."
    task = service.task_status(task_id)
    if task is None:
        return _failure("That import is unknown or has expired.", 404)
    return jsonify(
        {
            "result": "success",
            "state": task["state"],
            "processed": task.get("processed"),
            "error": task.get("error"),
            "dictionary": task.get("result"),
        }
    )


@bp.route("/remove", methods=["POST"])
@admin_only_if_multiuser_json
def remove_dictionary():
    "Delete the published dictionary."
    removed = service.remove()
    return jsonify(
        {
            "result": "success",
            "removed": removed,
            "status": service.status(),
        }
    )
