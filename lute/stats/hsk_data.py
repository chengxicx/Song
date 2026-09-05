"""Loader + matcher for the HSK (1-6) vocabulary list.

Data source: tomcumming/hsk-word-list (old HSK 2.0, 6-level list with
pinyin + English definitions).  Per-level JSON lists are loaded at first
use and cached for the process lifetime.

Mandarin terms are stored in Lute as the words produced by the jieba
segmentation, and the HSK list is keyed on the same surface words, so
matching is a straight normalized lookup -- Chinese has no surface-form
inflection to expand.  Traditional-char roots are not in this list, so a
traditional-only stored word may map to nothing.

A term is "seen" if its WoStatus is one of 1,2,3,4,5,99 (anything that is
not Unknown=0 or Ignored=98).  A term is "mastered" if WoStatus == 99.
"""

import json
from pathlib import Path

# levels ordered from beginner to advanced.
LEVELS = ["1", "2", "3", "4", "5", "6"]

_HSK_DIR = Path(__file__).parent / "hsk_data"
_CACHE = None
_LEVEL_WORDS_CACHE = {}


def level_words(level):
    "Return the raw vocab entries for a level, cached."
    if level in _LEVEL_WORDS_CACHE:
        return _LEVEL_WORDS_CACHE[level]
    path = _HSK_DIR / f"vocab-{level}.json"
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
        path = _HSK_DIR / f"vocab-{level}.json"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
        for entry in entries:
            word = (entry.get("word") or "").strip()
            if not word or word in word_to_level:
                continue
            word_to_level[word] = level
    _CACHE = word_to_level
    return _CACHE


def vocab_total():
    "Total number of HSK 1-6 entries."
    return len(_load_words())


def level_totals():
    "Return {level: total} entries per level."
    ret = {level: 0 for level in LEVELS}
    for level in _load_words().values():
        ret[level] += 1
    return ret


def normalize(word):
    "Normalize a Mandarin term for matching (strip whitespace only)."
    return (word or "").strip()


def hsk_level(word):
    """
    Return the lowest HSK level whose list contains the given stored word,
    or None.
    """
    w = normalize(word)
    if not w:
        return None
    return _load_words().get(w)


def base_forms_for(word):
    "HSK headwords a stored surface form expands to (itself, if present)."
    w = normalize(word)
    if not w:
        return set()
    word_list = _load_words()
    return {w} if w in word_list else set()