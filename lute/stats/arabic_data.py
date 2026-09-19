"""Loader + matcher for the Arabic CEFR (A1-C2) vocabulary list.

Data source: kotoshu/frequency-list-kelly (data/ar.json).  Per-level JSON
lists are loaded at first use and cached for the process lifetime.

Arabic is heavily accented and uses jotun affixes, so matching normalizes both
sides: it strips diacritics (harakat) and normalizes alef/hamza variants, then
expands a stored surface form into candidate headwords by stripping common
prefixes (ال, و, ف, ب, ل, ك, س, and article+conjunction combos) and possessive
pronoun suffixes.  A stored word counts if any candidate is present in the
list.

A term is "seen" if its WoStatus is in 1,2,3,4,5,99 and "mastered" if 99.
"""

import json
import re
from pathlib import Path

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

_AR_DIR = Path(__file__).parent / "arabic_data"
_CACHE = None
_LEVEL_WORDS_CACHE = {}

# Arabic diacritics and other marks that do not carry identity.
_DIACRITICS = re.compile(
    "[\u064B-\u0652\u0670\u0640\u06D6-\u06DC\u06DF-\u06E8\u06EA-\u06ED]"
)
_ALEF_MAP = str.maketrans({"آ": "ا", "أ": "ا", "إ": "ا", "ٱ": "ا", "ى": "ا"})

# preposition/conjunction prefixes (incl. article + conjunction combos)
_PREFIXES = (
    "وال", "فال", "بال", "كال", "لل", "والال", "بالال", "فالال",
    "و", "ف", "ب", "ل", "ك", "ال", "س",
)
# possessive / object pronoun suffixes
_SUFFIXES = ("كما", "كم", "كن", "ها", "هم", "هن", "نا", "ني", "ن", "ك", "ه")


def level_words(level):
    "Return the raw vocab entries for a level, cached."
    if level in _LEVEL_WORDS_CACHE:
        return _LEVEL_WORDS_CACHE[level]
    path = _AR_DIR / f"vocab-{level.lower()}.json"
    entries = []
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
    _LEVEL_WORDS_CACHE[level] = entries
    return entries


def _ar_norm(word):
    "Normalize an Arabic term: strip diacritics, normalize alef/hamza variants."
    w = (word or "").strip()
    w = _DIACRITICS.sub("", w)
    w = w.translate(_ALEF_MAP)
    return w


def normalize(word):
    "Public normalize hook used by the stats service."
    return _ar_norm(word)


def base_candidates(word):
    "Return candidate headwords for a stored (normalized) Arabic word."
    w = _ar_norm(word)
    if not w:
        return set()
    cands = {w}
    for stem in _candidate_stems(w):
        cands.update(_candidate_stems(stem))
    return {c for c in cands if c}


def _candidate_stems(w):
    out = {w}
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) > len(suf) + 1:
            out.add(w[: -len(suf)])
    # strip prefixes (bounded depth)
    work = {w}
    for _ in range(3):
        nxt = set()
        for cand in work:
            for pre in _PREFIXES:
                if cand.startswith(pre) and len(cand) > len(pre):
                    nxt.add(cand[len(pre):])
        out.update(nxt)
        work = nxt
        if not work:
            break
    return out


def _load_words():
    "Load {normalized word: lowest level} for every entry, cached."
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    word_to_level = {}
    for level in LEVELS:
        path = _AR_DIR / f"vocab-{level.lower()}.json"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
        for entry in entries:
            word = _ar_norm(entry.get("word") or "")
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


def arabic_level(word):
    """
    Return the lowest CEFR level whose list contains a candidate headword of
    the given stored Arabic word, or None.
    """
    w = _ar_norm(word)
    if not w:
        return None
    word_list = _load_words()
    if w in word_list:
        return word_list[w]
    best = None
    for cand in base_candidates(w):
        if cand in word_list:
            lvl = word_list[cand]
            if best is None or LEVELS.index(lvl) < LEVELS.index(best):
                best = lvl
    return best


def base_forms_for(word):
    "Arabic headwords a stored surface form expands to."
    w = _ar_norm(word)
    if not w:
        return set()
    word_list = _load_words()
    return {cand for cand in base_candidates(w) if cand in word_list}