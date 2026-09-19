"""Loader + matcher for the Russian CEFR (A1-C2) verb list.

Data source: StorkST/CoreRussianVerbs  (RussianVerbsClassification.csv).
The per-level JSON lists are loaded at first use and cached for the
process lifetime.

Russian verb terms are usually stored in Lute as infinitives (the
dictionary form), which is what the list is keyed on.  To stay tolerant
of the odd conjugated form, matching expands a stored word into a small
set of candidate infinitives via a lightweight morphological rule set
(reflexive suffixes, past/present stems), and a word counts if any
candidate is present in the list.

A term is "seen" if its WoStatus is one of 1,2,3,4,5,99 (anything that
is not Unknown=0 or Ignored=98).  A term is "mastered" if WoStatus == 99.
"""

import json
from pathlib import Path

# levels ordered from beginner to advanced.
LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

_RU_DIR = Path(__file__).parent / "russian_data"
_CACHE = None
_LEVEL_WORDS_CACHE = {}


def level_words(level):
    "Return the raw vocab entries for a level, cached."
    if level in _LEVEL_WORDS_CACHE:
        return _LEVEL_WORDS_CACHE[level]
    path = _RU_DIR / f"vocab-{level.lower()}.json"
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
        path = _RU_DIR / f"vocab-{level.lower()}.json"
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
    "Total number of A1-C2 verb entries."
    return len(_load_words())


def level_totals():
    "Return {level: total} with the number of verb entries per level."
    ret = {level: 0 for level in LEVELS}
    for level in _load_words().values():
        ret[level] += 1
    return ret


# ---------------------------------------------------------------------------
# Lightweight Russian verb form -> infinitive expansion.
# Each rule adds candidate infinitives; a stored infinitive always remains
# a candidate too, so it still matches its headword directly.
# ---------------------------------------------------------------------------


def base_candidates(word):
    "Return a set of candidate infinitives for a stored (lowercased) word."
    w = (word or "").strip().lower()
    if not w:
        return set()
    cands = {w}
    # ё is often typed/stored as е; match both.
    if "ё" in w:
        cands.add(w.replace("ё", "е"))
    # reflexive verbs: drop the reflexive marker.
    if w.endswith("ся") and len(w) > 4:
        cands.add(w[:-2])
    elif w.endswith("сь") and len(w) > 4:
        cands.add(w[:-2])
    cands |= _verb_infinitives(w)
    return cands


def _verb_infinitives(w):
    "Candidate infinitives for common non-infinitive stored verb forms."
    out = set()
    n = len(w)
    # Past-tense forms / participles: -л, -ла, -ло, -ли (+ reflexive).
    for end in ("лась", "лся", "лось", "лись", "ли", "ла", "ло", "л"):
        if w.endswith(end) and n > len(end) + 2:
            stem = w[: -len(end)]
            for t in ("ть", "ти", "чь"):
                out.add(stem + t)
    # Common finite forms: strip person/number endings, try -ть/-ти/-чь.
    for end in (
        "етесь",
        "ешься",
        "итесь",
        "ишься",
        "ются",
        "ятся",
        "ете",
        "ите",
        "ешь",
        "ишь",
        "ет",
        "ит",
        "ют",
        "ят",
        "ат",
        "ем",
        "им",
        "у",
        "ю",
    ):
        if w.endswith(end) and n > len(end) + 2:
            stem = w[: -len(end)]
            for t in ("ть", "ти", "чь"):
                out.add(stem + t)
    return out


def russian_level(word):
    """
    Return the lowest CEFR level whose list contains an infinitive candidate
    of the given stored Russian word, or None.
    """
    if not word:
        return None
    word = word.strip().lower()
    if not word:
        return None
    word_list = _load_words()
    # direct hit (covers stored infinitives and words already in base form)
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
    "Russian headwords a stored surface form expands to."
    w = (word or "").strip().lower()
    if not w:
        return set()
    word_list = _load_words()
    return {cand for cand in base_candidates(w) if cand in word_list}
