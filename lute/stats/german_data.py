"""Loader + matcher for the German CEFR (A1-C2) vocabulary list.

Data source: IamHamud/German-Language-Community (A1-C1 JSONL) supplemented
with the abdullahbutt Goethe C2 Wortschatz tables.  Per-level JSON lists are
loaded at first use and cached for the process lifetime.

German terms are usually stored in Lute as lowercased base forms (lemma).
Matching expands a stored word into a small set of candidate headwords via a
lightweight rule set (umlaut/ss variants and common inflectional endings), and
a stored word counts if any candidate is present in the list.

A term is "seen" if its WoStatus is one of 1,2,3,4,5,99 and "mastered" if 99.
"""

import json
from pathlib import Path

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

_DE_DIR = Path(__file__).parent / "german_data"
_CACHE = None
_LEVEL_WORDS_CACHE = {}


def level_words(level):
    "Return the raw vocab entries for a level, cached."
    if level in _LEVEL_WORDS_CACHE:
        return _LEVEL_WORDS_CACHE[level]
    path = _DE_DIR / f"vocab-{level.lower()}.json"
    entries = []
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
    _LEVEL_WORDS_CACHE[level] = entries
    return entries


def _load_words():
    "Load {normalized word: lowest level} for every entry, cached."
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    word_to_level = {}
    for level in LEVELS:
        path = _DE_DIR / f"vocab-{level.lower()}.json"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
        for entry in entries:
            word = (entry.get("word") or "").strip().lower()
            if not word:
                continue
            if word not in word_to_level:
                word_to_level[word] = level
    _CACHE = word_to_level
    return _CACHE


def vocab_total():
    "Total number of A1-C2 entries."
    return len(_load_words())


def level_totals():
    "Return {level: total} entries per level."
    ret = {level: 0 for level in LEVELS}
    for level in _load_words().values():
        ret[level] += 1
    return ret


# ---------------------------------------------------------------------------
# Lightweight German form -> headword expansion.
# ---------------------------------------------------------------------------

_UMLAUT_REPL = [
    ("ä", "ae"),
    ("ö", "oe"),
    ("ü", "ue"),
    ("ß", "ss"),
]

# common inflectional endings (noun plurals, weak adj/verb forms)
_ENDS = ("st", "en", "er", "es", "em", "et", "te", "est", "elt", "n", "e", "t", "s")


def base_candidates(word):
    "Return a set of candidate headwords for a stored (lowercased) word."
    w = (word or "").strip().lower()
    if not w:
        return set()
    cands = {w}
    # umlaut <-> ae/oe/ue and ss <-> ß variants
    for a, b in _UMLAUT_REPL:
        if a in w:
            cands.add(w.replace(a, b))
        if b in w:
            cands.add(w.replace(b, a))
    # strip common open/closed noun/verb/adjective endings to propose stems
    for end in _ENDS:
        if len(w) > len(end) + 2 and w.endswith(end):
            stem = w[: -len(end)]
            cands.add(stem)
            cands.add(stem + "en")
            cands.add(stem + "n")
    return cands


def german_level(word):
    """
    Return the lowest CEFR level whose list contains a candidate headword of
    the given stored German word, or None.
    """
    if not word:
        return None
    word = word.strip().lower()
    if not word:
        return None
    word_list = _load_words()
    if word in word_list:
        return word_list[word]
    best = None
    for cand in base_candidates(word):
        if cand in word_list:
            lvl = word_list[cand]
            if best is None or LEVELS.index(lvl) < LEVELS.index(best):
                best = lvl
    return best


def base_forms_for(word):
    "German headwords a stored surface form expands to."
    w = (word or "").strip().lower()
    if not w:
        return set()
    word_list = _load_words()
    return {cand for cand in base_candidates(w) if cand in word_list}
