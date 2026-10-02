"""
Which grammar groups a language's reading-page panel can hide.

Every grammar entry the reader shows carries a ``level``:

* Japanese: JLPT ``N5``..``N1`` (from ``lute/jlpt_data/grammar/n*.json``).
* Korean: TOPIK bands ``TOPIK 1-2`` / ``TOPIK 3-4`` / ``TOPIK 5-6``.
* The CEFR engines (en/es/fr/de/it/pt/ru/th/ar/zh/yue): ``A1``..``C2``.

The Japanese engine additionally synthesises two aggregate rows that are
not levels at all -- ``basic_forms`` ("Basic forms: ...") and
``basic_particles`` ("Particles: ...").  They fire on nearly every page,
so they get their own hideable tokens rather than being buried inside
``N5``.  Both are emitted with ``level == "N5"``, which is why hiding
``N5`` also hides them.

A language hides tokens by name; the reader drops every entry whose
``level`` or ``key`` is hidden.
"""

from lute.read.render.grammar_analysis import (
    grammar_engine_for,
    is_japanese_language,
    is_korean_language,
)
from lute.read.render.grammar_analysis_ja import ALL_LEVELS as _JA_LEVELS

# TOPIK bands actually used by lute/jlpt_data/grammar_ko.json and the
# Korean engine's built-in rules.  A drift-guard test pins this against
# that data file.
_KO_LEVELS = ["TOPIK 1-2", "TOPIK 3-4", "TOPIK 5-6"]

# The CEFR engines each use a subset of these, so the full band is
# offered rather than per-engine subsets.
_CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

# Japanese-only synthetic aggregate rows: (token, label).
_JA_AGGREGATES = [
    ("basic_forms", "Basic forms"),
    ("basic_particles", "Particles"),
]


def hideable_grammar_groups(language):
    """
    Return ``[(token, label)]`` of the groups this language's grammar
    panel can hide, or ``[]`` when the language has no dedicated engine.

    The generic regex fallback rules carry neither a level nor an
    aggregate key, so there is nothing to hide for such a language.
    """
    if language is None:
        return []
    # Order matters: the Japanese and Korean engines ship with their
    # parsers, so grammar_engine_for() does not know about them.
    if is_japanese_language(language):
        return [(lvl, f"JLPT {lvl}") for lvl in _JA_LEVELS] + list(_JA_AGGREGATES)
    if is_korean_language(language):
        return [(lvl, lvl) for lvl in _KO_LEVELS]
    label, _extra = grammar_engine_for(language)
    if label is not None:
        return [(lvl, f"CEFR {lvl}") for lvl in _CEFR_LEVELS]
    return []


def filter_hidden_grammar(results, hidden_tokens):
    """
    Drop every entry whose ``level`` or ``key`` is in ``hidden_tokens``.

    ``results`` is the list of dicts the grammar route returns; the
    input list is left untouched.
    """
    if not hidden_tokens:
        return results
    hidden = set(hidden_tokens)
    return [
        e
        for e in results
        if e.get("level") not in hidden and e.get("key") not in hidden
    ]
