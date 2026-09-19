"""Loader + matcher for the Thai frequency-bucket word lists.

Data source: PyThaiNLP/Phupha-Word-freq (CC0, Thai word frequencies from the
Common Crawl corpus).  Instead of CEFR levels (Thai has no official CEFR
vocabulary list), words are bucketed by frequency rank:

    "1-500", "501-1000", "1001-2000", "2001-5000", "5001-10000"

Per-bucket JSON lists (named vocab_<lo>_<hi>.json) are loaded at first use and
cached for the process lifetime.

Thai is uninflected, so matching is a direct normalized-text hit against the
bucket.  Lute's lute_thai parser and the Phupha dataset both use PyThaiNLP
segmentation, so stored term text matches bucket tokens consistently.

A term is "seen" if its WoStatus is in 1,2,3,4,5,99 and "mastered" if 99.
"""

import json
from pathlib import Path

# ordered from most to least common.
LEVELS = ["1-500", "501-1000", "1001-2000", "2001-5000", "5001-10000"]

_TH_DIR = Path(__file__).parent / "thai_data"
_CACHE = None
_LEVEL_WORDS_CACHE = {}


def _filename(level):
    return f"vocab_{level.replace('-', '_')}.json"


def level_words(level):
    "Return the raw vocab entries for a bucket, cached."
    if level in _LEVEL_WORDS_CACHE:
        return _LEVEL_WORDS_CACHE[level]
    path = _TH_DIR / _filename(level)
    entries = []
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
    _LEVEL_WORDS_CACHE[level] = entries
    return entries


def _load_words():
    "Load {normalized word: bucket} for every entry, cached."
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    word_to_level = {}
    for level in LEVELS:
        path = _TH_DIR / _filename(level)
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
    "Total number of bucketed words."
    return len(_load_words())


def level_totals():
    "Return {bucket: total} words per bucket."
    ret = {level: 0 for level in LEVELS}
    for level in _load_words().values():
        ret[level] += 1
    return ret


def thai_level(word):
    "Return the frequency bucket containing the stored Thai word, or None."
    if not word:
        return None
    word = word.strip()
    if not word:
        return None
    word_list = _load_words()
    return word_list.get(word)


def base_forms_for(word):
    "Thai headwords a stored term expands to (direct match only)."
    w = (word or "").strip()
    if not w:
        return set()
    word_list = _load_words()
    return {w} if w in word_list else set()
