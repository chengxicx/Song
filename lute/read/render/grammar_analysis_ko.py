"""
Korean grammar point detection.

Mirrors the Japanese engine (grammar_analysis_ja.py) but uses kiwipiepy
(Kiwi) instead of Sudachi for morphological analysis.

Kiwi tokenises each sentence into morphemes with a surface form (`form`),
a dictionary lemma and a single Sejong POS tag (e.g. VV / VA / VX / NNG /
JKB / EC / EF / ETM / ETN; irregular conjugations get a `-I` suffix).  A
token-aware matcher then recognises grammar constructions on the lemma
basis, so conjugated forms (할 수 있어요 / 할 수 없어요 / 할 수 있을 거예요)
match the same rule.

Hand-written rules cover the highest-frequency beginner constructions with
precise POS constraints (_KO_RULES).  On top of that, grammar_ko.json (the
snapshot generated from kimchi-grammar, CC-BY 4.0) is loaded data-driven:
each definition's `focus` literals (the <f>…</f> phrases the upstream
authors mark inside example sentences) are re-used as literal-substring
recognisers, so the long tail of constructions is covered without a
hand-written matcher for each one.

Rows merged from the "private-book" books (scripts/
grammar_materials_ko/) carry no focus literals; their matchers are derived
from the pattern text itself and validated against the row's own examples
(see _load_pattern_rules).  Those rows also carry the panel's optional
blocks -- a 接续 line, the book's 注意点, and a folded 参考例句 with its
translation and highlight offsets -- which the Japanese panel has and this
one did not.

Only text (meaning / examples) is consumed; the audio fields are ignored
(ElevenLabs commercial license).
"""

import json
import os
import re
import threading
import unicodedata

from lute.read.render.grammar_analysis_matcher import load_golden

# ---- tokenizer -------------------------------------------------------

_tokenizer = None


def _get_tokenizer():
    "Lazy, process-lifetime Kiwi tokenizer."
    global _tokenizer
    if _tokenizer is None:
        from kiwipiepy import Kiwi  # pylint: disable=import-outside-toplevel

        _tokenizer = Kiwi()
    return _tokenizer


