"""
Shared token-condition matcher for the European grammar engines.

The Japanese and Korean engines each embed their own matcher; this module
factors the same idea out in a language-neutral form so further languages
can be added as thin engines.  It works over tokens shaped like

    {"surface": str, "lemma": str, "pos": str,
     "morph": {str: str}, "idx": int,
     "parses": [{"lemma", "pos", "morph"}, ...]}

where "parses" is optional and lists alternative readings of an ambiguous
word (the Russian engine fills it from pymorphy3; for the spaCy engines it
is absent).  Condition keys:

* "surface_in":  list of surface strings, compared lowercased
* "surface_re":  compiled regex, fullmatch on the lowercased surface
* "lemma_in":    list of lemmas, compared lowercased
* "pos_in":      list of POS tags
* "morph":       dict of feature subset, e.g. {"Tense": "Past"}
* "morph_not":   dict of feature subset that vetoes the token
* "any":         matches anything

Within one spec the conditions are ANDed.  For tokens carrying "parses",
the lemma/pos/morph conditions hold when ANY parse satisfies them.

A rule's "match" is one of

* {"seq": [spec, ...]}              contiguous run of tokens; punctuation
                                    tokens between specs are skipped
* {"left": [...], "right": [...],
   "min_gap": n, "max_gap": m}      gapped two-anchor pattern (the gap is
                                    counted in non-punctuation tokens)
* {"re": compiled_regex}            literal substring recogniser
* {"any_of": [<match>, ...]}        first-shape alternation

An optional rule-level "not_after" spec vetoes matches whose nearest
left-hand non-punctuation neighbour satisfies it.
"""

import re

_PUNCT_POS = {"PUNCT", "SYM"}

_EXAMPLE_CAP = 3


# ---- sentence splitting ----------------------------------------------

