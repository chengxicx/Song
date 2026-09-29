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
(see _load_pattern_rules).

Only text (meaning / examples) is consumed; the audio fields are ignored
(ElevenLabs commercial license).
"""

import json
import os
import re

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


def _spec_spans(spec, tokens, sentence_text):
    "Character (start, end) ranges of sentence_text matched by one spec."
    spans = []
    if spec["type"] == "regex":
        for m in spec["re"].finditer(sentence_text):
            if m.group(0):
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
    key, name, meaning, patterns, level="TOPIK 1-2", zh="", ko="", kind="construction"
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
        [{"type": "tokens", "conds": [_POS("ETM"), _SURF("수"), _LEMMA("있다", "없다")]}],
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
        [{"type": "tokens", "conds": [{"surface": "니까", "pos": "EC"}]}],
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


def _load_data_rules():
    if not os.path.exists(_DATA_PATH):
        return []
    with open(_DATA_PATH, encoding="utf-8") as fh:
        entries = json.load(fh)
    rules = []
    for item in entries:
        name = item.get("name") or ""
        if _norm_name(name) in _HANDWRITTEN_NORMS:
            continue  # already covered precisely by a hand-written rule
        # Single-Hangul foci are too noisy as regexes; drop them.  The
        # rest are anchored at a so-called "word end" (followed by space /
        # end of string), since Korean grammatical morphemes attach to the
        # end of an 어절.  This avoids matching e.g. the 에 inside 이에요.
        foci = sorted(
            ((f + r"(?!\S)") for f in (item.get("focus") or []) if len(f) >= 2),
            key=len,
            reverse=True,
        )
        if not foci:
            continue
        alternation = re.compile("|".join(foci))
        rules.append(
            _rule(
                item.get("key") or name,
                name,
                (item.get("meaning") or "").strip() or name,
                [{"type": "regex", "re": alternation}],
                level=item.get("level")
                or _LEVEL_BY_TYPE.get(item.get("type", ""), "TOPIK 3-4"),
                zh=item.get("zh") or "",
                ko=item.get("ko") or "",
            )
        )
    return rules


_DATA_RULES = _load_data_rules()

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
    "해서": [{"lemma": {"하", "하다"}}, {"pos": "EC", "surface": ("어서", "아서", "여서")}],
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
    "그래요": [{"lemma": {"그렇", "그렇다"}}, {"pos": "EF", "surface": ("어요", "아요", "여요")}],
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


_ALL_RULES = _KO_RULES + _DATA_RULES


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


def _count_loaded_rules():
    "Total rules (hand-written + data-driven + pattern-derived)."
    return len(_ALL_RULES) + len(_get_pattern_rules())


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
    """
    # 🔊 and zero-width spaces are display artifacts of the reader, not
    # grammar; strip them so offsets align with the rendered text.
    page_text = page_text.replace("🔊", "").replace("\u200b", "")
    sentences = _split_sentences(page_text)
    rules = _ALL_RULES + _get_pattern_rules()
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
        matched.append(
            {
                "key": entry["key"],
                "name": entry["name"],
                "level": entry["level"],
                "desc": " / ".join(entry["meanings"]),
                "examples": entry["examples"],
            }
        )
    return matched
