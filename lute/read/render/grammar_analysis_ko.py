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
    if "surface" in cond and token["surface"] != cond["surface"]:
        return False
    if "lemma" in cond and token["lemma"] not in cond["lemma"]:
        return False
    if "pos" in cond and not token["pos"].startswith(cond["pos"]):
        # Prefix match: "V" matches VV/VA/VX/VCP/VV-I (irregular), "J" all
        # particles, "EC" only that connective ending, etc.
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
    "Character offset in the sentence of each token's start."
    offsets = []
    n = 0
    for t in tokens:
        offsets.append(n)
        n += len(t["surface"])
    return offsets


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
                spans.append((offsets[start], span_end))
    return spans


def _match_spans(rule, tokens, sentence_text):
    "All character spans of sentence_text matched across the rule's specs."
    spans = []
    for spec in rule["patterns"]:
        spans.extend(_spec_spans(spec, tokens, sentence_text))
    return spans


# ---- rule helpers ----------------------------------------------------

_SURF = lambda s: {"surface": s}
_LEMMA = lambda *lemmas: {"lemma": set(lemmas)}
_POS = lambda p: {"pos": p}


def _rule(key, name, meaning, patterns, level="TOPIK 1-2", zh="", kind="construction"):
    return {
        "key": key,
        "pattern": name,
        "level": level,
        "meaning": meaning,
        "zh": zh,
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
    ),
    _rule(
        "ko_su_issda",
        "-(으)ㄹ 수 있다/없다",
        "can / cannot do; be possible / impossible",
        [{"type": "tokens", "conds": [_POS("ETM"), _SURF("수"), _LEMMA("있다", "없다")]}],
        zh="能够/不能做……；有可能",
    ),
    _rule(
        "ko_go_sipda",
        "-고 싶다",
        "want to do",
        [{"type": "tokens", "conds": [{"surface": "고", "pos": "EC"}, _LEMMA("싶다")]}],
        zh="想做……；想要……",
    ),
    _rule(
        "ko_ji_anhda",
        "-지 않다",
        "negative: do not",
        [{"type": "tokens", "conds": [{"surface": "지", "pos": "EC"}, _LEMMA("않다")]}],
        zh="不……；否定",
    ),
    _rule(
        "ko_aeo_seo",
        "-아/어서",
        "because of; and so (reason / sequential)",
        [{"type": "tokens", "conds": [{"lemma": {"아서", "어서"}, "pos": "EC"}]}],
        zh="因为……；……所以……",
    ),
    _rule(
        "ko_eunikka",
        "-(으)니까",
        "because; since (reason)",
        [{"type": "tokens", "conds": [{"surface": "니까", "pos": "EC"}]}],
        zh="因为……；由于……",
    ),
    _rule(
        "ko_geo_future",
        "-(으)ㄹ 거예요",
        "will / going to do (future)",
        [{"type": "tokens", "conds": [_POS("ETM"), _SURF("거")]}],
        zh="将要……；打算……（将来）",
    ),
    _rule(
        "ko_aeo_juda",
        "-아/어 주다",
        "do (something) for someone",
        [{"type": "tokens", "conds": [{"surface": "어", "pos": "EC"}, _LEMMA("주다")]}],
        zh="为某人做……；帮……做",
    ),
    _rule(
        "ko_gi_jeone",
        "-기 전에",
        "before doing",
        [{"type": "tokens", "conds": [{"surface": "기", "pos": "ETN"}, {"surface": "전", "pos": "NNG"}]}],
        zh="在……之前",
    ),
    _rule(
        "ko_jung_ida",
        "-는 중이다",
        "in the middle of doing",
        [{"type": "tokens", "conds": [_SURF("중"), _POS("VCP")]}],
        zh="正在……当中；……中",
    ),
    _rule(
        "ko_copula_polite",
        "-입니다 / -이에요",
        "polite copula: is / am / are",
        [{"type": "regex", "re": re.compile(r"입니다|이에요|예요")}],
        zh="是……（礼貌体）",
    ),
    _rule(
        "ko_eumyon",
        "-(으)면",
        "if / when (conditional)",
        [{"type": "tokens", "conds": [{"lemma": {"면", "으면"}, "pos": "EC"}]}],
        zh="如果……；当……时",
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
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "jlpt_data", "grammar_ko.json"
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
            )
        )
    return rules


_DATA_RULES = _load_data_rules()

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
    if display_lang != "zh":
        return rule["meaning"]
    if rule["zh"]:
        return rule["zh"]
    return _KO_ZH.get(rule["pattern"]) or rule["meaning"]


def _count_loaded_rules():
    "Total rules (hand-written + data-driven), for diagnostics."
    return len(_ALL_RULES)


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
    by_name = {}
    order = []
    for sentence in sentences:
        if not sentence:
            continue
        tokens = _tokens_for(sentence)
        for rule in _ALL_RULES:
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
                entry["examples"].append({"sentence": sentence, "matches": list(matches)})
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