def _split_sentences(text):
    """
    Split a page of text into sentences on Korean punctuation or line breaks.

    Line breaks matter for subtitle/transcript books, whose lines usually
    carry no sentence-final punctuation and would otherwise collapse into
    one pseudo-sentence (making every grammar example the whole page).
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"(?<=[.!?！？])|\n", text)
    return [p.strip() for p in parts if p and p.strip()]


def _tokens_for(sentence):
    "Tokenize a sentence; return a list of token dicts."
    tok = _get_tokenizer()
    out = []
    for m in tok.tokenize(sentence):
        out.append(
            {
                # Kiwi's character offset of the morpheme in the input.
                # This is the one reliable anchor: a surface form's length
                # can diverge from its display width (할 is one syllable but
                # comes back as 하 + ᆯ), so accumulating lengths drifts.
                "start": m.start,
                "surface": m.form,
                "lemma": getattr(m, "lemma", None) or m.form,
                "pos": m.tag,
            }
        )
    return out


# ---- matcher ---------------------------------------------------------


def _match_condition(cond, token):
    "True if a single matcher (cond) matches a single Korean token."
    if cond.get("any"):
        return True
    if "surface" in cond:
        surfaces = cond["surface"]
        if isinstance(surfaces, str):
            surfaces = (surfaces,)
        # Final jamo differ between the pattern spelling (compatibility
        # jamo) and Kiwi's output (conjoining jamo); fold before comparing.
        got = token["surface"].translate(_JAMO_FOLD)
        if not any(got == s.translate(_JAMO_FOLD) for s in surfaces):
            return False
    if "lemma" in cond and token["lemma"] not in cond["lemma"]:
        return False
    if "pos" in cond:
        # Prefix match: "V" matches VV/VA/VX/VCP/VV-I (irregular), "J" all
        # particles, "EC" only that connective ending, etc.  A tuple means
        # "any of these tags" (다고 is EF or EC depending on the sentence).
        wanted = cond["pos"]
        if isinstance(wanted, str):
            wanted = (wanted,)
        if not any(token["pos"].startswith(p) for p in wanted):
            return False
    return True


def _try_match(conds, tokens, i, j):
    """
    Backtracking match of the condition sequence against the token list.

    Returns the number of tokens consumed, or -1 when the sequence does
    not match starting at token j.
    """
    if i == len(conds):
        return 0
    cond = conds[i]
    if cond.get("optional"):
        if j < len(tokens) and _match_condition(cond, tokens[j]):
            n = _try_match(conds, tokens, i + 1, j + 1)
            if n >= 0:
                return n + 1
        return _try_match(conds, tokens, i + 1, j)
    if j >= len(tokens):
        return -1
    if not _match_condition(cond, tokens[j]):
        return -1
    n = _try_match(conds, tokens, i + 1, j + 1)
    if n < 0:
        return -1
    return n + 1


def _token_span_runs(conds, tokens):
    "Token-index runs (start, end) where the condition sequence matches."
    runs = []
    for start in range(len(tokens)):
        n = _try_match(conds, tokens, 0, start)
        if n > 0:
            runs.append((start, start + n))
    return runs


def _token_offsets(tokens):
    """
    Character offset in the sentence of each token's start.

    Taken straight from Kiwi (`start`), never accumulated from surface
    lengths: Kiwi strips whitespace from surfaces and decomposes some
    syllables (할 -> 하 + ᆯ), so length arithmetic drifts.
    """
    return [t["start"] for t in tokens]


def _regex_aligned(match, spec, tokens):
    """
    Does a regex match start on a morpheme boundary?  (Opt-in per spec.)

    A literal that is the tail of a *contracted* verb form is ambiguous in
    the raw text: 라서 is the copula's ending (이 + 라서) but also the tail
    of 모르 + 어서 and 따르 + 어서.  Kiwi's morphemes tell the two apart,
    but a contracted stem reports its ending as starting *inside* itself
    -- 모르 spans the 몰라 and 어서 starts on the 라 -- so the test is that
    no morpheme strictly contains the match's first character.  A literal
    that legitimately spans a contracted stem -- 습니다만 is 슬 + ᆸ니다만
    -- must not ask for this.
    """
    if not spec.get("aligned") or not tokens:
        return True
    start = match.start()
    return not any(
        token["start"] < start < token["start"] + len(token["surface"])
        for token in tokens
    )


def _spec_spans(spec, tokens, sentence_text):
    "Character (start, end) ranges of sentence_text matched by one spec."
    spans = []
    if spec["type"] == "regex":
        for m in spec["re"].finditer(sentence_text):
            if m.group(0) and _regex_aligned(m, spec, tokens):
                spans.append(m.span())
    else:  # tokens
        offsets = _token_offsets(tokens)
        for start, end in _token_span_runs(spec["conds"], tokens):
            if end > start:
                # A pattern may match up to (and including) the final token
                # of a short line with no trailing 。: its character end is
                # then the end of the sentence, not a token-start offset.
                span_end = offsets[end] if end < len(offsets) else len(sentence_text)
                # offsets[end] is the *next* token's start, so a match that
                # ends before a space would swallow it (기가 무섭게 -> "기가
                # 무섭게 ").  Trim the gap; the span covers morphemes only.
                while (
                    span_end > offsets[start] and sentence_text[span_end - 1].isspace()
                ):
                    span_end -= 1
                spans.append((offsets[start], span_end))
    return spans


def _match_spans(rule, tokens, sentence_text):
    """
    Character spans of sentence_text matched across the rule's specs.

    Deduped, order preserved.  A rule normally carries several specs for the
    same construction -- a token path and a surface-alias path -- and when two
    of them land on the same words the panel would receive that range twice
    and highlight it twice.
    """
    spans, seen = [], set()
    for spec in rule["patterns"]:
        for span in _spec_spans(spec, tokens, sentence_text):
            if span not in seen:
                seen.add(span)
                spans.append(span)
    return spans


# ---- rule helpers ----------------------------------------------------

_SURF = lambda s: {"surface": s}
_LEMMA = lambda *lemmas: {"lemma": set(lemmas)}
_POS = lambda p: {"pos": p}


def _rule(
    key,
    name,
    meaning,
    patterns,
    level="TOPIK 1-2",
    zh="",
    ko="",
    kind="construction",
    formation="",
    formation_zh="",
    formation_notes="",
    formation_notes_zh="",
    reference=None,
    examples=None,
):
    return {
        "key": key,
        "pattern": name,
        "level": level,
        "meaning": meaning,
        "zh": zh,
        "ko": ko,
        "patterns": patterns,
        "kind": kind,
        # Optional panel blocks -- 接续 (formation), the folded 参考例句
        # (reference) and 注意点 (formation_notes).  Empty for the
        # hand-written rules, and for 接续/注意点 also for the vendored
        # kimchi rows (only the merged material rows state them); the
        # vendored rows do carry a reference since their example_en was
        # backfilled.  The panel omits a block whose fields are all empty.
        "formation": formation,
        "formation_zh": formation_zh,
        "formation_notes": formation_notes,
        "formation_notes_zh": formation_notes_zh,
        "reference": reference,
        # The library's example list, as {korean, chinese, english} rows.
        # The panel quotes a single curated `reference`; the library page has
        # room for all of them, and the vendored rows ship ~3 sentences each
        # with their translations.  Empty for the hand-written rules.
        "examples": list(examples or []),
    }


# ---- hand-written beginner rules -------------------------------------
#
# Each rule's "patterns" is a list; ANY spec matching marks the sentence.
# Token specs are ordered conditions over Kiwi morphemes.  POS compares by
# prefix (so VV-I / VA-I irregulars still match "V"/"VA").

_KO_RULES = [
    _rule(
        "ko_go_issda",
        "-고 있다",
        "is/am/are doing; in the middle of doing",
        [{"type": "tokens", "conds": [{"surface": "고", "pos": "EC"}, _LEMMA("있다")]}],
        zh="正在做……；……进行中",
        ko="동작이 진행 중임을 나타내는 표현.",
    ),
    _rule(
        "ko_su_issda",
        "-(으)ㄹ 수 있다/없다",
        "can / cannot do; be possible / impossible",
        [
            {
                "type": "tokens",
                "conds": [_POS("ETM"), _SURF("수"), _LEMMA("있다", "없다")],
            }
        ],
        zh="能够/不能做……；有可能",
        ko="능력이나 가능성을 나타내는 표현.",
    ),
    _rule(
        "ko_go_sipda",
        "-고 싶다",
        "want to do",
        [{"type": "tokens", "conds": [{"surface": "고", "pos": "EC"}, _LEMMA("싶다")]}],
        zh="想做……；想要……",
        ko="~하고 싶은 소망을 나타내는 표현.",
    ),
    _rule(
        "ko_ji_anhda",
        "-지 않다",
        "negative: do not",
        [{"type": "tokens", "conds": [{"surface": "지", "pos": "EC"}, _LEMMA("않다")]}],
        zh="不……；否定",
        ko="동사의 부정을 나타내는 표현.",
    ),
    _rule(
        "ko_aeo_seo",
        "-아/어서",
        "because of; and so (reason / sequential)",
        [{"type": "tokens", "conds": [{"lemma": {"아서", "어서"}, "pos": "EC"}]}],
        zh="因为……；……所以……",
        ko="앞 내용이 뒤 내용의 이유나 근거가 됨을 나타내는 연결 어미.",
    ),
    _rule(
        "ko_eunikka",
        "-(으)니까",
        "because; since (reason)",
        # Kiwi keeps the epenthetic 으 inside the ending after a consonant
        # stem (없으니까, 많으니까), so requiring the bare 니까 missed every
        # adjective and every ㄹ-less consonant stem -- half the uses of the
        # pattern.  Both spellings are the same ending.
        [{"type": "tokens", "conds": [{"surface": ("니까", "으니까"), "pos": "EC"}]}],
        zh="因为……；由于……",
        ko="이유나 근거를 나타내는 연결 어미.",
    ),
    _rule(
        "ko_geo_future",
        "-(으)ㄹ 거예요",
        "will / going to do (future)",
        [{"type": "tokens", "conds": [_POS("ETM"), _SURF("거")]}],
        zh="将要……；打算……（将来）",
        ko="앞으로의 계획이나 추측을 나타내는 표현.",
    ),
    _rule(
        "ko_aeo_juda",
        "-아/어 주다",
        "do (something) for someone",
        [{"type": "tokens", "conds": [{"surface": "어", "pos": "EC"}, _LEMMA("주다")]}],
        zh="为某人做……；帮……做",
        ko="남을 위해 행동함을 나타내는 보조 용언.",
    ),
    _rule(
        "ko_gi_jeone",
        "-기 전에",
        "before doing",
        [
            {
                "type": "tokens",
                "conds": [
                    {"surface": "기", "pos": "ETN"},
                    {"surface": "전", "pos": "NNG"},
                ],
            }
        ],
        zh="在……之前",
        ko="어떤 일보다 앞서 함을 나타내는 표현.",
    ),
    _rule(
        "ko_jung_ida",
        "-는 중이다",
        "in the middle of doing",
        [{"type": "tokens", "conds": [_SURF("중"), _POS("VCP")]}],
        zh="正在……当中；……中",
        ko="동작이 진행되고 있는 중임을 나타내는 표현.",
    ),
    _rule(
        "ko_copula_polite",
        "-입니다 / -이에요",
        "polite copula: is / am / are",
        [{"type": "regex", "re": re.compile(r"입니다|이에요|예요")}],
        zh="是……（礼貌体）",
        ko="정중하게 '~이다'를 나타내는 표현.",
    ),
    _rule(
        "ko_eumyon",
        "-(으)면",
        "if / when (conditional)",
        [{"type": "tokens", "conds": [{"lemma": {"면", "으면"}, "pos": "EC"}]}],
        zh="如果……；当……时",
        ko="앞 내용이 뒤 내용의 조건이나 가정이 됨을 나타내는 연결 어미.",
    ),
]


def _norm_name(name):
    "Normalise a grammar name so '-아/어서' and '아/어서' compare equal."
    n = name.strip().lstrip("-－~〜")
    n = re.sub(r"^\([^)]*\)", "", n)  # drop a leading (으)/(으)ㄹ qualifier
    return n


_HANDWRITTEN_NORMS = {_norm_name(r["pattern"]) for r in _KO_RULES}


# ---- data-driven rules (kimchi-grammar snapshot) ----------------------

# Source snapshot generated by lute/jlpt_data/generate_grammar_ko.py from
# kimchi-grammar (CC-BY 4.0).  Matches the layout of jlpt_data/grammar.
_DATA_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "jlpt_data",
        "grammar_ko.json",
    )
)

# Coarse band per upstream metadata type, used as a fallback when the data
# carries no KFL TOPIK grade.  Format matches the "TOPIK x-y" labels.
_LEVEL_BY_TYPE = {
    "noun": "TOPIK 1-2",
    "universal": "TOPIK 1-2",
    "verb": "TOPIK 3-4",
    "composite": "TOPIK 3-4",
}

_DATA_DIR = os.path.dirname(_DATA_PATH)


def _load_ko_enrichment():
    """
    Chinese wording for the merged rows' 接续 / 注意点, keyed by row key.

    The books state ``formation`` in Korean and ``notes`` in English, so a
    Chinese panel would have to print one of those under a Chinese heading
    (the Japanese panel shipped that bug) or hide the block.  This file
    supplies the Chinese, mirroring the Japanese line's
    ``grammar/zh_enrichment.json``.  It lives beside the library rather than
    inside it for two reasons: ``grammar_ko.json`` stays a faithful merge of
    the books, and a re-run of the merge cannot drop the wording.
    """
    path = os.path.join(_DATA_DIR, "grammar_ko_enrichment.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


_KO_ENRICH = _load_ko_enrichment()


def _notes_text(notes):
    "Formation notes in one display string; list or scalar, or '' when absent."
    if not notes:
        return ""
    if isinstance(notes, (list, tuple)):
        return " ".join(str(n).strip() for n in notes if str(n).strip())
    return str(notes).strip()


def _curated_reference(examples, translations, specs, tokenised):
    """
    The row's own example for the panel's folded 参考例句 block.

    Returns ``{"korean", "chinese", "english"}`` for the first example that
    carries a translation, or None when the row has no usable example -- the
    panel then hides the block.

    When ``specs`` are given, an example this rule actually matches is
    preferred over document order.  The panel highlights the point inside
    the quoted sentence, and the derivation only proves a spec matches the
    row's examples, not every one of them, so first-in-order can quote a
    sentence the matcher cannot mark.
    """
    usable = []
    for i, ko in enumerate(examples or []):
        ko = (ko or "").strip()
        if not ko:
            continue
        zh, en = translations[i] if i < len(translations) else ("", "")
        if not (zh or en):
            continue
        usable.append(
            {
                "korean": ko,
                "chinese": zh,
                "english": en,
                "tokens": tokenised[i] if i < len(tokenised) else None,
            }
        )
    if not usable:
        return None
    # The zh panel only quotes a reference it can show in Chinese, so prefer
    # the examples that carry a Chinese half.  The English half exists for
    # every index (kimchi ships it for all of its examples), so this costs
    # the English panel nothing and wins the Chinese panel the block.
    candidates = [ref for ref in usable if ref["chinese"]] or usable
    chosen = candidates[0]
    if specs:
        matcher = {"patterns": specs}
        for ref in candidates:
            tokens = ref["tokens"]
            if tokens is None:
                tokens = _tokens_for(ref["korean"])
            if _match_spans(matcher, tokens, ref["korean"]):
                chosen = ref
                break
    # The token list was only needed for the choice above; keep it out of the
    # rule dict, which is what the panel and the tests introspect.
    chosen.pop("tokens", None)
    return chosen


def _translations(item):
    """
    The row's example translations as index-aligned (zh, en) pairs.

    The merged material rows carry both halves; the vendored kimchi rows
    carry only the English one (kimchi ships a translation for every
    example -- the generator used to drop it).  Strict zip would lose the
    English half wherever the Chinese half is absent, so the sides are
    filled in by index and either may be "".
    """
    zh_list = item.get("example_zh") or []
    en_list = item.get("example_en") or []
    return [
        (
            (zh_list[i] if i < len(zh_list) else "") or "",
            (en_list[i] if i < len(en_list) else "") or "",
        )
        for i in range(max(len(zh_list), len(en_list)))
    ]


def _example_data(item):
    """
    The row's examples as index-aligned {korean, chinese, english} rows.

    The library page shows every sentence the row ships, not only the one
    the panel quotes as its 参考例句.  Sentences without any translation are
    kept -- an unglossed example is still an example, and the template simply
    omits the gloss line.
    """
    examples = [e.strip() for e in (item.get("examples") or []) if (e or "").strip()]
    translations = _translations(item)
    rows = []
    for i, korean in enumerate(examples):
        zh, en = translations[i] if i < len(translations) else ("", "")
        rows.append({"korean": korean, "chinese": zh, "english": en})
    return rows


def _panel_fields(key, item, specs, examples, tokenised):
    """
    The optional panel blocks for one merged row, ready to splat into _rule.

    Only the rows merged from the study materials carry a 接续 line and a
    注意点: the books state them, the vendored kimchi rows do not.  The
    参考例句 block is not merged-row-only any more -- the vendored rows'
    kimchi translations are backfilled into ``example_en``, so their rules
    carry a reference too (see _load_data_rules).
    """
    enrichment = _KO_ENRICH.get(key) or {}
    translations = _translations(item)
    return {
        "formation": (item.get("formation") or "").strip(),
        "formation_zh": (enrichment.get("formation") or "").strip(),
        "formation_notes": _notes_text(item.get("notes")),
        "formation_notes_zh": (enrichment.get("notes") or "").strip(),
        "reference": _curated_reference(examples, translations, specs, tokenised),
    }


# ---- focus-literal precision --------------------------------------------
#
# The Kimchi authors wrap the grammar morphemes of their example sentences
# in <f>…</f>, and the generator copies those marks into each row's
# `focus`; the row's matcher is the alternation of them.  What the authors
# marked is therefore the panel's precision -- and they also mark whole
# words: the 에 rows mark 갑니다 / 가서 / 있어요 beside the particle itself,
# and the 에다가 row marks its own tail 다가.  Left alone those literals
# fire on every sentence ending in -어요 or -다가.  Two mechanisms keep the
# matcher to the point:
#
#   * a literal is kept only when the row's own name explains it (the
#     Kimchi marks are the authors', the name is the point);
#   * a literal never fires as the tail of a longer literal anywhere in
#     the library -- 거나 stays silent inside -다거나, 에 inside 에게.

# Compatibility jamo that can close a syllable, with their coda index.
_JAMO_CODA = {
    "ㄱ": 1,
    "ㄲ": 2,
    "ㄳ": 3,
    "ㄴ": 4,
    "ㄵ": 5,
    "ㄶ": 6,
    "ㄷ": 7,
    "ㄹ": 8,
    "ㄺ": 9,
    "ㄻ": 10,
    "ㄼ": 11,
    "ㄽ": 12,
    "ㄾ": 13,
    "ㄿ": 14,
    "ㅀ": 15,
    "ㅁ": 16,
    "ㅂ": 17,
    "ㅄ": 18,
    "ㅅ": 19,
    "ㅆ": 20,
    "ㅇ": 21,
    "ㅈ": 22,
    "ㅊ": 23,
    "ㅋ": 24,
    "ㅌ": 25,
    "ㅍ": 26,
    "ㅎ": 27,
}

# The allomorph a pattern name spells with its optional group, as it can
# start a surface form: (으)ㄹ is 을 after a consonant and ㄹ after a vowel.
_ALLOMORPH_TAILS = ("을", "은", "음", "읍", "ㄹ", "ㄴ", "ㅁ", "ㅂ")


def _absorb(group, tail):
    """
    Contract an optional group into what follows it: (스)ㅂ니다 -> 습니다.

    The (..) of a pattern name is a choice, and taking it changes the
    syllable in front: 스 + ㅂ니다 is 습니다 (the ㅂ closes the 스) the way
    으 + ㄹ까 is 을까.  A plain concatenation (스ㅂ니다) spells a form no
    sentence ever uses, so the row would lose every literal it marks.
    """
    if not group or not tail:
        return group + tail
    code = ord(group[-1]) - 0xAC00
    if not 0 <= code < 11172 or code % 28:
        return group + tail  # not Hangul, or the syllable is already closed
    coda = _JAMO_CODA.get(tail[0])
    if coda is None:
        return group + tail  # what follows starts with a vowel, not a jamo
    onset, nucleus = divmod(code // 28, 21)
    closed = chr(0xAC00 + 588 * onset + 28 * nucleus + coda)
    return group[:-1] + closed + tail[1:]


def _name_forms(name):
    """
    The row's point as the surface forms a sentence can spell it in.

    "(이)라서" is written either way; "아/어 달다" is two words with a
    choice in the first; "(으)ㄹ까" contracts to 을까 after a consonant, ㄹ까
    after a vowel and 까 after a ㄹ-final stem (일 + ㄹ까 = 일까);
    "(스)ㅂ니다" is 습니다 after a consonant and ㅂ니다 after a vowel;
    "더라고(요)" drops its politeness syllable.  A focus literal is only
    kept when one of these forms shows up in it, so the set has to cover
    the spellings the books actually use.
    """
    out = set()

    def expand(piece):
        # An optional (..) group: emit the reading with and without it.
        match = re.search(r"\(([^)]*)\)", piece)
        if not match:
            return {piece}
        inner = match.group(1)
        head, tail = piece[: match.start()], piece[match.end() :]
        return expand(head + tail) | expand(head + _absorb(inner, tail))

    for piece in expand((name or "").strip().lstrip("-－~〜")):
        for chunk in re.split(r"[\s,、/]+", _PUNCT.sub("", piece)):
            chunk = chunk.strip()
            if not chunk:
                continue
            out.add(chunk)
            for tail in ("다", "요"):
                # A dictionary form is also its stem (당하다 -> 당하), and a
                # (요) ending is also the bare ending (더라고(요) -> 더라).
                if chunk.endswith(tail) and len(chunk) > 1:
                    out.add(chunk[: -len(tail)])
            # The (으)X allomorph also drops after a ㄹ-final stem, where
            # the stem's own ㄹ closes the syllable: 일 + (으)ㄹ까 = 일까.
            for prefix in _ALLOMORPH_TAILS:
                if chunk.startswith(prefix) and len(chunk) > len(prefix):
                    out.add(chunk[len(prefix) :])
    return out


def _fold(text):
    "Compatibility jamo to Kiwi's conjoining form, so ㄴ and ᆫ compare equal."
    return text.translate(_JAMO_FOLD)


def _literal_is_the_point(literal, forms):
    """
    Does the row's own point show up in this focus literal?

    Two readings, because a literal is a sentence fragment: the point can
    sit in the raw string (다가 is the point of the 다가 row, 에다가 of the
    에다가 row), or inside one of the literal's Kiwi morphemes, which is
    what keeps a contracted form alive -- 수정됐어요 carries 되 (되다's
    stem) and 출발할게요 carries ᆯ게요, whose ㄹ is the (으)ㄹ the name
    spells.  Matching the morpheme rather than the raw string is also what
    drops 갑니다 / 가서 / 있어요, which carry no morpheme of their row's
    point at all.
    """
    folded_forms = [_fold(form) for form in forms]
    folded_literal = _fold(literal)
    if any(form in folded_literal for form in folded_forms):
        return True
    for token in _tokens_for(literal):
        for text in (token["lemma"], token["surface"]):
            folded = _fold(text)
            if any(form in folded for form in folded_forms):
                return True
    return False


# Literals whose only trustworthy reading is on a morpheme boundary.  라서
# is the copula's ending (이 + 라서), but it is also the tail of 모르 + 어서
# and 따르 + 어서, which a plain 라서(?!\S) reports as the (이)라서 point.
# A literal that legitimately spans a contracted stem -- 습니다만 is 슬 +
# ᆸ니다만 -- must NOT be listed here.
_FOCUS_MORPHEME_ALIGNED = frozenset({"라서"})


def _focus_regexes(literals, pool):
    """
    One regex spec for a row: its literals, longest first, each guarded.

    Korean stacks morphemes at the end of an 어절, so a literal is often
    both its own ending and the tail of a longer one: 거나 is -거나 and the
    tail of -다거나 / -라거나, 에 is the particle and the tail of 에게.  A
    plain alternation reports both rows on the same span, so every literal
    is preceded by a lookbehind for each longer literal that ends with it.
    The pool is the whole library, so a newly merged row tightens the rows
    it nests inside without anyone editing them.
    """
    parts = []
    aligned = False
    for literal in literals:
        guards = sorted(
            {
                other[: -len(literal)]
                for other in pool
                if len(other) > len(literal) and other.endswith(literal)
            }
        )
        parts.append(
            "".join(f"(?<!{re.escape(prefix)})" for prefix in guards)
            + re.escape(literal)
            + r"(?!\S)"
        )
        aligned = aligned or literal in _FOCUS_MORPHEME_ALIGNED
    spec = {"type": "regex", "re": re.compile("|".join(parts))}
    if aligned:
        # At least one literal is the tail of a contracted verb form; see
        # _FOCUS_MORPHEME_ALIGNED and _regex_aligned.
        spec["aligned"] = True
    return spec


def _load_data_rules():
    if not os.path.exists(_DATA_PATH):
        return []
    with open(_DATA_PATH, encoding="utf-8") as fh:
        entries = json.load(fh)

    # Pass 1 -- the literals each row's own point explains.  A literal the
    # name does not explain is dropped here rather than firing on every
    # sentence that happens to end in it.
    kept = {}
    for item in entries:
        name = item.get("name") or ""
        if _norm_name(name) in _HANDWRITTEN_NORMS:
            continue  # already covered precisely by a hand-written rule
        forms = _name_forms(name)
        literals = [
            focus
            for focus in (item.get("focus") or [])
            # Single-Hangul foci are too noisy as regexes; drop them.  The
            # rest are anchored at a so-called "word end" (followed by
            # space / end of string), since Korean grammatical morphemes
            # attach to the end of an 어절.  This avoids matching e.g. the
            # 에 inside 이에요.
            if len(focus) >= 2 and _literal_is_the_point(focus, forms)
        ]
        if literals:
            kept[item.get("key") or name] = sorted(set(literals), key=len, reverse=True)

    # Pass 2 -- every surviving literal in the library is a tail-guard
    # source for the shorter ones, so the pool is collected up front.
    pool = {focus for literals in kept.values() for focus in literals}

    rules = []
    for item in entries:
        key = item.get("key") or item.get("name") or ""
        literals = kept.get(key)
        if not literals:
            continue
        specs = [_focus_regexes(literals, pool)]
        # kimchi ships a translation for every example; backfilled into
        # example_en, it gives the vendored rows a curated 参考例句 too.
        # The regex spec is passed so the chosen example is one this rule
        # actually highlights, not merely the first in document order.
        reference = _curated_reference(
            item.get("examples"), _translations(item), specs, []
        )
        rules.append(
            _rule(
                key,
                item.get("name") or "",
                (item.get("meaning") or "").strip() or item.get("name") or "",
                specs,
                level=item.get("level")
                or _LEVEL_BY_TYPE.get(item.get("type", ""), "TOPIK 3-4"),
                zh=item.get("zh") or "",
                ko=item.get("ko") or "",
                reference=reference,
                examples=_example_data(item),
            )
        )
    return rules


# ---- pattern-derived rules ------------------------------------------
#
# Rows merged from the "private-book" materials (scripts/
# grammar_materials_ko/) carry no `focus` literals -- the book states a
# pattern (V-(으)ㄹ 겸 -(으)ㄹ 겸), not the word forms a sentence happens to
# use.  For those rows the matcher is derived from the pattern itself:
#
#   * the pattern is split into alternatives on "," and into chunks on
#     spaces / hyphens (V-(으)ㄹ 겸 -(으)ㄹ 겸 -> [(으)ㄹ, 겸, (으)ㄹ, 겸]);
#   * a chunk that names an ending ("(으)ㄹ", "았/었", "은/는") becomes a
#     POS+surface condition, because Kiwi decomposes the conjugated form
#     into a stem plus that ending token (할 -> 하 + ᆯ), so the ending is
#     never a substring of the raw text;
#   * any other chunk is matched on its lemma (걸리다) or on the lemma set
#     {chunk, chunk+다}, so a noun (겸) and a verb stem (걸리) both hit;
#   * a chunk that is itself a run of known particles is decomposed
#     greedily (고도 -> 고 + 도).
#
# Every derived spec is then validated against the row's own example
# sentences; a row whose specs match none of its examples is dropped, so a
# mis-derived pattern can never report a false positive.  The work needs
# Kiwi, so it happens lazily on first analysis rather than at import.

_ENDING_CONDS = {
    "(으)ㄹ": {"pos": "ETM", "surface": ("ㄹ", "을")},
    "(으)ㄴ": {"pos": "ETM", "surface": ("ㄴ", "은")},
    "(으)ㅁ": {"pos": "ETN", "surface": ("ㅁ", "음")},
    "(으)려": {"pos": "EC", "surface": ("려", "으려")},
    "(으)러": {"pos": "EC", "surface": ("러", "으러")},
    "(으)니까": {"pos": "EC", "surface": ("니까", "으니까")},
    "(으)니": {"pos": "EC", "surface": ("니", "으니")},
    "(으)면": {"pos": "EC", "surface": ("면", "으면")},
    "(으)면서": {"pos": "EC", "surface": ("면서", "으면서")},
    "(으)며": {"pos": "EC", "surface": ("며", "으며")},
    "(으)로": {"pos": "JKB", "surface": ("로", "으로")},
    "(으)로서": {"pos": "JKB", "surface": ("로서", "으로서")},
    "(으)로써": {"pos": "JKB", "surface": ("로써", "으로써")},
    "(으)로부터": {"pos": "JKB", "surface": ("로부터", "으로부터")},
    "(으)시": {"pos": "EP", "surface": ("시", "으시")},
    "(으)세요": {"pos": "EF", "surface": ("세요", "으세요")},
    "(으)ㅂ시다": {"pos": "EF", "surface": ("ㅂ시다", "읍시다")},
    "(으)ㄹ게": {"pos": "EF", "surface": ("ㄹ게", "을게")},
    "(으)ㄹ래": {"pos": "EF", "surface": ("ㄹ래", "을래")},
    "(으)ㄹ까": {"pos": "EF", "surface": ("ㄹ까", "을까")},
    "(으)ㄹ걸": {"pos": "EF", "surface": ("ㄹ걸", "을걸")},
    "(으)ㄹ지": {"pos": "EC", "surface": ("ㄹ지", "을지")},
    "(으)ㄹ수록": {"pos": "EC", "surface": ("ㄹ수록", "을수록")},
    "았/었": {"pos": "EP", "surface": ("았", "었", "였")},
    "았/었/였": {"pos": "EP", "surface": ("았", "었", "였")},
    "아/어": {"pos": "EC", "surface": ("아", "어", "여")},
    "아/어/여": {"pos": "EC", "surface": ("아", "어", "여")},
    "어서/아서": {"pos": "EC", "surface": ("어서", "아서", "여서")},
    "은/는": {"pos": "JX", "surface": ("은", "는")},
    "이/가": {"pos": "JKS", "surface": ("이", "가")},
    "을/를": {"pos": "JKO", "surface": ("을", "를")},
    "와/과": {"pos": "JC", "surface": ("와", "과")},
    "(이)나": {"surface": ("이나", "나")},
    "(이)라도": {"surface": ("이라도", "라도")},
    "(이)며": {"surface": ("이며", "며")},
    "(이)라고": {"surface": ("이라고", "라고")},
    "는": {"pos": "ETM", "surface": ("는",)},
    "는가": {"pos": "EC", "surface": ("는가",)},
    "던": {"pos": "ETM", "surface": ("던",)},
    "다": {"pos": ("EF", "EC"), "surface": ("다",)},
    "고": {"pos": "EC", "surface": ("고",)},
    "서": {"pos": "EC", "surface": ("서",)},
    "지": {"pos": "EC", "surface": ("지",)},
    "나": {"pos": "EC", "surface": ("나",)},
    "며": {"pos": "EC", "surface": ("며",)},
    "야": {"pos": "EC", "surface": ("야",)},
    "자": {"pos": "EC", "surface": ("자",)},
    "니": {"pos": "EC", "surface": ("니",)},
    "게": {"pos": "EC", "surface": ("게",)},
    "도": {"pos": "JX", "surface": ("도",)},
    "만": {"pos": "JX", "surface": ("만",)},
    "에": {"pos": "JKB", "surface": ("에",)},
    "의": {"pos": "JKG", "surface": ("의",)},
    "로": {"pos": "JKB", "surface": ("로",)},
    "기": {"pos": "ETN", "surface": ("기",)},
    "가": {"pos": "JKS", "surface": ("가",)},
    "요": {"pos": "EF", "surface": ("요",)},
    "네": {"pos": "EF", "surface": ("네",)},
    "냐": {"pos": "EF", "surface": ("냐",)},
    "라": {"pos": "EC", "surface": ("라",)},
}

# Chunks whose surface the pattern spells out but Kiwi reports as two
# morphemes with a contracted stem (해서 = 하 + 어서, 했 = 하 + 았).
_CHUNK_ALIAS = {
    "해서": [
        {"lemma": {"하", "하다"}},
        {"pos": "EC", "surface": ("어서", "아서", "여서")},
    ],
    "해": [{"lemma": {"하", "하다"}}],
    "했": [{"lemma": {"하", "하다"}}, {"pos": "EP", "surface": ("았", "었")}],
    "돼": [{"lemma": {"되", "되다"}}],
    "됐": [{"lemma": {"되", "되다"}}, {"pos": "EP", "surface": ("았", "었")}],
    "갖": [{"lemma": {"가지", "가지다"}}, {"pos": "EP", "surface": ("았", "었")}],
    "봤": [{"lemma": {"보", "보다"}}, {"pos": "EP", "surface": ("았", "었")}],
    "왔": [{"lemma": {"오", "오다"}}, {"pos": "EP", "surface": ("았", "었")}],
    "갔": [{"lemma": {"가", "가다"}}, {"pos": "EP", "surface": ("았", "었")}],
    "줬": [{"lemma": {"주", "주다"}}, {"pos": "EP", "surface": ("았", "었")}],
    "다고": [{"surface": ("다고", "ᆫ다고", "는다고", "라고", "자고", "냐고")}],
    "라고": [{"surface": ("라고", "이라고")}],
    "하": [{"lemma": {"하", "하다"}}],
    "그래": [{"lemma": {"그렇", "그렇다"}}],
    "그래요": [
        {"lemma": {"그렇", "그렇다"}},
        {"pos": "EF", "surface": ("어요", "아요", "여요")},
    ],
    "그랬": [{"lemma": {"그렇", "그렇다"}}, {"pos": "EP", "surface": ("았", "었")}],
    "못해": [{"lemma": {"못하", "못하다"}}],
    "셈치": [{"lemma": {"셈", "셈이다"}}, {"lemma": {"치", "치다"}}],
}

# Extra whole-ending keys that Kiwi reports as one token (so they must not be
# split into their parts).
_ENDING_CONDS.update(
    {
        "(으)ㄹ지라도": {"pos": "EC", "surface": ("ㄹ지라도", "을지라도")},
        "건만": {"pos": "EC", "surface": ("건만",)},
        "느냐": {"pos": "EC", "surface": ("느냐",)},
        "던데": {"pos": "EC", "surface": ("던데",)},
    }
)

# Tails that may follow a stem inside one pattern chunk.  A superset of
# _ENDING_CONDS: the extra entries are verb stems that only make sense in
# the tail position (셈치다 -> 셈치 + 다, 걸어가다 -> 걸어 + 가다).
_TAIL_CONDS = dict(_ENDING_CONDS)
_TAIL_CONDS.update(
    {
        "치": {"lemma": {"치", "치다"}},
        "하": {"lemma": {"하", "하다"}},
        "되": {"lemma": {"되", "되다"}},
        "있": {"lemma": {"있", "있다"}},
        "없": {"lemma": {"없", "없다"}},
        "주": {"lemma": {"주", "주다"}},
        "보": {"lemma": {"보", "보다"}},
        "가": {"lemma": {"가", "가다"}},
        "오": {"lemma": {"오", "오다"}},
        "두": {"lemma": {"두", "두다"}},
        "놓": {"lemma": {"놓", "놓다"}},
        "버리": {"lemma": {"버리", "버리다"}},
        "달리": {"lemma": {"달리", "달리다"}},
    }
)
_TAIL_KEYS = sorted(_TAIL_CONDS, key=len, reverse=True)

# Kiwi returns a final jamo in its conjoining form (ㄴ = U+11AB), while the
# patterns are written with the compatibility jamo (ㄴ = U+3134).  Fold both
# sides before comparing surfaces.
_JAMO_FOLD = str.maketrans(
    {
        "\u3134": "\u11ab",  # ㄴ
        "\u3139": "\u11af",  # ㄹ
        "\u3141": "\u11b7",  # ㅁ
        "\u3142": "\u11b8",  # ㅂ
        "\u3145": "\u11ba",  # ㅅ
        "\u3147": "\u11bc",  # ㅇ
    }
)

_PATTERN_SEP = re.compile(r"[\s\-–—]+")
_PUNCT = re.compile(r"[?!.,;:'\"“”‘’()\[\]]+$")
# Longest-first so "고도" decomposes as 고 + 도, not 고 + 도 wrongly.
_DECOMPOSE_KEYS = sorted(_ENDING_CONDS, key=len, reverse=True)


def _decompose(chunk):
    "Greedy prefix decomposition of a chunk into known endings, or None."
    if not chunk:
        return None
    for key in _DECOMPOSE_KEYS:
        if chunk.startswith(key):
            rest = chunk[len(key) :]
            if not rest:
                return [dict(_ENDING_CONDS[key])]
            tail = _decompose(rest)
            if tail is not None:
                return [dict(_ENDING_CONDS[key])] + tail
    return None


def _head_cond(head):
    "Condition matching a stem / noun chunk by lemma."
    if not head:
        return None
    if head in _ENDING_CONDS:
        return dict(_ENDING_CONDS[head])
    return {"lemma": {head, head + "다"}}


def _chunk_variants(chunk):
    """
    Ordered candidate cond-lists for one pattern chunk.

    The same chunk can be a single word (나머지, 겸) or a stem plus a known
    ending (무섭게 = 무섭 + 게, 하던데 = 하 + 던데); the pattern alone cannot
    tell them apart, so every reading is offered and the caller keeps the
    one that actually matches the row's own examples.
    """
    chunk = _PUNCT.sub("", (chunk or "").strip())
    if not chunk:
        return [[]]
    out = []

    def add(conds):
        if conds and conds not in out:
            out.append(conds)

    if chunk in _CHUNK_ALIAS:
        add([dict(c) for c in _CHUNK_ALIAS[chunk]])
    if chunk in _ENDING_CONDS:
        add([dict(_ENDING_CONDS[chunk])])
    add(_decompose(chunk) or [])
    if "/" in chunk:
        first = chunk.split("/")[0]
        if first in _ENDING_CONDS:
            add([dict(_ENDING_CONDS[first])])
        add(_decompose(first) or [])
    if len(chunk) >= 2 and chunk.endswith("다"):
        # A dictionary form: whole word, then stem + known tail.
        add([{"lemma": {chunk}}])
        stem = chunk[:-1]
        add(_decompose(stem) or [])
        for key in _TAIL_KEYS:
            if stem.endswith(key) and len(stem) > len(key):
                head = _head_cond(stem[: -len(key)])
                if head:
                    add([head, dict(_TAIL_CONDS[key])])
                break
    add([{"lemma": {chunk, chunk + "다"}}])
    for key in _TAIL_KEYS:
        if chunk.endswith(key) and len(chunk) > len(key):
            head = _head_cond(chunk[: -len(key)])
            if head:
                add([head, dict(_TAIL_CONDS[key])])
            break
    return out or [[{"lemma": {chunk, chunk + "다"}}]]


def _pattern_specs(name):
    """
    Ordered candidate token specs derived from a Korean pattern name.

    Each alternative of the pattern becomes a Cartesian product of its
    chunks' readings; the product is capped so a long pattern cannot blow
    up, and the caller validates the candidates against the row's examples.
    """
    specs = []
    for alt in re.split(r"[,、]", (name or "").strip().lstrip("-~〜")):
        alt = alt.strip()
        if not alt:
            continue
        chunks = [c for c in _PATTERN_SEP.split(alt) if c]
        if not chunks:
            continue
        combos = [[]]
        for chunk in chunks:
            variants = _chunk_variants(chunk)
            grown = []
            for base in combos:
                for variant in variants:
                    if len(base) + len(variant) <= 8:
                        grown.append(base + variant)
            combos = grown[:24] or combos
        for conds in combos:
            spec = {"type": "tokens", "conds": conds}
            if conds and spec not in specs:
                specs.append(spec)
    return specs


# Rows whose grammar point really is one indivisible form, so a single
# lemma-only spec is the correct reading rather than a loose fallback.
# "기로서니" is a fixed four-syllable ending with no stem slot to anchor on.
_BARE_LEMMA_ALLOWED = {"kgm_기로서니__f22a6a"}


def _spec_is_specific(spec):
    """
    Reject a derived spec too loose to be trustworthy.

    The validation in _load_pattern_rules only asks "does this fire on one of
    the row's own examples?", and a spec that is a single bare lemma passes
    that trivially by matching a content word rather than the construction:
    the row ``V-되`` derived ``{lemma: 되/되다}`` and then fired on the
    ordinary verb 되다 (``그는 훌륭한 선생님이 되었다``), reporting a grammar
    point on a sentence that has none.

    A spec is trustworthy when it can tell a construction apart from a word,
    i.e. it anchors on a POS tag, or it is a sequence of >= 2 conditions.
    A single condition with only `lemma` does neither.  Rows whose point
    genuinely *is* one indivisible form can opt out via
    _BARE_LEMMA_ALLOWED.
    """
    if spec.get("type") == "regex":
        return True
    conds = spec.get("conds") or []
    if len(conds) >= 2:
        return True
    return any(c.get("pos") for c in conds)


def _load_pattern_rules():
    """
    Rules for data rows that carry no `focus` (the merged materials rows).

    Specs are derived from the row's name and kept only when they match at
    least one of the row's own examples, so the derivation can never invent
    a matcher the data does not support.  The examples used for that check
    are recorded in _PATTERN_RULE_EXAMPLES, which the tests read to re-run
    the validation.
    """
    if not os.path.exists(_DATA_PATH):
        return []
    with open(_DATA_PATH, encoding="utf-8") as fh:
        entries = json.load(fh)
    rules = []
    for item in entries:
        if item.get("focus"):
            continue  # already covered by the focus-literal matcher
        name = item.get("name") or ""
        if not name or _norm_name(name) in _HANDWRITTEN_NORMS:
            continue
        specs = _pattern_specs(name)
        examples = [e for e in (item.get("examples") or []) if e]
        if not specs or not examples:
            continue
        tokenised = [_tokens_for(s) for s in examples]
        key = item.get("key") or name
        validated = []
        for spec in specs:
            if not _spec_is_specific(spec) and key not in _BARE_LEMMA_ALLOWED:
                continue
            if any(
                _spec_spans(spec, tokens, sentence)
                for tokens, sentence in zip(tokenised, examples)
            ):
                validated.append(spec)
                if len(validated) >= 3:
                    break
        if not validated:
            continue
        _PATTERN_RULE_EXAMPLES[key] = examples
        rules.append(
            _rule(
                key,
                name,
                (item.get("meaning") or "").strip() or name,
                validated,
                level=item.get("level")
                or _LEVEL_BY_TYPE.get(item.get("type", ""), "TOPIK 3-4"),
                zh=item.get("zh") or "",
                ko=item.get("ko") or "",
                **_panel_fields(key, item, validated, examples, tokenised),
                examples=_example_data(item),
            )
        )
    return rules


# key -> the examples a pattern-derived rule was validated against.
_PATTERN_RULE_EXAMPLES = {}


_pattern_rules_cache = None


def _get_pattern_rules():
    "Lazily built (needs Kiwi), memoised for the process lifetime."
    global _pattern_rules_cache
    if _pattern_rules_cache is None:
        _pattern_rules_cache = _load_pattern_rules()
    return _pattern_rules_cache


# The data rules are built on first use, not at import: _load_data_rules()
# tokenizes the rows' examples, which builds the Kiwi instance (~270MB
# resident).  This module is imported at app start-up (lute/read/routes.py),
# so building it here made every process pay that cost even with no Korean
# books at all.  Same reasoning as _get_pattern_rules above.
_rules_lock = threading.RLock()
_data_rules_cache = None
_all_rules_cache = None

# Rows that would report the same span as another rule on the same sentence,
# so the panel shows only one of them.  The row keeps its library entry (it
# is real material) and only the reading panel drops it.
# ``scripts.audit_grammar_ko_overlaps`` re-derives this list (IoU >= 0.6 on
# at least two sentences); a new merged row that duplicates an existing point
# shows up there, and ``test_duplicate_rule_list_matches_the_overlap_audit``
# fails until it is listed.
#
# The three survivors of the first pass are the rows whose point another row
# already reports on the very same span:
#
#   * kgm_도록하다 / kgm_아어가지고 -- the books spell out the whole
#     construction (도록 하다, 아/어 가지고) while the vendored rows name
#     the same morphemes (도록, 아/어 가지다) and highlight the same words;
#   * 아어_달라고 -- -아/어 달다 and -아/어 달라고 are one ending, and both
#     rows highlight exactly 달라고 (six shared sentences).
#
# The audit's *other* candidates were precision bugs, not duplicates, and
# were fixed in the matcher instead: 거나 firing inside -다거나, 에다가's
# tail 다가 firing on -려다가, 거니 inside -겠거니, 라서 on 모르 + 어서, and
# the 에 rows' whole-word focus (갑니다 / 가서 / 있어요).  See the
# focus-literal precision block above _load_data_rules.
_DUPLICATE_RULE_IDS = frozenset(
    {
        "kgm_도록하다__f0c5de",
        "kgm_아어가지고__49d9eb",
        "아어_달라고__quote-favor-request",
    }
)


def _get_data_rules():
    "The data-driven rules, built on first use and memoised."
    global _data_rules_cache  # pylint: disable=global-statement
    with _rules_lock:
        if _data_rules_cache is None:
            _data_rules_cache = _load_data_rules()
        return _data_rules_cache


def _get_all_rules():
    "Hand-written plus data-driven rules, built and memoised on first use."
    global _all_rules_cache  # pylint: disable=global-statement
    with _rules_lock:
        if _all_rules_cache is None:
            _all_rules_cache = _KO_RULES + _get_data_rules()
        return _all_rules_cache


def _panel_rules():
    """
    Every rule the reading panel may show, silenced duplicates removed.

    The three sources are concatenated here rather than inside
    ``_get_all_rules`` because the library page and the overlap audit both
    need the *un*dropped set -- a silenced row still has to appear in the
    library and has to stay visible to the audit, or the list could never be
    re-derived.
    """
    rules = _get_all_rules() + _get_pattern_rules()
    if not _DUPLICATE_RULE_IDS:
        return rules
    return [rule for rule in rules if rule["key"] not in _DUPLICATE_RULE_IDS]


# ---- display ---------------------------------------------------------

# Chinese descriptions for the data-driven (kimchi-grammar) entries, keyed
# by the entry's Hangul `name`.  Authors are welcome to extend this table;
# names without an entry fall back to the (English) kimchi meaning.
_KO_ZH = {
    # ---- particles / markers ----
    "(으)로": "用……；以……（方式/工具）",
    "만큼": "和……一样；……的程度",
    "하고": "和……；与……一起",
    "에": "在……；于……（场所/时间）",
    "에게": "给……；向……（对象）",
    "한테": "给……；向……（口语对象）",
    "에서": "在……（动作场所）；从……",
    "(이)나": "或者；多达……",
    "부터": "从……起",
    "까지": "到……为止",
    "은/는": "……（主题助词）",
    "이/가": "……（主格助词）",
    "을/를": "……（宾格助词）",
    "와/과": "和……；与……",
    "도": "也；连……都",
    "만": "只；仅",
    "밖에": "只有……；除……外",
    "처럼": "像……一样",
    "보다": "比……",
    "들": "（复数助词）……们",
    "께": "（敬语）给……",
    "께서": "（敬语主格）……",
    "아/야": "（呼格）……啊",
    "이": "（名）这个……",
    "그": "（名）那个……",
    "저": "（名）那个……（远指）",
    # ---- endings / constructions ----
    "(으)ㄴ/는": "……的（定语形）",
    "(으)ㄹ": "（将来/可能定语形）……的",
    "(으)면서": "一边……一边……",
    "(으)면 되다": "……就可以；只要……就行",
    "기 때문에": "因为……；由于……",
    "기 전에": "在……之前",
    "아/어 보다": "试着……；……过",
    "아/어서": "因为……；……然后",
    "니까": "因为……；由于……",
    "고 있다": "正在……；……进行中",
    "지 않다": "不……；否定",
    "고 싶다": "想做……",
    "(으)ㄹ 수 있다": "能够……；有可能",
    "어도 되다": "可以……；……也行",
    "(으)ㄹ게요": "我会……（承诺）",
    "(으)ㄹ래요": "我要……；要不要……",
    "(으)ㄹ까요?": "要不要……？……好吗？",
    "(으)ㅂ시다": "……吧（提议）",
    "(으)니": "因为……；既然……",
    "아/어": "……（口语结尾/连接）",
    "(으)ㄴ 적이 있다": "曾经……过",
    "(으)려고 하다": "打算……；想……",
    "(으)러": "为了去……而",
    "(으)러 가다": "为了……而去",
}


def _desc(rule, display_lang):
    "Description for a rule in the requested display language."
    if display_lang == "ko":
        return rule.get("ko") or rule["meaning"]
    if display_lang != "zh":
        return rule["meaning"]
    if rule["zh"]:
        return rule["zh"]
    return _KO_ZH.get(rule["pattern"]) or rule["meaning"]


# Script probes for _in_display_language below.
_CJK_CHAR = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_HANGUL_CHAR = re.compile(r"[\uac00-\ud7af]")
_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")


def _in_display_language(text, display_lang):
    """
    Whether a raw source string belongs in the panel's display language.

    The books state one string per field, not one per language: ``formation``
    is Korean prose and ``notes`` English, and only the description is
    translated (the row's own zh/ko).  Printing either straight through is
    what put an untranslated line under a Chinese heading in the Japanese
    panel, so an enrichment field is shown only when its script matches what
    the reader asked for.  The Chinese wording belongs in
    grammar_ko_enrichment.json and is added there; nothing is
    machine-translated here.

    An empty string is never displayable.
    """
    text = (text or "").strip()
    if not text:
        return False
    if display_lang == "zh":
        # Korean notation («어간 + -기가 이를 데 없다») passes, while
        # "Although the stems of 닫다 ... end in ㄷ" does not: the Latin run
        # of three or more letters is the tell.
        return bool(_CJK_CHAR.search(text)) and not _LATIN_WORD.search(text)
    if display_lang == "ko":
        # Hangul alone is not enough here the way it is for the Japanese
        # engine (whose data holds no Korean at all): the books' ``notes``
        # are English prose that *quotes* Korean ("오전 and 오후 literally
        # mean ..."), so a Latin word means the line is not Korean.
        return bool(_HANGUL_CHAR.search(text)) and not _LATIN_WORD.search(text)
    # English is the source language of ``notes`` but not of ``formation``,
    # which is Korean by nature; ``en`` shows each field as the book wrote
    # it rather than dropping the 接续 line for want of a translation.
    return True


def _enrichment(rule, field, display_lang):
    """
    One enrichment field (``formation`` / ``formation_notes``) for the
    panel's display language.

    Chinese wording from grammar_ko_enrichment.json wins when present;
    otherwise the field is shown only when its script already matches the
    display language -- the filter that keeps a Korean 接续 line out of a
    Chinese panel while the wording is still being written.
    """
    if display_lang == "zh":
        zh = (rule.get(f"{field}_zh") or "").strip()
        if zh:
            return zh
    text = (rule.get(field) or "").strip()
    return text if _in_display_language(text, display_lang) else ""


def _reference_text(reference, display_lang):
    "The curated example's translation for the display language, or ''."
    if display_lang == "zh":
        return reference.get("chinese") or ""
    if display_lang == "ko":
        # The reference sentence is Korean already, so a Korean panel has
        # nothing to add and hides the block rather than quote it bare.
        return ""
    return reference.get("english") or reference.get("chinese") or ""


def _count_loaded_rules():
    "Total rules (hand-written + data-driven + pattern-derived)."
    return len(_get_all_rules()) + len(_get_pattern_rules())


# ---------------------------------------------------------------------


def analyze_korean(page_text, display_lang="en"):
    """
    Analyze a page of Korean text for grammar points.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    Rules appearing multiple times are merged; examples are deduped.
    Each example's "matches" is a list of {"start", "end"} character
    offsets (within "sentence") of the matched words / constructions, so
    the front-end can highlight exactly those words in the reading text.

    A row merged from the study materials also carries the panel's optional
    blocks, emitted only when the row has them *in the display language*:
      "formation"  the book's 接续 line
      "notes"      the book's 注意点
      "reference"  {"sentence", "text", "matches"} -- the row's own curated
                   example with its translation and the same {start, end}
                   offsets, so the front-end highlights it with the one
                   example renderer it already has.
    """
    # 🔊 and zero-width spaces are display artifacts of the reader, not
    # grammar; strip them so offsets align with the rendered text.
    page_text = page_text.replace("🔊", "").replace("\u200b", "")
    # Imported books are not guaranteed to be NFC: some carry a syllable
    # whose coda was split off as a standalone conjoining jamo
    # ("거세어지" + U+11AF instead of "거세어질", "원론적이" + U+11AB
    # instead of "원론적인") -- a morphological-analyser signature.  Kiwi
    # then sees the ㄹ/ㄴ ending as its own token, so the offset we report
    # starts *inside* the syllable and the panel's <mark> splits the
    # character in half.  Composing first makes the tokens, the offsets
    # and the rendered sentence agree.  (NFD input also matches nothing at
    # all, so this is what makes those books analysable in the first
    # place.)
    page_text = unicodedata.normalize("NFC", page_text)
    sentences = _split_sentences(page_text)
    rules = _panel_rules()
    by_name = {}
    order = []
    for sentence in sentences:
        if not sentence:
            continue
        tokens = _tokens_for(sentence)
        for rule in rules:
            spans = _match_spans(rule, tokens, sentence)
            if not spans:
                continue
            matches = [{"start": s, "end": e} for s, e in spans]
            name = rule["pattern"]
            entry = by_name.get(name)
            if entry is None:
                # Same surface form may carry several senses in the data
                # (e.g. 하고 = "and" / "together with"); merge them into one
                # panel entry whose desc lists the distinct meanings.
                entry = {
                    "key": rule["key"],
                    "name": name,
                    "level": rule["level"],
                    "meanings": [_desc(rule, display_lang)],
                    "examples": [],
                }
                formation = _enrichment(rule, "formation", display_lang)
                if formation:
                    entry["formation"] = formation
                notes = _enrichment(rule, "formation_notes", display_lang)
                if notes:
                    entry["notes"] = notes
                reference = rule.get("reference")
                if reference:
                    text = _reference_text(reference, display_lang)
                    if text:
                        # Distinct names -- rebinding the loop's `sentence`
                        # here once poisoned every later rule's page match
                        # with the reference text and its offsets (the page
                        # examples then quoted sentences not in the text at
                        # all).  The zh backfill made vendored rows take this
                        # path on the Chinese panel too, which is what
                        # finally caught it.
                        ref_sentence = reference["korean"]
                        entry["reference"] = {"sentence": ref_sentence, "text": text}
                        # The same offsets the page examples carry, computed
                        # by this rule's own matcher against its curated
                        # sentence, so the folded block highlights the point
                        # it illustrates instead of quoting it bare.
                        ref_spans = _match_spans(
                            rule, _tokens_for(ref_sentence), ref_sentence
                        )
                        if ref_spans:
                            entry["reference"]["matches"] = [
                                {"start": s, "end": e} for s, e in ref_spans
                            ]
                by_name[name] = entry
                order.append(name)
            else:
                desc_now = _desc(rule, display_lang)
                if desc_now not in entry["meanings"]:
                    entry["meanings"].append(desc_now)
            if not any(e["sentence"] == sentence for e in entry["examples"]):
                entry["examples"].append(
                    {"sentence": sentence, "matches": list(matches)}
                )
    matched = []
    for n in order:
        entry = by_name[n]
        out = {
            "key": entry["key"],
            "name": entry["name"],
            "level": entry["level"],
            "desc": " / ".join(entry["meanings"]),
            "examples": entry["examples"],
        }
        # Optional panel blocks, present only when this row carried them in
        # the display language (see the docstring above).
        out.update(
            {f: entry[f] for f in ("formation", "notes", "reference") if f in entry}
        )
        matched.append(out)
    return matched


# ---- library export --------------------------------------------------
#
# The /grammar page lists the whole corpus at once, where the panel lists
# only what the current page happens to contain.  Mirrors the Japanese
# engine's export (grammar_analysis_ja.library_entries) so both languages
# render through one template.

# The three bands grammar_ko.json uses, easiest first.  Same vocabulary as
# grammar_levels.hideable_grammar_groups.
_LIBRARY_LEVEL_RANK = {"TOPIK 1-2": 0, "TOPIK 3-4": 1, "TOPIK 5-6": 2}

_GOLDEN_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "jlpt_data",
        "grammar_ko_golden.json",
    )
)


def _golden_examples():
    """
    Curated example sentences for the hand-written rules, keyed by rule key.

    Shape ``key -> [{"text", "en", "zh", "ko"}, ...]``, the same store the
    tests re-run through each rule.  Written by hand rather than mined from
    the corpus: the curated rows that fire on these twelve beginner points
    are long and off-topic, so they make poor illustrations.
    """
    return load_golden(_GOLDEN_PATH)


def _example_rows(rule, display_lang, examples=None):
    """
    The rule's examples as display rows.

    The data rules carry {korean, chinese, english} rows; a golden row
    carries the shared {text, en, zh, ko} shape.  Both come back as
    {"text", "tr"} where a translation exists for the display language and
    as a plain sentence where it does not -- an English gloss under a
    Chinese heading would be worse than no gloss, the same rule the
    Japanese export follows.  The Korean panel gets no gloss: the sentence
    already is Korean.
    """
    rows = []
    for row in examples if examples is not None else rule.get("examples") or []:
        if isinstance(row, dict):
            korean = (row.get("korean") or row.get("text") or "").strip()
        else:
            korean = (row or "").strip()
        if not korean:
            continue
        tr = ""
        if isinstance(row, dict) and display_lang != "ko":
            if display_lang == "zh":
                tr = (row.get("chinese") or row.get("zh") or "").strip()
            else:
                tr = (row.get("english") or row.get("en") or "").strip()
        rows.append({"text": korean, "tr": tr} if tr else korean)
    return rows


def _library_entry(rule, display_lang, examples=None):
    "One rule as a library row, display-ready (see the Japanese analogue)."
    entry = {
        "key": rule["key"],
        "name": rule["pattern"],
        "level": rule["level"],
        "desc": _desc(rule, display_lang),
        "examples": _example_rows(rule, display_lang, examples),
        "skipped": False,
    }
    formation = _enrichment(rule, "formation", display_lang)
    if formation:
        entry["formation"] = formation
    notes = _enrichment(rule, "formation_notes", display_lang)
    if notes:
        entry["notes"] = notes
    reference = rule.get("reference")
    if reference:
        text = _reference_text(reference, display_lang)
        if text:
            entry["reference"] = {"japanese": reference["korean"], "text": text}
    return entry


def library_entries(display_lang="en"):
    """
    The whole Korean grammar library, in TOPIK band order (1-2 -> 5-6).

    Built on first use like the rules themselves, so an app start-up with
    no Korean books does not pay for the Kiwi instance.  Every rule the
    matcher can fire is included: unlike the Japanese corpus there is no
    reviewed "reference only" subset.

    The hand-written basics take their examples from the golden set
    (``grammar_ko_golden.json``); the curated rows carry their own.
    """
    golden = _golden_examples()
    rules = [(rule, golden.get(rule["key"])) for rule in _KO_RULES]
    rules += [(rule, None) for rule in _get_data_rules() + _get_pattern_rules()]
    entries = [_library_entry(rule, display_lang, examples) for rule, examples in rules]
    ordered = sorted(
        enumerate(entries),
        key=lambda pair: (_LIBRARY_LEVEL_RANK.get(pair[1]["level"], 9), pair[0]),
    )
    return [entry for _, entry in ordered]
