"""Loader + matcher for the Korean TOPIK vocabulary bands.

Data source: julienshim/combined_korean_vocabulary_list
  results.tsv (combines NIKL + TOPIK; TOPIK words carry a level of A/B/C).
Per-level JSON lists are loaded at first use and cached for the process
lifetime.

TOPIK 'A/B/C' bands roughly map to TOPIK levels: A = 1-2, B = 3-4, C = 5-6.

Korean dictionary forms (e.g. 가격02) may carry a trailing homonym number
in the source; the matching key strips that so stored Lute terms match.

A term is "seen" if its WoStatus is one of 1,2,3,4,5,99 (anything that
is not Unknown=0 or Ignored=98).  A term is "mastered" if WoStatus == 99.
"""

import json
import re
from pathlib import Path

# TOPIK bands ordered from beginner to advanced.
LEVELS = ["A", "B", "C"]

_TOPIK_DIR = Path(__file__).parent / "topik_data"
_CACHE = None
_LEVEL_WORDS_CACHE = {}


def level_words(level):
    "Return the raw vocab entries for a level, cached."
    if level in _LEVEL_WORDS_CACHE:
        return _LEVEL_WORDS_CACHE[level]
    path = _TOPIK_DIR / f"vocab-{level.lower()}.json"
    entries = []
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
    _LEVEL_WORDS_CACHE[level] = entries
    return entries


def _normalize_ko(text):
    "Normalize a Korean term for matching (strip trailing homonym digits)."
    return re.sub(r"[0-9]+$", "", (text or "").strip())


def _load_words():
    "Load {normalized word: level} for every TOPIK entry, cached."
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    word_to_level = {}
    for level in LEVELS:
        path = _TOPIK_DIR / f"vocab-{level.lower()}.json"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
        for entry in entries:
            word = _normalize_ko(entry.get("word") or "")
            if not word:
                continue
            # map a word to its lowest assigned level (one level only)
            if word not in word_to_level:
                word_to_level[word] = level
    _CACHE = word_to_level
    return _CACHE


def vocab_total():
    "Total number of TOPIK (A-C) vocab entries."
    return len(_load_words())


def level_totals():
    "Return {level: total} with the number of vocab entries per level."
    ret = {level: 0 for level in LEVELS}
    for level in _load_words().values():
        ret[level] += 1
    return ret


def topik_level(word):
    "Return the TOPIK band (A/B/C) for a given Korean word, or None."
    return _load_words().get(_normalize_ko(word))
