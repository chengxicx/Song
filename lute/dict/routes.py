"""
Dictionary routes.

Backing the settings page's "Local dictionaries (MDX)" card: list the
pool, import an MDX file into it, and remove one.  The read side (the
popup gloss and the reading page's dictionary tab) goes through
``lute.dict.service`` directly, and a language picks its dictionary on
its own settings page -- these routes never touch a language row except
to refuse to delete a dictionary one of them is still reading.
"""

import os
import uuid

from flask import Blueprint, current_app, jsonify, request

from lute.db import db
from lute.dict import service
from lute.models.language import LanguageDictionary
from lute.multiuser.permissions import admin_only_if_multiuser_json


bp = Blueprint("dict", __name__, url_prefix="/dict")


def _failure(message, code=400):
    "The shape the settings page's JS already knows how to display."
    return jsonify({"result": "failure", "message": message}), code


def _languages_using(name):
    "The names of the languages whose local dictionary is this file."
    rows = (
        db.session.query(LanguageDictionary)
        .filter(LanguageDictionary.dicttype == "localdict")
        .filter(LanguageDictionary.dicturi == name)
        .all()
    )
    return [d.language.name for d in rows]


@bp.route("/status", methods=["GET"])
def get_status():
    """
    Every dictionary in the pool, and what is in each.

    No file is singled out: clients -- the settings card, the language
    form, the phone app's download list -- present the whole list and
    the reader picks.  (There used to be a ``default_filename`` here for
    the phone's fixed URL; the phone now builds each URL from the list.)
    """
    return jsonify(
        {
            "result": "success",
            "dictionaries": service.list_installed(),
        }
    )


@bp.route("/import", methods=["POST"])
@admin_only_if_multiuser_json
def import_mdx():
    """
    Convert an uploaded .mdx into one of the pool's dictionaries.

    The upload is streamed to the temp dir and handed to a worker thread:
    parsing a dictionary takes minutes, so the response carries the
    task_id the settings page polls instead of the result.

    ``name`` is the file name to publish under; when it is missing, the
    .mdx file's own name is used.  A dictionary that already exists
    under that name is left alone until the conversion succeeds.
    """
    upload = request.files.get("mdx")
    if upload is None or not (upload.filename or "").strip():
        return _failure("Choose an .mdx file to import.")

    source_name = os.path.basename(upload.filename)
    if not source_name.lower().endswith(".mdx"):
        return _failure(f'"{source_name}" is not an .mdx file.')

    target = (request.form.get("name") or "").strip()
    if not target:
        target = service.suggested_filename(source_name)
    if not service.is_pool_name(target):
        return _failure(
            f'"{target}" is not a usable dictionary name -- use a plain file '
            "name ending in .db, with no folders in it."
        )

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
        target,
    )
    return jsonify(
        {
            "result": "success",
            "task_id": task_id,
            "source": source_name,
            "filename": target,
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
    """
    Delete one dictionary from the pool.

    A dictionary a language is still reading is refused (409, "in_use")
    until the caller confirms with ``force``: deleting it would quietly
    take the gloss away from that language, and the language page can
    only show it as "(not installed)".
    """
    name = request.form.get("name")
    if not name:
        payload = request.get_json(silent=True) or {}
        name = payload.get("name")
    if not service.is_pool_name(name):
        return _failure("No dictionary named.")

    users = _languages_using(name)
    if users and request.form.get("force") != "1":
        return (
            jsonify(
                {
                    "result": "in_use",
                    "message": f"{name} is still the dictionary for: "
                    + ", ".join(users)
                    + ".",
                    "languages": users,
                }
            ),
            409,
        )

    removed = service.remove(name)
    return jsonify(
        {
            "result": "success",
            "removed": removed,
            "filename": name,
            "dictionaries": service.list_installed(),
        }
    )
