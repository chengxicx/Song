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

from flask import Blueprint, abort, render_template

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
    "Pick a language whose grammar corpus can be browsed."
    languages = (
        db.session.query(Language).order_by(Language.name).all()  # pylint: disable=no-member
    )
    browsable = [lang for lang in languages if _library_engine(lang)]
    return render_template("grammar/index.html", languages=browsable)


@bp.route("/<int:langid>")
def library(langid):
    "The grammar library for one language, or an explanation page."
    language = LanguageRepository(db.session).find(langid)
    if language is None:
        abort(404)

    engine = _library_engine(language)
    if engine is None:
        return render_template("grammar/unsupported.html", language=language)

    entries = engine.library_entries(_display_lang(language))
    return render_template(
        "grammar/library.html",
        language=language,
        entries=entries,
        level_counts=_level_counts(entries),
        level_class=_level_class,
    )
