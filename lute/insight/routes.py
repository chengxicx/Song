"""
3D insight charts.

A separate blueprint rather than more routes on the stats page: the
charts here need three.js, and the stats page is a 2028-line template
that must keep working exactly as it does.  If this page fails to load
nothing else is affected.
"""

from flask import Blueprint, jsonify, render_template, request

from lute.db import db
from lute.insight import service
from lute.stats.service import get_term_languages

bp = Blueprint("insight", __name__, url_prefix="/insight")

CHARTS = ("retention", "galaxy", "terrain", "levels")


def _languages():
    "Active languages that actually have words."
    return [lang for lang in get_term_languages(db.session) if lang["count"] > 0]


def _language_id():
    "The requested language, falling back to the first one with words."
    requested = request.args.get("language_id", type=int)
    if requested:
        return requested
    langs = _languages()
    return langs[0]["id"] if langs else None


@bp.route("/", methods=["GET"])
def index():
    "The chart page."
    langs = _languages()
    language_id = _language_id()
    name = service.language_name(db.session, language_id) if language_id else ""
    return render_template(
        "insight/index.html",
        languages=langs,
        language_id=language_id,
        language_name=name,
        charts=CHARTS,
    )


def _payload(builder):
    "Shared plumbing: resolve the language, then run one builder."
    language_id = _language_id()
    if language_id is None:
        return jsonify({"empty": True, "error": "no language"}), 200
    return jsonify(builder(db.session, language_id))


@bp.route("/data/retention", methods=["GET"])
def data_retention():
    "Recall rate over stability x elapsed."
    return _payload(service.retention_surface)


@bp.route("/data/galaxy", methods=["GET"])
def data_galaxy():
    "Every studied word as a point."
    return _payload(service.lexicon_galaxy)


@bp.route("/data/terrain", methods=["GET"])
def data_terrain():
    "Daily words read as a height field."
    return _payload(service.reading_terrain)


@bp.route("/data/levels", methods=["GET"])
def data_levels():
    "Exam level towers."
    return _payload(lambda session, lang_id: service.level_towers(session, lang_id))
