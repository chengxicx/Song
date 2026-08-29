"""
Settings routes.
"""

from flask import (
    Blueprint,
    current_app,
    request,
    render_template,
    redirect,
    flash,
    jsonify,
)
from wtforms import BooleanField
from lute.models.language import Language
from lute.models.setting import UserSetting
from lute.models.repositories import UserSettingRepository
from lute.themes.service import Service as ThemeService
from lute.settings.forms import UserSettingsForm, UserShortcutsForm
from lute.settings.current import refresh_global_settings
from lute.settings.hotkey_data import categorized_hotkeys, hotkey_descriptions
from lute.db import db
from lute.parse.mecab_parser import JapaneseParser
from lute.parse.sudachi_parser import JapaneseSudachiParser


bp = Blueprint("settings", __name__, url_prefix="/settings")


def _book_tag_choices(session):
    """
    All book tags with their book counts, for the series-tags setting.
    """
    rows = session.execute(
        db.text(
            """
            SELECT t2.T2Text, COUNT(bt.BtBkID) AS n
            FROM tags2 t2
            LEFT OUTER JOIN booktags bt ON bt.BtT2ID = t2.T2ID
            GROUP BY t2.T2Text
            ORDER BY t2.T2Text COLLATE NOCASE
            """
        )
    ).fetchall()
    return [(r[0], f"{r[0]} ({r[1]} book{'s' if r[1] != 1 else ''})") for r in rows]


@bp.route("/index", methods=["GET", "POST"])
def edit_settings():
    "Edit settings."
    form = UserSettingsForm()

    with current_app.app_context():
        svc = ThemeService(db.session)
        form.current_theme.choices = svc.list_themes()
        form.book_series_tags.choices = _book_tag_choices(db.session)

    ac = current_app.env_config
    if ac.is_docker:
        # User shouldn't change some things with docker.
        kw = {"readonly": True, "style": "background-color: LightGray"}
        # Backup dir gets mounted from host.
        form.backup_dir.render_kw = kw

    repo = UserSettingRepository(db.session)
    if form.validate_on_submit():
        # Update the settings in the database
        for field in form:
            if field.id not in ("csrf_token", "submit"):
                val = field.data
                if isinstance(field, BooleanField):
                    val = "1" if val else "0"
                if field.id == "book_series_tags":
                    val = ",".join(val or [])
                repo.set_value(field.id, val)
        db.session.commit()
        refresh_global_settings(db.session)

        flash("Settings updated", "success")
        return redirect("/")

    # Load current settings from the database
    repo = UserSettingRepository(db.session)
    for field in form:
        if field.id != "csrf_token":
            try:
                field.data = repo.get_value(field.id)
            except Exception:  # pylint: disable=broad-exception-caught
                # Setting doesn't exist yet (e.g. restored from older version),
                # leave the default from the form.
                pass
        if field.id == "book_series_tags":
            # Stored comma-separated; the checkbox list wants a list.
            val = field.data if isinstance(field.data, list) else (field.data or "")
            field.data = [t for t in val.split(",") if t] if isinstance(val, str) else val
        if isinstance(field, BooleanField):
            # Hack: set boolean settings to ints, otherwise they're always checked.
            true_vals = {"1", "true", "True", "yes", "Yes", "on"}
            str_val = str(field.data or "")
            field.data = 1 if str_val in true_vals else 0

    return render_template("settings/form.html", form=form)


@bp.route("/test_mecab", methods=["GET"])
def test_parse():
    """
    Do a test parse for the JapaneseParser using the
    given path string.

    Returns { 'success': tokens }, or { 'error' msg }

    """
    mecab_path = request.args.get("mecab_path", None)
    repo = UserSettingRepository(db.session)
    old_setting = repo.get_value("mecab_path")
    result = {"failure": "tbd"}
    try:
        repo.set_value("mecab_path", mecab_path)
        # Parsing requires a language, even if it's a dummy.
        lang = Language()
        p = JapaneseParser()
        src = "私は元気です"
        toks = p.get_parsed_tokens(src, lang)
        toks = [tok.token for tok in toks if tok.token != "¶"]
        message = f"{src} parsed to [{ ', '.join(toks) }]"
        result = {"result": "success", "message": message}
    except Exception as e:  # pylint: disable=broad-exception-caught
        message = f"{type(e).__name__}: { str(e) }"
        result = {"result": "failure", "message": message}
    finally:
        repo.set_value("mecab_path", old_setting)

    return jsonify(result)


