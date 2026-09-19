"""Loader + matcher for the HSK vocabulary lists.

Two schemes are supported, sharing one storage layout:

- "2" : the old HSK 2.0 list (levels 1-6), data in ``hsk_data/`` from
  tomcumming/hsk-word-list.
- "3" : the new HSK 3.0 / HSK 1-9 list (levels 1-7, where 7 groups the
  7-9 band), data in ``hsk3_data/`` from drkameleon/complete-hsk-vocabulary.

Per-level JSON entries are ``{"word", "reading", "meanings": [...]}`` and are
loaded lazily and cached for the process lifetime.

Mandarin terms are stored in Lute as the words produced by the jieba
segmentation, and the HSK lists are keyed on the same surface words, so
matching is a straight normalized lookup -- Chinese has no surface-form
inflection to expand.  Traditional-char roots are not in the 2.0 list and
only appear as a secondary form in the 3.0 source, so a traditional-only
stored word may map to nothing.

A term is "seen" if its WoStatus is one of 1,2,3,4,5,99 (anything that is
not Unknown=0 or Ignored=98).  A term is "mastered" if WoStatus == 99.
"""

import json
from pathlib import Path

# levels ordered from beginner to advanced.
LEVELS2 = ["1", "2", "3", "4", "5", "6"]
LEVELS3 = ["1", "2", "3", "4", "5", "6", "7"]

_DIRS = {
    "2": Path(__file__).parent / "hsk_data",
    "3": Path(__file__).parent / "hsk3_data",
}

_LEVEL_WORDS_CACHE = {}
_VERSION_CACHE = {}


def _dir_for(scheme):
    return _DIRS.get(scheme, _DIRS["2"])


def level_words(scheme, level):
    "Return the raw vocab entries for a level, cached."
    key = (scheme, level)
    if key in _LEVEL_WORDS_CACHE:
        return _LEVEL_WORDS_CACHE[key]
    path = _dir_for(scheme) / f"vocab-{level}.json"
    entries = []
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
    _LEVEL_WORDS_CACHE[key] = entries
    return entries


def _load_words(scheme):
    "Load {normalized word: lowest level} for every entry of a scheme, cached."
    if scheme in _VERSION_CACHE:
        return _VERSION_CACHE[scheme]
    word_to_level = {}
    for level in _LEVELS_FOR(scheme):
        for entry in level_words(scheme, level):
            word = (entry.get("word") or "").strip()
            if not word or word in word_to_level:
                continue
            word_to_level[word] = level
    _VERSION_CACHE[scheme] = word_to_level
    return word_to_level


def _LEVELS_FOR(scheme):
    return LEVELS3 if scheme == "3" else LEVELS2


def vocab_total(scheme):
    "Total number of distinct entries in a scheme's lists."
    return len(_load_words(scheme))


def level_totals(scheme):
    "Return {level: total distinct entries} per level for a scheme."
    ret = {level: 0 for level in _LEVELS_FOR(scheme)}
    for level in _load_words(scheme).values():
        ret[level] += 1
    return ret


def normalize(word):
    "Normalize a Mandarin term for matching (strip whitespace only)."
    return (word or "").strip()


def _hsk_level(scheme, word):
    "Lowest level of a scheme containing the word, or None."
    w = normalize(word)
    if not w:
        return None
    return _load_words(scheme).get(w)


def hsk2_level(word):
    "Lowest HSK 2.0 level containing the word, or None."
    return _hsk_level("2", word)


def hsk3_level(word):
    "Lowest HSK 3.0 level containing the word, or None."
    return _hsk_level("3", word)


def _base_forms_for(scheme, word):
    "HSK headwords a stored surface form expands to (itself, if present)."
    w = normalize(word)
    if not w:
        return set()
    word_list = _load_words(scheme)
    return {w} if w in word_list else set()


def hsk2_headwords(word):
    "HSK 2.0 headwords for a stored surface form."
    return _base_forms_for("2", word)


def hsk3_headwords(word):
    "HSK 3.0 headwords for a stored surface form."
    return _base_forms_for("3", word)
