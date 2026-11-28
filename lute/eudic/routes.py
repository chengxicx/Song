"""
Eudic sync page and JSON endpoints.

The form here is deliberately not part of the global Settings page:
the studylist dropdown has to be filled from a live API call, and the
settings form writes every field it renders on each submit, which is a
bad place to keep a credential.  The token lives in its own table (see
lute/eudic/models.py) and is only ever sent as a password input.
"""

import csv
import io
import logging

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from lute.db import db
from lute.eudic.mapping import EUDIC_BUCKETS, PUSHABLE_STATUSES, star_for_status
from lute.eudic.service import EudicService
from lute.eudic import tasks
from lute.i18n import t
from lute.multiuser import context as mu_context
from lute.stats.service import get_term_languages

logger = logging.getLogger(__name__)

bp = Blueprint("eudic", __name__, url_prefix="/eudic")

SYNC_MODES = ("preview", "push", "pull", "twoway")


def _service():
    return EudicService(db.session)


@bp.route("/", methods=["GET"])
def index():
    "The sync page."
    service = _service()
    languages = get_term_languages(db.session)
    supported = {lang["id"]: lang for lang in languages}

    # Only offer languages that have at least one term; a language with no
    # words has nothing to reconcile.
    choices = [lang for lang in languages if lang["count"] > 0]
    offered = [lang["id"] for lang in choices]

    # An explicitly requested language always wins, even before it has a
    # studylist mapping: the page is where that mapping gets created, so
    # falling back to a *different* configured language made the dropdown
    # snap back to whatever was configured first.
    raw_id = request.args.get("language_id", type=int)
    language_id = raw_id if raw_id in offered else None
    if language_id is None:
        scopes = service.scopes()
        configured = [lg_id for lg_id in offered if lg_id in scopes]
        if configured:
            language_id = min(configured)
        elif offered:
            language_id = offered[0]

    scope = service.scope_for(language_id) if language_id is not None else None

    stats = service.stats(scope) if scope else None
    errors = service.recent_errors(scope) if scope else []
    has_key = bool(service.api_key())

    return render_template(
        "eudic/index.html",
        languages=choices,
        buckets=EUDIC_BUCKETS,
        language_id=language_id,
        scope=scope,
        scopes=service.scopes(),
        api_key_mask=service.api_key_mask(),
        has_key=has_key,
        stats=stats,
        errors=errors,
        supported_language_ids=sorted(supported.keys()),
    )


@bp.route("/config", methods=["POST"])
def save_config():
    "Save the API key and/or the language -> studylist mapping."
    service = _service()
    service.set_api_key(request.form.get("api_key"))

    language_id = request.form.get("language_id", type=int)
    bucket = (request.form.get("bucket") or "").strip()
    category_id = (request.form.get("category_id") or "").strip()
    category_name = (request.form.get("category_name") or "").strip()
    if language_id and bucket in EUDIC_BUCKETS and category_id:
        service.set_scope(language_id, bucket, category_id, category_name)
    return redirect(url_for("eudic.index", language_id=language_id))


@bp.route("/disconnect", methods=["POST"])
def disconnect():
    "Forget the stored API key."
    _service().clear_api_key()
    return redirect(url_for("eudic.index"))


@bp.route("/reset_errors", methods=["POST"])
def reset_errors():
    "Clear remembered per-word failures."
    service = _service()
    language_id = request.form.get("language_id", type=int)
    scope = service.scope_for(language_id) if language_id else None
    if scope is not None:
        service.sync_repo.clear_errors(scope.lg_id, scope.bucket, scope.category_id)
        db.session.commit()
    return redirect(url_for("eudic.index", language_id=language_id))


@bp.route("/categories", methods=["GET"])
def categories():
    "Live studylist list for the dropdown."
    service = _service()
    bucket = (request.args.get("bucket") or "").strip()
    if bucket not in EUDIC_BUCKETS:
        return jsonify({"ok": False, "error": "Unsupported bucket."}), 400
    try:
        rows = service.list_categories(bucket)
    except Exception as e:  # pylint: disable=broad-except
        return jsonify({"ok": False, "error": str(e)}), 200
    return jsonify(
        {
            "ok": True,
            "categories": [{"id": c.id, "name": c.name} for c in rows],
        }
    )


@bp.route("/<mode>", methods=["POST"])
def sync(mode):
    "Start a preview / push / pull / two-way run."
    if mode not in SYNC_MODES:
        return jsonify({"ok": False, "error": "Unknown mode."}), 400

    service = _service()
    language_id = request.form.get("language_id", type=int)
    scope = service.scope_for(language_id) if language_id else None
    if scope is None:
        return jsonify({"ok": False, "error": t("Pick an Eudic word list first.")}), 400
    if not service.api_key():
        return (
            jsonify({"ok": False, "error": t("Eudic API key is not configured.")}),
            400,
        )

    task_id, duplicate = tasks.start(
        current_app._get_current_object(),  # pylint: disable=protected-access
        mode,
        scope,
        mu_context.get_current_user(),
    )
    return jsonify(
        {
            "ok": True,
            "task_id": task_id,
            "duplicate": duplicate,
            "message": (t("Eudic sync is already running.") if duplicate else ""),
        }
    )


@bp.route("/task/<task_id>", methods=["GET"])
def task(task_id):
    "Poll one task."
    payload = tasks.task_status(task_id, mu_context.get_current_user())
    payload["ok"] = payload.get("state") != "unknown"
    return jsonify(payload)


@bp.route("/export.csv", methods=["GET"])
def export_csv():
    """
    Eudic-shaped CSV of the words that would be pushed.

    A fallback for when the API is unavailable, and the only route for
    languages whose list lives outside the four buckets.
    """
    service = _service()
    language_id = request.args.get("language_id", type=int)
    scope = service.scope_for(language_id) if language_id else None
    if scope is None:
        return redirect(url_for("eudic.index"))

    terms = service.term_repo.find_by_statuses(scope.lg_id, PUSHABLE_STATUSES)
    from lute.eudic.mapping import push_form, split_reading  # local: avoids cycles

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["word", "phon", "exp", "add_time", "star", "context_line"])
    for term in terms:
        star = star_for_status(term.status)
        if star is None:
            continue
        word, inline = split_reading(term.text)
        reading = (term.romanization or "").strip() or inline
        writer.writerow(
            [
                push_form(word, reading),
                reading,
                term.translation or "",
                "",
                star,
                "",
            ]
        )

    data = ("\ufeff" + out.getvalue()).encode("utf-8")
    filename = f"eudic_{scope.bucket}_{scope.category_id}.csv"
    return send_file(
        io.BytesIO(data),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )
