"""
Server-level permission gate for multi-user mode.

Some configuration in Lute describes the *server* rather than the
person using it: the MeCab library path, the Sudachi dictionary, the
language list (each user has their own db, but the parsers and
dictionaries they name are installed system-wide), the Whisper install
and its model cache, and the multi-user mode switch itself.  In
multi-user mode an admin decides these for everybody; in single-user
mode there is only one user, and they are the de-facto admin, so
everything is allowed.

Two decorators, because the two response shapes differ:

- admin_only_if_multiuser        HTML pages: flash + redirect to "/"
- admin_only_if_multiuser_json   fetch()/$.ajax endpoints: JSON + 403

This is deliberately NOT the same as lute.multiuser.routes.admin_required,
which means "this page only exists when multi-user mode is on" and
bounces to "/" in single-user mode.  The settings and language pages must
keep working in single-user mode, so they need this separate gate.
"""

from functools import wraps

from flask import flash, jsonify, redirect, session

from lute.multiuser import store


def is_admin_request():
    """
    True when the current request may touch server-level settings.

    Single-user mode: always True (the sole user owns the machine).
    Multi-user mode: True only for an account with role 'admin'.

    Reads session.get("user") directly, so it is only meaningful inside
    a request; the app_factory context processor calls it per request
    to build the `can_manage_server` template variable.
    """
    if not store.enabled():
        return True
    return store.is_admin(session.get("user"))


def _deny_json(message):
    "The shape the settings/whisper JS already knows how to display."
    return jsonify({"result": "failure", "ok": False, "message": message}), 403


def admin_only_if_multiuser(view):
    """
    Allow in single-user mode; admins only when multi-user mode is on.
    For HTML pages: flashes and redirects home.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        if is_admin_request():
            return view(*args, **kwargs)
        flash("Only an admin can change this.")
        return redirect("/")

    return wrapped


def admin_only_if_multiuser_json(view):
    """
    Same rule as admin_only_if_multiuser, but answers 403 JSON so the
    settings page's $.ajax / fetch handlers show the message instead of
    choking on an HTML body.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        if is_admin_request():
            return view(*args, **kwargs)
        return _deny_json("Only an admin can change this.")

    return wrapped
