"""
/netease routes: QR-code login for NetEase Cloud Music.

The login state (MUSIC_U cookie) is stored per user and is used by the
"NetEase Cloud Music" book import (and its song fetches) automatically.
"""

import json

from flask import Blueprint, jsonify, request

from lute.book.service import BookImportException
from lute.netease.service import (
    account_profile,
    clear_cookie,
    qr_check,
    qr_key,
    save_cookie,
)

bp = Blueprint("netease", __name__, url_prefix="/netease")


def _cookies_from_param(raw):
    "Parse the QR-login session cookies echoed back by the page."
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


@bp.route("/login/qr", methods=["GET"])
def login_qr():
    "Start a QR login: return the unikey, the QR code SVG, and its session cookies."
    try:
        key, svg, cookies = qr_key()
    except BookImportException as e:
        return jsonify({"ok": False, "message": e.message})
    return jsonify({"ok": True, "key": key, "qr_svg": svg, "cookies": cookies})


@bp.route("/login/qr/check", methods=["GET"])
def login_qr_check():
    "Poll the QR login state once (waiting / scanned / verify / expired / confirmed)."
    try:
        result = qr_check(
            request.args.get("key", ""),
            _cookies_from_param(request.args.get("cookies", "")),
        )
    except BookImportException as e:
        return jsonify({"ok": False, "message": e.message})
    return jsonify({"ok": True, **result})


@bp.route("/login/status", methods=["GET"])
def login_status():
    "Whether a working NetEase login is stored, plus its display name."
    profile = account_profile()
    if profile is None:
        return jsonify({"logged_in": False})
    return jsonify({"logged_in": True, **profile})


@bp.route("/login/cookie", methods=["POST"])
def login_cookie():
    "Manual-login fallback: store a pasted MUSIC_U cookie value."
    try:
        profile = save_cookie(request.form.get("cookie", ""))
    except BookImportException as e:
        return jsonify({"ok": False, "message": e.message})
    return jsonify({"ok": True, "profile": profile})


@bp.route("/logout", methods=["POST"])
def logout():
    "Log out: clear the stored cookie."
    clear_cookie()
    return jsonify({"ok": True})
