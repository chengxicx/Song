"""Loader + matcher for the Spanish DELE (A1-C2) vocabulary lists.

The per-level JSON lists are loaded at first use and cached for the
process lifetime.

Spanish terms are stored as surface forms in Lute (e.g. "hablando",
"casas", "roja"), while the DELE list mostly contains base forms
("hablar", "casa", "rojo").  Matching therefore expands a stored word
into a small set of candidate base forms via a lightweight rule set
(plurals, gender, and common verb forms), and a word counts if any
candidate is present in the DELE list.

A term is "seen" if its WoStatus is one of 1,2,3,4,5,99 (anything that
is not Unknown=0 or Ignored=98).  A term is "mastered" if WoStatus == 99.
"""

import json
from pathlib import Path

# levels ordered from beginner to advanced.
LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

_DELE_DIR = Path(__file__).parent / "dele_data"
_CACHE = None
_LEVEL_WORDS_CACHE = {}


def level_words(level):
    "Return the raw vocab entries for a level, cached."
    if level in _LEVEL_WORDS_CACHE:
        return _LEVEL_WORDS_CACHE[level]
    path = _DELE_DIR / f"vocab-{level.lower()}.json"
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
        path = _DELE_DIR / f"vocab-{level.lower()}.json"
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
# Lightweight Spanish inflection -> base form expansion.
# Each rule adds candidate base forms; a real (irregular) base form remains
# a candidate too, so stored surface forms still match their headword.
# ---------------------------------------------------------------------------


def base_candidates(word):
    "Return a set of candidate base forms for a stored (lowercased) word."
    w = (word or "").strip().lower()
    if not w:
        return set()
    cands = {w}
    n = len(w)

    # --- plurals ---
    if w.endswith("ces") and n > 4:
        cands.add(w[:-3] + "z")  # luces -> luz
        cands.add(w[:-2])  # luces -> luce
    if w.endswith("es") and n > 4:
        cands.add(w[:-2])  # colores -> color, grandes -> grande
    if w.endswith("s") and n > 3:
        cands.add(w[:-1])  # casas -> casa, rojos -> rojo

    # --- gender: feminine -> masculine ---
    if w.endswith("a") and n > 3:
        cands.add(w[:-1] + "o")  # roja -> rojo
        cands.add(w[:-1])  # profesora -> profesor, bonita -> bonito
    if w.endswith("ora") and n > 4:
        cands.add(w[:-3] + "or")  # actora -> actor

    # --- verbs: expand common conjugated forms to infinitive candidates ---
    cands |= _verb_infinitives(w)

    return cands


def _verb_infinitives(w):
    "Candidate infinitives for common conjugated verb forms."
    out = set()
    n = len(w)
    # past participles / gerunds
    if w.endswith("ando") and n > 5:
        stem = w[:-4]
        for end in ("ar", "er", "ir"):
            out.add(stem + end)  # hablando -> hablar
    if w.endswith("iendo") and n > 6:
        stem = w[:-5]
        for end in ("ar", "er", "ir"):
            out.add(stem + end)  # comiendo -> comer
    if w.endswith("ado") and n > 4:
        stem = w[:-3]
        for end in ("ar", "er", "ir"):
            out.add(stem + end)  # hablado -> hablar
    if w.endswith("ido") and n > 4:
        stem = w[:-3]
        for end in ("ar", "er", "ir"):
            out.add(stem + end)  # comido -> comer
    # present tense: strip common person endings, then try ar/er/ir
    for end in (
        "áis",
        "éis",
        "ís",
        "amos",
        "emos",
        "imos",
        "as",
        "es",
        "an",
        "en",
        "a",
        "e",
        "o",
    ):
        if w.endswith(end) and n > len(end) + 2:
            stem = w[: -len(end)]
            for tail in ("ar", "er", "ir"):
                out.add(stem + tail)
    # preterite: common endings
    for end in ("asteis", "aron", "ieron", "aste", "iste", "ó", "ió", "é", "í"):
        if w.endswith(end) and n > len(end) + 2:
            stem = w[: -len(end)]
            for tail in ("ar", "er", "ir"):
                out.add(stem + tail)
    # imperfect
    for end in (
        "ábamos",
        "íamos",
        "abais",
        "íais",
        "aban",
        "ían",
        "abas",
        "ías",
        "aba",
        "ía",
    ):
        if w.endswith(end) and n > len(end) + 2:
            stem = w[: -len(end)]
            for tail in ("ar", "er", "ir"):
                out.add(stem + tail)
    return out


def dele_level(word):
    """
    Return the lowest DELE level whose list contains a base-form candidate
    of the given stored Spanish word, or None.
    """
    if not word:
        return None
    word = word.strip().lower()
    if not word:
        return None
    word_list = _load_words()
    # direct hit (covers base forms and words already in base form)
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
    "DELE headwords a stored Spanish surface form expands to."
    w = (word or "").strip().lower()
    if not w:
        return set()
    word_list = _load_words()
    return {cand for cand in base_candidates(w) if cand in word_list}
