"""
Multi-user mode routes:

- /login, /logout           authentication
- /users/...                account management (admin only)
- /users/me/password        change own password
- /multiuser/switch         enable/disable multi-user mode
"""

import os
import shutil
import time
from functools import wraps

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from lute.db import db
from lute.db.management import add_default_user_settings
from lute.db.setup.main import setup_db
from lute.multiuser import context, paths, store, switching
from lute.multiuser.switching import SwitchError

bp = Blueprint("multiuser", __name__, template_folder="templates")


# ---------------------------------------------------------------------------
# helpers


def _base_config():
    "The underlying base AppConfig (not the per-user proxy)."
    return current_app.env_config.base_config


def admin_required(view):
    "Admin-only views.  In single-user mode, bounce to the home page."

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not store.enabled():
            flash("Multi-user mode is off.")
            return redirect("/")
        if not store.is_admin(session.get("user")):
            flash("Only an admin can manage users.")
            return redirect("/")
        return view(*args, **kwargs)

    return wrapped


def _init_user_database(username):
    "Create and seed a brand-new user's database."
    base = _base_config()
    ucfg = paths.user_config(base, username)
    paths.ensure_user_dirs(ucfg)
    setup_db(ucfg)
    with context.user_scope(username):
        with current_app.app_context():
            db.create_all()
            add_default_user_settings(db.session, ucfg.default_user_backup_path)


def _delete_user_data(username):
    "Remove a user's database and data directory."
    base = _base_config()
    udir = paths.user_dir(base, username)
    dbfile = paths.user_dbfilename(base, username)
    if os.path.exists(dbfile):
        os.remove(dbfile)
    if os.path.exists(udir) and os.path.isdir(udir):
        shutil.rmtree(udir)


def _local_next(fallback="/"):
    "Only follow same-site relative 'next' targets."
    nxt = request.args.get("next") or request.form.get("next") or ""
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return fallback


# ---------------------------------------------------------------------------
# login / logout

# (ip, username) -> [fail_count, last_fail_ts]
_login_failures = {}
_MAX_FAILS = 5
_RETRY_SECS = 60


def _too_many_failures(ip, username):
    entry = _login_failures.get((ip, username))
    if not entry:
        return False
    count, last_ts = entry
    if count < _MAX_FAILS:
        return False
    if time.time() - last_ts > _RETRY_SECS:
        _login_failures.pop((ip, username), None)
        return False
    return True


def _record_failure(ip, username):
    entry = _login_failures.setdefault((ip, username), [0, 0])
    if time.time() - entry[1] > _RETRY_SECS:
        entry[0] = 0
    entry[0] += 1
    entry[1] = time.time()


@bp.route("/login", methods=["GET", "POST"])
def login():
    "Login page.  Only reachable when multi-user mode is on."
    if not store.enabled():
        return redirect("/")

    if session.get("user") and store.get_user(session.get("user")):
        return redirect(_local_next())

    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "-").split(",")[
        0
    ]

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if _too_many_failures(ip, username):
            flash("Too many failed attempts.  Try again in a minute.")
        elif store.authenticate(username, password):
            _login_failures.pop((ip, username), None)
            session.clear()
            session["user"] = username
            session.permanent = True
            return redirect(_local_next())
        else:
            _record_failure(ip, username)
            flash("Wrong username or password.")

    return render_template("multiuser/login.html", next=_local_next())


@bp.route("/logout", methods=["POST"])
def logout():
    "Log out."
    session.clear()
    return redirect(url_for("multiuser.login"))


# ---------------------------------------------------------------------------
# user management (admin)


@bp.route("/users/index")
def users_index():
    """
    Account page.

    Admins get the full account-management list; other users see only
    their own account, with a change-password action.
    """
    if not store.enabled():
        flash("Multi-user mode is off.")
        return redirect("/")
    username = session.get("user")
    if not username or not store.get_user(username):
        return redirect("/login")
    if store.is_admin(username):
        return render_template("multiuser/users_index.html", users=store.users())
    return render_template(
        "multiuser/users_index.html", users=[store.get_user(username)], me_only=True
    )


