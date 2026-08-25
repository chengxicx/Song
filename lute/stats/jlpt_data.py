"""
Loader + matcher for the OpenJLPT N5-N1 vocabulary lists.

Data source: https://github.com/evanclan/OpenJLPT  (CC BY-SA 4.0)
The per-level JSON lists are loaded at first use and cached for the
process lifetime.

A term is "seen" if its WoStatus is one of 1,2,3,4,5,99 (anything that
is not Unknown=0 or Ignored=98).  A term is "mastered" if WoStatus == 99.
"""

import json
from pathlib import Path

# levels ordered from beginner to advanced.
LEVELS = ["N5", "N4", "N3", "N2", "N1"]

_JLPT_DIR = Path(__file__).parent / "jlpt_data"
_CACHE = None


def _load_words():
    "Load {word: level} for every vocab entry, cached for process lifetime."
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    word_to_level = {}
    for level in LEVELS:
        path = _JLPT_DIR / f"vocab-{level.lower()}.json"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
        for entry in entries:
            word = (entry.get("word") or "").strip()
            if not word:
                continue
            # a word is mapped to its lowest assigned level (one level only)
            if word not in word_to_level:
                word_to_level[word] = level
    _CACHE = word_to_level
    return _CACHE


def vocab_total():
    "Total number of N5-N1 vocab entries."
    return len(_load_words())


def level_totals():
    "Return {level: total} with the number of vocab entries per level."
    ret = {level: 0 for level in LEVELS}
    for word, level in _load_words().items():
        ret[level] += 1
    return ret


def jlpt_level(word):
    "Return the JLPT level for a given word, or None."
    return _load_words().get(word)