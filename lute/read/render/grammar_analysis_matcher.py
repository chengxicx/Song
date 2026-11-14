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
left-hand non-punctuation neighbour satisfies it; "followed_by" and
"not_followed_by" do the same on the right-hand side (the latter over the
next three non-punctuation tokens), so homographs that differ only in what
follows can be told apart.  "at_start" requires the match to open the
sentence, which is how a word that only reads as a command in first
position (Spanish "toma", "mira") is told apart from the same word used
declaratively, where a subject or an auxiliary normally precedes it.

Two more vetoes cover constructions that are wider than the span they are
confused with: "not_inside" rejects a hit when any token *between* the
span's ends satisfies the spec (the filler that turns "hace ... que" into
"hace falta que"), and "not_at_start_with" rejects a hit that opens the
sentence when its first token satisfies the spec (the command "dame",
which the tagger also reads as a preterite form).
"""

import gc
import json
import os
import re
import threading
import time

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
    (the Japanese/Korean matchers have the same limitation).  Full-width
    CJK sentence marks are included so the Chinese/Cantonese engines split
    with the same helper.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"(?<=[.!?…。！？])|\n", text)
    return [p.strip() for p in parts if p and p.strip()]


# ---- spaCy model lifetime ---------------------------------------------
#
# A loaded pipeline is 40-200 MB, and until now the only reference to it
# lived inside the closure returned by make_spacy_tokenizer: once a user
# opened the grammar panel for a language, that model stayed resident for
# the rest of the process, even after switching to another language's
# books or freezing the language.  Six spaCy engines means that adds up.
#
# The lifetime is explicit now:
#
# * loaded lazily, once, under a per-model lock -- two threads hitting the
#   panel for the first time used to run spacy.load() twice;
# * released when idle for _SPACY_IDLE_SECONDS (swept by the read
#   blueprint's before_request) and immediately when the language is
#   frozen (language.toggle_active);
# * a released model reloads transparently on the next analysis.
#
# Releasing while a call is in flight is safe: _tokens_for reads the
# pipeline into a local before using it, so that object survives the call
# and only the *next* call pays the reload.  Dropping the reference is
# enough -- spaCy's runtime modules are left imported on purpose, since
# unloading a thinc C extension by hand is a good way to segfault.
#
# Dropping the reference alone does NOT lower RSS: the freed pages land in
# the process's malloc arenas and stay there (measured on the production
# host: freeze then reload grew RSS by ~50 MB instead of returning it).
# _reclaim() below hands them back.  What comes back is the model, not the
# runtime: spaCy/thinc/numpy are ~90 MB of imported modules and stay.
#
# The arenas also fill up without any model being released, because every
# request thread gets its own: on the production host, eight grammar-panel
# requests took RSS from 216 MB to 303 MB and it stayed there for the next
# eight -- not a leak (the engine itself is flat over repeated runs), just
# per-thread slack.  trim_arenas() sweeps that up occasionally.
_SPACY_IDLE_SECONDS = 900
_TRIM_INTERVAL_SECONDS = 300

_MODELS = {}
_MODELS_LOCK = threading.Lock()
_last_trim = 0.0
_trim_lock = threading.Lock()


def _trim():
    """
    Ask the allocator to return its empty pages to the OS.

    malloc_trim walks every arena, not just the calling thread's, which
    is why it can reclaim slack the other request threads accumulated.
    Imported lazily and its absence tolerated, so this is a no-op off
    glibc (macOS development).
    """
    try:
        # pylint: disable-next=import-outside-toplevel
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def _reclaim():
    "Give back a just-freed pipeline: break its cycles, then trim."
    gc.collect()
    _trim()


def trim_arenas(interval_seconds=_TRIM_INTERVAL_SECONDS):
    """
    Trim the arenas, but at most once per interval; True if it ran.

    Called from the read blueprint's per-request hook.  Nothing needs
    collecting -- Python's own generational GC already handled that --
    so this is malloc_trim alone: no full gc.collect() pause on a heap
    this size, ten or so milliseconds once per interval.
    """
    global _last_trim  # pylint: disable=global-statement
    now = time.monotonic()
    with _trim_lock:
        if now - _last_trim < interval_seconds:
            return False
        _last_trim = now
    _trim()
    return True


class _ModelHolder:
    "Process-wide holder for one spaCy pipeline, with an idle timestamp."

    def __init__(self, model_name):
        self.model_name = model_name
        self.nlp = None
        self.last_used = 0.0
        self.load_lock = threading.Lock()

    def get(self):
        "Return the pipeline, loading it on first use."
        nlp = self.nlp
        if nlp is not None:
            self.last_used = time.monotonic()
            return nlp
        with self.load_lock:
            if self.nlp is None:
                # pylint: disable-next=import-outside-toplevel
                import spacy

                self.nlp = spacy.load(
                    self.model_name, exclude=["parser", "senter", "ner"]
                )
            self.last_used = time.monotonic()
            return self.nlp

    def drop(self):
        "Forget the pipeline; True if one was loaded.  The next get() reloads."
        with self.load_lock:
            had_one = self.nlp is not None
            self.nlp = None
            self.last_used = 0.0
            return had_one


def _holder(model_name):
    "The holder for a model, created on first mention."
    with _MODELS_LOCK:
        holder = _MODELS.get(model_name)
        if holder is None:
            holder = _ModelHolder(model_name)
            _MODELS[model_name] = holder
        return holder


def make_spacy_tokenizer(model_name):
    """
    Lazy spaCy tokenizer shared by the spaCy-based engines
    (en/es/fr/de/it/pt), which differ only in the model name.

    Returns _tokens_for(sentence) -> list of token dicts.  spaCy is
    imported on first use, so importing this module works without it;
    the route falls back to the generic rule library on ImportError.

    The pipeline itself lives in a module-level holder keyed by model
    name, so other modules can release it (see release_spacy_model).
    """
    holder = _holder(model_name)

    def _tokens_for(sentence):
        "Tokenize a sentence; return a list of token dicts."
        # Local reference: a concurrent release may clear the holder
        # between here and the call, and this call must still complete.
        nlp = holder.get()
        doc = nlp(sentence)
        return [
            {
                "surface": t.text,
                "lemma": t.lemma_,
                "pos": t.pos_,
                "morph": t.morph.to_dict(),
                "idx": t.idx,
            }
            for t in doc
        ]

    _tokens_for.spacy_model = model_name
    return _tokens_for


def release_spacy_model(model_name):
    """
    Drop one loaded pipeline; True if one was actually resident.

    The next grammar analysis that needs it reloads it.  A model that was
    registered but never loaded returns False, so callers can tell a real
    release from a no-op.
    """
    holder = _MODELS.get(model_name)
    if holder is None:
        return False
    return _release_holder(holder)


def _release_holder(holder):
    "Drop a holder's pipeline and give its pages back; True if it had one."
    if not holder.drop():
        return False
    _reclaim()
    return True


def loaded_spacy_models():
    "Names of the pipelines currently resident, for tests and diagnostics."
    return sorted(name for name, h in _MODELS.items() if h.nlp is not None)


def release_idle_spacy_models(idle_seconds=_SPACY_IDLE_SECONDS):
    """
    Drop every pipeline idle for at least idle_seconds; return their names.

    Cheap enough for a per-request hook: one dictionary walk over at most
    one entry per engine.
    """
    now = time.monotonic()
    dropped = []
    for name, holder in list(_MODELS.items()):
        if holder.nlp is not None and now - holder.last_used >= idle_seconds:
            if _release_holder(holder):
                dropped.append(name)
    return dropped


# ---- condition matching ----------------------------------------------


def _is_punct(token):
    return (token.get("pos") or "") in _PUNCT_POS


def _morph_subset_any(token, feats, min_score=0.0):
    """
    True if a reading of the token carries all the given features.

    ``min_score`` gates the reading by its analyser confidence.  It defaults
    to 0.0, which keeps every existing rule behaving exactly as before.  A
    rule that asks for it is asking for an *unambiguous* reading.

    That matters for engines whose analyser has no context disambiguation
    and will happily rank a fabricated reading first: pymorphy3 reads
    ``рублей`` (a genitive plural noun) as ``рублеть`` in the imperative at
    score 0.561, above the noun's 0.410 -- so no "is it the top reading"
    test can filter it, only a confidence floor can.  Genuine imperatives
    (``давайте``, ``иди``, ``скажите``) all score at or above 0.99.
    """
    best = token.get("score", 1.0) or 1.0
    readings = [(token.get("morph") or {}, best)]
    readings.extend(
        (p.get("morph") or {}, p.get("score", 0.0)) for p in token.get("parses", [])
    )
    return any(
        score >= min_score and all(m.get(k) == v for k, v in feats.items())
        for m, score in readings
    )


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
    return any(
        pos in wanted and score > _POS_SCORE_RATIO * best for pos, score in readings
    )


def _match_condition(cond, token):
    "True if a single spec matches a single token."
    if cond.get("any"):
        return True
    if "any_of" in cond:
        # Token-level alternation: context conditions often need "det or a
        # numeral", which no single keyed spec can express (keys are ANDed).
        return any(_match_condition(sub, token) for sub in cond["any_of"])
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
    if "morph" in cond and not _morph_subset_any(
        token, cond["morph"], cond.get("min_score", 0.0)
    ):
        return False
    if "morph_not" in cond and _morph_subset_any(
        token, cond["morph_not"], cond.get("min_score", 0.0)
    ):
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


def _token_bounds(tokens):
    """
    The character offsets where tokens start and end.

    A literal regex is matched against the raw sentence, so it also lands
    inside longer words: Thai สิ "imperative particle" is a substring of
    สิบ "ten", and ขา of ขาย "to sell".  These sets let a spec ask for a
    match that respects the token boundaries the engine already computed.
    """
    starts, ends = set(), set()
    for tok in tokens:
        start = tok.get("idx")
        if start is None:
            continue
        starts.add(start)
        ends.add(start + len(tok.get("surface") or ""))
    return starts, ends


def _regex_spans(match, sentence_text, tokens=None):
    """
    Character spans for {'re': ...} match shapes, collected recursively so
    a regex alternative inside {"any_of": [...]} counts too.

    ``boundary`` on the spec filters those spans: ``"token"`` keeps only a
    hit that covers a whole token, ``"start"`` only one that begins at a
    token start (a prefix or the first element of a compound), and
    ``"prefix"`` only one that begins a token *and* continues into it --
    for morphemes that never stand alone, such as the agent prefix นัก-
    (นักเรียน yes, บ่อยนัก "not at all" no).  Without a boundary the raw
    substring hit is kept, which is what the non-Thai engines use.
    """
    if "re" in match:
        spans = [m.span() for m in match["re"].finditer(sentence_text) if m.group(0)]
        boundary = match.get("boundary")
        if boundary and tokens:
            starts, ends = _token_bounds(tokens)
            if boundary == "token":
                spans = [(s, e) for s, e in spans if s in starts and e in ends]
            elif boundary == "start":
                spans = [(s, e) for s, e in spans if s in starts]
            elif boundary == "prefix":
                spans = [(s, e) for s, e in spans if s in starts and e not in ends]
        return spans
    if "any_of" in match:
        out = []
        for alt in match["any_of"]:
            out.extend(_regex_spans(alt, sentence_text, tokens))
        return out
    return []


def _left_neighbour(tokens, start):
    "Nearest non-punctuation token before token index start, or None."
    j = start - 1
    while j >= 0:
        if not _is_punct(tokens[j]):
            return tokens[j]
        j -= 1
    return None


# How far a trailing-context veto looks ahead, in non-punctuation tokens.
# French restrictive "ne ... que 'cinq étudiants'" has no finite verb within
# four words, while the homographic complementizer introduces a clause whose
# own verb may sit fourth ("pourvu que le directeur lui donne ..."), so the
# window has to cover that far to tell the two apart.
_TRAIL_WINDOW = 4


def _right_window(tokens, end, count):
    "Up to count non-punctuation tokens after index end (reading order)."
    out = []
    j = end
    while j < len(tokens) and len(out) < count:
        if not _is_punct(tokens[j]):
            out.append(tokens[j])
        j += 1
    return out


def match_rule(rule, tokens, sentence_text):
    """
    Character (start, end) spans of sentence_text matched by one rule.

    Several hits inside one sentence come back as separate spans so the
    panel can mark every occurrence.
    """
    match = rule["match"]
    runs = _token_runs(match, tokens)
    spans = []
    offsets = [t["idx"] for t in tokens]
    for start, end in runs:
        veto = False
        if rule.get("at_start") and _left_neighbour(tokens, start) is not None:
            veto = True
        if not veto and rule.get("not_after"):
            neighbour = _left_neighbour(tokens, start)
            veto = neighbour is not None and _match_condition(
                rule["not_after"], neighbour
            )
        if not veto and rule.get("not_at_start_with"):
            veto = _left_neighbour(tokens, start) is None and _match_condition(
                rule["not_at_start_with"], tokens[start]
            )
        if not veto and rule.get("not_inside"):
            veto = any(
                _match_condition(rule["not_inside"], tok)
                for tok in tokens[start + 1 : end - 1]
            )
        if not veto and rule.get("followed_by"):
            nxt = _right_window(tokens, end, 1)
            veto = nxt is None or not _match_condition(rule["followed_by"], nxt[0])
        if not veto and rule.get("not_followed_by"):
            veto = any(
                _match_condition(rule["not_followed_by"], tok)
                for tok in _right_window(tokens, end, _TRAIL_WINDOW)
            )
        if not veto and end > start:
            last = tokens[end - 1]
            # An engine may report the token's true end offset (used when
            # the matching "surface" is normalised shorter than the text).
            char_end = last.get("end") or offsets[end - 1] + len(last["surface"])
            spans.append((offsets[start], char_end))
    # Literal regex recognisers (top-level or nested in any_of).
    spans.extend(_regex_spans(match, sentence_text, tokens))
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


def make_rule(
    key,
    name,
    level,
    desc,
    match,
    zh=None,
    not_after=None,
    followed_by=None,
    not_followed_by=None,
    at_start=False,
    not_at_start_with=None,
    not_inside=None,
):
    """
    Build one engine rule dict.

    The two context keys mirror `not_after` on the right-hand side:
    `followed_by` *requires* the next non-punctuation token to match, and
    `not_followed_by` vetoes the hit when any of the next three does.
    `at_start` keeps only hits that open the sentence; `not_at_start_with`
    drops a sentence-opening hit whose first token matches the spec.
    `not_inside` drops a hit when a token inside its span matches the spec.
    """
    r = {"key": key, "name": name, "level": level, "desc": desc, "match": match}
    if zh:
        r["zh"] = zh
    if not_after:
        r["not_after"] = not_after
    if followed_by:
        r["followed_by"] = followed_by
    if not_followed_by:
        r["not_followed_by"] = not_followed_by
    if at_start:
        r["at_start"] = True
    if not_at_start_with:
        r["not_at_start_with"] = not_at_start_with
    if not_inside:
        r["not_inside"] = not_inside
    return r


# Korean descriptions for the panel's 한국어 display language, keyed by
# rule key across every matcher-based engine.  Each entry mirrors the
# English `desc` of the rule it names; rules without an entry fall back
# to their English description.
_KO_BY_KEY = {
    # ---- English ----
    "en_there_be": "there is / there are는 '~이 있다'의 뜻으로 존재를 나타내며, 복수에는 there are를 쓰는 표현.",
    "en_lets": "let's + 동사원형으로 '~하자'라는 제안을 나타내는 표현.",
    "en_can_could": "can은 능력이나 허락, could는 과거의 능력이나 정중한 부탁을 나타내는 조동사.",
    "en_want_to": "want to + 동사원형으로 '~하고 싶다'는 소망을 나타내는 표현.",
    "en_must_should": "must는 강한 의무, have to는 필수, should는 조언을 나타내는 조동사.",
    "en_past_simple": "일반과거로, 과거에 끝난 동작이나 상태를 나타냄(went, saw).",
    "en_present_continuous": "현재진행형(am/is/are + -ing)으로 지금 일어나고 있는 동작을 나타냄.",
    "en_past_continuous": "과거진행형(was/were + -ing)으로 과거 어느 순간 진행 중이던 동작을 나타냄.",
    "en_will_future": "will + 동사원형으로 미래나 즉석 결심, 약속을 나타냄.",
    "en_going_to": "be going to + 동사원형으로 계획·의도나 임박한 일을 나타냄.",
    "en_comparative": "비교급(-er / more ... than)으로 두 가지를 비교하며 than과 함께 씀.",
    "en_superlative": "최상급(the -est / the most)으로 무리 안에서 가장 높은 정도를 나타냄.",
    "en_like_ing": "like/love/hate/enjoy 같은 선호 동사 뒤에 -ing 형태를 쓰는 표현.",
    "en_too_to": "too + 형용사 + to로 '~해서 ...할 수 없다'는 과다를 나타내는 표현.",
    "en_as_as": "as ... as로 '만큼 ~한' 동등 비교를 나타내는 표현.",
    "en_either_or": "either ... or로 둘 중 하나를 고르는 선택을 나타내는 표현.",
    "en_present_perfect": "현재완료(have/has + 과거분사)로 과거 동작이 현재와 연결됨을 나타냄.",
    "en_past_perfect": "과거완료(had + 과거분사)로 두 과거 동작 중 더 이른 것을 나타냄.",
    "en_passive": "수동태(be + 과거분사)로 주어가 동작을 당하며, 행위자는 by 뒤에 오거나 생략됨.",
    "en_first_conditional": "제1조건문(if + 현재, will)로 실현 가능한 미래의 조건을 나타냄.",
    "en_second_conditional": "제2조건문(if + 과거, would)으로 실현되기 어려운 가정을 나타냄.",
    "en_so_that": "so + 형용사 + that로 '~할 정도로 ~하다'는 결과를 나타내는 표현.",
    "en_used_to": "used to + 동사원형으로 과거의 습관이나 지금은 아닌 상태를 나타냄.",
    "en_relative_pronouns": "관계대명사(who/which/whose)가 명사를 꾸며 주는 절을 연결함.",
    "en_reported_speech": "전달동사(said/told)의 과거형에 맞춰 시제를 한 단계 과거로 옮기는 간접화법.",
    "en_third_conditional": "제3조건문(if + had done, would have done)으로 과거에 실현되지 않은 가정을 나타냄.",
    "en_wish_past": "wish + 과거로 현재와 반대되는 소망을 나타내는 표현.",
    "en_must_have": "must have / can't have + 과거분사로 과거에 대한 추측을 나타내는 표현.",
    "en_have_sth_done": "have + 목적어 + 과거분사(사역 구문)로 남이 해 주는 것을 나타내는 표현.",
    "en_despite": "despite / in spite of는 절이 아닌 명사나 -ing를 이끄는 양보 표현.",
    "en_unless": "unless는 'if ... not', 즉 '~하지 않는 한'의 뜻.",
    "en_inversion": "부정·제한 부사를 문두에 놓으면 조동사가 앞으로 오는 도치 구문(Never have I seen).",
    "en_future_perfect": "미래완료(will have + 과거분사)로 미래 시점까지의 완료를 나타냄.",
    "en_future_continuous": "미래진행형(will be + -ing)으로 미래 어느 시점에 진행 중일 동작을 나타냄.",
    "en_whereas": "whereas / whilst는 격식 있는 대조나 시간 대비를 나타내는 접속사.",
    "en_cleft": "강조 구문(it was ... that)으로 문장을 나눠 특정 부분을 강조함.",
    "en_mandative_subjunctive": "suggest/recommend/insist 등의 that절에서 동사원형을 쓰는 명령형 가정법.",
    # ---- Spanish ----
    "es_ser_de": "ser + de는 출신이나 재료를 나타냄(es de España).",
    "es_estar_en": "estar + en은 위치를 나타냄(está en casa).",
    "es_hay": "hay / había는 비인칭 haber로 '~이 있다'를 나타내며 명사 앞에 관사를 쓰지 않음.",
    "es_gustar": "gustar류 동사는 좋아하는 것이 주어가 됨(me gusta el café).",
    "es_wh_questions": "qué/cómo/dónde 등의 의문사는 정보를 묻으며 강세 표시를 동반함.",
    "es_preterite": "단순과거로 시작과 끝이 분명한 완료된 과거 동작을 나타냄(comí, comió).",
    "es_imperfect": "과거미완으로 진행·반복되는 과거나 과거의 묘사를 나타냄(comía, vivía).",
    "es_ir_a": "ir a + 부정사로 '~하려고 하다'의 근접 미래를 나타냄(voy a comer).",
    "es_tener_que": "tener que + 부정사로 의무를 나타냄(tengo que estudiar).",
    "es_acabar_de": "acabar de + 부정사로 '방금 ~했다'를 나타냄.",
    "es_para_inf": "para + 부정사는 목적(~하기 위해)을, por는 원인·교환을 나타냄.",
    "es_desde_hace": "desde hace + 기간으로 지금까지 계속되는 기간을 나타냄.",
    "es_mas_que": "más/menos ... que로 '~보다 더/덜' 비교를 나타냄.",
    "es_tan_como": "tan + 형용사 + como로 '만큼 ~한' 동등 비교를 나타냄.",
    "es_le": "간접목적 대명사 le/les는 '그/그들에게'의 뜻으로 gustar류와도 쓰임.",
    "es_imperativo": "긍정 명령형: 동사가 문장 첫머리에 오면 명령이 되며(Hazlo ahora, Dame el libro), 대명사가 붙으면 dímelo처럼 한 단어로 씀.",
    "es_subj_present": "현재 접속법으로 의지·감정·의심·필요의 표현 뒤에 쓰임(quiero que).",
    "es_conditional": "조건법(compraría)으로 가정이나 정중한 표현을 나타냄.",
    "es_futuro": "단순미래(-ré/-rá/-remos)로 앞으로의 예측·약속·추측을 나타냄(vendrá mañana).",
    "es_present_perfect": "현재완료(he + 과거분사)로 현재와 연결된 과거 동작을 나타냄(he comido).",
    "es_pluperfect": "과거완료(había + 과거분사)로 두 과거 중 더 이른 동작을 나타냄.",
    "es_si_conditional": "si + 과거접속법, 조건법으로 비현실적인 가정을 나타냄(Si tuviera, compraría).",
    "es_subj_past": "과거접속법(-ra/-se)으로 과거 트리거 뒤나 si 절에서 쓰임.",
    "es_subj_pluperfect": "과거완료 접속법(hubiera + 과거분사)으로 실현되지 않은 과거 가정을 나타냄.",
    "es_conditional_perfect": "조건법 완료(habría + 과거분사)로 '과거에 ~했을 텐데'를 나타냄.",
    "es_se": "se는 재귀(se lava), 수동(se vende), 비인칭(se dice)을 나타냄.",
    "es_como_si": "como si + 접속법으로 '~인 것처럼'을 나타냄.",
    "es_llevar_gerund": "llevar + 기간 + gerundio로 지금까지 계속된 기간을 나타냄.",
    "es_de_haber": "de haber + 과거분사는 문어체 비현실 과거 조건(= si hubiera sabido).",
    "es_por_mas_que": "por más que + 접속법으로 '아무리 ~해도' 양보를 나타냄.",
    "es_y_eso_que": "y eso que는 '~했는데도'의 구어적 양보 표현.",
    "es_no_es_que": "no es que + 접속법으로 '~해서가 아니라'를 나타냄.",
    # ---- Russian ----
    "ru_u_menya": "у + 속격 + есть로 '~이 가지고 있다' 소유를 나타내고, нет으로 부정함.",
    "ru_nravitsya": "여격 + нравится로 좋아하는 것이 주어가 됨(мне нравится).",
    "ru_prep_loct": "в/на + 전치격으로 '~에(서)' 위치를 나타냄(в городе).",
    "ru_k_dat": "к + 여격으로 사람·장소를 향한 방향을 나타냄(к другу).",
    "ru_modal_inf": "можно/нужно/надо + 부정사의 비인칭 양태 표현.",
    "ru_imperative": "명령형으로 명령을 나타냄(читайте, сядьте).",
    "ru_s_ablt": "с + 조격으로 '~와 함께'를 나타냄(с другом).",
    "ru_gen_preps": "без/для/до/из/от 등의 전치사는 뒤에 속격을 요구함.",
    "ru_o_loct": "о/об + 전치격으로 '~에 대하여'를 나타냄(о работе).",
    "ru_nums_2_4": "2~4 뒤의 명사는 단수 속격을 취함(два часа).",
    "ru_nums_5": "5 이상(много/несколько 포함) 뒤의 명사는 복수 속격을 취함(пять книг).",
    "ru_past": "과거형(-л/-ла/-ли)으로 미완료체는 과정·습관, 완료체는 결과를 나타냄.",
    "ru_future": "미완료체는 буду + 부정사, 완료체는 단일 형태로 미래를 나타냄.",
    "ru_by": "бы + 과거형으로 가정법을 만듦(если бы).",
    "ru_chtoby": "чтобы는 목적('~하기 위해')이나 바라는 일을 나타냄.",
    "ru_poka_ne": "пока не는 '~할 때까지'를 나타냄.",
    "ru_posle_togo_kak": "после того как는 '~한 후에'를 나타냄.",
    "ru_pricastie": "형용분사(-щий/-вший/-мый)로 동작하는/동작한 대상을 꾸밈.",
    "ru_deepricastie": "부사분사(-я/-в)로 동시 동작이나 방식을 나타냄(читая).",
    "ru_motion_prefixes": "이동동사에 접두사(при-/у-/по-)가 붙어 의미가 달라짐(прийти 도착하다).",
    "ru_sya": "-ся/-сь 동사는 재귀를 나타냄(учится).",
    "ru_dolzhen_byl": "должен был + 부정사로 '하기로 되어 있었지만 안 했다'를 나타냄.",
    "ru_chut_ne": "чуть не + 완료체 과거로 '하마터면 ~할 뻔했다'를 나타냄.",
    "ru_stoilo_kak": "стоило ... как는 '~하자마자'를 나타냄.",
    "ru_v_techenie": "в течение + 속격으로 '~동안에'를 나타냄.",
    "ru_double_neg": "никогда/никто 등의 부정대명사는 не와 함께 쓰임(이중 부정).",
    "ru_kak_ni": "как ни는 '아무리 ~해도'의 양보를 나타냄.",
    "ru_edva_li": "едва ли / вряд ли는 '~일 리 없다'의 의심을 나타냄.",
    "ru_chem_tem": "чем ... , тем ...로 '~하면 할수록' 상관 비교를 나타냄.",
    "ru_stoit_kak": "стоит + 부정사 ..., как ...로 '~하기만 하면 곧'을 나타냄(стоит свернуть вправо, как ...).",
    # ---- French ----
    "fr_il_y_a": "il y a는 '~이 있다'를 나타내며 과거는 il y avait.",
    "fr_estceque": "est-ce que는 평서문을 예/아니오 의문문으로 바꿈.",
    "fr_c_est": "c'est는 '~이다'로 사물이나 사람을 지시·설명함.",
    "fr_wh_questions": "quand/comment/pourquoi 등의 의문사가 정보를 물음.",
    "fr_negation": "ne ... pas 등의 부정 구문: ne는 동사 앞, 나머지는 동사 뒤에 옴.",
    "fr_possessives": "소유형용사(mon/ma/mes)는 소유자가 아니라 소유물의 성·수에 일치함.",
    "fr_futur_proche": "근접미래(aller + 부정사)로 '~하려고 하다'를 나타냄(je vais partir).",
    "fr_passe_compose": "복합과거(avoir/être + 과거분사)로 회화체의 과거를 나타냄.",
    "fr_imparfait": "반과거로 지속·습관·묘사의 과거를 나타냄.",
    "fr_futur_simple": "단순미래(sera/irai)를 나타냄.",
    "fr_modals": "pouvoir/vouloir/devoir + 부정사로 능력·소망·의무를 나타냄.",
    "fr_comparative": "plus/moins/aussi ... que로 비교와 동등을 나타냄.",
    "fr_venir_de": "venir de + 부정사로 '방금 ~했다'를 나타냄.",
    "fr_depuis": "depuis는 지속을 나타내며 프랑스어는 현재시제와 씀(j'habite ici depuis deux ans).",
    "fr_relative": "관계대명사 qui(주어)/que(목적)/dont/où로 절을 연결함.",
    "fr_subjonctif": "접속법으로 의지·감정·의심·필요 뒤에 쓰임(il faut que).",
    "fr_il_faut": "il faut는 비인칭 필수('~해야 한다')를 나타냄.",
    "fr_conditionnel": "조건법(voudrais)으로 정중한 부탁과 가정을 나타냄.",
    "fr_ce_que": "ce qui / ce que는 '~하는 것'의 중성 관계대명사.",
    "fr_y_en": "대명사 y(장소)와 en(양·de 목적어)의 대용.",
    "fr_sans_inf": "sans + 부정사로 '~하지 않고'를 나타냄.",
    "fr_si_conditionnel": "si + 반과거, 조건법으로 비현실적인 현재 가정을 나타냄.",
    "fr_gerondif": "en + 현재분사(gérondif)로 '~하면서/~함으로써'를 나타냄.",
    "fr_subjonctif_passe": "과거 접속법으로 접속법 안의 선행 동작이나 비현실을 나타냄.",
    "fr_apres_avoir": "après avoir/être + 과거분사로 '~한 후에'를 나타냄.",
    "fr_passe_simple": "단순과거는 문어체 서사의 과거(il fit).",
    "fr_ne_que": "제한 부정 ne ... que로 '오직 ~만'을 나타냄(il n'y a que cinq étudiants).",
    "fr_demo": "지시형용사 ce/cet/cette/ces는 명사의 성·수에 일치함.",
    "fr_partitif": "부분관사 du/de la/des로 불특정한 양을 나타냄(du pain).",
    "fr_imperatif": "명령법(Parlez ! / Finis !)으로 지시나 명령을 나타냄.",
    "fr_pronominal": "재귀동사 se + 동사로 자신에게 행해지는 동작을 나타냄(se lever).",
    "fr_inversion": "주어-동사 도치로 의문문을 만듦(Voyage-t-il ?).",
    "fr_superlatif": "최상급 le/la/les + plus/moins로 '가장 ~'을 나타냄.",
    "fr_avant_de": "avant de + 부정사로 '~하기 전에'를 나타냄.",
    "fr_plus_que_parfait": "대과거(avait fini)로 과거 어느 시점 이전의 완료를 나타냄.",
    "fr_futur_anterieur": "전미래(aura fini)로 미래 시점까지의 완료를 나타냄.",
    "fr_pendant": "pendant + 기간으로 '~동안(의 지속)'을 나타냄(depuis와 대비).",
    "fr_faire_causatif": "사역 구문 faire + 부정사로 '~하게 하다/시키다'를 나타냄.",
    "fr_des_que": "dès que / aussitôt que로 '~하자마자'를 나타냄.",
    "fr_pour_que": "pour que + 접속법으로 목적 '~하기 위하여'를 나타냄.",
    "fr_bien_que": "bien que + 접속법으로 양보 '비록 ~이지만'을 나타냄.",
    "fr_comme_si": "comme si + 반과거로 '~인 것처럼'을 나타냄.",
    "fr_a_moins_que": "à moins que + 접속법으로 '~하지 않는 한'을 나타냄.",
    "de_genitiv": "속격(Genitiv: des/der/-es)은 소유를 나타내며 전치사 뒤에도 쓰임.",
    "de_komparativ": "비교급은 어미 -er을 붙이고 als와 함께 씀(größer als).",
    "de_superlativ": "최상급은 am + -sten 또는 -ste 형태로 나타냄(am größten).",
    "de_relativsatz": "관계대명사는 선행사의 성·수·격을 따르며, 관계절 동사는 문장 끝에 감.",
    "de_doppel_infinitiv": "지각·사역 동사는 완료시제에도 부정사를 유지함(ich habe ihn kommen sehen).",
    "de_doppelobjekt": "geben 등의 동사는 간접목적어(여격 사람) + 직접목적어(대격 사물)를 취함.",
    "de_modal_perfekt": "완료과거에서 조동사는 과거분사 대신 부정사를 유지함(hat ... gekonnt).",
    "de_zeitangabe": "시간 부사(heute/morgen/gestern)는 활용하지 않고 문두에 자주 옴.",
    "de_hauptsatzkoord": "und/aber/denn 등으로 연결된 주절은 어순을 유지한 채 병렬로 연결됨.",
    # ---- German ----
    "de_es_gibt": "es gibt + 대격으로 '~이 있다' 존재를 나타냄.",
    "de_modals": "활용된 조동사는 2위, 부정사는 문장 끝에 감(ich kann heute nicht kommen).",
    "de_negation": "nicht는 동사·형용사를, kein은 명사를 부정함.",
    "de_questions": "wer/was/wann/wo/warum 등의 의문사가 정보를 물음.",
    "de_possessives": "소유관사(mein/dein)는 소유물의 성·수·격에 따라 어미를 취함.",
    "de_perfekt": "완료과거(Perfekt: haben/sein + 과거분사)로 회화체의 과거를 나타냄.",
    "de_praeteritum": "과거(Präteritum)는 문어체 과거이며 회화에서는 sein/haben/조동사에 주로 씀.",
    "de_futur1": "Futur I(werden + 부정사)로 미래나 추측을 나타냄.",
    "de_akku": "직접목적어는 대격을 취함(den Mann).",
    "de_dativ": "간접목적어와 aus/bei/mit/nach/seit/von/zu 뒤는 여격을 취함.",
    "de_zu_inf": "zu + 부정사로 versuchen/beginnen 등과 연결됨.",
    "de_als": "als는 비교('~보다')와 과거 시간절('~했을 때')을 나타냄.",
    "de_gern": "gern은 동작에 대한 좋아함을 나타냄(ich schwimme gern).",
    "de_reflexive": "독일어에는 재귀동사가 많음(sich freuen, ich wasche mich).",
    "de_weil": "weil/obwohl/damit 같은 종속접속사 뒤의 동사는 문장 끝으로 감.",
    "de_werden_passiv": "수동태(werden + 과거분사)를 나타냄(es wird gemacht).",
    "de_konjunktiv2": "가정법 II(würde/hätte/wäre)로 정중한 부탁과 비현실을 나타냄.",
    "de_seit": "seit는 지속을 나타내며 독일어는 현재시제와 씀(seit zwei Jahren).",
    "de_ob": "ob는 '~인지 아닌지'의 간접 의문을 나타냄.",
    "de_trotz": "trotz + 속격은 '~에도 불구하고', trotzdem은 '그럼에도'의 뜻.",
    "de_je_desto": "je ... desto로 '~하면 할수록' 상관 비교를 나타냄.",
    "de_zwar": "zwar/jedoch/allerdings는 격식 있는 양보·대조를 나타냄.",
    "de_indem": "indem은 '~함으로써' 방식절을 이끌며 동사가 끝으로 감.",
    # ---- Italian ----
    "it_essere_di": "essere + di는 출신을 나타냄(sono di Roma).",
    "it_ce": "c'è / ci sono는 '~이 있다'(단수/복수)를 나타냄.",
    "it_piacere": "piacere는 좋아하는 것이 주어가 됨(mi piace la pizza).",
    "it_questions": "che/come/dove/perché 등의 의문사가 정보를 물음.",
    "it_stare_gerundio": "stare + gerundio로 진행 중인 동작을 나타냄(sto mangiando).",
    "it_passato_prossimo": "근접과거(avere/essere + 과거분사)로 회화체의 과거를 나타냄.",
    "it_imperfetto": "미완과거로 지속·습관·묘사의 과거를 나타냄.",
    "it_futuro": "단순미래(partirò, sarà)를 나타냄.",
    "it_modali": "potere/volere/dovere + 부정사로 능력·소망·의무를 나타냄.",
    "it_comparativo": "più/meno ... che/di로 비교를 나타냄.",
    "it_bisogna": "bisogna는 비인칭 필수('~할 필요가 있다')를 나타냄.",
    "it_congiuntivo": "접속법으로 의견·의심·감정 뒤에 쓰임(penso che sia).",
    "it_condizionale": "조건법(vorrei, comprerei)으로 정중한 부탁과 가정을 나타냄.",
    "it_cui": "관계대명사 cui(전치사와 함께)와 il quale(명사와 일치)를 나타냄.",
    "it_da_tempo": "da + 기간은 현재시제와 함께 지속을 나타냄(abito qui da due anni).",
    "it_ne": "부분대명사 ne가 di구나 수량을 대신함.",
    "it_prima_di": "prima di + 부정사로 '~하기 전에'를 나타냄.",
    "it_si_passivante": "si는 수동·비인칭을 나타냄(si parla, si dice).",
    "it_stare_per": "stare per + 부정사로 '막 ~하려는 참'임을 나타냄.",
    "it_congiuntivo_passato": "과거 접속법으로 접속법 안의 선행 동작을 나타냄.",
    "it_se_condizionale": "se + 접속법, 조건법으로 비현실적인 가정을 나타냄.",
    "it_congiuntivo_imperfetto": "미완 접속법(fossi/avessi)으로 비현실이나 과거 시제 일치의 접속법을 나타냄.",
    "it_dopo_aver": "dopo avere/essere + 과거분사로 '~한 후에'를 나타냄.",
    "it_passato_remoto": "원과거(passato remoto)는 역사 서사의 과거(fu, vide).",
    # ---- Portuguese ----
    "pt_estar_em": "estar + em은 위치를 나타냄(está em casa).",
    "pt_haver": "비인칭 há(구어 tem)는 '~이 있다'를 나타냄.",
    "pt_gostar": "gostar는 전치사 de를 취함(gosto de música).",
    "pt_questions": "onde/como/por que 등의 의문사가 정보를 물음.",
    "pt_progressivo": "진행형: 유럽식은 estar a + 부정사, 브라질식은 estar + gerúndio.",
    "pt_ir_inf": "ir + 부정사로 흔히 쓰이는 미래 표현(vou comer).",
    "pt_imperfeito": "미완과거로 지속·습관·묘사의 과거를 나타냄.",
    "pt_modais": "poder/dever/querer/precisar + 부정사로 능력·의무·소망을 나타냄.",
    "pt_ter_que": "ter que/de + 부정사로 의무를 나타냄(tenho que estudar).",
    "pt_comparativo": "mais/menos/tão ... que/como로 비교와 동등을 나타냄.",
    "pt_subjuntivo": "접속법으로 의지·의심·감정 뒤에 쓰임(espero que venha).",
    "pt_relative": "관계대명사 o qual(명사와 일치), cujo('~의'), quem을 나타냄.",
    "pt_ha_tempo": "há + 시간은 '~전에' 또는 '~동안'을 나타냄.",
    "pt_prima_inf": "antes de/depois de + 부정사로 '~하기 전에/후에'를 나타냄.",
    "pt_se_passivo": "clitic -se는 수동·비인칭을 나타냄(fala-se).",
    "pt_mais_que_perfeito": "대과거(tinha + 과거분사)로 두 과거 중 더 이른 동작을 나타냄.",
    "pt_se_condicional": "se + 접속법, 조건법으로 비현실적인 가정을 나타냄.",
    "pt_conjuntivo_imperfeito": "미완 접속법(fosse/tivesse)으로 비현실 조건을 나타냄.",
    # ---- Thai ----
    "th_pronouns": "인칭대명사: ผม(남자 나), ฉัน(여자 나), คุณ(당신), เขา(그/그녀), เรา(우리).",
    "th_copula": "계사: เป็น(종류), อยู่(위치), คือ(동일성).",
    "th_negation": "ไม่가 동사·형용사 앞에서 부정함(ไม่ไป, ไม่ดี).",
    "th_questions": "의문사: อะไร(무엇), ที่ไหน(어디), ใคร(누구), ทำไม(왜), เมื่อไหร่(언제).",
    "th_have": "มี는 '~이 있다' 소유나 존재를 나타냄.",
    "th_polite": "정중 어기사: ครับ(남자), ค่ะ/คะ(여자).",
    "th_and_or": "접속사: และ(~하고), หรือ(~또는), แต่(~그러나).",
    "th_future": "จะ가 동사 앞에서 미래를 나타냄(ฉันจะไป).",
    "th_progressive": "กำลัง(동사 앞)이나 อยู่(동사 뒤)가 진행을 나타냄.",
    "th_completive": "แล้ว가 완료나 상태 변화를 나타냄(กินแล้ว).",
    "th_want": "อยาก + 동사는 '~하고 싶다', ต้องการ는 원하다/필요하다.",
    "th_should_must": "ควร는 '~하는 게 좋다', ต้อง은 '~해야 한다(필수)'.",
    "th_yesno": "문장 끝 ไหม이 예/아니오 의문을 만듦.",
    "th_because": "เพราะ(ว่า)는 '~때문에', เพราะฉะนั้น은 '그러므로'.",
    "th_when": "เมื่อ/ตอนที่가 시간절을 이끎.",
    "th_thi": "ที่는 명사 뒤에서 관계절을 연결함(คนที่มา = 온 사람).",
    "th_can": "สามารถ ... ได้ 프레임이 능력을 나타냄.",
    "th_ever": "เคย가 동사 앞에서 과거 경험을 나타냄.",
    "th_give": "ให้는 '주다', 사동('~하게 하다'), '~을 위해'의 뜻.",
    "th_each_other": "กัน이 동사 뒤에서 '서로'를 나타냄(รักกัน).",
    "th_if": "ถ้า/หาก가 조건을 이끌며 결과절은 흔히 ก็와 짝을 이룸.",
    "th_na_worth": "น่า + 동사는 '~할 만하다/~한 느낌'을 나타냄(น่าสนใจ).",
    "th_also": "ด้วย/ก็가 '~도'를 나타내거나 강조함.",
    "th_passive": "ถูก(중립)나 โดน(불리한)이 수동을 나타냄.",
    "th_both": "ทั้ง ... และ로 '~와 ~ 둘 다'를 나타냄.",
    "th_almost": "เกือบ가 '거의/~할 뻔했다'를 나타냄.",
    "th_concessive": "แม้ว่า/ถึงแม้가 양보를 이끌며 흔히 แต่와 짝을 이룸.",
    "th_in_order_to": "เพื่อ가 목적('~하기 위해')을 나타냄.",
    "th_formal_concessive": "อย่างไรก็ตาม은 문어체 '~하든지 간에/그럼에도'의 양보.",
    # ---- Arabic ----
    "ar_questions": "هل은 예/아니오 의문을, ماذا/متى/أين/كيف/كم/لماذا는 정보를 물음.",
    "ar_prep_pron": "전치사(في/من/إلى/على/عن/مع)에 접미 인칭대명사가 붙은 형태(فيه/منها/عليه).",
    "ar_demonstratives": "지시대명사: هذا/هؤلاء(남성), هذه(여성), ذلك/تلك(저것).",
    "ar_neg_la": "لا가 미완료 동사 앞에서 부정함.",
    "ar_kana": "كان(~였다), أصبح(~되었다) 계사동사로 술어는 주격을 유지함.",
    "ar_pronouns": "독립 인칭대명사(أنا/هو/هي/نحن/هم).",
    "ar_but": "لكن은 '~그러나'를 나타냄.",
    "ar_because": "لأن는 '~때문에', لذلك/لهذا는 '그러므로'.",
    "ar_when": "عندما/حين이 시간절을 이끎.",
    "ar_sawfa": "سوف(또는 접두사 سـ)가 현재 동사를 미래로 만듦.",
    "ar_also": "أيضا/كذلك은 '~또한'을 나타냄.",
    "ar_every": "كل(~모든), بعض(~일부) 뒤에는 속격이 옴.",
    "ar_anna": "أن/إن이 내용절을 이끌며 إنّ은 강조도 나타냄.",
    "ar_if": "إذا는 실현 가능한 조건, لو는 비현실 가정을 나타냄.",
    "ar_lan_lam": "لن + 접속형은 '~하지 않을 것이다', لم + 절단형은 '~하지 않았다'.",
    "ar_relative": "관계대명사 الذي/التي/الذين이 선행사와 일치함.",
    "ar_qad": "قد + 과거는 '이미/과연', قد + 현재는 '~일지도 모른다'.",
    "ar_suffix_ha": "접미사 ها가 명사·전치사에 붙어 '그녀의/그것의'를 나타냄.",
    "ar_suffix_plural": "복수 소유 접미사 ـهم/ـكم/ـنا가 단어에 직접 붙음.",
    "ar_hatta": "حتى는 '~까지', 또는 명사 앞에서 '~조차'의 뜻.",
    "ar_laysa": "ليس는 명사문을 부정함(ليس صعبا).",
    "ar_wish": "ليت는 '~이면 좋겠다', لعل은 '~일지도 모른다'.",
    "ar_kullama": "كلما는 '~하면 할수록/Whenever'의 상관 표현.",
    "ar_lam_yakun": "لم يكن + 분사는 '~하지 않았었다' 과거완료 부정.",
    # ---- Mandarin ----
    "zh_le": "동사 + 了가 완료된 동작이나 새로운 상황을 나타냄(我吃了).",
    "zh_zhe": "동사 + 着가 지속 상태나 방식을 나타냄(坐着, 拿着).",
    "zh_guo": "동사 + 过가 경험한 과거를 나타냄(去过中国).",
    "zh_bu": "不가 현재·미래나 습관적 동작을 부정함(我不去).",
    "zh_mei": "没(有)가 과거 동작을 부정하거나 '아직 아님'을 나타냄(我没去).",
    "zh_zai": "在 + 장소는 '~에서', 在 + 동사는 진행을 나타냄(在看书).",
    "zh_question_words": "의문사: 什么(무엇), 哪(어느), 谁(누구), 怎么(어떻게), 几/多少(몇).",
    "zh_ma": "문장 끝 吗가 평서문을 예/아니오 의문문으로 바꿈.",
    "zh_ne": "문장 끝 呢가 되묻거나 어조를 부드럽게 함.",
    "zh_ba_q": "문장 끝 吧가 제안이나 추측의 어조를 더함(走吧).",
    "zh_ye_dou": "也(~도), 都(~모두)는 주제 뒤에 옴.",
    "zh_ge": "個는 일반적인 양사(一个人).",
    "zh_shi_de": "是 ... 的 강조 구문으로 이미 일어난 일의 시간·방식·장소를 강조함(他是昨天来的).",
    "zh_ba_sentence": "把문은 목적어를 동사 앞으로 옮기고 보어를 동반하는 처리문(把作业写完了).",
    "zh_bei_sentence": "被문은 수동을 나타냄(被雨淋了).",
    "zh_zhengzai": "正在가 동작의 진행을 나타냄(他正在看书).",
    "zh_cai_jiu": "就는 예상보다 이른, 才는 예상보다 늦은 시간을 나타냄.",
    "zh_geng_zui": "更(~더), 最(~가장)의 비교 표현.",
    "zh_bibi": "X 比 Y + 형용사로 '~보다 ~하다' 비교를 나타냄.",
    "zh_yinwei_suoyi": "因为(~때문에)가 원인을, 所以(~그래서)가 결과를 나타냄.",
    "zh_hai": "还는 '~아직/~게다가'를 나타냄.",
    "zh_yueyue": "越 ... 越 ...로 '~하면 할수록'을 나타냄(越来越好).",
    "zh_yibian_yibian": "一边 ... 一边 ...로 두 동작의 동시 진행을 나타냄.",
    "zh_ruguo": "如果/要是가 조건을 이끌며 흔히 就와 짝을 이룸.",
    "zh_chule": "除了 ... 以外/都/还로 '~를 제외하고/~외에도'를 나타냄.",
    "zh_yijing": "已经가 기준 시각 대비 완료를 나타냄.",
    "zh_zhiqian_zhihou": "以前/以后/之前/之后의 시간 상대 표현.",
    "zh_rang_jiao": "让/叫이 사동이나 구어 수동을 만듦(让他去, 叫人骗了).",
    "zh_wulun": "无论/不管 + 의문사·선택지 + 都로 '~이든 간에'를 나타냄.",
    "zh_erqie": "而且/并且가 내용을 더하며 而且는 단계적 강화도 나타냄.",
    "zh_jingran": "竟然/居然가 뜻밖임을 나타냄(他竟然来了).",
    "zh_genju": "根据/按照가 행동의 근거를 나타내는 전치사.",
    "zh_yimian": "以免가 '~되지 않도록' 회피 결과를 이끎.",
    "zh_bijing": "毕竟/反正가 '~어쨌든/~어차피'의 담화 부사.",
    "zh_shenzhi": "甚至가 '~심지어/~조차'의 단계적 강화를 나타냄.",
    "zh_hekuang": "何况/况且가 '~하물며/게다가'의 형식적 첨가 논거.",
    "zh_nanyi": "加以/予以/难以의 문어체 동사 구성(加以解决, 难以接受).",
    "zh_yizhi": "以至/以致가 결과를 이어 주며 以致는 주로 나쁜 결과를 나타냄.",
    "zh_ershi": "然而/却가 문어체 대조를 나타냄.",
    # HSK 3.0 大纲补充规则 (HSK 一~九级 → A1~C2)
    "zh_dixu": "第 + 수사로 서수를 나타냄(第一, 第三课).",
    "zh_men_plural": "접미사 们은 사람 명사 뒤에 붙어 복수를 나타냄(朋友们, 孩子们).",
    "zh_xiao_prefix": "小 + 성씨는 젊은 사람을 친근하게 부르는 호칭(小王, 小李).",
    "zh_nengyuan_1": "능원동사: 会(배운 기능), 能(능력·가능), 可以(허락)를 나타냄.",
    "zh_nengyuan_2": "능원동사 想(~하고 싶다), 要(~하고자 하다/곧 ~할 것이다).",
    "zh_keneng_modal": "可能는 가능성이나 추측을 나타냄(明天可能下雨).",
    "zh_chengdu_1": "정도부사: 很/非常(매우), 太(너무, 太…了와 호응), 真(정말), 有点儿(조금).",
    "zh_dui_he": "전치사 和(~와), 对(~에게/~에 대해)는 대상을 이끎.",
    "zh_zhengfan": "정반문: 술어의 긍정과 부정을 병렬해 물음(是不是, 去不去, 好不好).",
    "zh_you_np": "有字句는 소유·존재를 나타내며 수량사와 어울림(我有一个哥哥, 桌上有一本书).",
    "zh_liandong_1": "연동문: 두 동사가 주어를 공유하며 뒤 동사가 목적을 나타냄(去图书馆看书).",
    "zh_shuangbin": "쌍빈어문: 동사 + 간접목적어 + 직접목적어(给我一本书, 告诉他真相).",
    "zh_yao_kuai_le": "要/快要/就要……了는 일이 곧 일어남을 나타냄(快要下雨了).",
    "zh_dou_le": "都……了는 '벌써 ~가 되었다'의 지적이나 놀람을 나타냄(都十二点了).",
    "zh_haishi_ba": "还是……吧는 여러 선택지를 저울질한 끝에 하나를 정하는 제안(还是走吧).",
    "zh_bie": "别 + 동사는 행동의 금지·제지를 나타냄(别走, 别忘了).",
    "zh_cong_wang": "전치사 从(~로부터), 往(~쪽으로)는 기점과 방향을 나타냄.",
    "zh_gei_prep": "전치사 给는 수여·지향의 대상을 이끎(给他打电话, 给妈妈做饭).",
    "zh_gen_prep": "전치사 跟는 함께함이나 상대를 이끎(跟朋友一起去).",
    "zh_suiran_danshi": "양보·전환: 虽然(~에도 불구하고)과 但是/可是(그러나)가 짝을 이룸.",
    "zh_yi_jiu": "一……就……는 '~하자마자 ~하다'의 연결을 나타냄(一到家就睡觉).",
    "zh_gaishu_duo": "근삿수: 수사 + 多 + 양사(三十多个, 五块多).",
    "zh_dongliangci_ci": "동량사 次는 동작의 횟수를 셈(第一次, 去过三次).",
    "zh_mei_distribution": "每 + 양사는 낱낱이 분배를 나타냄(每天, 每个人).",
    "zh_xuanze_q": "선택문: 还是로 선택지를 연결해 물음(喝茶还是咖啡?).",
    "zh_verb_redup": "동사 중첩은 동작을 가볍게·짧게 나타냄(看一看, 看了看, 讨论讨论).",
    "zh_adj_redup": "형용사 중첩은 생생함이나 완곡함을 더함(高高的, 干干净净).",
    "zh_pinlv_1": "빈도부사: 有时(때로), 经常(자주).",
    "zh_yiqi_yikuai": "一起/一块儿는 '같이'를 나타냄(我们一起去).",
    "zh_zai_fanfu": "再은 동작의 반복(~을 다시)을 나타냄(再来一次, 再想想).",
    "zh_de_adverbial": "구조조사 地는 형용사 + 地 + 동사의 부사적 수식을 만듦(认真地学习).",
    "zh_jieguo_buYu": "결과보어: 동사 + 完/好/懂/错/到로 결과를 나타냄(看完, 听懂, 写错).",
    "zh_quxiang_buYu": "향향보어: 동사 + 来/去/上/下/进/出/回/过 및 복합형(走进去, 想起来).",
    "zh_zhuangtai_buYu": "상태보어: 동사 + 得 + 서술어로 방식·정도를 나타냄(跑得很快, 说得清楚).",
    "zh_shuliang_buYu": "수량보어: 동사 + 了 + 시량/동량 구는 지속·횟수를 나타냄(等了三个小时).",
    "zh_bijiao_you_mei": "비교문: A 有/没有 B(这么/那么) + 형용사는 기준에 미침을 나타냄.",
    "zh_nengyuan_3": "능원동사: 需要(~가 필요하다), 应该/该(~해야 하다), 愿意(~하고자 하다), 得(děi, ~해야 하다).",
    "zh_chengdu_2": "정도부사: 比较(비교적), 特别(특히), 挺(꽤), 有些(다소).",
    "zh_qwh_renzhi": "의문대명사 임지: 의문사 + 都/也는 '어떤 ~이든/아무도 ~않다'를 나타냄(什么都吃).",
    "zh_liangci_chongdie": "양사 중첩은 '매 ~마다'를 나타냄(个个, 天天).",
    "zh_fanwei_3": "범위부사: 一共(합계), 到处(곳곳에), 只是(단지).",
    "zh_shijian_3": "시간부사: 马上(곧), 刚/刚刚(방금), 一直(줄곧).",
    "zh_xian_zaizhihou": "先……再/然后는 '먼저 ~하고 그다음 ~하다'의 순서를 나타냄.",
    "zh_pinlv_3": "빈도부사: 总是(늘), 常常(자주), 又(또).",
    "zh_qingtai_3": "양태부사: 大概(아마), 必须(반드시), 一定(반드시), 差不多(거의), 好像(~인 듯하다), 几乎(거의).",
    "zh_yuqi_3": "어기부사: 当然(당연히), 其实(사실은), 终于(마침내).",
    "zh_bubi_buyong": "不必/不用는 '~할 필요가 없다'를 나타냄(不用客气).",
    "zh_huozhe": "접속사 或/或者는 '~거나 ~거나' 선택을 나타냄.",
    "zh_jieci_3": "전치사 向(~쪽으로), 关于(~에 관하여).",
    "zh_zhiyou_cai": "필요조건: 只有……才는 '~해야만 ~하다'를 나타냄.",
    "zh_zhiyao_jiu": "충분조건: 只要……就는 '~하기만 하면 ~하다'를 나타냄.",
    "zh_budan": "점진: 不但……而且는 '~할 뿐만 아니라 ~도'를 나타냄.",
    "zh_keshi": "可是는 '~하지만' 전환을 나타냄.",
    "zh_ranhou": "然后는 '~한 다음에' 순차 연결을 나타냄.",
    "zh_dehua": "조사 的话는 조건절을 닫음(~라면)(下雨的话就不去).",
    "zh_cong_qi": "从……起는 '~부터' 시작점을 나타냄(从明天起).",
    "zh_dui_laishuo": "对……来说(~에게는), 在……看来(~의 눈에는)의 관점 프레임.",
    "zh_yi_dou_bu": "강조 부정: 一……也/都不/没는 '조금도 ~않다'를 나타냄(一点儿也不知道).",
    "zh_gai_le": "该……了는 '~할 때가 되었다'를 나타냄(该走了).",
    "zh_keneng_buYu": "가능보어: 동사 + 得/不 + 보어는 '~할 수 있다/없다'를 나타냄(看不懂, 买不起).",
    "zh_chengdu_buYu_1": "정도보어: 형용사/동사 + 得很/极了는 정도가 큼을 나타냄(好得很, 累极了).",
    "zh_fanwen_bushi": "반문: 不是……吗?는 부정형으로 당연함을 강조함.",
    "zh_bijiao_geng_hai": "비교문: A 比 B 更/还 + 형용사는 '~보다 더 ~하다'를 나타냄.",
    "zh_bijiao_yiyang": "비교문: A 跟 B(一)样는 '~와 같다'를 나타냄.",
    "zh_bu_bibi": "비교문: A 不比 B + 형용사는 '~보다 더 ~하지 않다'를 나타냄.",
    "zh_bijiao_duoshao": "비교문: 比……多/少/早/晚 + 동사 + 수량은 차이를 나타냄(比我多买了两本).",
    "zh_huozhe_huozhe": "선택복문: 或者……或者……는 '~이거나 ~이거나'를 나타냄.",
    "zh_yihui_yihui": "병렬복문: 一会儿……一会儿……는 '~했다가 ~했다가'를 나타냄.",
    "zh_you_you": "병렬복문: 又……又……는 '~하고 ~하기까지 하다'를 나타냄(又大又甜).",
    "zh_weile": "为了는 '~을 위해' 목적을 나타냄.",
    "zh_gaishu_linjin": "근삿수: 인접 수사 병용(三四个, 七八个).",
    "zh_cunxian_3": "존현문: 장소 + 동사 + 수량사 + 명사로 출현·소실을 나타냄(家里来了一个客人).",
    "zh_dongliangci_3": "동량사 口/回/遍/声/下는 동작의 차수를 셈(读了两遍).",
    "zh_gan_modal": "능원동사 敢는 '감히 ~하다'를 나타냄(不敢去).",
    "zh_ge_renhe": "지시대명사 各(각각의), 任何(어떤 ~이든).",
    "zh_chengdu_4": "정도부사: 十分(몹시), 更加(더욱), 稍微(조금), 尤其(특히), 多么(얼마나).",
    "zh_fanwei_4": "범위부사: 共(모두), 仅(겨우), 至少(적어도).",
    "zh_shijian_4": "시간·빈도부사: 从来(한 번도), 赶紧/赶快(서둘러), 忽然(문득), 本来(원래), 重新(다시), 往往(흔히), 偶尔(이따금).",
    "zh_fangshi_4": "방식부사: 故意(일부러), 互相/相互(서로).",
    "zh_qingtai_4": "양태부사: 也许(어쩌면), 恐怕(아마 ~일 것이다).",
    "zh_bing_fou": "부분 부정: 并不/并没有는 예상과 달리 '~한 것이 아님'을 나타냄.",
    "zh_yuqi_4": "어기부사: 千万(절대로), 确实(확실히), 只好(부득이), 差点儿(하마터면), 正好(마침), 究竟/到底(대체).",
    "zh_nandao": "반문: 难道……吗?는 강한 반문을 나타냄.",
    "zh_jieci_4": "전치사: 对于(~에 대해), 由于(~때문에), 随着(~에 따라), 作为(~로서).",
    "zh_lianci_4": "접속사: 此外(이 밖에), 不过(그러나), 另外(또한), 因此(그러므로).",
    "zh_jiran_jiu": "인과복문: 既然……就는 '~한 이상 ~하다'를 나타냄.",
    "zh_budan_buguang": "점진: 不仅/不光는 '~할 뿐만 아니라'를 나타냄.",
    "zh_shi_causative": "사동: 使 + 사람 + 동사구는 '~하게 하다'를 나타냄(使我很感动).",
    "zh_buru": "비교문: A 不如 B는 '~만 못하다'를 나타냄.",
    "zh_xiangbi": "비교문: 跟……相比는 '~와 비교하면'을 나타냄.",
    "zh_shuangchong_fouding": "이중 부정: 不得不/非……不可는 '~하지 않을 수 없다'로 강한 긍정을 나타냄.",
    "zh_bushi_ershi": "병렬복문: 不是……而是는 '~인 것이 아니라 ~이다'를 나타냄.",
    "zh_ji_you": "병렬복문: 既……又/也는 '~하기도 하고 ~하기도 하다'를 나타냄.",
    "zh_yifangmian": "병렬복문: 一方面……另一方面는 '한편으로는 ~, 다른 한편으로는 ~'를 나타냄.",
    "zh_shouci_qici": "순차복문: 首先……其次는 '먼저 ~, 다음 ~'을 나타냄.",
    "zh_yushi": "于是는 '~하자 ~했다'의 연쇄를 나타냄.",
    "zh_lian_ye_dou": "극단 표시: 连……也/都는 '~조차'를 나타냄(连他都不知道).",
    "zh_bushi_jiushi": "선택복문: 不是……就是는 '~이거나 ~이거나 둘 중 하나'를 나타냄.",
    "zh_jinguan": "양보복문: 尽管……但是는 '~에도 불구하고'를 나타냄.",
    "zh_fouze": "否则는 '~하지 않으면'을 나타냄.",
    "zh_jishi_ye": "양보복문: 即使……也는 '~라 해도 ~하다'를 나타냄.",
    "zh_jiushi_ye": "양보복문: 就是……也는 '~라 해도 여전히 ~하다'를 나타냄.",
    "zh_bu_ye": "압축복문: 不……也는 '~하지 않아도 ~하다'를 나타냄(不去也行).",
    "zh_hao_mudi": "목적복문: ……，好……는 앞 절 행동의 목적을 나타냄(早点出发，好赶上).",
    "zh_weile_er": "为了……而는 '~을 위해 ~하다'의 목적 프레임.",
    "zh_you_shenme_hao": "有什么（好）X의는 '~할 것이 없다'의 일축을 나타냄.",
    "zh_gaishu_3": "근삿수: 수사 + 来 + 양사, 大约/左右/前后, 배수 ……倍.",
    "zh_dongliangci_chang": "동량사 场/顿/趟는 사건·끼니·왕복을 셈(一场雨, 一顿饭, 一趟车).",
    "zh_guding_4a": "관용구: 来得及(시간이 맞다), 来不及(못 맞추다), 有的是(넉넉하다), 说不定(어쩌면), 不怎么(별로 ~않다).",
    "zh_guding_4b": "고정형: 再也不/没(다시는 ~않다), 怎么都/也 + 不/没(아무리 해도 ~않다).",
    "zh_zai_fangmian_na": "고정형: 在……方面(~면에서), 拿……来说(~를 예로 들면).",
    "zh_yi_liang_biyiliang": "고정형: 一 + 양사 + 比 + 一 + 양사는 '~할수록 점점'을 나타냄(一年比一年高).",
    "zh_gou_de": "够……의는 '참 ~하다' 정도가 큼을 나타냄(够累的).",
    "zh_chengdu_buYu_2": "정도보어: 형용사/심리동사 + 死了/厉害는 극심한 정도를 나타냄(累死了, 热得厉害).",
    "zh_sizige_1": "사자격: 一A一B/无A无B/有A有B/没A没B(一心一意, 无边无际).",
    "zh_chengdu_5": "정도부사: 过于(지나치게), 相当(상당히), 格外(유달리), 极其(극히), 较为(비교적).",
    "zh_shijian_5": "시간부사: 曾经(한때), 立刻(즉시), 始终(끝내), 早已(벌써), 即将(곧), 渐渐(점점).",
    "zh_pinlv_5": "빈도부사: 老是(늘), 通常(보통), 时常(자주).",
    "zh_fangshi_5": "방식부사: 尽量(될 수 있는 한), 亲自(친히).",
    "zh_yidan_jiu": "一旦……就는 '~하는 순간 ~하다'를 나타냄.",
    "zh_sihu_fangfu": "양태부사: 似乎/仿佛는 '~인 듯하다'를 나타냄.",
    "zh_yuqi_5": "어기부사: 根本(전혀), 果然(과연), 简直(그야말로), 绝对(절대로), 的确(확실히), 反而(오히려), 白(헛되이).",
    "zh_jieci_5": "전치사: 自从(~이후), 朝/沿(~쪽으로/~을 따라), 替(~을 대신해), 凭(~에 의거해).",
    "zh_lianci_5": "접속사: 以及(~및), 从而(그로써), 可见(~을 알 수 있다), 总之(요컨대), 假如(만약).",
    "zh_shide": "조사 似的는 像……似的로 '~처럼' 비유를 나타냄.",
    "zh_chongdie_gushi": "중첩 고정형: A来A去, A着A着, 说A就A(想来想去, 说走就走).",
    "zh_A_de_A": "고정형: A的A，B的B는 '~은 ~하고 ~은 ~하다'를 나타냄(大的大，小的小).",
    "zh_guding_5": "고정형: 从……来看(~로 볼 때), (自)……以来(~이래), 由……组成(~으로 구성되다).",
    "zh_you_zhe": "有字句: 有着는 서면체 소유·내재를 나타냄(有着深远的影响).",
    "zh_ba_yi_V": "把字句: 把 + 목적어 + 一 + 동사는 단번에 하는 동작을 나타냄(把门一关).",
    "zh_bei_gei": "피동문: 被……给V는 구어 피동을 나타냄(被他给打破了).",
    "zh_bijiao_shuliang": "비교문: A + 형용사 + B + 수량구는 차이의 양을 나타냄(弟弟比我小三岁).",
    "zh_keneng_bude": "가능보어: 동사 + 得/不得는 '~해도 되다/~해서는 안 되다'를 나타냄(去不得).",
    "zh_chengdu_buYu_3": "정도보어: …… + 得 + 不得了/要命/要死는 극심한 정도를 나타냄.",
    "zh_huoshi_huoshi": "선택복문: 或是……或是는 '~이거나 ~이거나'를 나타냄.",
    "zh_jiashe_5": "가정복문: 万一(만약 ~하면), 要不然/不然(그렇지 않으면).",
    "zh_yiner": "인과복문: 因而는 '~하므로 그 결과'를 나타냄.",
    "zh_napa_ye": "양보복문: 哪怕……也는 '설령 ~라도'를 나타냄.",
    "zh_budanbu_faner": "점진복문: 不但不/没有……反而는 '~하기는커녕 오히려'를 나타냄.",
    "zh_weideshi": "목적복문: 为的是는 '~이 목적이다'를 나타냄.",
    "zh_meiyou_jiu_meiyou": "압축복문: 没有……就没有는 '~이 없다면 ~도 없다'를 나타냄.",
    "zh_bu_bu": "압축복문·사자격: 不X不Y는 '~도 ~도 아니다'를 나타냄(不大不小).",
    "zh_zai_ye": "압축복문: 再……也는 '아무리 ~해도'를 나타냄(再难也要坚持).",
    "zh_dongliangci_5": "양사 眼/番/串/滴/扇(看了一眼, 一番话, 一串葡萄).",
    "zh_yichang_dadu": "정도·범위부사: 异常(이상도록), 大都(대부분), 净(그저 ~하기만 하다).",
    "zh_shijian_6": "시간부사: 一时(한때), 不时(이따금), 仍旧/依旧(여전히), 一向(늘).",
    "zh_yizai_zaishan": "빈도부사: 一再/再三는 거듭을 나타냄.",
    "zh_fangshi_6": "방식부사: 不禁(저절로), 赶忙(급히), 特地/特意(특별히), 偷偷(몰래).",
    "zh_weibi": "未必는 '반드시 ~한 것은 아니다'를 나타냄.",
    "zh_yuqi_6": "어기부사: 恰好(마침), 倒是(그렇긴 하다), 干脆(차라리), 明明(분명히), 总算(드디어).",
    "zh_zhiyu_chuwai": "전치사 至于(~에 관해서), 除……之外(~외에).",
    "zh_buliao": "不料는 '~할 줄 몰랐는데' 뜻밖을 나타냄.",
    "zh_suo_zhu": "구조조사 所는 서면체 관계구 所 + 동사를 만듦(所说, 所想).",
    "zh_yuqi_zhuci": "어기조사 啦/嘛, 也好……也罢는 '~든 ~든 상관없다'를 나타냄.",
    "zh_haobu_rongyi": "好（不）容易는 '간신히 ~하다'를 나타냄(好不容易才买到).",
    "zh_nadao_ye_shi": "那倒（也）는 상대의 말에 대한 동의를 나타냄.",
    "zh_suanle_dele": "算了(됐다), 得了(그만하자)는 만류·중단을 나타냄.",
    "zh_dao_weizhi": "到……为止는 '~까지'를 나타냄.",
    "zh_ni_yi_ju": "고정형: A一量，B一量/东一A西一A는 번갈아 일어남을 나타냄(你一言，我一语).",
    "zh_fangzhe_bu": "放着X不Y는 두고두고 ~하지 않음을 나타냄.",
    "zh_gui_gui": "X归X는 'X는 어디까지나 X'를 나타냄(说归说，做归做).",
    "zh_kan_ni_de": "看/瞧 + 너 + X의는 상태를 놀리듯 지적함(瞧你高兴的).",
    "zh_chengdu_buYu_4": "정도보어: 동사/형용사 + 透（了）/透顶은 '철저히 ~하다'를 나타냄(坏透了).",
    "zh_yao_me_yao_me": "선택복문: 要么……要么는 '~하든가 ~하든가'를 나타냄.",
    "zh_sui_dan": "虽……但/可/却는 서면체 양보 '~에도 불구하고'를 나타냄.",
    "zh_fanshi_dou": "조건복문: 凡是……都는 '~하는 것은 모두'를 나타냄.",
    "zh_chufei_cai": "조건복문: 除非……才/否则는 '~하는 경우 외에는'을 나타냄.",
    "zh_jiusuan_ye": "양보복문: 就算……也는 '설령 ~라도'를 나타냄.",
    "zh_yibian": "以便는 '~하기 위해/~하도록'을 나타냄.",
    "zh_yishi_yishi": "병렬복문: 一时……一时는 '~했다가 ~했다가'를 나타냄.",
    "zh_sizige_6": "사자격: 左A右B/东A西B(左顾右盼, 东奔西走).",
    "zh_chengdu_79": "서면체 정도부사: 大为(크게), 分外(유달리), 略(약간), 颇(꽤), 万分(몹시), 尤为(특히), 愈加/越发(더욱더).",
    "zh_fanwei_79": "범위부사: 皆/均(모두), 统统(전부), 唯独(오직), 无非(~에 지나지 않다), 一概(일률적으로).",
    "zh_shijian_79": "시간부사: 姑且(일단), 届时(그때가 되면), 历来(줄곧), 尚(아직), 随即(곧바로), 向来(늘).",
    "zh_pinlv_79": "빈도부사: 屡次(거듭), 时而(~했다가 ~했다가), 相继(잇달아), 一连(연이어), 再度(다시).",
    "zh_fangshi_79": "서면체 방식부사: 不由得(저절로), 趁机(호기를 타), 竭力(애써), 擅自(함부로), 毅然(결연히), 骤然(갑자기), 逐一(하나씩), 专程(특별히 찾아가).",
    "zh_fouding_79": "부정부사: 不见得(~일 리는 없다), 绝不(결단코 ~않다), 无从(~할 길이 없다), 无须(~할 필요 없다), 无可(~할 수 없다), 勿(~하지 말라).",
    "zh_yuqi_79": "서면체 어기부사: 必定(반드시), 不妨(~해 보는 것도 좋다), 反倒(도리어), 好在(다행히), 何必(꼭 ~할 필요 있느냐), 莫非(설마), 难怪(어쩐지), 偏偏(굳이), 势必(필시), 索性(아예), 万万(결코), 未免(너무 ~한 감이 있다), 务必(부디), 幸亏(다행히), 终究(끝내).",
    "zh_nai_shi": "판단부사 乃는 서면체 계사 '~이다'를 나타냄(乃是).",
    "zh_jieci_79": "전치사: 本着(~의 정신에 입각해), 基于(~에 근거해), 鉴于(~을 고려해), 当着(~의 면에서).",
    "zh_lianci_79": "접속사: 免得(~되지 않게), 倘若/若(만약 ~이면), 以防(~에 대비해), 加之(게다가), 乃至(~나아가).",
    "zh_zhuci_79": "어기조사: 罢了(~에 불과하다), 也罢(~든 ~든), 矣(문언체 종결).",
    "zh_dongci_79": "서면체 동사: 巴不得/恨不得(~하고 간절히 바라다), 免不了(~하기 쉽다), 可谓(~라고 할 수 있다), 所谓(소위), 并非(~이 아니다), 有所(다소 ~하다).",
    "zh_huayu_biaoji": "담화 표지: 归根到底(궁극적으로), 换句话说(다시 말해), 无论如何(어떤 경우에도), 总而言之(요컨대), 综上所述(상기한 바와 같이), 说真的/老实说(솔직히).",
    "zh_dongbudong_jiu": "动不动就는 '~하기 일쑤다'를 나타냄.",
    "zh_shumian_fanwen": "서면체 반문: 何至于(어찌 ~할 만큼이냐), 谈何(어찌 ~하기 쉽겠는가).",
    "zh_shumian_bijiao": "서면체 비교: 仅次于(~다음가다), 莫过于(~보다 나은 것이 없다).",
    "zh_shi_er_ding": "视……而定는 '~에 따라 정해지다'를 나타냄.",
    "zh_zhici_zhiji": "值此……之际는 '~하는 시점에'를 나타냄.",
    "zh_bieti_duo": "别提（有）多……了는 '말할 나위도 없이 ~하다'를 나타냄.",
    "zh_yin_er": "因……而는 '~으로 인하여 ~하다'를 나타냄.",
    "zh_mei_shenme_tou": "没什么 + 동사 + 头儿는 '~할 만한 것이 없다'를 나타냄(没什么看头).",
    "zh_bijiao_yu_guo": "서면체 비교문: A + 형용사 + 于/过 + B(高于, 胜过, 超过).",
    "zh_yilai_erlai": "병렬복문: 一来……二来/一则……二则는 이유를 나열함('~인 데다 ~이기도 하다').",
    "zh_suowei_jiushi": "해설복문: 所谓……就是는 '소위 ~란 바로 ~이다'를 나타냄.",
    "zh_huo_huo": "선택복문: 或……或는 '~하거나 ~하거나'를 나타냄(서면체).",
    "zh_yuqi_ningke": "선택복문: 与其……宁可/不如는 '~하기보다 ~하는 편이 낫다'를 나타냄.",
    "zh_biekan_suishuo": "전환: 别看/虽说는 '~라고은 하지만'을 나타냄.",
    "zh_wulun_yufou": "조건복문: 无论……与否는 '~하든 안 하든'를 나타냄.",
    "zh_fan_zhe_jun": "조건복문: 凡……（者）均은 '~하는 자는 모두'를 나타냄(서면체).",
    "zh_jiaruo": "가정복문: 假若/假使는 '~이라면'을 나타냄(서면체).",
    "zh_zongran": "가정복문: 纵然/纵使는 '설령 ~이라 해도'를 나타냄.",
    "zh_gu_lianjie": "，故……는 서면체 인과 '~하므로'를 나타냄.",
    "zh_bieshuo_dou": "점진복문: 别说……都/也는 '~은 말할 것도 없고'를 나타냄.",
    "zh_bushuo_hai": "점진복문: ……不说，还는 '~인 데다 오히려'를 나타냄.",
    "zh_shangqie_hekuang": "점진복문: 尚且……何况는 '~조차 그런데 하물며'를 나타냄.",
    "zh_wei_qijian": "목적복문: 为（了）……起见는 '~를 위해'를 나타냄.",
    "zh_ren_ye": "압축복문: 任（凭）……也는 '아무리 ~해도'를 나타냄.",
    "zh_feide_cai": "압축복문: 非得……才는 '꼭 ~해야만'을 나타냄.",
    "zh_ningke_yebu": "선택복문: 宁可/宁愿……也不는 '~하는 편이 낫다'를 나타냄.",
    # ---- Cantonese ----
    "yue_ge": "嘅은 普通话 的에 해당하며 소유·수식을 나타냄(我嘅書).",
    "yue_zo": "동사 + 咗는 了에 해당하며 완료를 나타냄(食咗飯).",
    "yue_gan": "동사 + 緊은 '~하고 있는 중' 진행을 나타냄(食緊飯).",
    "yue_m": "唔가 동사·형용사를 부정함(唔去, 唔好).",
    "yue_pronouns": "인칭대명사: 佢(그/그녀), 복수는 哋(我哋/你哋/佢哋).",
    "yue_questions": "의문사: 乜嘢/咩(무엇), 邊度(어디), 邊個(누구), 點解(왜), 幾多(얼마).",
    "yue_hai": "喺는 在에 해당하며 위치를 나타냄(喺屋企).",
    "yue_yiga": "而家는 '지금'을 나타냄.",
    "yue_zyu": "동사 + 住가 동작 중의 상태 유지를 나타냄(拎住).",
    "yue_sai": "동사 + 晒는 '전부' 완료를, 完은 '끝남'을 나타냄.",
    "yue_gam": "咁이 형용사 앞에서 '~이리', 噉은 '~저리'를 나타냄.",
    "yue_zung": "仲은 '~아직/~게다가'를 나타냄(仲未食).",
    "yue_tungmaai": "同埋는 '~하고', 定係는 의문문의 '~아니고'를 나타냄.",
    "yue_final_particles": "문장 말 어기사: 啦(제안), 喎(당연), 囉(그러려니) 등.",
    "yue_sik": "識 + 동사는 '~할 줄 알다'를 나타냄.",
    "yue_msai": "唔使는 '~할 필요 없다', 使唔使는 '~할 필요가 있나?'를 나타냄.",
    "yue_dak": "동사 뒤 得이 가능이나 보어 표지(講得快).",
    "yue_maai": "동사 + 埋는 '~까지 포함해서'를 나타냄(買埋呢個).",
    "yue_sik_jyu": "好似 ... 噉이 '~처럼' 비유를 나타냄.",
    "yue_jauh_sik": "就算 ... 都가 '~해도' 양보를 나타냄.",
    "yue_jyuhai": "冇는 没에 해당하며 소유 부정이나 과거 부정(冇去)을 나타냄.",
    "yue_mdaanzi": "唔單止 ... 仲(要)가 '~뿐만 아니라 ~도'를 나타냄.",
    "yue_mhtung": "唔通은 '~일 리는 없지 않을까' 되묻는 의심 표지.",
    "yue_mingming": "明明이 '~분명한데도'를 강조함.",
    "yue_lahngwaah": "담화 표지: 講真(솔직히), 話時話(그나저나).",
    "yue_ngaanghai": "硬係가 '~고집스럽게 ~하다'를 나타냄(佢硬係唔信).",
}


def _desc(rule, display_lang):
    "Description for a rule in the requested display language."
    if display_lang == "ko":
        ko = _KO_BY_KEY.get(rule.get("key")) or (rule.get("ko") or "").strip()
        if ko:
            return ko
    if display_lang == "zh" and rule.get("zh"):
        return rule["zh"]
    return rule["desc"]


def desc_for_rule(rule, display_lang):
    """
    Public alias of :func:`_desc` for the library-page exporters.

    The /grammar page lists rules the panel never saw on a page, so it has
    to resolve the description the same way the panel does -- otherwise a
    Korean reader gets Korean example translations under English headings.
    """
    return _desc(rule, display_lang)


def example_gloss(example, display_lang):
    """
    The translation to print under an example, in the display language.

    An engine stores its examples with whatever translations the corpus
    carries -- Spanish as ``{"es", "en", "zh"}``, Russian as
    ``{"ru", "zh"}``, Japanese as ``{"japanese", "english", "chinese"}``
    -- so the field names differ per language.  Each engine therefore
    resolves its own rows (see the per-engine ``_example_gloss``); this
    shared helper covers the common ``{"<lang>", "en", "zh", "ko"}``
    shape and is what the library-page exporters use.

    A missing translation stays empty rather than falling back to English
    under a Chinese or Korean heading: the front end hides an empty gloss,
    which is honest, while a wrong-language one is not.
    """
    if not isinstance(example, dict):
        return ""
    if display_lang == "zh":
        return (example.get("zh") or example.get("chinese") or "").strip()
    if display_lang == "ko":
        return (example.get("ko") or "").strip()
    return (example.get("en") or example.get("english") or "").strip()


def paired_examples(own_field, texts, row):
    """
    A corpus row's examples as dicts carrying whichever glosses it has.

    The Spanish / French / German / Russian corpora store the sentences as a
    bare list plus parallel gloss lists (``example_zh``, ``example_en``,
    ``example_ko``).  Pairing them here keeps every engine's loader on one
    rule: a gloss list that does not line up with the sentences is dropped
    whole rather than applied to the wrong ones, and a row with no glosses
    at all keeps returning plain strings so nothing downstream sees a
    dict it does not need.
    """
    lists = {}
    for field in ("zh", "en", "ko"):
        values = [
            str(value or "").strip() for value in (row.get("example_" + field) or [])
        ]
        if len(values) == len(texts):
            lists[field] = values
    if not lists:
        return list(texts)
    paired = []
    for index, text in enumerate(texts):
        entry = {own_field: text}
        for field, values in lists.items():
            if values[index]:
                entry[field] = values[index]
        paired.append(entry)
    return paired


def reference_gloss(reference, display_lang):
    """
    Translation for a rule's folded reference example, in the display
    language, or "" when the corpus carries none for it.
    """
    return example_gloss(reference, display_lang)


# ---- library export ----------------------------------------------------
#
# The /grammar page lists the whole corpus at once, where the panel lists
# only what the current page happens to contain.  The engines with a data
# layer (ja/ko/ru/es/fr/de/th) each build their own rows, because their
# rows carry examples, levels and enrichment the shared shape does not
# cover; the engines whose corpus *is* their hand-written rule set share
# the exporter below.

_CEFR_ORDER = ("A1", "A2", "B1", "B2", "C1", "C2")

_golden_lock = threading.Lock()
_golden_cache = {}


def load_golden(path):
    """
    Curated example sentences from one golden file, keyed by rule key.

    A golden file is ``{rule key: {"name": ..., "examples": [...]}}`` where
    an example carries the sentence in the language's own field plus
    whatever translations it has (``en`` / ``zh`` / ``ko``).  The loader
    keeps only examples with a sentence in the engine's own field -- a row
    whose text field is empty is a data error, and shipping it would show
    a blank example on the library page.

    Missing file means "no golden set for this engine", which is the normal
    state for a rule set that was written without examples.
    """
    with _golden_lock:
        if path not in _golden_cache:
            examples = {}
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    for key, entry in json.load(fh).items():
                        rows = [
                            example
                            for example in entry.get("examples") or []
                            if (example.get("text") or "").strip()
                        ]
                        if rows:
                            examples[key] = rows
            _golden_cache[path] = examples
        return _golden_cache[path]


def rule_library_entries(rules, display_lang="en", examples_by_key=None):
    """
    Export a hand-written rule list as browsable library rows.

    Every rule becomes a row -- these rule sets have no reference-only
    rows, so nothing is flagged ``skipped`` -- ordered by CEFR band and
    stable within a band (the order the rules are written in, which is
    the teaching order).

    ``examples_by_key`` optionally supplies curated sentences per rule key,
    the ``{key: [{"<lang>", "en", "zh", "ko"}, ...]}`` shape a golden set
    uses.  Each becomes a ``{"text", "tr"}`` row with the translation for
    the display language, or a bare sentence where the set carries none for
    it.  A rule set with no golden file simply passes nothing and its rows
    stay example-less, as before.
    """
    examples_by_key = examples_by_key or {}
    entries = []
    for rule in rules:
        rows = []
        for example in examples_by_key.get(rule["key"]) or []:
            if isinstance(example, dict):
                text = (example.get("text") or "").strip()
                if not text:
                    continue
                tr = example_gloss(example, display_lang)
                rows.append({"text": text, "tr": tr} if tr else text)
            else:
                text = (example or "").strip()
                if text:
                    rows.append(text)
        entries.append(
            {
                "key": rule["key"],
                "name": rule["name"],
                "level": rule["level"],
                "desc": desc_for_rule(rule, display_lang),
                "examples": rows,
                "skipped": False,
            }
        )
    return sorted(
        entries,
        key=lambda e: (
            _CEFR_ORDER.index(e["level"])
            if e["level"] in _CEFR_ORDER
            else len(_CEFR_ORDER)
        ),
    )


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
                # The entry's own curated example and its translation, for
                # the panel's folded reference block.  Optional: only the
                # engines that carry one on their rules emit it, so the
                # block simply disappears for the rest.  The translation is
                # resolved here, in the display language, rather than in
                # the front end -- the same rule the engines follow for
                # `desc`, so a zh panel never sees an English gloss.
                reference = rule.get("reference")
                if reference:
                    text = reference_gloss(reference, display_lang)
                    if text:
                        entry["reference"] = {
                            "sentence": (reference.get("sentence") or "").strip(),
                            "text": text,
                        }
                by_key[rule["key"]] = entry
                order.append(rule["key"])
            if len(entry["examples"]) >= _EXAMPLE_CAP:
                continue
            if any(ex["sentence"] == sentence for ex in entry["examples"]):
                continue
            entry["examples"].append(
                {
                    "sentence": sentence,
                    "matches": [{"start": s, "end": e} for s, e in spans],
                }
            )
    return [by_key[k] for k in order]