@bp.route("/test_sudachi", methods=["GET"])
def test_sudachi():
    """
    Do a test parse for the JapaneseSudachiParser using the
    current Sudachi settings.

    Returns { 'result': 'success', 'message': msg }, or
            { 'result': 'failure', 'message': msg }
    """
    result = {"result": "failure", "message": "tbd"}
    try:
        # Parsing requires a language, even if it's a dummy.
        lang = Language()
        p = JapaneseSudachiParser()
        src = "私は元気です"
        toks = p.get_parsed_tokens(src, lang)
        toks = [tok.token for tok in toks if tok.token != "¶"]
        message = f"{src} parsed to [{ ', '.join(toks) }]"
        result = {"result": "success", "message": message}
    except Exception as e:  # pylint: disable=broad-exception-caught
        message = f"{type(e).__name__}: { str(e) }"
        result = {"result": "failure", "message": message}

    return jsonify(result)


@bp.route("/set/<key>/<value>", methods=["POST"])
def set_key_value(key, value):
    "Set a UserSetting key to value."
    repo = UserSettingRepository(db.session)
    old_value = repo.get_value(key)
    try:
        repo.set_value(key, value)
        result = {"result": "success", "message": "OK"}
    except Exception as e:  # pylint: disable=broad-exception-caught
        message = f"{type(e).__name__}: { str(e) }"
        repo.set_value(key, old_value)
        result = {"result": "failure", "message": message}
    db.session.commit()
    refresh_global_settings(db.session)
    return jsonify(result)


def _get_categorized_hotkeys():
    """
    Return hotkey UserSetting keys and values,
    grouped by category.
    """
    categorized = categorized_hotkeys()
    settings = {h.key: h.value for h in db.session.query(UserSetting).all()}
    return {
        category: {k: settings[k] for k in keylist}
        for category, keylist in categorized.items()
    }


@bp.route("/shortcuts", methods=["GET", "POST"])
def edit_shortcuts():
    "Edit shortcuts."
    repo = UserSettingRepository(db.session)
    form = UserShortcutsForm()
    if form.validate_on_submit():
        # print(request.form, flush=True)
        # Update the settings in the database
        for k, v in request.form.items():
            # print(f"{k} = {v}", flush=True)
            repo.set_value(k, v)
        db.session.commit()
        refresh_global_settings(db.session)
        flash("Shortcuts updated", "success")
        return redirect("/")

    categorized_settings = _get_categorized_hotkeys()
    return render_template(
        "settings/shortcuts.html",
        setting_descs=hotkey_descriptions(),
        categorized_settings=categorized_settings,
    )


@bp.route("/datatables_state/save", methods=["POST"])
def save_datatables_state():
    """
    Save a datatables state (e.g. column visibility) to the user settings.
    Payload: { "key": "<state_key>", "value": "<json_string>" }
    """
    payload = request.get_json(silent=True) or request.form
    key = payload.get("key")
    value = payload.get("value")
    if not key:
        return jsonify({"result": "failure", "message": "Missing key"}), 400
    if value is None:
        return jsonify({"result": "failure", "message": "Missing value"}), 400
    repo = UserSettingRepository(db.session)
    try:
        # Prefix the key to avoid collisions with known setting keys.
        repo.set_dynamic_value(f"dt_state_{key}", value)
        db.session.commit()
        return jsonify({"result": "success", "message": "OK"})
    except Exception as e:  # pylint: disable=broad-exception-caught
        return jsonify({"result": "failure", "message": f"{type(e).__name__}: {str(e)}"}), 500


@bp.route("/datatables_state/load/<path:key>", methods=["GET"])
def load_datatables_state(key):
    """
    Load a datatables state from the user settings.
    Returns { "value": "<json_string>" } or 404 if not found.
    """
    if not key:
        return jsonify({"result": "failure", "message": "Missing key"}), 400
    repo = UserSettingRepository(db.session)
    value = repo.get_dynamic_value(f"dt_state_{key}")
    if value is None:
        return jsonify({"value": None}), 200
    return jsonify({"value": value})
