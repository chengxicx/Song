"""
Japanese grammar point detection (JLPT N5-N1).

Uses SudachiPy to tokenize each sentence with full POS and a
lemma (dictionary form), then a token-aware matcher recognises the
grammar constructions.  Using lemmas means conjugated forms (e.g.
食べている / 食べていました) match the same rule.

Rules come from two places:

* ``_N5_RULES`` below -- a small hand-written, debugged set covering the
  N5 basics (particles, て forms, politeness), with examples from OpenJLPT
  (CC BY-SA 4.0, https://github.com/evanclan/OpenJLPT).
* ``lute/jlpt_data/grammar/n{5..1}.json`` -- the full curated JLPT grammar
  library (595 points), from "japanese-language-data" by Justin Kindrix and
  contributors (CC BY-SA 4.0,
  https://github.com/jkindrix/japanese-language-data), vendored verbatim.
  ``zh.json`` beside them holds this project's Chinese glosses, keyed by the
  upstream entry id.

Matcher specs are *derived* from each entry's descriptive pattern and
validated against that entry's own examples -- see ``_load_level``.
"""

import json
import os
import re

# ---- tokenizer -------------------------------------------------------

_tokenizer = None


def _get_tokenizer():
    "Lazy, process-lifetime Sudachi tokenizer (mode C)."
    global _tokenizer
    if _tokenizer is None:
        from sudachipy import Dictionary

        _tokenizer = Dictionary().create()
    return _tokenizer


