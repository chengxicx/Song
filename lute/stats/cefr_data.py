"""Loader + matcher for the English CEFR (A1-C2) vocabulary lists.

Data source: katherine-welbourne/english-cefr-text-generator
  ENGLISH_CERF_WORDS.csv  (MIT license).
The per-level JSON lists are loaded at first use and cached for the
process lifetime.

English terms are stored as surface forms in Lute (e.g. "running", "ran"),
but the CEFR list is keyed on base forms ("run").  Matching therefore
expands a stored word into a small set of candidate base forms via a
lightweight morphological rule set, and a word counts if any candidate is
present in the CEFR list.

A term is "seen" if its WoStatus is one of 1,2,3,4,5,99 (anything that
is not Unknown=0 or Ignored=98).  A term is "mastered" if WoStatus == 99.
"""

import json
from pathlib import Path

# levels ordered from beginner to advanced.
LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

_CEFR_DIR = Path(__file__).parent / "cefr_data"
_CACHE = None
_LEVEL_WORDS_CACHE = {}


def level_words(level):
    "Return the raw vocab entries for a level, cached."
    if level in _LEVEL_WORDS_CACHE:
        return _LEVEL_WORDS_CACHE[level]
    path = _CEFR_DIR / f"vocab-{level.lower()}.json"
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
        path = _CEFR_DIR / f"vocab-{level.lower()}.json"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
        for entry in entries:
            word = (entry.get("word") or "").strip().lower()
            if not word:
                continue
            # map a word to its lowest assigned level (one level only)
            if word not in word_to_level:
                word_to_level[word] = level
    _CACHE = word_to_level
    return _CACHE


def vocab_total():
    "Total number of A1-C2 vocab entries."
    return len(_load_words())


def level_totals():
    "Return {level: total} with the number of vocab entries per level."
    ret = {level: 0 for level in LEVELS}
    for level in _load_words().values():
        ret[level] += 1
    return ret


# ---------------------------------------------------------------------------
# Lightweight English inflection -> base form expansion.
# Each rule adds a candidate base form; a real (irregular) base form remains
# a candidate too, so stored surface forms still match their headword.
# ---------------------------------------------------------------------------

def base_candidates(word):
    "Return a set of candidate base forms for a stored (lowercased) word."
    w = (word or "").strip().lower()
    if not w:
        return set()
    cands = {w}
    n = len(w)

    if w.endswith("ies") and n > 4:
        cands.add(w[:-3] + "y")          # cities -> city
    if w.endswith("es") and n > 4 and not w.endswith("sses"):
        cands.add(w[:-2])                # boxes -> box, watches -> watch
    if w.endswith("s") and n > 3 and not w.endswith(("ss", "us")):
        cands.add(w[:-1])                # dogs -> dog

    if w.endswith("ied") and n > 5:
        cands.add(w[:-3] + "y")          # studied -> study
    if w.endswith("ed"):
        base = w[:-2]
        cands.add(base)                  # walked -> walk
        cands.add(base + "e")            # loved -> love
        if len(base) > 1 and base[-1] == base[-2]:
            cands.add(base[:-1])         # stopped -> stop
    if w.endswith("ing"):
        base = w[:-3]
        cands.add(base)
        cands.add(base + "e")            # making -> make
        if len(base) > 1 and base[-1] == base[-2]:
            cands.add(base[:-1])         # running -> run

    return cands


def cefr_level(word):
    """
    Return the lowest CEFR level whose list contains a base-form candidate
    of the given stored English word, or None.
    """
    if not word:
        return None
    word = word.strip().lower()
    if not word:
        return None
    # direct hit (covers base forms and words already in base form)
    if word in _load_words():
        return _load_words()[word]
    best = None
    for cand in base_candidates(word):
        if cand in _load_words():
            lvl = _load_words()[cand]
            if best is None or LEVELS.index(lvl) < LEVELS.index(best):
                best = lvl
    return best


def base_forms_for(word):
    "CEFR headwords a stored English surface form expands to."
    w = (word or "").strip().lower()
    if not w:
        return set()
    word_list = _load_words()
    return {cand for cand in base_candidates(w) if cand in word_list}