@bp.route("/users/new", methods=["GET", "POST"])
@admin_required
def users_new():
    "Create an account."
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        role = request.form.get("role") or "user"
        if password != confirm:
            flash("Passwords do not match.")
        else:
            try:
                store.create_user(username, password, role)
                _init_user_database(username)
                flash(f"User '{username}' created.")
                return redirect("/users/index")
            except ValueError as e:
                flash(str(e))
    return render_template("multiuser/user_form.html")


@bp.route("/users/delete/<name>", methods=["POST"])
@admin_required
def users_delete(name):
    "Delete an account and all of its data."
    try:
        store.delete_user(name)
        _delete_user_data(name)
        flash(f"User '{name}' and all their data were deleted.")
    except ValueError as e:
        flash(str(e))
    return redirect("/users/index")


@bp.route("/users/<name>/password", methods=["GET", "POST"])
@admin_required
def users_reset_password(name):
    "Admin resets a user's password."
    if not store.user_exists(name):
        flash(f"User '{name}' does not exist.")
        return redirect("/users/index")
    if request.method == "POST":
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        if password != confirm:
            flash("Passwords do not match.")
        else:
            try:
                store.set_password(name, password)
                flash(f"Password for '{name}' was changed.")
                return redirect("/users/index")
            except ValueError as e:
                flash(str(e))
    return render_template("multiuser/user_password.html", target=name)


@bp.route("/users/me/password", methods=["GET", "POST"])
def change_own_password():
    "Change own password (multi-user mode only)."
    if not store.enabled():
        flash("Multi-user mode is off.")
        return redirect("/")
    username = session.get("user")
    if not username or not store.get_user(username):
        return redirect("/login")
    if request.method == "POST":
        current = request.form.get("current") or ""
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        if not store.authenticate(username, current):
            flash("Current password is wrong.")
        elif password != confirm:
            flash("Passwords do not match.")
        else:
            try:
                store.set_password(username, password)
                flash("Your password was changed.")
                return redirect("/")
            except ValueError as e:
                flash(str(e))
    return render_template("multiuser/user_password.html", target=None)


# ---------------------------------------------------------------------------
# mode switch


@bp.route("/multiuser/switch")
def switch_page():
    "Enable/disable multi-user mode."
    admin_names = (
        store.admin_usernames() if os.path.exists(store.users_dbfile()) else []
    )
    return render_template(
        "multiuser/switch.html",
        enabled=store.enabled(),
        has_users_db=os.path.exists(store.users_dbfile()),
        admin_names=admin_names,
        me=session.get("user"),
    )


@bp.route("/multiuser/enable", methods=["POST"])
def enable():
    "Enable multi-user mode, migrating current data into the admin account."
    try:
        with switching.MIGRATION_LOCK:
            base = _base_config()
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            confirm = request.form.get("confirm") or ""
            if os.path.exists(store.users_dbfile()):
                switching.re_enable(base, username, password)
            else:
                if password != confirm:
                    raise SwitchError("Passwords do not match.")
                switching.enable_fresh(base, username, password)
            session.clear()
            session["user"] = username
            session.permanent = True
            with context.user_scope(username):
                from lute.settings.current import refresh_global_settings

                refresh_global_settings(db.session)
        flash("Multi-user mode is on.  Your data was migrated to your account.")
        return redirect("/")
    except SwitchError as e:
        flash(str(e))
        return redirect(url_for("multiuser.switch_page"))


@bp.route("/multiuser/disable", methods=["POST"])
def disable():
    "Disable multi-user mode; the admin's data returns to single-user."
    try:
        username = session.get("user")
        if not store.is_admin(username):
            raise SwitchError("Only an admin can disable multi-user mode.")
        with switching.MIGRATION_LOCK:
            switching.disable(_base_config(), username)
            session.clear()
        flash("Multi-user mode is off.  Other accounts are kept but frozen.")
        return redirect("/")
    except SwitchError as e:
        flash(str(e))
        return redirect(url_for("multiuser.switch_page"))
