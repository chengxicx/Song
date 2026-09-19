"""Loader + matcher for the French CEFR (A1-C2) vocabulary list.

Data source: hyunahparc/bienvenue-au-croissant (French-Korean CEFR word lists).
Per-level JSON lists are loaded at first use and cached for the process
lifetime.

French terms are usually stored in Lute as lowercased base forms (lemma).
Matching expands a stored word into a small set of candidate headwords via a
lightweight rule set (accent variants, elided forms, and common inflectional
endings), and a stored word counts if any candidate is present in the list.

A term is "seen" if its WoStatus is in 1,2,3,4,5,99 and "mastered" if 99.
"""

import json
import unicodedata
from pathlib import Path

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

_FR_DIR = Path(__file__).parent / "french_data"
_CACHE = None
_LEVEL_WORDS_CACHE = {}


def level_words(level):
    "Return the raw vocab entries for a level, cached."
    if level in _LEVEL_WORDS_CACHE:
        return _LEVEL_WORDS_CACHE[level]
    path = _FR_DIR / f"vocab-{level.lower()}.json"
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
        path = _FR_DIR / f"vocab-{level.lower()}.json"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
        for entry in entries:
            word = _norm(entry.get("word") or "")
            if not word or word in word_to_level:
                continue
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


def _norm(word):
    "Normalize a French term: lowercase, strip."
    return (word or "").strip().lower()


def _unaccent(word):
    "Return the same word with all diacritics removed (NFD + drop marks)."
    decomp = unicodedata.normalize("NFD", word)
    return "".join(ch for ch in decomp if not unicodedata.combining(ch))


# common inflectional endings (noun/adjective plural, verb conjugations)
_ENDS = ("ions", "aient", "iez", "ais", "ait", "ons", "ez", "es", "e", "s")
_ELISIONS = ("l", "d", "qu", "c", "s", "j", "n", "m", "t")


def base_candidates(word):
    "Return a set of candidate headwords for a stored (lowercased) word."
    w = _norm(word)
    if not w:
        return set()
    cands = {w}
    un = _unaccent(w)
    if un != w:
        cands.add(un)
    # elided determiners/pronouns: l'homme -> homme
    for el in _ELISIONS:
        if w.startswith(el) and len(w) > len(el) + 1 and w[len(el)] == "'":
            cands.add(w[len(el) + 1:])
    for end in _ENDS:
        if len(w) > len(end) + 2 and w.endswith(end):
            stem = w[: -len(end)]
            cands.add(stem)
            cands.add(_unaccent(stem))
    return cands


def french_level(word):
    """
    Return the lowest CEFR level whose list contains a candidate headword of
    the given stored French word, or None.
    """
    return _lowest_level(word, base_candidates)


def _lowest_level(word, cand_fn):
    if not word:
        return None
    word = _norm(word)
    if not word:
        return None
    word_list = _load_words()
    if word in word_list:
        return word_list[word]
    best = None
    for cand in cand_fn(word):
        if cand in word_list:
            lvl = word_list[cand]
            if best is None or LEVELS.index(lvl) < LEVELS.index(best):
                best = lvl
    return best


def base_forms_for(word):
    "French headwords a stored surface form expands to."
    w = _norm(word)
    if not w:
        return set()
    word_list = _load_words()
    return {cand for cand in base_candidates(w) if cand in word_list}