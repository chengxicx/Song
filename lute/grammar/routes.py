"""
/grammar endpoints: the browsable grammar library.

The reading page's grammar panel shows only the points that happen to be
*in* the page being read, so a corpus entry the reader has not run into
yet is invisible.  These pages list the whole corpus per language -- the
curated bases, the level of each point, the wording, the cross-links --
so the library can be browsed and searched like any other reference.

Only the Japanese and Korean engines have such a corpus; every other
language is served by the generic regex rules, which carry no point-level
library, so /grammar/<langid> explains that instead of 404ing.
"""

from flask import Blueprint, abort, redirect, render_template, request, url_for

import lute.utils.formutils
from lute.db import db
from lute.models.language import Language
from lute.models.repositories import LanguageRepository
from lute.read.render.grammar_analysis import (
    is_japanese_language,
    is_korean_language,
)

bp = Blueprint("grammar", __name__, url_prefix="/grammar")

# Levels carried by the corpora; anything else is drawn with the neutral
# accent (see the panel's own levelClass in static/js/lute-commands.js).
_JLPT_LEVELS = ("N5", "N4", "N3", "N2", "N1")


def _library_engine(language):
    """
    The engine module holding the language's grammar corpus, or None.

    Imported lazily: both engines are optional extras (sudachi /
    kiwipiepy may not be installed), so a top-level import would make the
    whole app fail to start for a user who reads only other languages.
    If the tokenizer is missing the import raises, and the language
    simply has no browsable library.
    """
    if is_japanese_language(language):
        from lute.read.render import grammar_analysis_ja

        return grammar_analysis_ja
    if is_korean_language(language):
        try:
            from lute.read.render import grammar_analysis_ko
        except ImportError:
            return None
        return grammar_analysis_ko
    return None


def _display_lang(language):
    "The language's grammar display language ('en' / 'zh' / 'ko')."
    return getattr(language, "grammar_translate_lang", "") or "en"


def _level_class(level):
    "CSS token for a level, matching the reading panel's card classes."
    return level if level in _JLPT_LEVELS else "N"


def _level_counts(entries):
    """
    {level: {"total", "matchable", "reference"}} in the entries' own level
    order.

    Split deliberately: ``matchable`` is what the reader's own text can
    trigger (the rows the matcher actually fires), and the rest are
    reference-only (the corpus rows whose pattern is a vocabulary word or
    a class of forms, recorded for the record).  Neither number is a
    progress denominator -- the corpus is a merge of several sources with
    level overlap -- so the page reports them as inventory, not progress.
    """
    counts = {}
    for entry in entries:
        bucket = counts.setdefault(
            entry["level"], {"total": 0, "matchable": 0, "reference": 0}
        )
        bucket["total"] += 1
        if entry.get("skipped"):
            bucket["reference"] += 1
        else:
            bucket["matchable"] += 1
    return counts


@bp.route("/")
def index():
    """
    The grammar library of the language the user is studying.

    The menu item jumps straight there: the reader already picked their
    language when they started reading, so a chooser page would just
    re-ask a question the settings already answer.  ``?pick=1`` (the
    "Switch language" link on a library page) opens the list of
    languages that carry a corpus, which is also the fallback when the
    current language has none (only Japanese/Korean do).
    """
    if request.args.get("pick") != "1":
        current_id = lute.utils.formutils.valid_current_language_id(db.session)
        current = (
            db.session.query(Language)
            .filter(Language.id == current_id)
            .first()  # pylint: disable=no-member
            if current_id
            else None
        )
        if current is not None and _library_engine(current) is not None:
            return redirect(url_for("grammar.library", langid=current.id))

    languages = (
        db.session.query(Language)
        .order_by(Language.name)
        .all()  # pylint: disable=no-member
    )
    browsable = [lang for lang in languages if _library_engine(lang)]
    return render_template("grammar/index.html", languages=browsable)


@bp.route("/<int:langid>")
def library(langid):
    """
    One language's grammar library: every level on one page.

    The corpus runs to ~1400 points.  The page is a full-width grid of
    compact cards grouped by level (N5 -> N1), each card linking to the
    point's own page, with the sticky toolbar's search filtering across
    all levels at once.  A legacy ``?level=`` query is accepted and
    ignored: every level is already on the page.
    """
    language = LanguageRepository(db.session).find(langid)
    if language is None:
        abort(404)

    engine = _library_engine(language)
    if engine is None:
        return render_template("grammar/unsupported.html", language=language)

    entries = engine.library_entries(_display_lang(language))
    counts = _level_counts(entries)
    levels = list(counts) or ["N"]

    sections = [
        {
            "level": level,
            "rows": [e for e in entries if e["level"] == level],
            "total": counts[level]["total"],
            "matchable": counts[level]["matchable"],
            "reference": counts[level]["reference"],
        }
        for level in levels
    ]

    return render_template(
        "grammar/library.html",
        language=language,
        sections=sections,
        total=sum(s["total"] for s in sections),
        level_class=_level_class,
    )


@bp.route("/<int:langid>/<key>")
def detail(langid, key):
    """
    One grammar point's page: the full card the index rows link to.

    Walking the level with prev/next keeps the reader in the list they
    came from.  An unknown key (or a language with no library) falls back
    to the index rather than 404 -- the index's hash redirect and stale
    bookmarks both land here.
    """
    language = LanguageRepository(db.session).find(langid)
    if language is None:
        abort(404)

    engine = _library_engine(language)
    if engine is None:
        return redirect(url_for("grammar.library", langid=langid))

    entries = engine.library_entries(_display_lang(language))
    index_of = {e["key"]: i for i, e in enumerate(entries)}
    i = index_of.get(key)
    if i is None:
        return redirect(url_for("grammar.library", langid=langid))

    entry = entries[i]
    # Prev/next stay inside the point's own level (the index is per level).
    siblings = [j for j, e in enumerate(entries) if e["level"] == entry["level"]]
    pos = siblings.index(i)

    return render_template(
        "grammar/detail.html",
        language=language,
        entry=entry,
        prev_entry=entries[siblings[pos - 1]] if pos > 0 else None,
        next_entry=entries[siblings[pos + 1]] if pos < len(siblings) - 1 else None,
        position=pos + 1,
        level_total=len(siblings),
        level_class=_level_class,
    )