def _split_sentences(text):
    """
    Split a page of text into sentences on Japanese punctuation or line breaks.

    Line breaks matter for subtitle/transcript books, whose lines usually
    carry no 。/！ so an entire page would otherwise collapse into one
    pseudo-sentence (making every grammar example the whole page).
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"(?<=[。．！？!?])|\n", text)
    return [p.strip() for p in parts if p and p.strip()]


def _tokens_for(sentence):
    "Tokenize a sentence; return a list of token dicts."
    tok = _get_tokenizer()
    out = []
    for m in tok.tokenize(sentence):
        out.append(
            {
                "surface": m.surface(),
                "lemma": m.dictionary_form(),
                "pos": m.part_of_speech(),
            }
        )
    return out


# ---- matcher ---------------------------------------------------------

def _match_condition(cond, token):
    "True if a single matcher (cond) matches a single token."
    if cond.get("any"):
        return True
    if "surface" in cond and token["surface"] != cond["surface"]:
        return False
    if "surface_in" in cond and token["surface"] not in cond["surface_in"]:
        return False
    if "surface_not" in cond and token["surface"] in cond["surface_not"]:
        return False
    if "lemma" in cond and token["lemma"] not in cond["lemma"]:
        return False
    if "pos1" in cond and token["pos"][0] != cond["pos1"]:
        return False
    if "pos2" in cond and token["pos"][1] != cond["pos2"]:
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
    "Character offset in the sentence text of each token's start."
    offsets = []
    n = 0
    for t in tokens:
        offsets.append(n)
        n += len(t["surface"])
    return offsets


def _on_token_edges(start, end, offsets, token_ends):
    """
    True if a character range starts and ends exactly on token boundaries.

    Sudachi covers the sentence exactly, so token starts/ends are the only
    positions where a literal may begin or finish without cutting a word in
    half.
    """
    boundaries = set(offsets)
    boundaries.update(token_ends)
    return start in boundaries and end in boundaries


def _anchor_spans(before, after, tokens, maxgap):
    """
    Token runs matched by a two-anchor (gapped) spec.

    `before` and `after` are token-condition sequences; the spec matches
    when both appear in order with at most `maxgap` tokens between them,
    e.g. から...にかけて, まんざら...でもない, もう...ました.
    """
    runs = []
    n = len(tokens)
    for start in range(n):
        nbefore = _try_match(before, tokens, 0, start)
        if nbefore <= 0:
            continue
        gap_from = start + nbefore
        for j in range(gap_from, min(gap_from + maxgap + 1, n)):
            nafter = _try_match(after, tokens, 0, j)
            if nafter > 0:
                runs.append((start, j + nafter))
                break
    return runs


def _starts_inside_verb(start, tokens, offsets):
    """
    True if a character offset falls *strictly inside* a verb token.

    Kana-only literals are otherwise free to start mid-token -- くありません
    begins inside the inflected adjective 面白く, which is legitimate -- but a
    verb's tail is never the construction: the って of 困っている is that
    verb's own て-form, not the colloquial quotative 〜って.  Without this the
    〜って entry fires on almost every sentence that uses a て-form.
    """
    for token, offset in zip(tokens, offsets):
        if offset < start < offset + len(token["surface"]):
            return token["pos"][0] == "動詞"
    return False


def _match_spans(rule, tokens, sentence_text):
    """
    (start, end) character ranges of sentence_text matched by this rule.

    Regex specs return their match span; token specs return the span of
    the matched token run.  Exact offsets let the front-end highlight
    precisely the matched words (and never, say, the で inside です).
    """
    spans = []
    offsets = _token_offsets(tokens)
    token_ends = [o + len(t["surface"]) for o, t in zip(offsets, tokens)]
    for spec in rule["patterns"]:
        if spec["type"] == "regex":
            for m in spec["re"].finditer(sentence_text):
                if not m.group(0):
                    continue
                # A kanji-leading literal is anchored to whole tokens: the
                # 上に of 〜上に must not be found inside the word 地上.
                # Kana-leading literals are left alone -- くありません
                # legitimately starts inside the inflected 面白く.
                if spec.get("anchor") and not _on_token_edges(
                    m.start(), m.end(), offsets, token_ends
                ):
                    continue
                # Neither may any literal start inside a 動詞 (see above).
                if _starts_inside_verb(m.start(), tokens, offsets):
                    continue
                spans.append(m.span())
            continue
        if spec["type"] == "anchors":
            runs = _anchor_spans(
                spec["before"], spec["after"], tokens, spec.get("maxgap", 8)
            )
        else:  # tokens
            runs = _token_span_runs(spec["conds"], tokens)
        for start, end in runs:
            if end > start:
                # A pattern may match up to (and including) the final
                # token of the sentence (e.g. a short subtitle line
                # with no trailing 。).  Its character end is then the
                # end of the sentence, not a token start offset.
                span_end = offsets[end] if end < len(offsets) else len(sentence_text)
                spans.append((offsets[start], span_end))
    return spans


def _matches(rule, tokens, sentence_text):
    "True if any pattern-spec of the rule matches this sentence."
    return bool(_match_spans(rule, tokens, sentence_text))


# ---- rules -----------------------------------------------------------

_P = lambda *ks: {k: v for k, v in zip(("pos1", "pos2", "pos3"), ks)}
# short helpers: fixed surface, fixed lemma(set), a plain particle token
_SURF = lambda s: {"surface": s}
# Alternate surfaces of one token: the て-form is て after す/く/る/つ and で
# after む/ぶ/ぬ/ぐ (読んで, 遊んで, 死んで), which Sudachi reports as a
# separate で token -- a rule written against て alone misses half the verbs.
_SURF_IN = lambda *surfaces: {"surface_in": tuple(surfaces)}
_LEMMA = lambda *lemmas: {"lemma": set(lemmas)}
_OPT = lambda cond: {**cond, "optional": True}


def _rule(key, pattern, meaning, examples, patterns, kind="construction"):
    return {
        "key": key,
        "pattern": pattern,
        "level": "N5",
        "meaning": meaning,
        "examples": examples,
        "patterns": patterns,
        "kind": kind,
    }


# Each rule's "patterns" is a list; ANY spec matching marks the sentence.
# "tokens" specs are ordered token conditions (with optional support),
# "regex" specs are plain literal substrings searched in the sentence.
_N5_RULES = [
    _rule(
        "ga_but",
        "〜が（but）",
        "but; however",
        ["この本は高いですが、面白いです。", "日本語が好きですが、難しいです。"],
        [
            {"type": "tokens", "conds": [{"surface": "が", "pos1": "助詞", "pos2": "接続助詞"}]},
        ],
    ),
    _rule(
        "ga_imasu_arimasu",
        "〜がいます / 〜があります",
        "there is/are (animate or inanimate)",
        ["公園に猫がいます。", "机の上に本があります。"],
        [
            {"type": "tokens", "conds": [_SURF("が"), _LEMMA("いる", "ある")]},
        ],
    ),
    _rule(
        "kara_reason",
        "〜から",
        "because; since",
        ["雨が降っているから、出かけません。", "眠いから、早く寝ます。"],
        [
            # から is 格助詞 after a noun (駅から) and 接続助詞 after a predicate
            # (寒いから); only the latter is the reason reading.  The name is
            # deliberately the bare 〜から so it folds together with the data
            # rules for the same form (_merge_same_name) instead of showing the
            # same から twice.
            {"type": "tokens", "conds": [{"surface": "から", "pos1": "助詞", "pos2": "接続助詞"}]},
        ],
    ),
    _rule(
        "suki_kirai",
        "〜が好きです / 〜が嫌いです",
        "like / dislike",
        ["私は猫が好きです。", "彼は野菜が嫌いです。"],
        [
            {"type": "regex", "re": re.compile(r"が好き|が嫌い")},
        ],
    ),
    _rule(
        "tai",
        "〜たい",
        "want to do",
        ["日本に行きたいです。", "水が飲みたいです。"],
        [
            {"type": "tokens", "conds": [_LEMMA("たい")]},
        ],
    ),
    _rule(
        "ta_koto_ga_arimasu",
        "〜たことがあります",
        "have done before; experienced",
        ["日本に行ったことがあります。", "寿司を食べたことがありません。"],
        [
            {"type": "regex", "re": re.compile(r"たこと")},
        ],
    ),
    _rule(
        "de_place_means",
        "〜で（place/means）",
        "marks the place of action or the means of doing something",
        ["学校で勉強します。", "バスで帰ります。"],
        [
            {"type": "tokens", "conds": [{"surface": "で", "pos1": "助詞", "pos2": "格助詞"}]},
        ],
        kind="particle",
    ),
    _rule(
        "te_iru",
        "〜ている",
        "is/am/are doing (progressive) or resultant state",
        ["今、ご飯を食べています。", "電気がついています。"],
        [
            # て *or* で -- and no 動詞 constraint in front, deliberately.
            # Widening て -> て/で is worth +8 pages of 400 (む/ぶ/ぬ/ぐ verbs
            # like 読んでいます, which were silently missed), and requiring a
            # 動詞 right before the て would take back 5 of them: in the passive
            # 言われています / 展示されていました the token before て is the
            # 受身 auxiliary, not the verb.  Costing those back to exclude
            # 元気でいる is a bad trade -- でいる after a な-adjective states a
            # continuing condition and reads correctly as 〜ている.
            {"type": "tokens", "conds": [_SURF_IN("て", "で"), _LEMMA("いる")]},
        ],
    ),
    _rule(
        "te_kudasai",
        "〜てください",
        "please do (for me)",
        ["窓を開けてください。", "ここに名前を書いてください。"],
        [
            {"type": "regex", "re": re.compile(r"てください")},
        ],
    ),
    _rule(
        "deshita",
        "〜でした",
        "was/were (polite past of です)",
        ["昨日は日曜日でした。", "昔、ここは静かでした。"],
        [
            {"type": "regex", "re": re.compile(r"でした")},
        ],
    ),
    _rule(
        "de_wa_arimasen",
        "〜ではありません",
        "is not (polite negative)",
        ["私は学生ではありません。", "今日は寒くありません。"],
        [
            {"type": "regex", "re": re.compile(r"(ではありません|じゃありません)")},
        ],
    ),
    _rule(
        "te_wa_ikemasen",
        "〜てはいけません",
        "must not do",
        ["ここで写真を撮ってはいけません。", "嘘をついてはいけません。"],
        [
            {"type": "regex", "re": re.compile(r"てはいけませ")},
        ],
    ),
    _rule(
        "te_mo_ii_desu",
        "〜てもいいです",
        "may do; it's okay to do",
        ["写真を撮ってもいいですか。", "ここで食べてもいいです。"],
        [
            {"type": "regex", "re": re.compile(r"てもいい")},
        ],
    ),
    _rule(
        "to_and_with",
        "〜と（and/with）",
        "and; with; together with",
        ["友達と映画を見ます。", "春になると、桜が咲きます。"],
        [
            {"type": "tokens", "conds": [{"surface": "と", "pos1": "助詞"}]},
        ],
        kind="particle",
    ),
    _rule(
        "nakute_mo_ii_desu",
        "〜なくてもいいです",
        "don't have to; need not",
        ["明日は早く来なくてもいいです。", "帽子をかぶらなくてもいいです。"],
        [
            {"type": "regex", "re": re.compile(r"なくてもいい")},
        ],
    ),
    _rule(
        "nakereba_narimasen",
        "〜なければなりません / 〜なくてはいけません",
        "must do; have to do",
        ["明日までにレポートを出さなければなりません。", "薬を飲まなくてはいけません。"],
        [
            {"type": "regex", "re": re.compile(r"(なければなりませ|なくてはいけませ|なくてはなりませ)")},
        ],
    ),
    _rule(
        "ni_time_destination",
        "〜に（time/destination）",
        "marks time, destination, or indirect target",
        ["7時に起きます。", "日本に行きます。"],
        [
            {"type": "tokens", "conds": [{"surface": "に", "pos1": "助詞", "pos2": "格助詞"}]},
        ],
        kind="particle",
    ),
    _rule(
        "mashou",
        "〜ましょう",
        "let's do; shall we do",
        ["一緒に行きましょう。", "週末に映画を見ましょう。"],
        [
            {"type": "regex", "re": re.compile(r"ましょう")},
        ],
    ),
    _rule(
        "masen_ka",
        "〜ませんか",
        "shall we?; won't you?",
        ["一緒に食べませんか。", "明日、公園を散歩しませんか。"],
        [
            {"type": "regex", "re": re.compile(r"ませんか")},
        ],
    ),
    _rule(
        "wo_object",
        "〜を（object particle）",
        "marks the direct object of a verb",
        ["本を読みます。", "コーヒーを飲みます。"],
        [
            {"type": "tokens", "conds": [{"surface": "を", "pos1": "助詞", "pos2": "格助詞"}]},
        ],
        kind="particle",
    ),
]


# Surface symbol shown for each particle rule when they are aggregated.
_PARTICLE_SYMBOLS = {
    "de_place_means": "で",
    "ni_time_destination": "に",
    "to_and_with": "と",
    "wo_object": "を",
}


# ---- data-driven rules (N5-N1) ----------------------------------------

# Levels loaded from the curated JSON data files under lute/jlpt_data/grammar/.
# N5 keeps its hand-written, debugged rules in _N5_RULES above; a data rule
# whose matcher duplicates a hand-written one is dropped at load time so the
# same point is never reported twice.
_ALL_LEVELS = ["N5", "N4", "N3", "N2", "N1"]

# Where the grammar JSON lives, relative to this module:
#   lute/read/render/grammar_analysis_ja.py  ->  lute/jlpt_data/grammar/
_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "jlpt_data", "grammar")
)

# Upstream patterns are *descriptive*, not literal: "Noun / V dict + に難くない",
# "Plain form + だろう / でしょう", "Verb → potential form".  The matcher spec is
# therefore derived by pulling the Japanese literal fragments out of that
# description and validating every fragment against the entry's own examples.
_ONLY_JP = re.compile(r"^[\u3040-\u309f\u30a0-\u30ff\u30fc\u3005\u4e00-\u9fff]+$")
_FRAG_SPLIT = re.compile(r"[+/／、，,;；]")
_PARENS = re.compile(r"[（(][^）)]*[）)]")
_KANJI = re.compile(r"[\u4e00-\u9fff\u3005]")
_LATIN = re.compile(r"[A-Za-z\[\]]")

# A fragment with no kanji and at most this many kana is too generic to be a
# rule's own display name (です / ます / これ / ほど ...), so such an entry is
# titled with its whole fragment list rather than its longest piece.  This
# affects *naming only*: which entries get folded into the aggregated row is
# decided by _FUNCTION_WORD_IDS below, never by the shape of the text.
_SHORT_KANA_LIMIT = 3

# Entries that carry no grammar point of their own and are far too frequent to
# give a row each: the copula / polite paradigm (です・ます・ました・だった), the
# demonstratives and the counting question words.  Their sentences are folded
# into one capped "basic forms" entry, the same treatment the hand-written
# particle rules get.
#
# Listed by id on purpose, because the pattern text cannot separate these from
# real grammar points: most JLPT points are *also* short kana tails (のに,
# ながら, ばかり, つつ, ものの, まみれ, だらけ, くらい, さえ ...) and the obvious
# regexes mis-fire in both directions -- "Noun + です" also matches ですら (N1,
# "even"), "それ" also matches それで (N4, "therefore"), "あの" also matches
# ほどの (N1).  Keep this list explicit and reviewed.
_FUNCTION_WORD_IDS = frozenset({
    "desu-polite-copula",
    "i-adj-desu-politeness",
    "na-adjective-nonpast",
    "na-adjective-past",
    "masu-polite-verb",
    "mashita-polite-past-verb",
    "kore-sore-are-demonstratives",
    "kono-sono-ano-dono-attributive",
    "koko-soko-asoko-doko",
    "ikutsu-how-many",
    "ikura-how-much",
})

# Data entries whose form a hand-written rule already reports precisely, and
# whose own derived spec would either fire on a different reading of the form
# or duplicate that rule under a misleading name:
#
#   kara-cause           から is 格助詞 after a noun (駅から, "from") and
#                        接続助詞 after a predicate (寒いから, "because"); a
#                        bare から cannot tell them apart, so deriving it puts
#                        a "because" gloss on every "from".  The hand-written
#                        〜から reports the reason reading by part of speech.
#   imasu-existence-animate
#                        its pattern ("Place に Animate が います") yields the
#                        bare literal います, which also matches the います of
#                        知っています -- and the hand-written
#                        〜がいます / 〜があります reports existence anyway.
#   te-imasu-progressive  〜ています is the same point as the hand-written
#                        〜ている, but its fragments reduce to います, so it
#                        showed up as a second row named "〜います" next to the
#                        real 〜います.  て/で is handled by that rule.
_SUPERSEDED_IDS = frozenset({
    "kara-cause",
    "imasu-existence-animate",
    "te-imasu-progressive",
})

# Data entries that are particle usages: their sentences join the single
# particle row instead of getting a row each, the same treatment the
# hand-written particle rules get.  (The "from" reading of から is a particle.)
_PARTICLE_IDS = frozenset({"particle-kara-from"})

# Widest 〜 gap tolerated between the two anchors of a gapped construction
# (から ... にかけて).  Short windows keep the highlight tight.
_ANCHOR_MAXGAP = 8

# ---- pattern text -> matcher spec -------------------------------------


def _slug(text):
    "ASCII slug for rule keys."
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "base"


def _jp_fragments(text):
    """
    Japanese literal fragments of a descriptive pattern/formation string.

    "Noun / V dict + に難くない"      -> ["に難くない"]
    "Plain form + だろう / でしょう"   -> ["だろう", "でしょう"]
    "Noun + の + 上/下/中"            -> ["上", "下", "中"] is dropped by the
                                         length filter, so such entries are
                                         skipped rather than matched loosely.
    "Verb → potential form"           -> [] (pure conjugation table)

    Fragments keep only kana / kanji runs: Latin placeholders ("Noun",
    "V dict", "[adj]") and parenthetical qualifiers ("(non-past)") are
    dropped, which is what turns a description into something matchable.
    """
    if not text:
        return []
    text = text.replace("〜", "").replace("～", "").replace("＋", "+").replace("／", "/")
    out = []
    for clause in _FRAG_SPLIT.split(text):
        frag = _PARENS.sub("", clause).strip().strip("。．.　 ")
        if len(frag) < 2 or not _ONLY_JP.match(frag):
            continue
        if frag not in out:
            out.append(frag)
    return out


def _is_specific(fragment):
    "True if a fragment is distinctive enough to name a rule on its own."
    return bool(_KANJI.search(fragment)) or len(fragment) > _SHORT_KANA_LIMIT


# Descriptive patterns name what comes *before* the literal fragment
# ("Verb-て + もいい", "Verb stem + 終わる").  Matching the bare fragment
# over-fires -- どうでもいいです is not 〜てもいいです, and the 終わる of
# 昼が終わって is not the compound 〜終わる -- so the description is used to
# require the tokens in front of the fragment.
#
# Each marker is (pattern in the description, required tokens, kanji_only).
# "kanji_only" applies the marker only when the fragment itself starts with a
# kanji: 上に / 内に style fragments collide with the ordinary noun + の
# compound (いすの上に is not the N2 〜上に), while kana-only fragments
# (ので / のに / と思う) have no such homograph and need no guard.
_CONTEXT_MARKERS = [
    (
        re.compile(r"[Vv]erb-?て|V-て|て ?form|て-form"),
        [{"pos1": "動詞"}, {"surface_in": ("て", "で")}],
        False,
    ),
    (
        re.compile(r"[Vv]erb-?た|V-た|た ?form|た-form"),
        [{"pos1": "動詞"}, {"surface_in": ("た", "だ")}],
        False,
    ),
    (re.compile(r"[Vv]erb[- ]?stem|V stem|[Vv]erb-?ない stem"), [{"pos1": "動詞"}], False),
    (re.compile(r"[Vv]erb-?ば form|ば ?form"), [{"surface": "ば"}], False),
    (re.compile(r"[Pp]lain form|V plain|[Vv]erb-?plain"), [{"surface_not": ("の",)}], True),
]

# Descriptions of pure conjugation tables ("Verb → potential form").  Their
# Japanese fragments are endings like れる / られる, which fire on unrelated
# words, so such entries are never derived from the formation field.
_CONJUGATION_TABLE = re.compile(r"→|->")


def _context_cond(description, fragment):
    "Tokens required in front of the fragment, as named by the description."
    kanji_lead = bool(_KANJI.match(fragment or ""))
    for marker, conds, kanji_only in _CONTEXT_MARKERS:
        if kanji_only and not kanji_lead:
            continue
        if marker.search(description):
            return conds
    return None


def _spec_matches(spec, tokens, sentence):
    "Run one candidate spec through the real matcher."
    return bool(_match_spans({"patterns": [spec]}, tokens, sentence))


def _fragment_spec(fragment, joined_examples, example_tokens, prefix=None):
    """
    Match spec for one literal fragment, validated with the real matcher
    against the entry's own examples -- a fragment that never matches them
    cannot be a matcher, and a spec that only matches under looser semantics
    than the matcher applies is useless.

    Candidates are tried most-precise-first:

      * with a `prefix` (see _CONTEXT_MARKERS) the fragment is matched as a
        token sequence so the tokens in front of it can be required too --
        surface-first, because Sudachi lemmatises ましょう to ます and a lemma
        chain would also match a plain ますか question;
      * a kanji-leading literal is anchored to whole tokens, so the 上に of
        〜上に is not found inside the word 地上;
      * the same literal unanchored, for entries whose own examples only ever
        show it inside a bigger token (直す inside 書き直す);
      * finally a lemma chain, which lets inflected forms match
        (ことがある matching ことがあります).
    """
    tokens = _tokens_for(fragment)
    if prefix:
        candidates = [
            {"type": "tokens", "conds": prefix + [{"surface": t["surface"]} for t in tokens]},
            {"type": "tokens", "conds": prefix + [{"lemma": {t["lemma"]}} for t in tokens]},
        ]
    else:
        candidates = [
            {
                "type": "regex",
                "re": re.compile(re.escape(fragment)),
                "anchor": bool(_KANJI.match(fragment)),
            },
            {"type": "regex", "re": re.compile(re.escape(fragment))},
            {"type": "tokens", "conds": [{"lemma": {t["lemma"]}} for t in tokens]},
        ]
    for spec in candidates:
        if _spec_matches(spec, example_tokens, joined_examples):
            return spec
    return None


def _gap_spec(fragments, pattern, joined_examples, example_tokens):
    """
    Spec for a gapped construction such as から ... にかけて.

    Only built when the description really places a placeholder between the
    first and the last fragment ("Noun + から + Noun + にかけて"); the two
    anchors then have to occur in order within a short window.  Returns None
    when the entry is not gapped or the spec does not match its own examples.
    """
    if len(fragments) < 2:
        return None
    first, last = fragments[0], fragments[-1]
    i, j = pattern.find(first), pattern.rfind(last)
    if i < 0 or j <= i or not _LATIN.search(pattern[i + len(first) : j]):
        return None
    spec = {
        "type": "anchors",
        "before": [{"lemma": {t["lemma"]}} for t in _tokens_for(first)],
        "after": [{"lemma": {t["lemma"]}} for t in _tokens_for(last)],
        "maxgap": _ANCHOR_MAXGAP,
    }
    if not _spec_matches(spec, example_tokens, joined_examples):
        return None
    return spec


# Literals already covered by the hand-written N5 rules above (their regex
# specs).  A data rule whose fragment is covered by one of these is redundant
# -- e.g. the data 〜てください entry vs. the hand-written "てください" rule.
_HAND_WRITTEN_LITERALS = [
    spec["re"].pattern
    for rule in _N5_RULES
    for spec in rule["patterns"]
    if spec["type"] == "regex"
]
# Lemma specs of the hand-written rules (e.g. 〜たい), so a data entry for the
# same inflected form is not reported a second time.
_HAND_WRITTEN_LEMMAS = {
    lemma
    for rule in _N5_RULES
    for spec in rule["patterns"]
    if spec["type"] == "tokens" and len(spec["conds"]) == 1
    for lemma in (spec["conds"][0].get("lemma") or set())
}


def _covered_by_hand_written(fragment):
    "True if a hand-written N5 rule already reports this fragment."
    if fragment in _HAND_WRITTEN_LEMMAS:
        return True
    return any(fragment in literal for literal in _HAND_WRITTEN_LITERALS)


def _load_zh():
    """
    Curated Chinese glosses, keyed by data entry id (zh.json next to the
    level files).  Kept beside the vendored data rather than inside it so the
    upstream files stay verbatim.
    """
    path = os.path.join(_DATA_DIR, "zh.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


_ZH_BY_ID = _load_zh()


def _make_data_rule(level, item, idx, skipped, specs=None, shown=None, kind="construction"):
    "Turn one curated JSON entry into a rule dict."
    shown = shown or []
    if len(shown) > 1:
        # Alternatives (だろう / でしょう) are one grammar point: show both.
        name = "〜" + "・".join(shown)
    elif shown:
        name = "〜" + shown[0]
    else:
        # Nothing matchable was derived; the descriptive pattern is kept for
        # the record but the rule never fires.
        name = item.get("pattern") or item.get("id") or f"{level}-{idx}"
    return {
        "key": "ds_" + _slug(item.get("id") or f"{level}-{idx}"),
        "pattern": name,
        "descriptive": item.get("pattern") or "",
        "level": level,
        "meaning": item.get("meaning_en") or "",
        "meaning_zh": _ZH_BY_ID.get(item.get("id") or "", ""),
        "formation": item.get("formation") or "",
        "examples": [e["japanese"] for e in item.get("examples") or []],
        "patterns": [] if skipped else (specs or []),
        "kind": kind,
        "skipped": skipped,
    }


def _load_level(level):
    """
    Load one level's JSON file and turn each entry into a rule dict.

    Match specs are derived from the descriptive pattern text and validated
    against the entry's own examples:

      * a gapped construction (から ... にかけて) becomes a two-anchor spec;
      * otherwise every contentful fragment becomes its own spec -- a literal
        substring regex when it occurs in the examples, else a lemma-based
        token spec so inflected forms still match (ようにする / ようにします);
      * entries that yield nothing matchable -- pure conjugation tables such
        as "Verb → potential form", fragments already covered by the
        hand-written N5 rules, or fragments too vague to match reliably --
        are marked ``skipped`` and never fire, so they cannot cause false
        positives and never appear in the panel.

    The ``kind`` is then taken from the entry itself: only the reviewed
    function-word ids (see _FUNCTION_WORD_IDS) are folded into the aggregated
    row, everything else -- however short and kana-only its pattern is --
    reports on its own, because it is a grammar point the learner is here to
    study (のに, ばかり, ほど, つつ, ものの, まみれ ...).
    """
    path = os.path.join(_DATA_DIR, f"{level.lower()}.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        entries = json.load(fh)
    rules = []
    for idx, item in enumerate(entries):
        if item.get("id") in _SUPERSEDED_IDS:
            # See _SUPERSEDED_IDS: a hand-written rule reports this form
            # already, and this entry's own spec would also fire on the
            # other reading of it.
            rules.append(_make_data_rule(level, item, idx, skipped=True))
            continue
        pattern = item.get("pattern") or ""
        fragments = _jp_fragments(pattern)
        if not fragments and not _CONJUGATION_TABLE.search(pattern):
            # Conjugation tables ("Verb → potential form") describe no
            # literal construction; their formation text only yields bare
            # endings, so they are never matched loosely either.
            fragments = _jp_fragments(item.get("formation") or "")
        joined = "".join(e["japanese"] for e in item.get("examples") or [])
        example_tokens = _tokens_for(joined)

        gap = _gap_spec(fragments, pattern, joined, example_tokens)
        if gap is not None:
            specs = [gap]
            shown = [fragments[0], fragments[-1]]
        else:
            # Prefer distinctive fragments for the title; an entry whose
            # fragments are all short kana is titled with all of them (のに).
            specific = [f for f in fragments if _is_specific(f)]
            specs, shown = [], []
            for fragment in specific or fragments:
                if _covered_by_hand_written(fragment):
                    continue
                # What the description says comes before this fragment
                # ("Verb-て + もいい" -> the fragment needs a て in front).
                prefix = _context_cond(pattern[: pattern.find(fragment)], fragment)
                spec = _fragment_spec(fragment, joined, example_tokens, prefix)
                if spec is not None:
                    specs.append(spec)
                    shown.append(fragment)
            if not specs:
                rules.append(_make_data_rule(level, item, idx, skipped=True))
                continue

        # Which row an entry lands in is a property of the entry, not of how
        # its pattern happens to be spelled: only the reviewed function-word
        # ids are folded together, every other entry reports on its own.
        if item.get("id") in _FUNCTION_WORD_IDS:
            kind = "basic"
        elif item.get("id") in _PARTICLE_IDS:
            kind = "particle"
        else:
            kind = "construction"

        rules.append(
            _make_data_rule(
                level, item, idx, skipped=False, specs=specs, shown=shown, kind=kind
            )
        )
    return rules


def _load_all():
    "Load all data-driven levels, tallying how many rules were auto-skipped."
    rules = []
    for lvl in _ALL_LEVELS:
        rules.extend(_load_level(lvl))
    return rules


_DATA_RULES = _load_all()

_ALL_RULES = _N5_RULES + _DATA_RULES


# Chinese descriptions for grammar analysis, keyed by rule "pattern".
# Authoritative table; rules lacking an entry fall back to the English
# meaning when the display language is Chinese.
_ZH_DESC = {
    # ---- N5 (hand-written rules) ----
    "〜が（but）": "但是；不过；可是",
    "〜がいます / 〜があります": "有……；存在……（生物或非生物）",
    "〜から": "因为；由于",
    "〜が好きです / 〜が嫌いです": "喜欢……／讨厌……",
    "〜たい": "想要做……",
    "〜たことがあります": "曾经做过……；有……的经历",
    "〜で（place/means）": "表示动作的场所或方式、手段",
    "〜ている": "正在……；表示进行或持续的状态",
    "〜てください": "请做……",
    "〜でした": "是……（です的礼貌过去式）",
    "〜ではありません": "不是……（礼貌否定）",
    "〜てはいけません": "不可以……；禁止……",
    "〜てもいいです": "可以……；……也可以",
    "〜と（and/with）": "和……一起；跟……",
    "〜なくてもいいです": "不必……；不需要……",
    "〜なければなりません / 〜なくてはいけません": "必须……；不得不……",
    "〜に（time/destination）": "表示时间、目的地或间接对象",
    "〜ましょう": "……吧；一起做某事吧",
    "〜ませんか": "要不要……？……好吗？",
    "〜を（object particle）": "表示动作的直接宾语",
    # ---- N4 ----
    "〜ことにする": "决定做某事",
    "〜ずに": "没有……就；不……而",
    "〜そうだ（appearance）": "看起来好像……；似乎",
    "〜つもり": "打算……；计划做……",
    "〜てあげる": "为（别人）做某事",
    "〜ていく": "……而去；继续……下去",
    "〜ておく": "事先做好；保持某种状态",
    "〜てくる": "……而来；逐渐开始……",
    "〜てくれる": "（别人）为我做某事",
    "〜てしまう": "完成或不小心做完；因某事发生而遗憾",
    "〜てみる": "试着做某事",
    "〜てもらう": "请（别人）为我做某事；承蒙……",
    "〜に違いない": "一定是；肯定；毫无疑问",
    "〜に決まっている": "一定；必然；毫无疑问",
    "〜みたい": "像……一样；好像（口语）",
    "〜らしい": "好像；听说；有……的特征",
    "〜わけにはいかない": "不能……；不可以……",
    "〜過ぎる": "太……；过度……",
    "〜中": "……中；正在……期间",
    "〜方": "……的方法；……的做法",
    # ---- N3 ----
    "〜ことだ": "应该……；重要的是要……（劝告）",
    "〜ことはない": "不必……；不会发生……",
    "〜そうにする": "装作……；做出……的样子",
    "〜ところ": "……的时候；……的地方；将要……；正在……",
    "〜について": "关于……；就……而言",
    "〜によって": "通过……；由于……；根据……；表示被动态的施动者",
    "〜によると": "根据……；据……",
    "〜に対して": "对……；相对于……；针对……",
    "〜ばかり": "净是……；只……；满是……；有……的倾向",
    "〜ばかりでなく": "不仅……而且……",
    "〜ものだ": "理所当然……；确实……；就应该……",
    "〜ものではない": "不应该……；不该这样……",
    "〜よう": "像……一样；……的样子；为了……",
    "〜ようにする": "设法做到……；注意（努力）做到……",
    "〜ようになる": "变得……；逐渐能够……",
    "〜らしい": "好像；听说；有……的特征（比みたい正式）",
    "〜わけがない": "不可能……；怎么会……",
    "〜わけだ": "难怪……；也就是说……；当然会……",
    "〜わけではない": "并不是……；不一定……",
    "〜一方": "一方面……另一方面……；一边……一边……",
    # ---- N2 ----
    "〜かねない": "有可能……；有……的危险",
    "〜かのようだ": "好像……一样；宛如……",
    "〜ずにはいられない": "不禁……；忍不住……",
    "〜にわたって": "历时……；遍及……；长达……",
    "〜に沿って": "沿着……；按照……；顺应……",
    "〜に応じて": "根据……；按照……；配合……",
    "〜に加えて": "再加上……；除……之外还有……",
    "〜に基づいて": "根据……；基于……；按照……",
    "〜に際して": "在……之际；在……之时",
    "〜に至って": "到……；到了……的地步；直至……",
    "〜に代わって": "代替……；代替……做",
    "〜に伴って": "随着……；伴随……；与……同时",
    "〜に反して": "与……相反；违背……",
    "〜をきっかけに": "以……为契机；趁着……的机会",
    "〜をめぐって": "围绕……；就……",
    "〜を禁じえない": "不禁……；忍不住……",
    "〜を除いて": "除……之外",
    "〜を通して": "通过……；贯穿……；整个……",
    "〜を通じて": "通过……；经由……；在整个……期间",
    "〜を余儀なくされる": "被迫……；不得不……",
    # ---- N1 ----
    "〜かたわら": "在……的同时；一方面……另一方面……",
    "〜がち": "容易……；常常……；动辄……",
    "〜がてら": "顺便……；……的时候顺便……",
    "〜かねる": "难以……；不能……（委婉拒绝）",
    "〜が早いか": "刚一……就……；……的同时",
    "〜ずくめ": "净是……；全是……（多为积极或中性）",
    "〜すら": "连……都；甚至……（强调）",
    "〜たところで": "即使……也……（也无济于事）",
    "〜だらけ": "满是……；净是……（多含贬义）",
    "〜たら最後": "一旦……就完了；一旦……便……",
    "〜た末": "经过……之后；最终……；……的结果",
    "〜てからというもの": "自从……以后；从……起",
    "〜てやまない": "不断……；持续……；衷心……",
    "〜ともなると": "一到了……；一旦……；说到……时",
    "〜と相まって": "与……相互作用；再加上……",
    "〜ならでは": "只有……才……；……特有的；唯有……",
    "〜べく": "为了……；为了做到……（书面语）",
    "〜まい": "不会……吧；不想……；最好不要……",
    "〜ものなら": "如果（能）……就……；假如……",
    "〜ようによっては": "根据……的方法；看……如何处理",
}

_ZH_PARTICLE = "基础 N5 助词检测"
_ZH_BASICS = "基础敬体・指示词・疑问词（です・ます・これ 等），出现极频繁，仅示意"

# Number of example sentences shown for the two aggregated entries, and how
# many distinct symbols (particles / basic forms) are listed in their titles.
# Past the cap the title ends in … rather than pretending to be complete.
_AGGREGATE_EXAMPLE_CAP = 6
_AGGREGATE_SYMBOL_CAP = 6

# Examples kept per constructive grammar point.  A full JLPT library matches
# many points on a normal page, so the panel shows a few anchors per point
# instead of every single instance (which made the pane hundreds of rows).
_CONSTRUCTION_EXAMPLE_CAP = 3

# Ordering of the levels, easiest first.  Used both to label a merged row with
# the level where its form is introduced and by the front-end, which groups the
# panel by level.
_LEVEL_RANK = {"N5": 0, "N4": 1, "N3": 2, "N2": 3, "N1": 4}


def _aggregate_symbols(buckets):
    """
    Symbols of the rules that fired, in rule order and without duplicates.

    An entry that lists alternatives (〜この・あの・どの) contributes only its
    first form: as one "symbol" the whole string reads as three separate
    particles next to the real ones.  A trailing … marks a truncated list.
    """
    symbols = []
    for rule in _ALL_RULES:
        if rule["key"] not in buckets:
            continue
        symbol = _PARTICLE_SYMBOLS.get(rule["key"]) or rule["pattern"].lstrip("〜")
        symbol = symbol.split("・")[0].strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
        if len(symbols) >= _AGGREGATE_SYMBOL_CAP:
            symbols.append("…")
            break
    return symbols


def _aggregate_examples(buckets):
    "First few distinct sentences across the buckets, merging overlapping matches."
    shown = []
    for examples in buckets.values():
        for sentence, spans in examples:
            if len(shown) >= _AGGREGATE_EXAMPLE_CAP:
                return shown
            existing = next((e for e in shown if e["sentence"] == sentence), None)
            if existing is None:
                shown.append({"sentence": sentence, "matches": list(spans)})
            else:
                for span in spans:
                    if span not in existing["matches"]:
                        existing["matches"].append(span)
    return shown


def _merge_same_name(entries):
    """
    Fold entries that would show the same headline into a single row.

    One form can carry several readings -- 〜から is both the starting point
    (駅から) and the reason (寒いから), 〜こそ is plain emphasis and the てこそ
    construction -- and a bare literal cannot tell which one it is.  Two rows
    under the same name show the same sentence with the same highlight twice,
    which reads as a bug; one row listing both glosses reads the way a
    textbook lists them.  The row keeps the easiest level of the group, i.e.
    the level at which the form is first introduced.
    """
    merged = []
    for entry in entries:
        existing = next((e for e in merged if e["name"] == entry["name"]), None)
        if existing is None:
            # Copy the example list too: this row may absorb another entry's
            # examples below, and mutating the caller's list would be a
            # surprise for anyone holding on to the unmerged results.
            merged.append({**entry, "examples": list(entry["examples"])})
            continue
        if entry["desc"] and entry["desc"] not in existing["desc"]:
            existing["desc"] = (existing["desc"] + "；" + entry["desc"]).strip("；")
        if _LEVEL_RANK.get(entry["level"], 9) < _LEVEL_RANK.get(existing["level"], 9):
            existing["level"] = entry["level"]
        for ex in entry["examples"]:
            if len(existing["examples"]) >= _CONSTRUCTION_EXAMPLE_CAP:
                break
            if not any(e["sentence"] == ex["sentence"] for e in existing["examples"]):
                existing["examples"].append(ex)
    return merged


def _desc(rule, display_lang):
    "Description for a rule in the requested display language."
    if display_lang == "zh":
        # Curated Chinese gloss shipped with the data beats both the legacy
        # hand-written table and the English meaning.
        if rule.get("meaning_zh"):
            return rule["meaning_zh"]
        return _ZH_DESC.get(rule["pattern"], rule["meaning"])
    return rule["meaning"]


def analyze_japanese(page_text, display_lang="en"):
    """
    Analyze a page of Japanese text for grammar points.

    Constructive grammar rules become individual entries; basic particle
    rules are folded into one trailing "Particles" entry so the panel
    isn't dominated by を/に/で/と hits.

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
    matched = []
    particle_examples = {}  # particle key -> [(sentence, [matches])]
    basic_examples = {}  # data rule key -> [(sentence, [matches])] for basics
    for sentence in sentences:
        if not sentence:
            continue
        tokens = _tokens_for(sentence)
        for rule in _ALL_RULES:
            spans = _match_spans(rule, tokens, sentence)
            if not spans:
                continue
            matches = [{"start": s, "end": e} for s, e in spans]
            kind = rule.get("kind")
            if kind in ("particle", "basic"):
                # Both are far too frequent to list one entry per hit; the
                # examples are collected per rule and folded into a single
                # capped aggregate entry below.
                bucket = particle_examples if kind == "particle" else basic_examples
                ex = bucket.setdefault(rule["key"], [])
                if not any(s == sentence for s, _ in ex):
                    ex.append((sentence, matches))
                continue
            entry = next((e for e in matched if e["key"] == rule["key"]), None)
            if entry is None:
                entry = {
                    "key": rule["key"],
                    "name": rule["pattern"],
                    "level": rule["level"],
                    "desc": _desc(rule, display_lang),
                    "examples": [],
                }
                matched.append(entry)
            if not any(e["sentence"] == sentence for e in entry["examples"]):
                if len(entry["examples"]) < _CONSTRUCTION_EXAMPLE_CAP:
                    entry["examples"].append({"sentence": sentence, "matches": matches})

    if basic_examples:
        matched.append(
            {
                "key": "basic_forms",
                "name": "Basic forms: " + "・".join(_aggregate_symbols(basic_examples)),
                "level": "N5",
                "desc": _ZH_BASICS if display_lang == "zh" else "Copula, demonstratives and question words detected",
                "examples": _aggregate_examples(basic_examples),
            }
        )

    if particle_examples:
        matched.append(
            {
                "key": "basic_particles",
                "name": "Particles: " + "・".join(_aggregate_symbols(particle_examples)),
                "level": "N5",
                "desc": _ZH_PARTICLE if display_lang == "zh" else "Basic N5 particles detected",
                "examples": _aggregate_examples(particle_examples),
            }
        )
    return _merge_same_name(matched)