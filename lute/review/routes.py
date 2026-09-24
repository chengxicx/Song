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
from lute.models.repositories import (
    MissingUserSettingKeyException,
    UserSettingRepository,
)
from lute.review import enqueue, scheduler, service
from lute.review.forms import ReviewSettingsForm
from lute.review.scheduler import SchedulerUnavailableError
from lute.settings.current import refresh_global_settings


bp = Blueprint("review", __name__, url_prefix="/review")


@bp.route("/index")
def review_index():
    "Review dashboard: counts and scheduler status."
    # Auto-admit before counting, so the numbers include any learning
    # terms not yet queued.  Additive and idempotent.
    enqueue.auto_admit(db.session)
    return render_template(
        "/review/index.html",
        fsrs_status=scheduler.fsrs_status(),
        counts=service.counts(db.session),
    )


@bp.route("/session")
def session_page():
    "Review session page; the cards load via POST /review/start."
    return render_template("/review/session.html")


@bp.route("/settings", methods=["GET", "POST"])
def review_settings():
    """
    Review scheduling settings and the global card-type switches.

    Field ids are the settings-table keys (see ReviewSettingsForm), so
    the form writes straight through the repository, as
    lute.settings.routes.edit_settings does.  The card-type checkboxes
    are not settings keys; they serialize into review_card_types.
    """
    form = ReviewSettingsForm()
    repo = UserSettingRepository(db.session)

    if form.validate_on_submit():
        for field in form:
            if field.id not in ("csrf_token", "submit", "card_recognition", "card_cloze"):
                repo.set_value(field.id, field.data)
        enabled = [
            ct
            for ct, field in (
                ("recognition", form.card_recognition),
                ("cloze", form.card_cloze),
            )
            if field.data
        ]
        enqueue.set_enabled_card_types(db.session, enabled)
        db.session.commit()
        refresh_global_settings(db.session)
        flash("Review settings updated.", "success")
        return redirect("/review/index", 302)

    enabled = enqueue.enabled_card_types(db.session)
    form.card_recognition.data = "recognition" in enabled
    form.card_cloze.data = "cloze" in enabled

    # Show what is actually stored, so the form is not the only truth.
    for field in form:
        if field.id in ("csrf_token", "card_recognition", "card_cloze"):
            continue
        try:
            field.data = repo.get_value(field.id)
        except MissingUserSettingKeyException:
            # Restored from an older db: keep the form's default.
            pass

    return render_template(
        "/review/settings.html",
        form=form,
        fsrs_status=scheduler.fsrs_status(),
        counts=service.counts(db.session),
    )


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


@bp.route("/undo", methods=["POST"])
def undo():
    "Reverse the most recent grading."
    try:
        return jsonify(service.undo_last(db.session))
    except ValueError as ex:
        return jsonify({"error": str(ex)}), 400


@bp.route("/scheduler/install", methods=["POST"])
def scheduler_install():
    "One-click pip install of the fsrs package."
    ok, message = scheduler.install_fsrs()
    return jsonify({"ok": ok, "message": message})
