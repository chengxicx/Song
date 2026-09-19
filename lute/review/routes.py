"""
Review queue routes.
"""

from flask import (
    Blueprint,
    request,
    jsonify,
    render_template,
    redirect,
    flash,
)

from lute.db import db
from lute.models.language import Language
from lute.models.review import ReviewSpec
from lute.review import enqueue, scheduler, service
from lute.review.forms import ReviewSpecForm
from lute.review.scheduler import SchedulerUnavailableError


bp = Blueprint("review", __name__, url_prefix="/review")


@bp.route("/index")
def review_index():
    "Review dashboard: counts, specs, scheduler status."
    specs = db.session.query(ReviewSpec).all()
    specs_json = [
        {
            "id": spec.id,
            "name": spec.name,
            "criteria": spec.criteria,
            "card_types": ", ".join(spec.card_types_enabled),
            "active": "yes" if spec.active else "no",
        }
        for spec in specs
    ]
    return render_template(
        "/review/index.html",
        fsrs_status=scheduler.fsrs_status(),
        counts=service.counts(db.session),
        specs_json=specs_json,
        language_names=[lang.name for lang in db.session.query(Language).all()],
    )


@bp.route("/sync", methods=["POST"])
def sync():
    "Sync the queue from all active specs, and report."
    result = enqueue.run_sync(commit=True)
    if result.errors:
        response = jsonify(result.as_dict())
        response.status_code = 400
        return response
    return jsonify(result.as_dict())


@bp.route("/start", methods=["POST"])
def start():
    "Start a review session."
    try:
        return jsonify(service.start_session(db.session))
    except SchedulerUnavailableError as ex:
        return jsonify({"error": str(ex), "needs_fsrs": True}), 400


@bp.route("/grade", methods=["POST"])
def grade():
    "Grade one card."
    data = request.get_json()
    try:
        ret = service.grade(
            db.session,
            int(data["card_id"]),
            int(data["rating"]),
            data.get("typed"),
        )
        return jsonify(ret)
    except SchedulerUnavailableError as ex:
        return jsonify({"error": str(ex), "needs_fsrs": True}), 400


@bp.route("/scheduler/install", methods=["POST"])
def scheduler_install():
    "One-click pip install of the fsrs package."
    ok, message = scheduler.install_fsrs()
    return jsonify({"ok": ok, "message": message})


def _handle_form(spec, form_template_name):
    "Handle the spec new/edit form."
    form = ReviewSpecForm(obj=spec)
    if request.method == "GET" and spec.id is not None:
        enabled = spec.card_types_enabled
        form.card_recognition.data = "recognition" in enabled
        form.card_recall.data = "recall" in enabled
        form.card_cloze.data = "cloze" in enabled

    if form.validate_on_submit():
        spec.name = form.name.data
        spec.criteria = form.criteria.data or ""
        spec.active = form.active.data
        spec.set_card_types(form.enabled_card_types())
        db.session.add(spec)
        db.session.commit()
        return redirect("/review/index", 302)

    language_names = [lang.name for lang in db.session.query(Language).all()]
    return render_template(
        form_template_name, form=form, spec=spec, language_names=language_names
    )


@bp.route("/spec/edit/<int:spec_id>", methods=["GET", "POST"])
def edit_spec(spec_id):
    "Edit a spec."
    spec = db.session.get(ReviewSpec, spec_id)
    return _handle_form(spec, "/review/edit.html")


@bp.route("/spec/new", methods=["GET", "POST"])
def new_spec():
    "Make a new spec."
    spec = ReviewSpec()
    if spec.active is None:
        spec.active = True
    return _handle_form(spec, "/review/new.html")


@bp.route("/spec/delete/<int:spec_id>", methods=["GET", "POST"])
def delete_spec(spec_id):
    "Delete a spec.  Cards already queued are kept."
    spec = db.session.get(ReviewSpec, spec_id)
    db.session.delete(spec)
    db.session.commit()
    flash("Review spec deleted.  Already-queued cards are kept.")
    return redirect("/review/index", 302)
