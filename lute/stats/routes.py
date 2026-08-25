"""
/stats endpoints.
"""

from flask import (
    Blueprint,
    render_template,
    jsonify,
    request,
    Response,
    stream_with_context,
)
from lute.stats.service import (
    get_chart_data,
    get_table_data,
    get_reading_streak,
    get_new_terms,
    get_mastered_terms,
    get_heatmap_data,
    get_term_languages,
    get_term_summary,
)
from lute.stats.service import get_jlpt_data as _get_jlpt_data
from lute.stats.service import get_jlpt_words as _get_jlpt_words
from lute.db import db
import lute.utils.formutils

bp = Blueprint("stats", __name__, url_prefix="/stats")


@bp.route("/")
def index():
    "Main page."
    read_table_data = get_table_data(db.session)
    reading_streak = get_reading_streak(db.session)
    term_languages = get_term_languages(db.session)
    default_lang_id = lute.utils.formutils.valid_current_language_id(db.session)
    return render_template(
        "stats/index.html",
        hide_homelink=True,
        read_table_data=read_table_data,
        reading_streak=reading_streak,
        term_languages=term_languages,
        default_lang_id=default_lang_id,
    )


@bp.route("/data")
def get_data():
    "Ajax call for reading stats."
    chartdata = get_chart_data(db.session)
    return jsonify(chartdata)


@bp.route("/term_data")
def get_term_data():
    "Ajax call for term-related charts."
    period = request.args.get("period", "7days")
    if period not in ("today", "7days", "monthly"):
        period = "7days"

    lang_param = request.args.get("lang_id", "")
    lang_id = None
    if lang_param and lang_param != "all":
        try:
            lang_id = int(lang_param)
        except ValueError:
            lang_id = None

    return jsonify(
        {
            "summary": get_term_summary(db.session, lang_id, period),
            "new_terms": get_new_terms(db.session, period, lang_id),
            "mastered_terms": get_mastered_terms(db.session, period, lang_id),
            "heatmap": get_heatmap_data(db.session, lang_id),
        }
    )


@bp.route("/jlpt_data")
def get_jlpt_data():
    "Ajax call for the JLPT progress report for a Japanese language."
    lang_param = request.args.get("lang_id", "")
    try:
        lang_id = int(lang_param)
    except (TypeError, ValueError):
        return jsonify({"error": "lang_id required"}), 400

    data = _get_jlpt_data(db.session, lang_id)
    return jsonify(data)


def _jlpt_request_params():
    "Parse and validate lang_id/level/filter params for JLPT endpoints."
    from lute.stats.jlpt_data import LEVELS

    try:
        lang_id = int(request.args.get("lang_id", ""))
    except (TypeError, ValueError):
        return None
    level = request.args.get("level", "")
    word_filter = request.args.get("filter", "unmastered")
    if word_filter not in ("unmastered", "notseen", "mastered", "all"):
        return None
    if level != "all" and level not in LEVELS:
        return None
    return lang_id, level, word_filter


@bp.route("/jlpt_words")
def jlpt_words():
    "Paged word list for one JLPT level and filter."
    params = _jlpt_request_params()
    if params is None:
        return jsonify({"error": "invalid parameters"}), 400
    lang_id, level, word_filter = params
    if level == "all":
        return jsonify({"error": "level required"}), 400
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid pagination"}), 400

    words = _get_jlpt_words(db.session, lang_id, level, word_filter)
    start = (page - 1) * per_page
    return jsonify(
        {
            "total": len(words),
            "page": page,
            "per_page": per_page,
            "words": words[start : start + per_page],
        }
    )


@bp.route("/jlpt_export")
def jlpt_export():
    "CSV export of JLPT words for a level+filter, or all levels."
    import csv
    import io

    from lute.stats.jlpt_data import LEVELS
    from lute.stats.service import get_jlpt_words

    params = _jlpt_request_params()
    if params is None:
        return jsonify({"error": "invalid parameters"}), 400
    lang_id, level, word_filter = params

    levels = LEVELS if level == "all" else [level]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Level", "Word", "Reading", "Meaning", "Status"])
    for lvl in levels:
        for w in get_jlpt_words(db.session, lang_id, lvl, word_filter):
            writer.writerow(
                [lvl, w["word"], w["reading"], w["meaning"], w["status_text"] or ""]
            )
    out.seek(0)
    filename = f"JLPT_{level}_{word_filter}.csv"
    return Response(
        stream_with_context(iter([out.getvalue()])),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