def split_sentences(text):
    """
    Split a page of text into sentences on .!?… or line breaks.

    Line breaks matter for subtitle/transcript books, whose lines usually
    carry no sentence-final punctuation and would otherwise collapse into
    one pseudo-sentence (making every grammar example the whole page).
    Abbreviations like "Mr." or "e.g." split too; the engines accept that
    (the Japanese/Korean matchers have the same limitation).
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"(?<=[.!?…])|\n", text)
    return [p.strip() for p in parts if p and p.strip()]


# ---- condition matching ----------------------------------------------

def _is_punct(token):
    return (token.get("pos") or "") in _PUNCT_POS


def _morph_subset_any(token, feats):
    "True if any reading of the token carries all the given features."
    morphs = [token.get("morph") or {}]
    morphs.extend(p.get("morph") or {} for p in token.get("parses", []))
    return any(all(m.get(k) == v for k, v in feats.items()) for m in morphs)


# A POS reading counts only when it is a significant reading of the word,
# not a dictionary ghost: pymorphy3 lists every reading, and "чай" carries
# a 14% imperative-verb reading that must not fire imperative rules.
_POS_SCORE_RATIO = 0.3


def _pos_match_any(token, wanted):
    "True if a significant reading of the token has one of the wanted POS."
    best = token.get("score", 1.0) or 1.0
    readings = [(token.get("pos") or "", best)]
    readings.extend(
        (p.get("pos") or "", p.get("score", 0.0)) for p in token.get("parses", [])
    )
    return any(pos in wanted and score > _POS_SCORE_RATIO * best for pos, score in readings)


def _match_condition(cond, token):
    "True if a single spec matches a single token."
    if cond.get("any"):
        return True
    surface = (token.get("surface") or "").lower()
    if "surface_in" in cond and surface not in cond["surface_in"]:
        return False
    if "surface_re" in cond and not cond["surface_re"].fullmatch(surface):
        return False
    if "lemma_in" in cond:
        lemmas = {(token.get("lemma") or "").lower()}
        lemmas.update((p.get("lemma") or "").lower() for p in token.get("parses", []))
        if not set(cond["lemma_in"]) & lemmas:
            return False
    if "pos_in" in cond and not _pos_match_any(token, set(cond["pos_in"])):
        return False
    if "morph" in cond and not _morph_subset_any(token, cond["morph"]):
        return False
    if "morph_not" in cond and _morph_subset_any(token, cond["morph_not"]):
        return False
    return True


def _try_match(conds, tokens, i, j):
    """
    Match the condition sequence against tokens starting at index j.

    Returns the end index (exclusive) of the consumed run, or -1 when the
    sequence does not match.  Punctuation tokens are skipped between
    sequence elements (never before the first one).
    """
    if i == len(conds):
        return j
    if i > 0:
        while j < len(tokens) and _is_punct(tokens[j]):
            j += 1
    if j >= len(tokens):
        return -1
    if not _match_condition(conds[i], tokens[j]):
        return -1
    return _try_match(conds, tokens, i + 1, j + 1)


def _token_runs(match, tokens):
    "(start, end) token-index runs where the match shape holds."
    runs = []
    if "any_of" in match:
        for alt in match["any_of"]:
            runs.extend(_token_runs(alt, tokens))
        return runs
    if "re" in match:
        return []  # regex matches carry no token anchors (handled below)
    if "seq" in match:
        for start in range(len(tokens)):
            end = _try_match(match["seq"], tokens, 0, start)
            if end > start:
                runs.append((start, end))
        return runs
    # Gapped two-anchor pattern.
    left, right = match["left"], match["right"]
    min_gap = match.get("min_gap", 0)
    max_gap = match.get("max_gap", 8)
    for lstart in range(len(tokens)):
        lend = _try_match(left, tokens, 0, lstart)
        if lend < 0:
            continue
        for rstart in range(lend, len(tokens)):
            gap = sum(1 for t in tokens[lend:rstart] if not _is_punct(t))
            if gap < min_gap:
                continue
            if gap > max_gap:
                break
            rend = _try_match(right, tokens, 0, rstart)
            if rend > rstart:
                runs.append((lstart, rend))
    return runs


def _regex_spans(match, sentence_text):
    "Character spans for a literal {'re': ...} match shape."
    if "re" not in match:
        return []
    spans = []
    for m in match["re"].finditer(sentence_text):
        if m.group(0):
            spans.append(m.span())
    return spans


def _left_neighbour(tokens, start):
    "Nearest non-punctuation token before token index start, or None."
    j = start - 1
    while j >= 0:
        if not _is_punct(tokens[j]):
            return tokens[j]
        j -= 1
    return None


def match_rule(rule, tokens, sentence_text):
    """
    Character (start, end) spans of sentence_text matched by one rule.

    Several hits inside one sentence come back as separate spans so the
    panel can mark every occurrence.
    """
    match = rule["match"]
    runs = _token_runs(match, tokens)
    if "re" in match:
        spans = _regex_spans(match, sentence_text)
    else:
        spans = []
        offsets = [t["idx"] for t in tokens]
        for start, end in runs:
            veto = False
            if rule.get("not_after"):
                neighbour = _left_neighbour(tokens, start)
                veto = neighbour is not None and _match_condition(rule["not_after"], neighbour)
            if not veto and end > start:
                char_end = offsets[end - 1] + len(tokens[end - 1]["surface"])
                spans.append((offsets[start], char_end))
    # Drop duplicate / contained spans, keep reading order.
    spans = sorted(set(spans))
    merged = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            continue
        merged.append((start, end))
    return merged


# ---- rule helpers ------------------------------------------------------

def spec_surface(*surfaces):
    "Spec matching any of the given surface forms (case-insensitive)."
    return {"surface_in": [s.lower() for s in surfaces]}


def spec_lemma(*lemmas):
    return {"lemma_in": [l.lower() for l in lemmas]}


def spec_pos(*pos):
    return {"pos_in": list(pos)}


def spec_morph(**features):
    return {"morph": features}


def make_rule(key, name, level, desc, match, zh=None, not_after=None):
    "Build one engine rule dict."
    r = {"key": key, "name": name, "level": level, "desc": desc, "match": match}
    if zh:
        r["zh"] = zh
    if not_after:
        r["not_after"] = not_after
    return r


def _desc(rule, display_lang):
    "Description for a rule in the requested display language."
    if display_lang == "zh" and rule.get("zh"):
        return rule["zh"]
    return rule["desc"]


# ---- analysis driver ---------------------------------------------------

def analyze_tokens(page_text, rules, tokens_for_sentence, display_lang):
    """
    Run the rules over a page of text.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    Rules appearing on the page are merged by key; examples are deduped
    and capped per rule.  Each example's "matches" lists {"start", "end"}
    character offsets within "sentence" so the front-end can highlight the
    matched words in the reading text.
    """
    page_text = page_text.replace("🔊", "").replace("\u200b", "")
    by_key = {}
    order = []
    for sentence in split_sentences(page_text):
        if not sentence:
            continue
        tokens = tokens_for_sentence(sentence)
        if not tokens:
            continue
        for rule in rules:
            spans = match_rule(rule, tokens, sentence)
            if not spans:
                continue
            entry = by_key.get(rule["key"])
            if entry is None:
                entry = {
                    "key": rule["key"],
                    "name": rule["name"],
                    "level": rule["level"],
                    "desc": _desc(rule, display_lang),
                    "examples": [],
                }
                by_key[rule["key"]] = entry
                order.append(rule["key"])
            if len(entry["examples"]) >= _EXAMPLE_CAP:
                continue
            if any(ex["sentence"] == sentence for ex in entry["examples"]):
                continue
            entry["examples"].append(
                {"sentence": sentence, "matches": [{"start": s, "end": e} for s, e in spans]}
            )
    return [by_key[k] for k in order]
