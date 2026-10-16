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
  upstream entry id, and ``zh_enrichment.json`` the Chinese wording for the
  rows' 接续 / 注意点 / 参考例句译文 (the vendored files stay verbatim).

Matcher specs are *derived* from each entry's descriptive pattern and
validated against that entry's own examples -- see ``_load_level``.
"""

import json
import os
import re
import threading

# ---- tokenizer -------------------------------------------------------

# Tokenizer instances are NOT shareable.  sudachipy's Tokenizer wraps a
# mutable Rust object, and the Python binding borrows it for the whole of
# every tokenize() call, so two threads tokenizing at once raise
# "RuntimeError: Already borrowed".  Lute serves requests from a thread
# pool (waitress, 12 threads by default -- see lute/main.py), and this
# module is reached straight from the grammar route, so a shared instance
# meant that two readers on the same Japanese page -- or one reader
# double-clicking the panel -- got a 500.
#
# The Dictionary *is* shareable and is the expensive part to build, so
# load exactly one per process and give each thread its own cheap
# Tokenizer.  This mirrors lute/parse/sudachi_parser.py, which carries
# the same contract for the reading page.
_dictionary = None
_dictionary_lock = threading.Lock()
_thread_local = threading.local()


def _get_tokenizer():
    "Lazy, process-lifetime Sudachi tokenizer (mode C), one per thread."
    tokenizer = getattr(_thread_local, "tokenizer", None)
    if tokenizer is not None:
        return tokenizer
    global _dictionary  # pylint: disable=global-statement
    if _dictionary is None:
        # Locked, but only around the expensive load: the dictionary must
        # not be built once per thread, and must be built exactly once
        # even if several requests arrive together.  The import stays
        # inside so a base install without the sudachi extra still raises
        # ImportError here, which is how _load_all() below detects it.
        with _dictionary_lock:
            if _dictionary is None:
                # pylint: disable-next=import-outside-toplevel
                from sudachipy import Dictionary

                _dictionary = Dictionary()
    tokenizer = _dictionary.create()
    _thread_local.tokenizer = tokenizer
    return tokenizer


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
    token_end_set = set(token_ends)
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
                # A literal may not *end* inside a token either, whatever it
                # starts with: an end that is not a token boundary means the
                # literal is a prefix of a longer word, not the construction.
                # もので (the short form of 〜ものだから) is a literal prefix of
                # ものです (もの + です), so 〜ものだから was reported for
                # 写真は祖父母が若いときのものです。; the same hole gave ので
                # (〜ので) and もの (〜もの) a free hit on that sentence.
                # Starting inside a token stays allowed -- see above.
                if m.end() not in token_end_set:
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
        # A spec may name a token that must not sit immediately in front of
        # the run (see _NOT_AFTER).  A run at the start of the sentence has
        # nothing in front of it, so nothing excludes it.  Anchors specs take
        # the same guard: the run starts at the first anchor, so the token in
        # front of it is the anchor's own left context (しか preceded by a
        # verb is 〜しかない "no choice", not 〜しか "only").
        if spec.get("not_after") is not None:
            runs = [
                (s, e)
                for (s, e) in runs
                if s == 0 or not _match_condition(spec["not_after"], tokens[s - 1])
            ]
        # Mirror image: a token that must not sit immediately *after* the
        # run.  〜てください is the request; 〜てくださいました / 〜てくださ
        # います is the honorific "did for me", and the token that follows
        # the bare ください is what tells the two apart -- the tokens
        # matcher cannot look ahead any other way.
        #
        # Which token that is depends on what the run is.  A token spec's run
        # *is* the construction, so the guard is the token behind it.  An
        # anchors spec's run opens with the `before` sequence and closes at
        # the far anchor, so what matters is the first token of the gap --
        # あまり + に is the degree reading ("too much", its own row), while
        # あまり + a predicate is the "not very" the 〜あまり〜ない row wants.
        if spec.get("not_next") is not None:
            head = len(spec["before"]) if spec["type"] == "anchors" else None
            guarded = []
            for start, end in runs:
                index = end if head is None else start + head
                if index < len(tokens) and _match_condition(
                    spec["not_next"], tokens[index]
                ):
                    continue
                guarded.append((start, end))
            runs = guarded
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
        "meaning_ko": "",
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
            {
                "type": "tokens",
                "conds": [{"surface": "が", "pos1": "助詞", "pos2": "接続助詞"}],
            },
        ],
    ),
    _rule(
        "ga_imasu_arimasu",
        "〜がいます / 〜があります",
        "there is/are (animate or inanimate)",
        ["公園に猫がいます。", "机の上に本があります。"],
        [
            # not_after こと: in ことがあります the が+ある run is the polite
            # form of the experience construction 〜ことがある (its own row),
            # not the existence reading this rule reports.
            {
                "type": "tokens",
                "conds": [_SURF("が"), _LEMMA("いる", "ある")],
                "not_after": {"lemma": {"こと"}},
            },
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
            {
                "type": "tokens",
                "conds": [{"surface": "から", "pos1": "助詞", "pos2": "接続助詞"}],
            },
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
        "de_place_means",
        "〜で（place/means）",
        "marks the place of action or the means of doing something",
        ["学校で勉強します。", "バスで帰ります。"],
        [
            {
                "type": "tokens",
                "conds": [{"surface": "で", "pos1": "助詞", "pos2": "格助詞"}],
            },
        ],
        kind="particle",
    ),
    _rule(
        "te_iru",
        "〜ている",
        "is/am/are doing (progressive) or resultant state",
        ["今、ご飯を食べています。", "電気がついています。"],
        [
            # て *or* で, but only the connective form (助詞,接続助詞).
            # Widening て -> て/で is worth +8 pages of 400 (む/ぶ/ぬ/ぐ verbs
            # like 読んでいます, which were silently missed).  Requiring a
            # 動詞 right before the て would take back 5 of them: in the
            # passive 言われています / 展示されていました the token before て
            # is the 受身 auxiliary, not the verb -- so the earlier review
            # kept the な-adjective 元気でいる in this row's claims as the
            # lesser cost.  The part of speech does the same job for free:
            # the copula's て-form (元気**で**いる, lemma だ) is 助動詞, the
            # verb's is 助詞,接続助詞, so the passive stays and the copula
            # moves to the 〜でいる row that is about exactly that state.
            {
                "type": "tokens",
                "conds": [
                    {"surface_in": ("て", "で"), "pos2": "接続助詞"},
                    _LEMMA("いる"),
                ],
            },
        ],
    ),
    _rule(
        "te_kudasai",
        "〜てください",
        "please do (for me)",
        ["窓を開けてください。", "ここに名前を書いてください。"],
        [
            # 動詞 + て/で + ください.  The で form is the same request -- 読んで,
            # 遊んで, 死んで -- and a bare て-regex missed all of them, which is
            # how 読んでください ended up reported as 〜ないでください.
            # not_next: a ます tail after the bare ください is the honorific
            # (〜てくださいました / 〜てくださいます, "kindly did for me"), not
            # the request -- V-て + くださる is the くださる row's reading, and
            # the request row must not claim it too.  てくださいませんか keeps
            # its ませ token and stays a request.
            {
                "type": "tokens",
                "conds": [_P("動詞"), _SURF_IN("て", "で"), _SURF("ください")],
                "not_next": {"surface_in": ("まし", "ます")},
            },
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
            {
                "type": "tokens",
                "conds": [{"surface": "に", "pos1": "助詞", "pos2": "格助詞"}],
            },
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
            {
                "type": "tokens",
                "conds": [{"surface": "を", "pos1": "助詞", "pos2": "格助詞"}],
            },
        ],
        kind="particle",
    ),
]

# ---- combined constructions (hand-written, N1/N2) -----------------------
#
# Merged material entries whose *shape* is two occurrences of the same
# fragment (AつBつ, AといいBといい, V意志形にも同一Vない形, ...) derive no
# matcher: the fragmenting logic treats each occurrence as one candidate and
# cannot express "the same piece twice", so the entries end up skipped
# (patterns == []) and silent.  These hand-written rules carry the matching
# for them.  Every spec below was written against a Sudachi tokenization
# probe of its own example sentence and is pinned by tests that fire each
# rule on its own examples and probe near-miss sentences.
#
# Tokenizations the specs rely on (Sudachi):
#   抜きつ抜かれつ  -> 抜き(動詞) つ(助詞) 抜か(動詞) れ(助動詞) つ(助詞)
#   電話するなり...するなり -> する(動詞) なり(助詞,接続助詞) x2
#   言おうにも言えない -> 言おう(動詞) に(格助詞) も(係助詞) 言え(動詞) ない(助動詞)
#   行こうが行くまいが -> 行こう(動詞) が(接続助詞) 行く(動詞) まい(助動詞) が


def _crule(key, level, pattern, meaning, examples, patterns):
    "A combined-construction rule: like _rule but with an explicit level."
    return {
        "key": key,
        "pattern": pattern,
        "level": level,
        "meaning": meaning,
        "meaning_ko": "",
        "examples": examples,
        "patterns": patterns,
        "kind": "construction",
    }


_CONSTRUCTION_RULES = [
    _crule(
        "a_tsu_b_tsu",
        "N1",
        "〜つ〜つ",
        "alternating actions (A-ing and B-ing) / one after the other",
        ["両者は抜きつ抜かれつの死闘を繰り広げた。"],
        [
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "動詞"},
                    _SURF("つ"),
                    {"pos1": "動詞"},
                    _OPT({"pos1": "助動詞"}),
                    _SURF("つ"),
                ],
            },
        ],
    ),
    _crule(
        "to_ii_to_ii",
        "N1",
        "〜といい〜といい / 〜といわず〜といわず",
        "whether X or Y; in both X and Y (no exception anywhere)",
        [
            "昼といわず夜といわず、働き続けた。",
            "北海道は夏といい冬といい、観光にいい。",
        ],
        [
            {"type": "regex", "re": re.compile(r"といわず[^。、]{0,10}といわず")},
            {"type": "regex", "re": re.compile(r"といい[^。、]{0,10}といい")},
        ],
    ),
    _crule(
        "a_nari_b_nari",
        "N1",
        "〜なり〜なり",
        "do X or Y -- whichever (list of alternatives to choose from)",
        ["電話するなりメールするなり、連絡してください。"],
        [
            {
                # The two halves are not contiguous tokens: サ変 verbs split
                # (電話/する) and a noun may sit between the halves, so this
                # is a two-anchor spec, not a single token run.
                "type": "anchors",
                "before": [
                    {"pos1": "動詞"},
                    {"surface": "なり", "pos2": "接続助詞"},
                ],
                "after": [
                    {"pos1": "動詞"},
                    {"surface": "なり", "pos2": "接続助詞"},
                ],
                "maxgap": 3,
            },
        ],
    ),
    _crule(
        "mo_ba_mo",
        "N2",
        "〜も〜ば〜も",
        "both X and Y exist (parallel states: XもあればYもある)",
        ["人生には楽もあれば苦もある。"],
        [
            {
                # Same non-contiguity: 楽もあれば苦もある has the noun 苦
                # between ば and the second も.
                "type": "anchors",
                "before": [_SURF("も"), {"pos1": "動詞"}, _SURF("ば")],
                "after": [_SURF("も"), {"pos1": "動詞"}],
                "maxgap": 2,
            },
        ],
    ),
    _crule(
        "ba_hodo",
        "N2",
        "〜ば〜ほど",
        "the more X, the more Y",
        ["聞けば聞くほど分からなくなる。", "早ければ早いほどいい。"],
        [
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "動詞"},
                    _SURF("ば"),
                    {"pos1": "動詞"},
                    _SURF("ほど"),
                ],
            },
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "形容詞"},
                    _SURF("ば"),
                    {"pos1": "形容詞"},
                    _SURF("ほど"),
                ],
            },
        ],
    ),
    _crule(
        "volitional_ni_mo_nai",
        "N1",
        "〜ようにも〜ない",
        "cannot do X even if one wanted to (V volitional + にも + same V negative)",
        ["言おうにも言えない。", "行こうにも行けない。"],
        [
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "動詞"},
                    _SURF("に"),
                    _SURF("も"),
                    {"pos1": "動詞"},
                    _SURF("ない"),
                ],
            },
        ],
    ),
    _crule(
        "volitional_mai",
        "N1",
        "〜（よ）うが〜まいが / 〜（よ）うと〜まいと",
        "whether one does X or not (V volitional + が/と + same V + まい + が/と)",
        ["行こうが行くまいが、結果は同じだ。"],
        [
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "動詞"},
                    _SURF_IN("が", "と"),
                    {"pos1": "動詞"},
                    _SURF("まい"),
                    _SURF_IN("が", "と"),
                ],
            },
        ],
    ),
    _crule(
        "nara_tomo_kaku",
        "N1",
        "〜ならともかく",
        "X might be excusable, but (X is one thing, yet Y is going too far)",
        ["値段ならともかく、味は正直期待できない。"],
        [
            {"type": "regex", "re": re.compile(r"ならともかく")},
        ],
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

# Public alias.  The language settings page offers these levels as
# "hide in the panel" choices, and that list must not drift from the
# levels this engine actually emits, so it is read from here rather
# than duplicated.
ALL_LEVELS = _ALL_LEVELS

# Where the grammar JSON lives, relative to this module:
#   lute/read/render/grammar_analysis_ja.py  ->  lute/jlpt_data/grammar/
_DATA_DIR = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "jlpt_data", "grammar"
    )
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
# affects *naming only*, and that is an invariant worth keeping: every fragment
# still becomes a matcher, because a pattern that spells out alternatives means
# all of them -- "い-adjective stem + くない / くありません" is two forms, and
# letting the longer one hide the shorter one silently lost くない, だろう,
# けど, どこ and a dozen more.  Which entries get folded into the aggregated row
# is likewise decided by _FUNCTION_WORD_IDS below, never by the shape of the
# text.
_SHORT_KANA_LIMIT = 3

# Entries that carry no grammar point of their own and are far too frequent to
# give a row each: the copula / polite paradigm (です・ます・ました・だった), the
# こそあど series and the counting question words.  Their sentences are folded
# into one capped "basic forms" entry, the same treatment the hand-written
# particle rules get.
#
# Listed by id on purpose, because the pattern text cannot separate these from
# real grammar points: most JLPT points are *also* short kana tails (のに,
# ながら, ばかり, つつ, ものの, まみれ, だらけ, くらい, さえ ...) and the obvious
# regexes mis-fire in both directions -- "Noun + です" also matches ですら (N1,
# "even"), "それ" also matches それで (N4, "therefore"), "あの" also matches
# ほどの (N1).  Keep this list explicit and reviewed.
#
# question-words-basic (何 / 誰 / どこ / いつ / どう / どうして) joins them: it is
# the same series as koko-soko-asoko-doko above, which どこ already appears in,
# and once the derivation stopped dropping a pattern's shorter fragments it
# reached 33% of pages -- and a question word is what the word popup answers.
_FUNCTION_WORD_IDS = frozenset(
    {
        "desu-polite-copula",
        "i-adj-desu-politeness",
        "na-adjective-nonpast",
        "na-adjective-past",
        "masu-polite-verb",
        "mashita-polite-past-verb",
        "kore-sore-are-demonstratives",
        "kono-sono-ano-dono-attributive",
        "koko-soko-asoko-doko",
        "question-words-basic",
        "ikutsu-how-many",
        "ikura-how-much",
    }
)

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
#   arimasu-existence-inanimate
#                        the mirror of imasu-existence-animate: its pattern
#                        yields the bare literal あります, which fires on the
#                        existence verb wherever it appears, and the same
#                        hand-written rule reports it.
_SUPERSEDED_IDS = frozenset(
    {
        "kara-cause",
        "imasu-existence-animate",
        "arimasu-existence-inanimate",
        "te-imasu-progressive",
    }
)

# Data entries that are particle usages: their sentences join the single
# particle row instead of getting a row each, the same treatment the
# hand-written particle rules get.  (The "from" reading of から is a particle.)
_PARTICLE_IDS = frozenset({"particle-kara-from"})

# Data entries that are vocabulary, not grammar.  The panel answers "which
# constructions are in this sentence"; Lute answers "what does 時間 mean" from
# the word popup, so a row reading "〜時間 = ……小时" costs a row and teaches no
# grammar.  These entries are dropped rather than folded into a third
# aggregated row, which would repeat the "Basics:" mistake -- a bucket is only
# useful when the things in it are worth studying together.
#
# The list is *derived*, not guessed: scripts/screen_grammar_library.py looks
# for entries whose descriptive pattern carries no conjugation slot
# ("Plain form", "Verb-て form", "ます-stem") and whose spec then degenerated to
# a bare literal starting with a content word.  That screen is high-recall and
# over-flags -- こと / つもり / はず / ところ / まま are also 名詞 and are real
# points, まみれ / ぐるみ / 向け / 抜きで are 接尾辞 -- so its output was
# reviewed entry by entry before landing here.  Only the groups below survived:
# a row that restates the dictionary entry, and nothing more.
#
# Deliberately *not* here, though the screen flags them:
#   ga-wakaru / ga-dekiru / ga-hoshii / ga-kikoeru-mieru
#       "Noun + が + predicate" entries.  Their rows carry the が-marking,
#       which the word popup does not.
#   iwaba / nani-se / sorede / ...  (all under 4% of pages)
#       N1/N2 discourse adverbs and conjunctions.  Lexical, but they are
#       listed as grammar and cheap enough on the panel not to matter.
_VOCAB_IDS = frozenset(
    {
        # number + counter: the counter's own reading is the point, and the word
        # popup already shows it
        "counter-tsu",
        "counter-people-nin",
        "counter-ji-oclock",
        "counter-fun-minute",
        "counter-sai-age",
        "counter-en-money",
        "counter-hon-long",
        "counter-mai-flat",
        "jikan-time-duration",
        # calendar / time words
        "mai-every-prefix",
        "nanji-what-time",
        "nanyoubi-day-of-week",
        # lexical adverbs: the row repeats the word's gloss
        "issho-ni-together",
        "ichiban-superlative",
        # ---- merged N1-N3 material entries reviewed 2026-09-29 ----
        # onomatopoeia / bare adverbs: the row restates the word popup
        "zuto",  # ずっと
        "moto",  # もっと
        "guto",  # ぐっと
        "jito",  # じっと
        "soto",  # そっと
        "hoto",  # ほっと
        "hato",  # はっと
        "zoto",  # ぞっと
        "zoto-bdfe97",  # ぞっと（毛骨悚然）
        "zato",  # ざっと
        "yato",  # やっと
        "mada",  # まだ
        "tatoeba",  # たとえば
        "hajimete",  # はじめて
        "atari",  # あたり（大致）
        "zukitozehi",  # 必ず/きっと/ぜひ
        # lexical words, not constructions
        "chigaiigaii",  # 気持ちがいい/気分がいい
        "desu",  # 予定です
        # standalone verbs whose suffix readings jkindrix already carries
        # (hajimeru-auxiliary / tsuzukeru-continue / dashi-suddenly-start);
        # a bare だす/はじめる spec also fires on the standalone verb (本を出す)
        "hajimeru",
        "tsuzukeru",
        "dasu",
        # duplicates of existing jkindrix rows for the same construction
        "soudesu",  # そうです（传闻） -> ds_souda
        "soudesu-6efe6b",  # そうです（样态） -> ds_souda
        "tsumorida",  # つもりだ -> tsumori-intention
        "hazudesu",  # はずです -> hazu-expected
        "naidenakute",  # ないで/なくて -> nai-de (hand + slot spec)
        "tokorodasuru",  # ところだ（する） -> ta-tokoro / tokorodesu
        "basoremadedanarasoremadedatarasoremadedatomosoremadeda",
        #                    -> ba-sore-made already matches every variant on
        #                    the bare literal それまでだ
        # ---- merged N4/N5 book entries reviewed 2026-09-29 ----
        # person/time suffixes: they fire on every 〜さん/〜たち occurrence
        # (word formation, not a sentence construction).  さん/ちゃん do not
        # trip the screen's vocab-shape heuristic, so they live here (same
        # skip semantics) rather than in _VOCAB_IDS, where the screen
        # invariant would reject them.
        "tachi",
        "gata",
        "sugi",
        # ---- merged N1/N2/N3 book entries reviewed 2026-09-30 ----
        # honorific verbs: 召す / 存じ上げる are the keigo forms of 着る・食べる
        # and 知る, i.e. lexicon entries.  The word popup answers 召す outright,
        # so the row ("「着る、食べる…」等动词的尊敬语动词") repeats it and teaches
        # no construction.
        "mat-a81d6a",  # 召す
        "jigeru",  # 存じ上げる
    }
)

# Material entries whose construction jkindrix already carries under a
# different id (or whose derived spec is so broad it duplicates an existing
# row).  Same skip semantics as _VOCAB_IDS, but a separate reviewed list:
# these are *duplicate* silences, not vocabulary silences, so the screen's
# vocab-flag invariant does not apply to them.
_DUPLICATE_IDS = frozenset(
    {
        "tekarataatode",  # てから/たあとで -> kara_reason (52.2% pages, bare から)
        "tekuru",  # てくる -> te-kuru (34.6%)
        "tekureru",  # てくれる -> te-kureru-favor-received (28.3%)
        "noyouda",  # のようだ -> you-da-appearance (24.5%)
        "taritarisuru",  # たり~たりする -> tari-tari-suru (10.2%)
        "temiru",  # てみる -> te-miru (8.0%)
        "temiru-4eb5b2",  # てみる（尝试） -> te-miru
        "teoku",  # ておく -> te-oku (2.6%)
        "naa",  # なあ interjection (2.6%)
        # duplicates from the N4/N5 book merge (2026-09-29): their derived
        # spec or displayed name collides with an existing jkindrix rule and
        # _merge_same_name would otherwise shadow that rule.  Identified by
        # spec-fingerprint + display-name grouping, not guessed.
        "tekara",
        "tekuru-f9236c",
        "naide",
        "desu-674648",
        "masu",
        # ておく: the N4 material row collides with the jkindrix te-oku row
        # ("Verb-て form + おく", richer meaning/notes/related, zh.json gloss
        # present) -- one construction, two levels, reported 17x on the
        # overlap corpus (2026-10-16 audit).
        "teoku-7dcaca",
        "darou",
        "deshiyou",
        "nasai",
        "kadouka",
        "kamoshirenai",
        "kotonisuru",
        "kotoninaru",
        # koto-nominalizer sat silent for years only by accident: its bare
        # こと fragment was a *substring* of the hand-written たこと regex
        # literal, so _covered_by_hand_written swallowed it -- coverage that
        # rule never actually provided (言うこと has no た).  Retiring the
        # loose hand-written row (folded into ds_ta-koto-ga-aru, 2026-10-16)
        # un-suppressed it; keep it silent on purpose, because a bare-こと
        # row fires on nearly every page and restates the word popup plus
        # the ことがある / ことにする / ことになる family rows.
        "koto-nominalizer",
        "nisuru",
        "nisuru-138b7a",
        "ninaru",
        "ninaru-78e038",
        "nitsuite",
        "nikui",
        "yasui",
        "rashii",
        "youni",
        "younisuru",
        "noni-ed37cd",
        "naramadashimo",
        "naraizarazunaratomokaku",
        "nishite-59525e",
        "nishiteha-1d2bbe",
        "toyara",
        "hoshiitou",
        "dakedenaku",
        "okini",
        "makuru",
        "taitou",
        "hougaii",
        "kotogaaru",
        "takotogaaru",
        "kotogadekiru",
        "sugirusugida",
        "sugirusugida-be289f",
        "mashiyou",
        "mashiyouka",
        "temiru-75d0e1",
        "temoiidesu-6734b2",
        "tekudasai-381e5c",
        "tearu-3390f7",
        "teiku-9a9071",
        "mat-e2eac7",
        "meru",
        "waru",
        "keru",
        "hokanaiyorihokanaihokahanaihokashikataganai",
        "toshitetoshitehatoshitemo-1f4501",
        # N4/N5 book merge 2026-09-29, second pass (measure-driven): rules
        # that duplicate an existing row or are already reported by the
        # basic-particle aggregate.
        "tekuru-9ff820",  # ~てくる -> te-kuru (34.6% pages)
        "tekurerutekudasaru",  # ~てくれる/てくださる -> te-kureru-favor-received (33.3%)
        "tou-2f500e",  # ~と言う -> to-iu (23.4%)
        "teshimau-c89b54",  # ~てしまう -> te-shimau (15.2%)
        "teiku-f054ed",  # ~ていく -> te-iku (10.6%)
        "toiu",  # という -> to-iu (15.9%)
        "niha",  # ~には -> basic_particles aggregate (35.0%)
        "tame",  # ため -> tame-ni (13.6%)
        "san",  # ~さん suffix (48.7% pages, word formation)
        "chiyan",  # ~ちゃん suffix (16.7%, word formation)
        "noda",  # ~のだ -> n-desu-explanation (all examples are
        "noda-cd8e33",  #   んです/のです forms, same construction)
        # N1/N2/N3 private-material book merge 2026-09-30: same-level entries whose
        # display name and derived spec both collide with a row that is already
        # in the library, so _merge_same_name would fold them into that row and
        # append a second, near-identical gloss ("…之极；极度（の極み）；跟
        # 「の至り」的意思类似…").  Identified by grouping every derived rule on
        # (display name, spec fingerprint) and keeping the group members that
        # arrived in this merge, not guessed.  Two of them
        # (nakuhanainakumonai-9bc5e0, nishitatenishitemo-a3ea75) are duplicates
        # *within* this merge -- the book lists the same point twice, and the
        # id collided so the second got a hash suffix.  Six more
        # (ageku, mat-150c91 以上, mat-67312c 反面, mat-865ba0 抜く,
        # mat-fa7d2d 以来, poi) carry a bare-literal regex where the existing
        # row carries a stricter token spec; the name is the same, so they are
        # the same row.
        # N1:
        "gaika",
        "gurumi-1758dc",
        "nikatakunai-453107",
        "nomi",
        "nori",
        "ojinai",
        "omote",
        "orini",
        "sonomono",
        "taritomo",
        "teyamanai-4005e7",
        "zunihaokanainaidehaokanai",
        # N2:
        "ageku",
        "dakenokotohaaru",
        "karakoso",
        "karaniha",
        "kotodakara",
        "kotokara",
        "mat-150c91",
        "mat-67312c",
        "mat-865ba0",
        "mokamawazu-6f8ba7",
        "naikotoniha-c0be26",
        "nakuhanainakumonai-9bc5e0",
        "nihokanaranai-4931cc",
        "nimokakawarazu",
        "nirazu",
        "nishitatenishitemo-a3ea75",
        "nite",
        "omotonishiteomotonishita",
        "owazuhawazu",
        "tenaranai-1884a0",
        "toiumonoda",
        "toiumonodehanaitoiumonodemonai",
        "tsutsuaru",
        "zaruonai",
        "zurai",
        # N3:
        "buripuri",
        "mat-fa7d2d",
        "poi",
        "tabini",
        "toshite",
        "totomoni",
        # Also from the 2026-09-30 merge, but the collision is with a row in
        # the same family rather than a same-named one: 〜わけ appears next to
        # the 〜わけだ / 〜わけではない / 〜わけがない rows it already has, and
        # 〜のだ next to 〜んです (the N4 〜のだ entries were silenced for the
        # same reason in the previous round -- plain and polite form of one
        # construction).  Both fired on ~9% / ~21% of pages with a gloss the
        # family row already carries.
        "wake",
        "noda-e4b5a5",
        # 限り family (2026-10-16 overlap audit): 限り is one literal with
        # five readings, and before this review four rows carried the same
        # bare `限り` regex -- every 限り in the language, including the
        # verb 限る's own inflections, fired on all of them.  The readings
        # are now separated per row in _SLOT_SPECS; these three *material*
        # rows each duplicate a jkindrix row that stays, so they silence.
        "kagiri-as-long-as",  # N3 conditional 限り -> rihanairiha (N2,
        #   jkindrix, same formation: である/動詞/ない + 限り(は))
        "ni-kagiri",  # N2 に限り notice -> niru-071752 (N3 に限る, the
        #   same restriction reading; that row's derived lemma spec also
        #   claimed に限って / に限らず until this review)
        "kagiri-da-emotional",  # N2 限りだ -> rida (N1, jkindrix)
        # ところ family (2026-10-16 overlap audit): ところ is a formal noun
        # carrying a dozen constructions, and several rows had derived the
        # same bare `ところ` literal, so one sentence with one reading
        # reported three or four of them.  The readings are now separated
        # per row in _SLOT_SPECS; these rows each duplicate a row that
        # stays.
        "ta-tokoro-de",  # N2 material たところで ("even if") -> takaorode
        #   (N1 jkindrix, same construction)
        "tokoro-wo",  # N1 material ところを -> tokoroo (N2 jkindrix)
        "to-itta-tokoro-da",  # N1 material といったところだ ->
        #   toiutokorodatoitatokoro (N1 jkindrix), whose といったところ
        #   spec also covers the です form this row's literal missed
        "tokorodata",  # N1 ところだった -> tokorodata-df8a13 (N3): the same
        #   point listed in two books; keep the row with the fuller
        #   formation and both examples
        # てやる/てもらう family (2026-10-16 overlap audit): the N4 merged
        # rows carry the union of the forms (〜てやる/てあげる/てさしあげる,
        # 〜てもらう/いただく) with the fuller formations and examples, so the
        # single-form rows fold into them -- the same て+やる / て+もらう
        # token spec on two rows reported one sentence twice.
        "teyaru",  # N3 てやる -> teyaruteagerutesashiageru (N4 merged row)
        "te-ageru-favor-given",  # N4 material てあげる -> same merged row
        "temorau",  # N3 てもらう -> temorauteitadaku (N4 merged row)
        "te-morau-request-favor",  # N4 material てもらう -> same merged row
        "te-itadaku-polite-favor-received",  # N4 material いただく -> same
        #   merged row (〜てもらう・いただく)
        # もの family (2026-10-16 overlap audit): もの is a formal noun
        # carrying a dozen constructions (〜もの reason, 〜ものだ,
        # 〜ものではない, 〜ものだから, 〜ものの, 〜ものがある, 〜ものか,
        # 〜ものなら, 〜ものを, 〜というもの ...) and the derivation handed
        # the reason row the same bare `もの` literal as all of them, so
        # every もの in the language reported as 〜もの.  The readings are
        # separated per row in _SLOT_SPECS; mono-da-general's ものだ spec
        # duplicated the N2 jkindrix row and is silenced here.
        "mono-da-general",  # N3 material ものだ -> monodamonodehanai
        #   (N2 jkindrix, same spec plus ものではない)
        # 〜のだろう / のでしょう listed twice in the same merge: the second
        # arrival collided on the id and got the hash suffix (2026-10-16
        # overlap audit, 13-count pair).  Same pattern, same formation, same
        # spec -- the glosses only differ in wording.
        "nodarounodeshiyou-9ec170",
        # 思う/向け/あまり family (2026-10-16 overlap audit).  思う: the plain
        # と思う reading lived on two rows (to-omou, toutohawanai) with the
        # same bare と思う literal -- the surviving row carries とは思わない
        # too, and the volitional ようと思う is a different reading with its
        # own row (now spec-separated).  向け/向き: the same-level book rows
        # duplicated the fuller rows and are folded into them.
        "to-omou",  # N4 material と思う -> toutohawanai (N4 jkindrix, same
        #   reading, 12 examples, とは思わない as well)
        "mat-e459cf",  # N3 向け -> muke-targeted-for (N3, same construction,
        #   fuller: 向け / 向けに / 向けの)
        "mat-d26c40",  # N3 向き -> muki-suitable-for (N3, same construction,
        #   fuller: 向き / 向きの)
        # Containing-form pairs (2026-10-16 overlap audit, second pass): each
        # silenced row's derived spec is a strict subset of the row that
        # stays -- the shorter row's literal sits inside the longer row's
        # form set (を機にして contains を機に, つきまして contains つき ...),
        # so both fired on the same sentence with the same highlight.  The
        # surviving row claims every sentence the silenced one did.
        "oni",  # N1 を機に -> wo-ki-ni-shite (を機にして / を機に, same
        #   "taking X as an opportunity" point)
        "okomete",  # N2 をこめて -> okometeokometa (+ をこめた)
        "nishite-8d1c26",  # N2 に際して -> nishitenishinishiteno (+ に際し)
        "oniganinate",  # N2 を契機に -> wo-keiki-ni (+ を契機として)
        "wo-yogi-naku-sareru",  # N1 を余儀なくされる -> onakusareruonakusaseru
        #   (same passive row plus the active を余儀なくさせる)
        "wa-tomokaku",  # N2 はともかく -> hatomokakutoshitenaratomokakutoshite
        #   (+ ならともかく)
        "made-mo-nai",  # N2 までもない -> mademonaimadenokotomonai
        #   (+ までのこともない)
        "nitsukinitsukimashite",  # N2 につきまして -> ni-tsuki-due-to, whose
        #   につき spec already claims つきまして sentences and covers both
        #   senses of つき (due to / per) -- the narrower gloss loses nothing
        "ni-kakawarazu-regardless",  # N2 にかかわらず ->
        #   nikakawarazunihakakawarinaku (+ にかかわりなく)
        "kaimonaku",  # N1 かいもなく -> kaigaarugaigaarukaimonaku
        #   (かいがある / かいもなく)
        "mat-510d37",  # N2 material 得る -> uru-possible (N2 jkindrix
        #   得る / 得ない; the material row's [lemma 得る] spec matches the
        #   same 得/得る tokens, with a worse highlight on 得ない)
        "toshitaratosurutotosureba",  # N1 としたら -> to-sureba
        #   (とすれば / とすると / としたら)
        "ka-no-you-ni",  # N2 かのように -> ka-no-you-da
        #   (かのようだ / かのように / かのような)
        # Same-surface different-reading pairs (2026-10-16 overlap audit,
        # second pass): one word, two rows, where one row's spec fires on
        # the other row's reading as well.  ごとく is the 連体形 of ごとし
        # and the ごとし row's lemma spec already claims it; ばよかった is
        # one formation of the fuller 〜ばよかった・とよかった row.
        "gotoku-as-if",  # N1 ごとく -> gotoshi (lemma spec covers ごとし /
        #   ごとく / ごとき, with the fuller formation)
        "ba-yokatta",  # N3 よかった -> bayokatatarayokatatoyokata
        #   (ば / たら / と / なければ + よかった, five formations)
        # Same-construction book twins (2026-10-16 overlap audit, third
        # pass): each silenced row's derived spec is a subset of the row
        # that stays, so the survivor already claims every sentence.
        "shidai-de",  # N2 次第で・次第では -> dehaniyoteda
        #   (次第で / 次第では / 次第だ -- same 视…而定 point, one book
        #   each; 次第で matches inside 次第では)
        "nitaerutaenai",  # N1 にたえる・たえない -> nitaerunitaenai
        #   (same に + たえる token spec, and its にたえない literal keeps
        #   the negative reading)
        "kedakenikeno",  # N2 向けの -> muke-targeted-for (向け / 向けに /
        #   向けの)
        "kidakinikino",  # N2 向きだ・向きの -> muki-suitable-for
        #   (向きの / 向き -- the bare 向き covers 向きだ)
    }
)

# Data entries that describe a *class* of forms rather than a construction.
# Their formation text is a list of members, not a template, and nothing in a
# sentence marks the entry -- so there is no row to show, and the derivation
# can only pick out whichever members its own examples happen to contain.
#
#   jidoushi-tadoushi  "Transitive verb (他動詞) vs intransitive verb (自動詞)
#                      pairs" -- a category article.  Its formation lists 14
#                      pairs; the derivation could validate only the two its
#                      examples contain (開く, 消す), so 11.2% of pages showed
#                      a row titled "〜開く・消す" pointing at two arbitrary
#                      verbs.  A sentence does not contain "the transitive /
#                      intransitive distinction"; the verb in it does, and the
#                      word popup already says which one it is.
#   i-adjective-nonpast
#                      "い-adjective non-past" -- whether an い-adjective is
#                      in its dictionary form is a property of the *word*, and
#                      the panel reports the constructions a sentence is made
#                      of.  Matched on 形容詞 in dictionary form it fired on
#                      83% of a 400-page corpus, because the dictionary form
#                      is the default form: nearly every page carries one, so
#                      the row was furniture, and it said nothing the reader
#                      could not see in the word itself.  The two forms that
#                      have to be *recognised* in a sentence -- 〜かった and
#                      〜くありません -- keep their own rows.
#
#                      Nothing else carries this: the term layer stores no
#                      part of speech (nothing in the templates or JS mentions
#                      one), and the 変形 -> 辞書形 link in `wordparents` is
#                      written only by TermRepository.find_or_new(), which
#                      returns early when the term already exists -- terms are
#                      created when a page opens -- and by construction never
#                      fires when lemma == surface, i.e. never for a
#                      dictionary form.  248 of the 8117 Japanese terms on
#                      production carried a parent, almost all of them verbs.
_CONCEPT_IDS = frozenset({"jidoushi-tadoushi", "i-adjective-nonpast"})

# Data entries whose pattern names a *form* rather than a literal string
# ("Verb ない-form + で"), or names one alongside a literal too short to stand
# alone ("Verb-ない + でください").  Their formation text is a list of examples
# of that form, so the fallback that reads it titles the entry after whichever
# example word comes first.
#
#   nai-de-without-doing  came out as "〜言わないで", a row that never appeared
#                         at all, because its examples are 食べないで /
#                         言わないで / しないで.
#   nai-de-kudasai        came out as a bare literal でください -- the ない of
#                         its pattern was dropped, and で was too short to
#                         survive -- which then matched *any* でください.  So
#                         読んでください ("please read") was reported as
#                         〜ないでください ("please don't"), the opposite
#                         request, and 〜てください could not be reported
#                         instead because the hand-written rule matched a bare
#                         て.  Fixed at both ends: 〜てください now accepts
#                         動詞 + て/で + ください, and this entry says what it
#                         means.
#
# A general "the pattern names Verb-ない, so require ない in front" marker was
# tried and withdrawn: it broke naku-mo-nai, whose fragment (くもない) already
# carries ない's 連用 tail, so the required ない landed in the wrong place and
# the entry stopped firing (2.8% of pages -> 0).
#
# Kept as a reviewed map rather than a general slot -> part-of-speech
# translator, because the conditions below are not inferable from the pattern:
# "Verb ない-form + で" is 動詞 + ない + で and Sudachi reports that ない as
# 助動詞, so no part-of-speech lookup would find it.
#
# Each spec is validated against the entry's own examples at load time (see
# _load_level), so a library update that invalidates one falls back to the old
# derivation instead of reporting something wrong; a test asserts they stay
# valid, so that fallback never actually happens.
_SLOT_SPECS = {
    "nai-de-without-doing": (
        "ないで",
        [
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "動詞"},
                    {"lemma": {"ない"}},
                    {"surface": "で"},
                ],
            }
        ],
    ),
    "nai-de-kudasai": (
        "ないでください",
        [
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "動詞"},
                    {"lemma": {"ない"}},
                    {"surface": "で"},
                    {"surface": "ください"},
                ],
            }
        ],
    ),
    # のを見る/のを聞く: the derivation's second spec degraded to a bare 聞く
    # lemma, which fired on 29% of pages -- every 聞く is not the construction.
    # Reviewed: the pattern needs の + を in front of the perception verb, and
    # の/を are contiguous in both readings.
    "nooruku": (
        "のを見る・聞く",
        [
            {
                "type": "tokens",
                "conds": [{"lemma": {"の"}}, {"lemma": {"を"}}, {"lemma": {"見る"}}],
            },
            {
                "type": "tokens",
                "conds": [{"lemma": {"の"}}, {"lemma": {"を"}}, {"lemma": {"聞く"}}],
            },
        ],
    ),
    # N4/N5 book merge (2026-09-29).  The derivation turns a two-fragment
    # pattern like ~から~まで into one spec PER fragment, so each half fires
    # on its own (から alone: 84% of pages).  Reviewed specs below restate
    # the construction the entry actually describes.
    "karamade": (
        "から〜まで",
        [
            {
                "type": "anchors",
                "before": [{"surface": "から"}],
                "after": [{"surface": "まで"}],
                "maxgap": 12,
            }
        ],
    ),
    "amarinai": (
        "あまり〜ない",
        [
            {
                # The negative tail surface varies: ない / ません(ん, lemma ぬ) /
                # ありません(ん).  Match the auxiliary lemma family instead.
                "type": "anchors",
                "before": [{"surface": "あまり"}],
                "after": [{"lemma": {"ない", "ぬ"}}],
                "maxgap": 8,
                # not_next に: あまり + に is the other degree reading --
                # あまりに寒くて外に出られない ("so cold that ...", its own
                # 〜あまりに row) -- and the gap bridged straight across it.
                # For an anchors spec the guard looks at the token right
                # after the あまり anchor.
                "not_next": {"surface_in": ("に",)},
            }
        ],
    ),
    "kunaru": (
        "くなる",
        # く rides inside the adjective token (明るく), so the condition is
        # the adjective POS, not a bare く surface.
        [{"type": "tokens", "conds": [{"pos1": "形容詞"}, {"lemma": {"なる"}}]}],
    ),
    "kusuru": (
        "くする",
        [
            # く rides inside the adjective token (明るく), so the condition
            # is the adjective POS, not a bare く surface.  Sudachi also reads
            # adverbised よく as 副詞, so the kana sequence itself backs the
            # adjective-POS spec up (each spec is validated against the
            # entry's own examples; at match time they are alternatives).
            {"type": "tokens", "conds": [{"pos1": "形容詞"}, {"lemma": {"する"}}]},
            {"type": "regex", "re": re.compile("くする")},
        ],
    ),
    # なか（形式名词）is the superlative "among these", always なかで.
    "naka": (
        "なか",
        [{"type": "tokens", "conds": [{"surface": "なか"}, {"surface": "で"}]}],
    ),
    # N1/N2/N3 book merge (2026-09-30).  These entries' descriptive pattern is
    # the bare form itself ("よう", "なる", "とて", "とも"), so the derivation
    # fell back to a literal-substring regex and each fired on every occurrence
    # of that substring -- including words that merely contain it.  The
    # attachment the entry's own formation field names is what tells them
    # apart, and Sudachi tags it: 変わりよう/喜びよう take よう as 接尾辞 while
    # the ようだ / ように reading is 形状詞, 健全なる takes なる as 助動詞
    # (なり) while 春になる is the verb, 男女とも takes とも as 接尾辞 while
    # 〜とも思わない is と + も, and とても is one 副詞 token, so a regex とて
    # matched a different word outright.
    "you": (
        "よう",
        [{"type": "tokens", "conds": [{"pos1": "接尾辞", "surface": "よう"}]}],
    ),
    "naru": (
        "なる",
        [
            # Only the 連体形 spells なる (健全なる精神, 改革なるもの).  The
            # lemma alone also matched the conditional なら and the classical
            # 未然形 なら of 〜ならでは / のみならず, so every なら reported
            # this row as well.
            {
                "type": "tokens",
                "conds": [{"pos1": "助動詞", "lemma": {"なり"}, "surface": "なる"}],
            }
        ],
    ),
    "tote": (
        "とて",
        [{"type": "tokens", "conds": [{"surface": "とて", "pos1": "助詞"}]}],
    ),
    "tomo": (
        "とも",
        [{"type": "tokens", "conds": [{"pos1": "接尾辞", "surface": "とも"}]}],
    ),
    # 〜しか〜ない: the derivation produced an unanchored `しか` regex, which
    # also matched the しかし conjunction ("しか" sits inside しかし).  The
    # entry describes a two-anchor construction -- しか licensed by a negative
    # predicate -- so match it as one, like あまり〜ない.  not_after 動詞: a
    # verb right in front is the other reading -- 歩く + しかない ("no choice
    # but to walk", the 〜しかない row), while 名 + しか (+ negative) is this
    # row's "only" (100円しかない, 日本語しか話せません).
    "shika-nai": (
        "しか",
        [
            {
                "type": "anchors",
                "before": [{"surface": "しか"}],
                # ない itself, ん (lemma ぬ) as in ません / 話せません, and the
                # ん of ありません.
                "after": [{"lemma": {"ない", "ぬ"}}],
                "maxgap": 8,
                "not_after": {"pos1": "動詞"},
            }
        ],
    ),
    # ように has two readings and a bare literal cannot tell them apart: the
    # *purpose* marker ("so that / in order to", 聞こえるように / 忘れないように)
    # and the *resemblance* marker ("like ___", 氷のように冷たい).  The
    # derivation produced an unanchored `ように` regex for the purpose entry, so
    # 氷の**ように**冷たい手です was reported as 为了……；以便 -- the gloss of a
    # construction that sentence does not contain.
    #
    # What separates them is the left context, and it is load-bearing: a purpose
    # ように takes a verb / ない / な / adjective in front of it, the resemblance
    # reading takes 名詞 + の (or a demonstrative 様).  So the purpose entry is
    # matched on the token run よう + に -- Sudachi splits ように into よう
    # (形状詞,助動詞語幹) + に (助動詞,連用形-ニ) -- and the left context is
    # reviewed in _NOT_AFTER below.
    #
    # The n4 entry 〜ように ("purpose / wish") needs no guard of its own: it is
    # in _DUPLICATE_IDS (its row would shadow this one), so it never fires.
    "you-ni-purpose": (
        "ように",
        [{"type": "tokens", "conds": [{"surface": "よう"}, {"surface": "に"}]}],
    ),
    # The reading the entry above must not claim, as its own library row
    # (you-ni-resemblance): the 比況助動詞 in its 連用形 / 連体形, which the
    # 終止形-only 〜ようだ row cannot see at all.  Guarding the purpose entry
    # alone would only delete the row -- the panel would then say nothing about
    # a construction the sentence is made of.
    #
    # Three specs, disjoint by left context:
    #
    #   1. 名詞 + の + ように / ような -- the simile proper.  The run starts at
    #      the noun, so the highlight covers the whole 氷のように.
    #   2. この / その / あの / どの + ように / ような -- the demonstrative 様.
    #   3. a bare ような -- the 連体形 after a predicate (人の手ではないような
    #      手, 何も知らないような顔), which is neither a noun + の nor a
    #      demonstrative.  `not_after` keeps it off the two contexts above so
    #      the three specs cannot both claim one ような.
    #
    # ように after a *predicate* (雨が降るように見える) is deliberately not
    # claimed here: it is ambiguous with the purpose reading (雨が降るように
    # 祈る) and the purpose entry owns it.
    "you-ni-resemblance": (
        "のように・のような",
        [
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "名詞"},
                    {"surface": "の"},
                    {"surface": "よう"},
                    {"surface_in": ("に", "な")},
                ],
            },
            {
                "type": "tokens",
                "conds": [
                    {"surface_in": ("この", "その", "あの", "どの")},
                    {"surface": "よう"},
                    {"surface_in": ("に", "な")},
                ],
            },
            {
                "type": "tokens",
                "conds": [{"surface": "よう"}, {"surface": "な"}],
                "not_after": {
                    "surface_in": ("の", "この", "その", "あの", "どの"),
                },
            },
        ],
    ),
    # --- 限り / 限る: one literal, five readings ---------------------------
    #
    # 限り carries five readings (max extent 力の限り / conditional
    # 生きている限り / knowledge scope 限りでは / notice に限り /
    # recommendation に限る) and the derivation handed four rows the same
    # bare `限り` regex, so every 限り in the language fired on all of
    # them -- including the verb 限る's own inflections: Sudachi reads
    # 背広に**限り**ます as に + 限り with lemma 限る, so no literal test
    # can see that it is the verb.  What separates the readings is the
    # token context, so each row below matches only the context its own
    # formation names, and every 限り cond pins the *noun* (lemma 限り)
    # rather than the verb (lemma 限る).
    "mat-51adc0": (
        "限り",
        [
            # 名「の」+限り (力の限り): の must sit directly in front --
            # 私の見る限りでは has the verb in between and is the
            # knowledge-scope reading (riderideha), not this one.
            {
                "type": "tokens",
                "conds": [{"surface": "の"}, {"surface": "限り", "lemma": {"限り"}}],
            },
            # 動詞 + 限りの + N (思いつく限りのアイデア): the adnominal
            # half of the max-extent formation.
            {
                "type": "tokens",
                "conds": [{"surface": "限り", "lemma": {"限り"}}, {"surface": "の"}],
            },
            # できる限り is a single 副詞 token, so no token run can
            # claim it; the anchored regex is exactly that one token.
            {"type": "regex", "re": re.compile("できる限り"), "anchor": True},
        ],
    ),
    "riderideha": (
        "限りでは",
        [
            # 名「の」+限り(で)は (取り調べの限りでは).
            {
                "type": "tokens",
                "conds": [
                    {"surface": "の"},
                    {"surface": "限り", "lemma": {"限り"}},
                    {"surface": "で"},
                ],
            },
            # 動詞+限り(で)は (私の見る限りでは, 生きている限りでは).
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "動詞"},
                    {"surface": "限り", "lemma": {"限り"}},
                    {"surface": "で"},
                ],
            },
            # 動詞「た」+限り(で)は (昨日調べた限りでは): た is 助動詞,
            # so the verb POS above misses it.
            {
                "type": "tokens",
                "conds": [
                    {"lemma": {"た"}},
                    {"surface": "限り", "lemma": {"限り"}},
                    {"surface": "で"},
                ],
            },
            # The emotional 限りで (お恥ずかしい限りで, 残念な限りで)
            # shares the 限り+で tail but takes an adjective in front,
            # which none of the three contexts above allow; it has no
            # row of its own (the 限りだ entries teach 限りだ/限りです).
        ],
    ),
    "rihanairiha": (
        "限り・ない限り",
        [
            # 動詞/である+限り (会員である限り, 生きている限り): the
            # token in front is ある (動詞,非自立可能).
            {
                "type": "tokens",
                "conds": [{"pos1": "動詞"}, {"surface": "限り", "lemma": {"限り"}}],
            },
            # イ辞+限り(は): the は separates the conditional adjective
            # (私の目の黒い限りは) from the emotional 限りだ, which
            # carries だ/です in that slot (嬉しい限りです -> rida).
            # The library's own example spells it 限り(は), so the
            # parenthesis is an optional cond of its own.
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "形容詞"},
                    {"surface": "限り", "lemma": {"限り"}},
                    {"surface_in": ("(", "（"), "optional": True},
                    {"surface": "は"},
                ],
            },
            # ない+限り (確かめない限り): ない is 助動詞, not 動詞.
            {
                "type": "tokens",
                "conds": [{"lemma": {"ない"}}, {"surface": "限り", "lemma": {"限り"}}],
            },
        ],
    ),
    "niru-071752": (
        "に限る",
        [
            # に限る / に限り / に限ります.  The derivation's lemma chain
            # (に + 限る) also claimed に限って / に限らず /
            # に限ったことではない -- separate rows with separate
            # readings -- so the surface is pinned to the two forms this
            # entry teaches.  The notice に限り shares the run with the
            # ni-kagiri row (silenced 2026-10-16, same reading).
            {
                "type": "tokens",
                "conds": [{"surface": "に"}, {"surface_in": ("限る", "限り")}],
            },
        ],
    ),
    "niru": (
        "に限る",
        [
            # Same spec and same headline as niru-071752 above: the N3
            # restriction reading ("only X") and the N2 recommendation
            # reading ("X is best") are one form carrying two glosses,
            # which _merge_same_name lists on a single row.  The literal
            # に限る the derivation chose missed the ます-form of this
            # entry's own example (服装は背広に限りますよ).
            {
                "type": "tokens",
                "conds": [{"surface": "に"}, {"surface_in": ("限る", "限り")}],
            },
        ],
    ),
    "rida": (
        "限りだ",
        [
            # イ辞+限りだ/限りです (寂しい限りだ, 嬉しい限りです): the
            # derivation's literal 限りだ never matched the polite
            # 限りです of this entry's own first example.  The left
            # context is required so a knowledge-scope 限りです
            # (私の知る限りです) is not read as an emotional one.
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "形容詞"},
                    {"surface": "限り", "lemma": {"限り"}},
                    {"surface_in": ("だ", "です")},
                ],
            },
            # ナ「な」+限りだ/限りです (幸せな限りです): な is 助動詞
            # with lemma だ.
            {
                "type": "tokens",
                "conds": [
                    {"lemma": {"だ"}},
                    {"surface": "限り", "lemma": {"限り"}},
                    {"surface_in": ("だ", "です")},
                ],
            },
        ],
    ),
    # --- ところ: one formal noun, a dozen readings ------------------------
    #
    # ところ carries a dozen constructions (temporal 〜ところ, sequential
    # たところ, source ところでは, even-if たところで, almost ところだった,
    # just-when ところに/を/へ/から, place というところ ...) and the
    # derivation handed three rows the same bare `ところ` literal, so a
    # sentence with one reading reported three or four of them.  Each row
    # below matches only the context its own formation names.
    "ta-tokoro": (
        "ところ",
        [
            # V non-past / V-ている + ところ + だ/です (今から出かける
            # ところだ, 食べているところです).  The だ/です the formation
            # calls optional is what keeps this off 〜ところでは (source),
            # 〜ところだった (almost) and 〜ところに/を/へ/から, which all
            # share the V+ところ run.
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "動詞"},
                    {"surface": "ところ"},
                    {"surface_in": ("だ", "です")},
                ],
            },
            # V-た + ところ + だ/です (着いたところです): た is 助動詞,
            # so the verb POS above misses it.
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "動詞"},
                    {"lemma": {"た"}},
                    {"surface": "ところ"},
                    {"surface_in": ("だ", "です")},
                ],
            },
        ],
    ),
    "tatokoro": (
        "たところ",
        [
            # た+ところ + a following clause (相談したところ、彼は…;
            # 思ったところが、…).  Every other た+ところ form has a row of
            # its own -- たところで (even if), たところです (just did),
            # たところだ, ところだった (almost), ところに/を/へ/から --
            # and a token cond cannot name what must *not* follow, so the
            # exclusion is a negative lookahead on the literal.  A bare
            # た+ところ token pair would claim all of them: Sudachi reads
            # 謝った as 謝っ + た.
            {
                "type": "regex",
                "re": re.compile("たところ(?!で|です|だ|に|を|へ|から)"),
            },
        ],
    ),
    "tokorotokorodehatokoroniyoru": (
        "ところでは",
        [
            # 動詞+ところ(では) (わたしの聞くところでは).  The library's
            # own example spells it 聞くところ(では), so the parenthesis
            # is an optional cond of its own.  The bare た+ところ this
            # entry also listed (本人に確かめたところ、…) is the sequential
            # reading and stays with takaoro.
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "動詞"},
                    {"surface": "ところ"},
                    {"surface_in": ("(", "（"), "optional": True},
                    {"surface": "で"},
                ],
            },
        ],
    ),
    "toiutokoro": (
        "というところ",
        [
            # 名+というところ+に/で (森の里というところに住んで).  The
            # particle the entry's own examples carry is what keeps this
            # off the N1 approximation reading (〜というところだ), which
            # shares the というところ run.
            {
                "type": "tokens",
                "conds": [
                    {"surface": "と"},
                    {"surface": "いう"},
                    {"surface": "ところ"},
                    {"surface_in": ("に", "で")},
                ],
            },
        ],
    ),
    "temorauteitadaku": (
        "てもらう・いただく",
        [
            # 動詞+て+もらう.  もらえる (てもらえませんか) is the same
            # construction in its request form, and Sudachi reports it as
            # its own lemma, so the dictionary form alone missed every
            # polite request.
            {
                "type": "tokens",
                "conds": [{"lemma": {"て"}}, {"lemma": {"もらう", "もらえる"}}],
            },
            # 動詞+て+いただく, same request-form note: いただけますか /
            # いただけませんか reads いただける.
            {
                "type": "tokens",
                "conds": [{"lemma": {"て"}}, {"lemma": {"いただく", "いただける"}}],
            },
        ],
    ),
    # 〜という (N1) is the repeated-noun reading -- 壁という壁, 力という力,
    # "every single wall / all the strength there is" -- and the derivation
    # handed it a bare `という` literal, so the row fired on every という in
    # the language: 〜ということ, 〜というもの, 〜というより, 〜というのは all
    # reported as "所有的......".  The construction is the noun said twice,
    # so the spec says it twice: a backreference pins the word after という
    # to the one in front of it, which is also what keeps the row off the
    # nested forms above (each has a row of its own).
    "toiu-ee4dee": (
        "という",
        [
            {
                "type": "regex",
                "re": re.compile(r"([一-龥]+)という\1"),
                "anchor": True,
            },
        ],
    ),
    # --- もの: one formal noun, a dozen readings --------------------------
    #
    # Same shape as ところ: もの carries the reason 〜もの, 〜ものだ,
    # 〜ものではない, 〜ものだから, 〜ものの, 〜ものがある, 〜ものか,
    # 〜ものなら, 〜ものを, 〜というもの ..., and the derivation handed the
    # reason row the same bare `もの` literal as all of them.  The reason
    # reading is the one at a clause boundary -- the sentence-end もの of
    # 疲れているもの。/ 寒いんだもの、... -- so the spec requires that
    # boundary; every suffixed form (ものだ, ものから, ものの ...) has a
    # row of its own and stops here.  The lookbehind keeps it off 〜という
    # もの, which is the N5 explanation row's form, never the reason.
    "mono-reason-assertion": (
        "もの",
        [
            {
                "type": "regex",
                "re": re.compile(r"(?<!という)(?:もの|もん)(?=[。、！？」』!?．]|$)"),
            },
        ],
    ),
    # 〜ものだから / もので: the bare もので literal also matched the もの
    # では of 〜ものではない and the もの でも of 〜ないものでもない, both
    # of which have rows of their own, so the も tail is excluded.
    "mono-dakara": (
        "ものだから",
        [
            {"type": "regex", "re": re.compile("ものだから")},
            {"type": "regex", "re": re.compile("もので(?!は|も)")},
        ],
    ),
    # 〜ものだ / ものではない: the ものだ literal also matched the もの だ
    # から of 〜ものだから (mono-dakara's row), so から is excluded.
    "monodamonodehanai": (
        "ものだ / ものではない",
        [
            # The predicate readings (動詞/形容詞 + ものだ) against the nominal
            # ones (名 + の / という / ような / その + ものだ): what sits in
            # front of もの is what tells them apart.  の covers every
            # 名+の+もの (ぐらいの / ほどの / ゆえの / 私の ...), いう the
            # 〜という+もの quotation (友だちというものは...), ような the
            # resemblance (試金石のようなものだ -- the 〜ように row owns it),
            # その the 〜そのもの "nothing but" row, んな the
            # こんな/そんな/どんな + もの demonstratives.  Every one of those
            # has a row of its own; the predicate readings never end in any
            # of them, so nothing the entry's own examples do is lost.
            {
                "type": "regex",
                "re": re.compile(
                    "(?<!その)(?<!いう)(?<!ような)(?<!の)(?<!んな)ものだ(?!から)"
                ),
            },
            {
                "type": "regex",
                "re": re.compile(
                    "(?<!その)(?<!いう)(?<!ような)(?<!の)(?<!んな)ものではない"
                ),
            },
        ],
    ),
    # 〜くださる: Sudachi reports ください (the request form of 〜てください)
    # with the same lemma くださる as the honorific verb itself, so the
    # derived [lemma くださる] spec claimed every てください on the page for
    # the honorific row (68-count overlap with the hand-written te_kudasai
    # rule).  What separates them is the form, not the lemma: the honorific
    # inflects (くださる / くださった / くださって) or takes a polite tail
    # (くださいました, 〜てくださいます), while the request is a bare くだ
    # さい at the clause end.  A blanket not_after て would be wrong --
    # V-て + くださる/くださった is the honorific reading itself
    # (お手紙をくださってありがとうございます).
    "kudasaru-honorific-verb": (
        "くださる",
        [
            # くださる / くださった / くださって / ...: any くださる-family
            # token that is not the bare ください.
            {
                "type": "tokens",
                "conds": [{"surface_not": ["ください"], "lemma": {"くださる"}}],
            },
            # くださいました / 〜てくださいます: the polite honorific, where
            # the ます tail follows the ください surface.
            {
                "type": "tokens",
                "conds": [{"surface": "ください"}, {"surface_in": ("まし", "ます")}],
            },
        ],
    ),
    # 〜をください: the request "give me X".  The bare literal also matched
    # the honorific をくださいました / をくださいませんか (先生が本をください
    # ました), whose ます tail is what tells the two apart.
    "okudasai": (
        "をください",
        [{"type": "regex", "re": re.compile("をください(?!ま)")}],
    ),
    # --- 思う: one verb, five attachments ---------------------------------
    #
    # 思う attaches to a plain-form clause (〜と思う "I think"), a volitional
    # (ようと思う "I intend to"), たい (たいと思う "I want to"), か (ようか
    # と思う "I'm wondering") and た+か (かと思うと "no sooner ... than").
    # The derivation handed the plain row and the volitional row the same
    # bare と思う literal, so one sentence reported both -- 早く起きようと思
    # います read as "I think I'll get up early".  Each row now matches only
    # the attachment its own formation names.
    "you-to-omou-volitional-intent": (
        "と思う",
        [
            # よう/おう + と思う: the volitional tail precedes the と.  A
            # token cond cannot name a surface ending, so the lookbehind
            # does it.
            {
                "type": "regex",
                "re": re.compile(r"(?<=よう|おう)(?:と思う|と思い|と思っ|と思わ)"),
            },
        ],
    ),
    "toutohawanai": (
        "と思う・とは思わない",
        [
            # Plain-form clause + 思う in any inflection (と思う / と思い
            # ます / と思って / と思っています / と思った ...).  The
            # volitional (起きようと思う), たい (行きたいと思う) and
            # ようか attachments are separate rows with separate readings,
            # and the characters in front of the と are what name them
            # -- so they are excluded with a lookbehind on the literal.
            # ようか / おうか needs its own (three-character) lookbehind:
            # the か question mark sits between the volitional and the と,
            # so the two-character one sails past it.
            {
                "type": "regex",
                "re": re.compile(
                    r"(?<!よう|おう|たい)(?<!ようか|おうか)(?:と思う|と思い|と思っ|と思わ)"
                ),
            },
            # とは思わない / とは思っていない / とは思いませんでした.
            {
                "type": "tokens",
                "conds": [{"surface": "と"}, {"surface": "は"}, {"lemma": {"思う"}}],
            },
        ],
    ),
    # 〜かと思う ("I'm wondering whether to") is the volitional question:
    # しようか / おうか + かと思う, in every inflection of 思う (かと思う /
    # かと思います / かと思っている -- the bare かと思う literal missed all
    # of the 思っ/思い ones, so 買おうか思います was reported by nobody).
    # The lookbehind keeps the row off its near neighbours: 〜ないかと思う
    # ("I thought maybe ..."), 日本人かと思うほど ("so fluent I'd take him
    # for Japanese") and た+かと思うと ("no sooner ... than", a row of its
    # own) all spell the same かと思う without a volitional in front.
    "you-ka-to-omou": (
        "かと思う",
        [
            {
                "type": "regex",
                "re": re.compile(r"(?<=よう|おう)か(?:と思う|と思い|と思っ|と思わ)"),
            },
        ],
    ),
    # --- あまり: two readings, one literal --------------------------------
    #
    # あまり is the degree adverb of あまり〜ない ("not very") and the noun
    # of 〜のあまり ("from too much X").  The derivation gave the N2 "too
    # much" row a bare あまり literal, so it fired on every あまり〜ない
    # sentence as well.  The reading attaches to a preceding の / な / た
    # (驚きのあまり, 働きすぎたあまり); the adverb stands in front of its
    # predicate and has its own anchors row.
    "amari-too-much": (
        "あまり",
        [
            {
                "type": "tokens",
                "conds": [
                    {"surface_in": ("の", "な", "た")},
                    {"surface": "あまり"},
                ],
            },
        ],
    ),
    # 〜たいと思う: the desire attachment.  The derived spec pinned the
    # terminal 思う only, so 行きたいと思います / 探したいと思っていました
    # never fired; the lemma family covers every inflection.
    "tai-to-omou-want-to-think": (
        "たいと思う",
        [
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "動詞"},
                    {"surface": "たい"},
                    {"surface": "と"},
                    {"lemma": {"思う"}},
                ],
            },
        ],
    ),
    # --- 〜のだろう: spoken contraction and a homograph の -----------------
    #
    # The derived spec was a bare のだろう regex, and five of the entry's six
    # examples are the spoken contraction 〜んだろう / 〜んでしょう, so the row
    # never fired on the sentences it exists to explain.  Token specs rather
    # than an anchored regex: なんでしょう ("I wonder why") spells the same
    # んでしょう but its ん sits inside the single なん token, and the ん of
    # 〜てくださいませんでしょうが is the negative 助動詞 (lemma ぬ), not the
    # copula's 助詞 -- neither is this construction.  The の branch is token-
    # anchored for the mirror reason: the の inside もの (〜ものだろうか,
    # "I wonder if...") is part of the noun, not the explanatory の.
    "nodarounodeshiyou": (
        "のだろう",
        [
            {
                "type": "tokens",
                "conds": [{"surface": "の"}, {"surface_in": ("だろう", "でしょう")}],
            },
            {
                "type": "tokens",
                "conds": [
                    {"surface": "ん", "pos1": "助詞"},
                    {"surface_in": ("だろう", "でしょう")},
                ],
            },
        ],
    ),
    # --- てご覧: request vs. honorific, and the ください homograph --------
    #
    # 〜てご覧ください is the request ("please try it"), 〜てご覧くださいます /
    # 〜てご覧くださいました is the honorific ("kindly tried"), and Sudachi
    # reports the request form ください with the same lemma くださる as the
    # honorific verb -- so the honorific row's [て, ご覧, くださる] spec
    # claimed every てご覧ください on the page, and the request row's bare
    # てご覧 literal claimed every てご覧になる in return.  The ます tail is
    # what separates them, and every spec below is validated against the
    # entry's own examples (the load-time guard drops a slot that is not),
    # which is why the honorific row's one-token くださいます form has no
    # spec of its own: no example of the entry spells it out.
    "tegokudasarutegoninaru": (
        "てご覧くださる・てご覧になる",
        [
            # て + ご覧 + ください + まし/ます/ませ: the honorific with its
            # polite tail (くださいました / くださいます split / ませんか).
            # A bare てご覧ください has no such tail and stays with the
            # request row.
            {
                "type": "tokens",
                "conds": [
                    {"surface_in": ("て", "で")},
                    {"surface": "ご覧"},
                    {"surface": "ください"},
                    {"surface_in": ("まし", "ます", "ませ")},
                ],
            },
            # ご覧になる / ご覧になって / ご覧になりました ...: the honorific
            # proper, which the request row's bare てご覧 must not claim.
            # な(?:る|[りっ]) is the whole inflection family -- なって and
            # なった are な+っ/て, and a literal になる only ever matched the
            # dictionary form.
            {"type": "regex", "re": re.compile("てご覧にな(?:る|[りっ])")},
        ],
    ),
    "tegotegonasaitegokudasai": (
        "てご覧・てご覧なさい・てご覧ください",
        [
            # Bare てご覧 ("try it and see"): the lookahead keeps it off the
            # honorific てご覧になる / てご覧になって / てご覧くださいます and
            # off its own longer siblings, each of which has a spec below.
            # にな covers every inflection of the honorific -- なって is not
            # なる, so a literal になる would have missed all of them.
            {"type": "regex", "re": re.compile("てご覧(?!にな|くださ|なさい|ください)")},
            {"type": "regex", "re": re.compile("てご覧なさい")},
            # The request; a まし/ます/ませ tail is the honorific
            # (〜てご覧くださいました / 〜てご覧くださいませんか), owned by the
            # てご覧くださる row.
            {"type": "regex", "re": re.compile("てご覧ください(?!まし|ます|ませ)")},
        ],
    ),
    # --- なさい: the command form is not the honorific ----------------------
    #
    # なさい is both the imperative of なさる ("食べなさい", the 〜なさい row)
    # and, spelled the same, the 連用形 inside なさいます (なさい + ます,
    # the 〜なさる row).  Each row's literal claimed both readings, so one
    # sentence reported two cards; the ます tail is the whole difference.
    # The honorific row's spec is the lookahead form plus なさっ / なさら
    # (なさって / なさった / なさらない) -- its own examples spell なさいます,
    # which is what keeps the spec valid at load time.
    "nasai-command": (
        "なさい",
        [
            # The command: なさい with no ます tail behind it.
            {"type": "regex", "re": re.compile("なさい(?!ます)")},
        ],
    ),
    "nasaru-honorific": (
        "なさる",
        [
            # なさいます = なさい + ます: the polite honorific, and the only
            # なさい the honorific row may claim.  なさって / なさった /
            # なさらない spell なさっ / なさら and never なさい.  One regex,
            # because the load-time guard validates a spec as a whole: the
            # なさい(?=ます) branch is what the entry's own examples carry,
            # and it carries the なさ[っら] branch along.
            {"type": "regex", "re": re.compile(r"なさい(?=ます)|なさ[っら]")},
        ],
    ),
    # --- あろうとも: the volitional reading is not the 接尾辞 ----------------
    #
    # 〜あろうとも ("no matter what") carried a bare とも regex beside its
    # あろうとも literal, so the row fired on every とも in the language --
    # 男女とも, 2日とも, the 接尾辞 reading the 〜とも row owns.  The reading
    # is volitional + とも (起きようとも / 行こうとも / あろうとも), and the
    # lookbehind says exactly that; あろう ends in ろう, not おう, so it
    # keeps a literal of its own.
    "aro-u-tomo": (
        "あろうとも",
        [
            # One regex, as with なさる: the あろうとも branch is what the
            # entry's own examples carry and it carries the lookbehind
            # branch along.  あろう ends in ろう, not おう, so it needs the
            # literal beside the volitional lookbehind.
            {"type": "regex", "re": re.compile(r"(?<=よう|おう)とも|あろうとも")},
        ],
    ),
    # --- しかない: "no choice but to" --------------------------------------
    #
    # The other reading of the しか literal: verb + しかない ("have no choice
    # but to"), which the 〜しか row must not claim and this one must not
    # claim 名 + しか + negative (see the not_after note on shika-nai above).
    "shikanai": (
        "しかない",
        [
            {
                "type": "tokens",
                "conds": [{"pos1": "動詞"}, {"surface": "しか"}, {"lemma": {"ない"}}],
            },
        ],
    ),
    # --- でいる: the copula's て-form, not the verb's ------------------------
    #
    # でいる is 名 / ナ + で + いる ("remain in a state"), where the で is the
    # copula's て-form (lemma だ, 助動詞).  The derived bare でいる regex also
    # matched the *verb's* て-form で (読んでいる, 助詞,接続助詞), which is the
    # 〜ている row's reading -- 22 sentences carried both cards.  The part of
    # speech tells them apart, and おる covers the humble でおり the entry's
    # own second example spells.
    "deiru": (
        "でいる",
        [
            {
                "type": "tokens",
                "conds": [
                    {"surface": "で", "pos1": "助動詞"},
                    {"lemma": {"いる", "おる"}},
                ],
            },
        ],
    ),
    # --- だろうか・でしょうか: the polite question has its own row ----------
    #
    # The entry teaches both だろうか and でしょうか, and its derived spec
    # claimed both -- but でしょうか is also a point of its own (the polite
    # ですか / ますか), with a row that glosses it as such.  The entry keeps
    # the だろうか it is really about; bare でしょうか goes to that row.
    "daroukadeshiyouka": (
        "だろうか・でしょうか",
        [{"type": "regex", "re": re.compile("だろうか")}],
    ),
    # --- 次第: 视…而定 vs. the formal 原委 --------------------------------    #
    # One word, two readings, three rows: 名 + 次第で / 次第だ ("depends on",
    # 视…而定) and 動詞 + 次第だ / 次第です ("that is why I...", the formal
    # report closing).  The token before 次第 is what names the reading --
    # 気分次第だ / 天気次第で are nouns, 差し上げた次第です / 申し上げる次第で
    # ございます are a verb (or its た), and each row claimed both shapes.
    "dehaniyoteda": (
        "次第で・次第だ",
        [
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "名詞"},
                    {"surface": "次第"},
                    {"surface_in": ("で", "だ")},
                ],
            },
        ],
    ),
    "mat-8c61a1": (
        "次第だ",
        [
            # 動詞 + 次第だ / 次第です (申し上げる次第でございます).
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "動詞"},
                    {"surface": "次第"},
                    {"surface_in": ("です", "で", "だ")},
                ],
            },
            # The た-form: 差し上げた次第です -- the token in front is the
            # 助動詞 た, not the verb itself.
            {
                "type": "tokens",
                "conds": [
                    {"pos1": "助動詞", "surface": "た"},
                    {"surface": "次第"},
                    {"surface_in": ("です", "で", "だ")},
                ],
            },
        ],
    ),
    # The two 〜次第 rows (N3 结果次第で / 着き次第 and N2 到着次第 -- same
    # display name, so the panel folds them into one) carried a bare anchored
    # 次第 regex, which claimed every 次第 in the language: the 视…而定
    # sentences (結果次第で / 気分次第だ, the two rows above), the formal
    # 原委 (差し上げた次第です / 申し上げる次第でございます, mat-8c61a1) and
    # even the adverb 次第に ("gradually").  What is left after the tail is
    # the "as soon as" reading -- V ます-stem + 次第 at a clause break
    # (着き次第、電話します) -- which is why the tail, not the head, is the
    # discriminator.
    "shidai-depending": (
        "次第",
        [
            {
                "type": "tokens",
                "conds": [{"surface": "次第"}],
                "not_next": {"surface_in": ("です", "で", "だ", "に")},
            },
        ],
    ),
    "mat-2cf646": (
        "次第",
        [
            {
                "type": "tokens",
                "conds": [{"surface": "次第"}],
                "not_next": {"surface_in": ("です", "で", "だ", "に")},
            },
        ],
    ),
    # --- にしても: three rows, one literal ---------------------------------
    #
    # にしても is claimed by three entries (N3 即使 / N2 从…角度 / N2 排除
    # 一切条件), and each also teaches a distinctive sibling -- にしたら,
    # にしたって.  The shared form goes to the N3 row (即使, the plainest
    # reading) and each N2 row keeps only the sibling it is named for.
    "nishitaranisurebanishitemo": (
        "にしたら・にしても",
        [{"type": "regex", "re": re.compile("にしたら")}],
    ),
    "nishitatenishitemo": (
        "にしたって・にしても",
        [{"type": "regex", "re": re.compile("にしたって")}],
    ),
    # --- ならともかく: the hand-written row owns it ------------------------
    #
    # Hand-written nara_tomo_kaku reports ならともかく already; the N2 data
    # row claims both はともかく and ならともかく, so the same sentence
    # carried two cards.  The data row keeps はともかく (what its own
    # examples spell); ならともかく stays with the hand-written row.
    "hatomokakutoshitenaratomokakutoshite": (
        "はともかく・ならともかく",
        [{"type": "regex", "re": re.compile("はともかく")}],
    ),
    # --- あまり: のあまり is the N2 row's ----------------------------------
    #
    # のあまり ("驚きのあまり", from too much X) is the N2 〜あまり row's own
    # reading and its token spec claims it; this row's のあまり literal
    # reported the same sentences again.  Its distinctive form is あまりに
    # (あまりに寒い), which the N2 row does not claim.
    "amarininoamarini": (
        "あまりに・のあまり",
        [{"type": "regex", "re": re.compile("あまりに")}],
    ),
    # --- たことがある: the spoken が-ellipsis -------------------------------
    #
    # 動詞 + た + こと + が + ある is the experience construction, and the
    # derivation required the が outright -- so the way it is actually
    # spoken, 見たことありますか / 見たことない, never fired.  The retired
    # hand-written たこと regex did cover those, and covered every
    # nominalization with them (転校したことです, 忘れたことに, 言ったことは),
    # which is why it is gone; restoring the bare literal would bring all of
    # that back.  Anchors instead: 動詞+た+こと, then at most one token, then
    # ある/ない -- the gap is the が (or も / は) when it is spelled, and a
    # nominalization never ends in ある/ない.  not_next で keeps out
    # 〜たことである, the written judgement, which is not the experience
    # reading; everything the entry's own examples carry (ことがあります,
    # ことがありません) sits in the gap the spec allows.
    "ta-koto-ga-aru": (
        "ことがある",
        [
            {
                "type": "anchors",
                "before": [
                    {"pos1": "動詞"},
                    {"surface_in": ("た", "だ")},
                    {"lemma": {"こと"}},
                ],
                "after": [{"lemma": {"ある", "ない", "ぬ"}}],
                "maxgap": 1,
                "not_next": {"surface_in": ("で",)},
            },
        ],
    ),
}

# The て-form connective: Sudachi reports both the て of 知っ+て and the で of
# 読ん+で as 助詞,接続助詞, while the homograph で of 電車で行きます ("go by
# train") is 助詞,格助詞 -- a bare て/で test would drop that sentence too.
_TE_CONNECTIVE = {"pos1": "助詞", "pos2": "接続助詞", "surface_in": ("て", "で")}

# Data entries whose match must not sit directly after a given token.
#
# The polite suffixes are also how a 〜て + auxiliary chain closes, and "Verb +
# ます" cannot tell the two apart: 知っ+て+い+ます tokenises as 動詞 + 助詞 +
# 動詞(非自立可能) + 助動詞, so the ます entry matched the います of every
# 〜ている, and the panel offered 知っています as an example of 〜ます with います
# highlighted.  Those chains are grammar points in their own right and have
# their own rows (〜ている, 〜てしまう, 〜てある, 〜ておく ...), so 〜ます must not
# claim them.  The auxiliary's own part of speech cannot be used instead: 行き /
# 来 / あり are 動詞,非自立可能 too, so nothing about the auxiliary separates it
# from 行き -- what precedes it does.
#
# The left context that makes a ように a *resemblance* rather than a purpose:
# 名詞 + の ("like a hand", 氷のように冷たい) and the demonstrative 様
# (この / その / あの / どの + ように, "like this").  A purpose ように instead
# takes a verb / ない / な / adjective in front of it, which is every other
# token, so the exclusion is written as the marked cases.  See _SLOT_SPECS.
_YOU_NI_RESEMBLANCE_BEFORE = {
    "surface_in": ("の", "この", "その", "あの", "どの"),
}

_NOT_AFTER = {
    "masu-polite-verb": _TE_CONNECTIVE,
    "mashita-polite-past-verb": _TE_CONNECTIVE,
    "masendeshita-polite-past-negative-verb": _TE_CONNECTIVE,
    # 〜ように is the purpose marker only when what precedes it is a predicate;
    # after 名詞 + の (or a demonstrative) it is the resemblance marker, which
    # has its own entry (you-ni-resemblance).
    "you-ni-purpose": _YOU_NI_RESEMBLANCE_BEFORE,
}

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

    Note the order in the body: the split comes first, and parentheses are
    stripped out of each *segment*.  Doing it the other way round -- strip,
    then split -- reads better and is wrong.  "お + V ます-stem + する (or ご +
    Sino-Japanese noun + する)" would then yield the bare literal する, which
    matches every する verb in the language: a 「〜する」 row appeared on 91.5%
    of a 400-page corpus.  Those parentheticals hold the *rest of the
    construction*, not a qualifier, so peeling them off frees a literal that
    is not distinctive.  If the order is ever changed, every entry that gains
    a fragment has to be reviewed -- scripts/measure_grammar_panel.py prints
    the share of pages each row reaches, before and after.
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
    (
        re.compile(r"[Pp]lain form|V plain|[Vv]erb-?plain"),
        [{"surface_not": ("の",)}],
        True,
    ),
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
            {
                "type": "tokens",
                "conds": prefix + [{"surface": t["surface"]} for t in tokens],
            },
            {
                "type": "tokens",
                "conds": prefix + [{"lemma": {t["lemma"]}} for t in tokens],
            },
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


# Literals already covered by the hand-written N5 rules above.  A data rule
# whose fragment is covered by one of these is redundant -- e.g. the data
# 〜てください entry vs. the hand-written one.
_HAND_WRITTEN_LITERALS = [
    spec["re"].pattern
    for rule in _N5_RULES
    for spec in rule["patterns"]
    if spec["type"] == "regex"
] + [
    # Fixed surfaces a hand-written *token* spec requires, for the same reason:
    # whether 〜てください is spelled as a regex or as 動詞 + て + ください, it
    # is still one rule, and the data entry it covers has to stay covered --
    # deriving the list from the regex specs alone silently un-suppressed it and
    # the panel showed 〜てください and 〜ください side by side.
    #
    # Listed by hand rather than derived from every hand-written token spec,
    # because covering is per-*reading*: the hand-written 〜から is the reason
    # reading, and letting its から count as covered also swallowed 〜てから
    # ("after doing"), a different reading of the same particle.  Surfaces of
    # one or two kana cannot over-reach here anyway -- they are too short to be
    # a fragment of their own.
    "ください",
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


def _load_ko():
    """
    Curated Korean glosses, keyed by data entry id (ko.json next to the
    level files) -- the panel's 한국어 display language, mirroring zh.json.
    """
    path = os.path.join(_DATA_DIR, "ko.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


_KO_BY_ID = _load_ko()


def _load_zh_enrichment():
    """
    Chinese wording for the vendored rows' enrichment fields, keyed by
    data entry id: ``{"formation", "notes", "examples": {<jp>: <zh>}}``.

    ``zh.json`` only covers the description, so the jkindrix rows' 接续 /
    注意点 / 例句译文 stayed English and the panel printed English under a
    Chinese heading.  The wording lives here rather than in the level files
    for the same reason ``zh.json`` does: those files stay verbatim copies
    of the upstream data.  Example translations are keyed by the Japanese
    sentence, so an index shift can never mis-assign one.
    """
    path = os.path.join(_DATA_DIR, "zh_enrichment.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


_ZH_ENRICH = _load_zh_enrichment()


def _load_ko_enrichment():
    """
    Korean wording for the data rows' enrichment fields, keyed by data
    entry id: ``{"formation", "notes", "examples": {<jp>: <ko>}}``.

    Same shape and rationale as zh_enrichment.json above: ``ko.json`` only
    covers the description, so the 接续 / 注意点 / 例句译文 fields stayed
    English or Chinese and the panel printed the wrong language under a
    Korean heading.  Example translations are keyed by the Japanese
    sentence, so an index shift can never mis-assign one.
    """
    path = os.path.join(_DATA_DIR, "ko_enrichment.json")
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


def _curated_reference(item, specs=None):
    """
    First curated example that carries a translation, for the panel's
    folded reference block (书内例句 + 译文).  Returns None when the entry
    has no usable example; the panel then hides the block.

    When ``specs`` are given, an example this rule actually matches is
    preferred over mere document order.  The panel highlights the point
    inside the quoted sentence, and the derivation only guarantees a spec
    matches the entry's examples *joined*, so first-in-order left the
    highlight missing on ~10% of entries (じゃない chosen for a row whose
    example says ではありません).  Every example of an entry carries a
    translation, so preferring one costs nothing.
    """
    usable = []
    for ex in item.get("examples") or []:
        jp = (ex.get("japanese") or "").strip()
        zh = (ex.get("chinese") or "").strip()
        en = (ex.get("english") or "").strip()
        if jp and (zh or en):
            usable.append({"japanese": jp, "chinese": zh, "english": en})
    if not usable:
        return None
    if specs:
        matcher = {"patterns": specs}
        for ref in usable:
            if _match_spans(matcher, _tokens_for(ref["japanese"]), ref["japanese"]):
                return ref
    return usable[0]


def _make_data_rule(
    level, item, idx, skipped, specs=None, shown=None, kind="construction"
):
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
    entry_id = item.get("id") or ""
    enrichment = _ZH_ENRICH.get(entry_id) or {}
    ko_enrichment = _KO_ENRICH.get(entry_id) or {}
    reference = _curated_reference(item, None if skipped else specs)
    return {
        "key": "ds_" + _slug(item.get("id") or f"{level}-{idx}"),
        "pattern": name,
        "descriptive": item.get("pattern") or "",
        "level": level,
        "meaning": item.get("meaning_en") or "",
        "meaning_zh": _ZH_BY_ID.get(entry_id, ""),
        "meaning_ko": _KO_BY_ID.get(entry_id, ""),
        # The long explanation, register and cross-links the curated rows
        # carry but the panel never showed.  `related` holds upstream entry
        # ids; _related_entries resolves them to keys/names below.  The
        # entry id itself is kept because the rule key is slugged, so it is
        # no longer recoverable from the key alone.
        "entry_id": entry_id,
        "meaning_detailed": item.get("meaning_detailed") or "",
        "formality": item.get("formality") or "",
        "related": list(item.get("related") or []),
        # n3.json lists an alternate spelling beside the main pattern
        # (〜つつある / 〜つつ); recorded but unused by the matcher.
        "pattern_alt": item.get("pattern_alt") or "",
        "formation": item.get("formation") or "",
        # Chinese wording for the 接续 / 注意点 / 例句译文 of the English-only
        # vendored rows (see _load_zh_enrichment); empty for the merged
        # material rows, whose own fields are already Chinese.
        "formation_zh": enrichment.get("formation") or "",
        "formation_notes_zh": enrichment.get("notes") or "",
        "reference_zh": (
            (enrichment.get("examples") or {}).get(reference["japanese"], "")
            if reference
            else ""
        ),
        # Korean wording for the same three fields of every data row (see
        # _load_ko_enrichment); empty only where an entry has no reference
        # sentence to translate.
        "formation_ko": ko_enrichment.get("formation") or "",
        "formation_notes_ko": ko_enrichment.get("notes") or "",
        "reference_ko": (
            (ko_enrichment.get("examples") or {}).get(reference["japanese"], "")
            if reference
            else ""
        ),
        # jkindrix entries carry notes as a list, materials as a string;
        # normalize to one string so the panel can drop it in verbatim.
        "formation_notes": _notes_text(item.get("formation_notes")),
        "reference": reference,
        "examples": [e["japanese"] for e in item.get("examples") or []],
        # "patterns" is what the matcher reads, so an empty list is how a
        # skipped rule stays silent.  "derived" keeps whatever the derivation
        # did produce, for introspection: scripts/screen_grammar_library.py
        # reads it to re-derive which entries look like vocabulary, and the
        # tests check it to prove a skipped entry was skipped on purpose
        # rather than because its pattern yielded nothing.
        "patterns": [] if skipped else (specs or []),
        "derived": list(specs or []),
        "kind": kind,
        "skipped": skipped,
    }


# Fragments whose negative counterpart an entry teaches as part of the same
# point but does not name in its pattern: 〜ことがある lists only the
# affirmative, yet its own examples and notes cover ことがない ("have never
# done"), and before the hand-written N5 〜たことがあります row was folded
# into this entry, that row's loose たこと regex still reported the negative.
# Each alias derives a second spec for the same rule, with the same left
# context, validated against the entry's own examples like any fragment.
_FRAGMENT_ALIASES = {
    "ことがある": ["ことがない"],
}

# Per-entry replacement for the fragments the generic derivation reads out of
# the pattern.  Used when the pattern's shape yields pieces that each validate
# against the entry's own examples yet are not constructions on their own:
# "Noun + の + こと + ながら" yields こと and ながら, and both halves then fire
# on bare こと / 〜ながら sentences (〜ことだ, ということ, 〜ながら), while the
# construction is the contiguous ことながら.
_FRAGMENT_OVERRIDES = {
    "koto-nagara": ["ことながら"],
}


def _load_level(level):
    """
    Load one level's JSON file and turn each entry into a rule dict.

    Match specs are derived from the descriptive pattern text and validated
    against the entry's own examples:

      * an entry listed in ``_SLOT_SPECS`` whose pattern names a form rather
        than a literal string is matched on that form;
      * a gapped construction (から ... にかけて) becomes a two-anchor spec;
      * a reviewed entry listed in ``_NOT_AFTER`` additionally names a token
        that must not sit immediately in front of its match;
      * otherwise every contentful fragment becomes its own spec -- a literal
        substring regex when it occurs in the examples, else a lemma-based
        token spec so inflected forms still match (ようにする / ようにします);
      * entries that yield nothing matchable -- pure conjugation tables such
        as "Verb → potential form", fragments already covered by the
        hand-written N5 rules, or fragments too vague to match reliably --
        are marked ``skipped`` and never fire, so they cannot cause false
        positives and never appear in the panel.  The reviewed ids in
        ``_VOCAB_IDS`` (their row would only restate the word popup) and
        ``_CONCEPT_IDS`` (a class of forms, with nothing in the sentence to
        point at) are skipped the same way, but only *after* the derivation
        runs, so ``derived`` still records what it produced.

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
        joined = "".join(e["japanese"] for e in item.get("examples") or [])
        example_tokens = _tokens_for(joined)

        # See _SLOT_SPECS: a pattern that names a form instead of a literal is
        # matched on the form.  Validated against the entry's own examples, so
        # a spec the library has outgrown degrades to the old derivation
        # rather than misreporting.
        slot = _SLOT_SPECS.get(item.get("id") or "")
        if slot is not None and not all(
            _spec_matches(spec, example_tokens, joined) for spec in slot[1]
        ):
            slot = None

        fragments = _jp_fragments(pattern)
        if not fragments and slot is None and not _CONJUGATION_TABLE.search(pattern):
            # Nothing in the pattern is matchable and there is no reviewed
            # spec for it, so the only text left is the formation field.  For
            # an entry that describes a form that field is a list of examples
            # of it, and reading it titles the entry after one of them -- a
            # last resort, correct only when the field names the construction
            # itself.  (Conjugation tables such as "Verb → potential form"
            # describe no literal construction at all; their formation text
            # yields bare endings, so they are never matched loosely either.)
            fragments = _jp_fragments(item.get("formation") or "")

        # See _FRAGMENT_OVERRIDES: a reviewed entry whose derivation pieces are
        # not constructions on their own keeps the contiguous literal instead.
        fragments = _FRAGMENT_OVERRIDES.get(item.get("id") or "", fragments)

        if slot is not None:
            shown = [slot[0]]
            specs = list(slot[1])
        else:
            gap = _gap_spec(fragments, pattern, joined, example_tokens)
            if gap is not None:
                specs = [gap]
                shown = [fragments[0], fragments[-1]]
            else:
                # Every fragment becomes a matcher.  The kana-length rule picks
                # the *title*, never which forms the entry teaches: a pattern
                # that spells out alternatives means all of them, and iterating
                # `specific or fragments` instead dropped the shorter one
                # whenever the longer one qualified -- くない next to
                # くありません, だろう next to でしょう, けど, どこ / いつ --
                # which is why those forms were never reported at all.
                specific = [f for f in fragments if _is_specific(f)]
                specs, matched = [], []
                for fragment in fragments:
                    if _covered_by_hand_written(fragment):
                        continue
                    # What the description says comes before this fragment
                    # ("Verb-て + もいい" -> the fragment needs a て in front).
                    prefix = _context_cond(pattern[: pattern.find(fragment)], fragment)
                    spec = _fragment_spec(fragment, joined, example_tokens, prefix)
                    if spec is not None:
                        specs.append(spec)
                        matched.append(fragment)
                        # See _FRAGMENT_ALIASES: the negative counterpart of
                        # the fragment is the same grammar point and reports
                        # on the same row; it never extends the title.
                        for alias in _FRAGMENT_ALIASES.get(fragment, []):
                            alias_spec = _fragment_spec(
                                alias, joined, example_tokens, prefix
                            )
                            if alias_spec is not None:
                                specs.append(alias_spec)
                if not specs:
                    rules.append(_make_data_rule(level, item, idx, skipped=True))
                    continue
                # Title from the distinctive fragments; an entry whose
                # fragments are all short kana is titled with all of them (のに).
                shown = [f for f in matched if f in specific] or matched

        # See _NOT_AFTER: a reviewed entry whose match must not follow a
        # particular token (ます after a て-form).  Added to the derived spec
        # rather than written out, so the derivation stays the source of what
        # the entry matches and only the left context is reviewed.
        excluded = _NOT_AFTER.get(item.get("id") or "")
        if excluded is not None:
            specs = [
                dict(spec, not_after=excluded) if spec.get("type") == "tokens" else spec
                for spec in specs
            ]

        # Which row an entry lands in is a property of the entry, not of how
        # its pattern happens to be spelled: only the reviewed function-word
        # ids are folded together, every other entry reports on its own.
        if item.get("id") in _FUNCTION_WORD_IDS:
            kind = "basic"
        elif item.get("id") in _PARTICLE_IDS:
            kind = "particle"
        else:
            kind = "construction"

        # A reviewed entry that carries no row of its own is skipped *after*
        # its spec is derived, so "derived" still records what the derivation
        # produced: _VOCAB_IDS (the word popup already answers it) and
        # _CONCEPT_IDS (a class of forms, not a construction).
        skipped = (
            item.get("id") in _VOCAB_IDS
            or item.get("id") in _CONCEPT_IDS
            or item.get("id") in _DUPLICATE_IDS
        )
        rules.append(
            _make_data_rule(
                level, item, idx, skipped=skipped, specs=specs, shown=shown, kind=kind
            )
        )
    return rules


def _load_all():
    "Load all data-driven levels, tallying how many rules were auto-skipped."
    # The data rules are derived from Sudachi tokenization of the library
    # examples.  Base installs ship without the sudachi extra, and this
    # module is imported at app startup (read routes), so derive nothing
    # there -- the reading screen falls back to the hand-written rules /
    # regex library when the engine cannot run.
    try:
        _get_tokenizer()
    except ImportError:
        return []
    rules = []
    for lvl in _ALL_LEVELS:
        rules.extend(_load_level(lvl))
    return rules


# The data rules are built on first use, not at import.  _load_all()
# tokenizes every data row's examples, which builds the Sudachi dictionary
# (~33MB of private memory plus a 217MB mmap of system.dic).  This module is
# imported at app start-up (lute/read/routes.py), so building it here made
# every process pay that cost even with no Japanese books at all.
_rules_lock = threading.RLock()
_data_rules_cache = None
_all_rules_cache = None


def _get_data_rules():
    """
    The data-driven rules, built on first use and memoised for the
    process lifetime.  _load_all() needs the Sudachi dictionary, so this
    is deliberately not computed at import.
    """
    global _data_rules_cache  # pylint: disable=global-statement
    with _rules_lock:
        if _data_rules_cache is None:
            _data_rules_cache = _load_all()
        return _data_rules_cache


def _get_all_rules():
    "Hand-written plus data-driven rules, built and memoised on first use."
    global _all_rules_cache  # pylint: disable=global-statement
    with _rules_lock:
        if _all_rules_cache is None:
            _all_rules_cache = _N5_RULES + _CONSTRUCTION_RULES + _get_data_rules()
        return _all_rules_cache


# The curated rows' `related` field names upstream entry ids, which differ
# from the rules' own `key` (slugged), so following a cross-link needs both
# an id -> rule lookup and a display name.  Built once, lazily, beside the
# rules it is derived from.
_related_index_cache = None


def _get_related_index():
    "{entry id: {key, name, level}} over the curated rows, memoised."
    global _related_index_cache  # pylint: disable=global-statement
    with _rules_lock:
        if _related_index_cache is None:
            index = {}
            for rule in _get_data_rules():
                entry_id = rule.get("entry_id")
                if entry_id and entry_id not in index:
                    index[entry_id] = {
                        "key": rule["key"],
                        "name": rule["pattern"],
                        "level": rule["level"],
                    }
            _related_index_cache = index
        return _related_index_cache


def _related_entries(rule):
    """
    The rule's `related` ids resolved to {id, key, name, level}, in order.

    Ids the corpus does not define are dropped rather than rendered as a
    dangling link: the curated files are hand-maintained, and a typo there
    must not put a dead anchor on the page.
    """
    index = _get_related_index()
    out = []
    for entry_id in rule.get("related") or []:
        info = index.get(entry_id)
        if info:
            out.append({"id": entry_id, **info})
    return out


# Korean descriptions for the hand-written rules (data rules get theirs
# from ko.json by entry id) -- the panel's 한국어 display language.
_KO_HAND = {
    "ga_but": '역접을 나타내는 が("하지만").',
    "ga_imasu_arimasu": "존재를 나타내는 がいます(사람·동물)/があります(사물).",
    "kara_reason": '이유를 나타내는 から("~때문에").',
    "suki_kirai": "좋아함·싫어함을 나타내는 好きです/嫌いです(대상은 が).",
    "tai": '"~하고 싶다"의 소망을 나타내는 たい.',
    "de_place_means": "동작의 장소나 수단을 나타내는 で.",
    "te_iru": "진행이나 결과 상태를 나타내는 ている.",
    "te_kudasai": '"~해 주세요"의 부탁 てください.',
    "deshita": 'です의 과거형 でした("~이었습니다").',
    "de_wa_arimasen": '"~이 아닙니다"의 정중 부정 ではありません.',
    "te_wa_ikemasen": '금지를 나타내는 てはいけません("~해선 안 됩니다").',
    "te_mo_ii_desu": '허가를 나타내는 てもいいです("~해도 됩니다").',
    "to_and_with": '"~와/~하고"의 と.',
    "nakute_mo_ii_desu": '"~하지 않아도 됩니다"의 なくてもいいです.',
    "nakereba_narimasen": '의무를 나타내는 なければなりません/なくてはいけません("~해야 합니다").',
    "ni_time_destination": "시간·목적지·간접 대상을 나타내는 조사 に.",
    "mashou": '"~합시다"의 권유 ましょう.',
    "masen_ka": '"~하지 않으시겠습니까?"의 권유 ませんか.',
    "wo_object": "직접목적어를 나타내는 조사 を.",
}
for _hand_rule in _N5_RULES:
    _hand_rule["meaning_ko"] = _KO_HAND.get(_hand_rule["key"], "")

# Korean descriptions for the combined-construction rules.
_KO_HAND_CONSTRUCTION = {
    "a_tsu_b_tsu": 'AつBつ: 번갈아 일어나는 동작("~했다가 ~했다가").',
    "to_ii_to_ii": 'といい~といい/といわず~といわず: 어디에서나 예외 없이("~에서도 ~에서도").',
    "a_nari_b_nari": 'なり~なり: 선택지를 나열("~거나 ~거나").',
    "mo_ba_mo": 'も~ば~も: 공존하는 두 상태("~도 있으면 ~도 있다").',
    "ba_hodo": 'ば~ほど: 비례 증가("~하면 할수록").',
    "volitional_ni_mo_nai": '의지형+にも+같은 동사 부정: 하고 싶어도 할 수 없음("~하려 해도 ~할 수 없다").',
    "volitional_mai": '의지형+が/と+같은 동사+まい+が/と: 하든 안 하든("~으든 ~으든").',
    "nara_tomo_kaku": 'ならともかく: ~은 뭐라 할 수 있지만("~라면 모를까").',
}
for _hand_rule in _CONSTRUCTION_RULES:
    _hand_rule["meaning_ko"] = _KO_HAND_CONSTRUCTION.get(_hand_rule["key"], "")


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
    # ---- combined constructions (hand-written) ----
    "〜つ〜つ": "一会儿……一会儿……；交替进行（抜きつ抜かれつ）",
    "〜といい〜といい / 〜といわず〜といわず": "无论是……还是……（到处都、无一例外）",
    "〜なり〜なり": "或是……或是……（列举选项任选其一）",
    "〜も〜ば〜も": "既有……又有……（も～ば～も 并存）",
    "〜ば〜ほど": "越……越……",
    "〜ようにも〜ない": "即使想……也不能……",
    "〜（よ）うが〜まいが / 〜（よ）うと〜まいと": "无论……还是不……（都一样）",
    "〜ならともかく": "如果是……还说得过去，可偏偏……",
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
_KO_PARTICLE = "기초 N5 조사 감지"
_KO_BASICS = "기초 정중체·지시어·의문사（です・ます・これ 등）가 극히 자주 나타나 요약만 표시"

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
    for rule in _get_all_rules():
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
    if display_lang == "ko":
        return rule.get("meaning_ko") or rule["meaning"]
    if display_lang == "zh":
        # Curated Chinese gloss shipped with the data beats both the legacy
        # hand-written table and the English meaning.
        if rule.get("meaning_zh"):
            return rule["meaning_zh"]
        return _ZH_DESC.get(rule["pattern"], rule["meaning"])
    return rule["meaning"]


# Script probes for _in_display_language below.
_CJK_CHAR = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_HANGUL_CHAR = re.compile(r"[\uac00-\ud7af]")
_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")


def _in_display_language(text, display_lang):
    """
    Whether a raw source string belongs in the panel's display language.

    The data carries one string per field, not one per language: the
    vendored jkindrix rows hold English prose ("Not yet: まだ + V ていない
    / ...") while the merged study-material rows hold Chinese, and only
    the *description* is translated (zh.json / ko.json).  Passing the
    jkindrix text straight through is what put English into the Chinese
    panel (the formation line, the folded reference example's translation
    and the notes block), so an enrichment field is shown only when its
    script matches what the reader asked for.  The missing Chinese
    wording belongs in the data and is being added there; nothing is
    machine-translated here.

    An empty string is never displayable.  English is the source language
    of every field, so nothing is filtered in that mode.
    """
    text = (text or "").strip()
    if not text:
        return False
    if display_lang == "zh":
        # Japanese notation («名词+の/动词「た」形+あとで») passes, while
        # "Noun + に難くない" does not: the Latin run of three or more
        # letters is the tell, since the notation itself is kana/kanji.
        return bool(_CJK_CHAR.search(text)) and not _LATIN_WORD.search(text)
    if display_lang == "ko":
        # The Korean wording from ko_enrichment.json wins in _enrichment
        # before this filter runs, so the Hangul probe here only guards the
        # fallback path -- a field with no Korean translation yet stays
        # hidden rather than showing English under a Korean heading.
        return bool(_HANGUL_CHAR.search(text))
    return True


def _reference_text(reference, rule, display_lang):
    "The curated example's translation for the display language, or ''."
    if display_lang == "zh":
        # Merged rows carry their own Chinese example; the vendored rows get
        # it from zh_enrichment.json.  No Chinese means no block, rather
        # than the English sentence under a Chinese heading.
        return reference.get("chinese") or rule.get("reference_zh") or ""
    if display_lang == "ko":
        # Data rows carry a Korean translation from ko_enrichment.json;
        # hand-written rules have none, so their block stays hidden rather
        # than showing Japanese/English under a Korean heading.
        return rule.get("reference_ko") or ""
    return reference.get("english") or reference.get("chinese") or ""


def _enrichment(rule, field, display_lang):
    """
    One enrichment field (``formation`` / ``formation_notes``) for the
    panel's display language.

    The Chinese wording shipped in zh_enrichment.json wins when present;
    likewise the Korean wording from ko_enrichment.json for ``ko``.
    Otherwise a field is shown only when its script already matches the
    display language -- the filter that kept "Not yet: まだ + V ていない"
    out of the Chinese panel while the wording was still being written.
    English is the source language of every field, so it needs neither.
    """
    if display_lang == "zh":
        zh = (rule.get(f"{field}_zh") or "").strip()
        if zh:
            return zh
    if display_lang == "ko":
        ko = (rule.get(f"{field}_ko") or "").strip()
        if ko:
            return ko
    text = (rule.get(field) or "").strip()
    return text if _in_display_language(text, display_lang) else ""


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
        for rule in _get_all_rules():
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
                # Register (neutral / formal / casual) -- shipped as the raw
                # upstream token; the front end maps it to the display
                # language, so no translation table is needed here.
                formality = (rule.get("formality") or "").strip()
                if formality:
                    entry["formality"] = formality
                # The long explanation is English prose with no zh/ko
                # translation in the data, so it is emitted only for an
                # English panel -- a zh/ko panel must never show English.
                detailed = (rule.get("meaning_detailed") or "").strip()
                if detailed and display_lang == "en":
                    entry["meaning_detailed"] = detailed
                # Cross-links to the other points this one is contrasted
                # with.  The names are Japanese fragments, so they are shown
                # in every display language.
                related = _related_entries(rule)
                if related:
                    entry["related"] = related
                # Panel-enrichment fields (formation line, folded reference
                # block); only emitted when the underlying entry carries
                # them *in the display language* -- hand-written rules and
                # old data carry neither, and the untranslated English
                # halves of the vendored rows stay out of a zh/ko panel.
                formation = _enrichment(rule, "formation", display_lang)
                if formation:
                    entry["formation"] = formation
                notes = _enrichment(rule, "formation_notes", display_lang)
                if notes:
                    entry["notes"] = notes
                reference = rule.get("reference")
                if reference:
                    text = _reference_text(reference, rule, display_lang)
                    if text:
                        japanese = reference["japanese"]
                        entry["reference"] = {
                            "japanese": japanese,
                            "text": text,
                        }
                        # The same offsets the page examples carry, computed
                        # by this rule's own matcher against its curated
                        # sentence, so the folded block highlights the point
                        # it illustrates instead of quoting it bare.
                        spans = _match_spans(rule, _tokens_for(japanese), japanese)
                        if spans:
                            entry["reference"]["matches"] = [
                                {"start": s, "end": e} for s, e in spans
                            ]
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
                "desc": (
                    _KO_BASICS
                    if display_lang == "ko"
                    else _ZH_BASICS
                    if display_lang == "zh"
                    else "Copula, demonstratives and question words detected"
                ),
                "examples": _aggregate_examples(basic_examples),
            }
        )

    if particle_examples:
        matched.append(
            {
                "key": "basic_particles",
                "name": "Particles: " + "・".join(_aggregate_symbols(particle_examples)),
                "level": "N5",
                "desc": (
                    _KO_PARTICLE
                    if display_lang == "ko"
                    else _ZH_PARTICLE
                    if display_lang == "zh"
                    else "Basic N5 particles detected"
                ),
                "examples": _aggregate_examples(particle_examples),
            }
        )
    return _merge_same_name(matched)


# ---- library export --------------------------------------------------
#
# The /grammar page lists the whole corpus at once, where the panel lists
# only what the current page happens to contain.  Same display fields as
# the panel entries (minus the page-specific match offsets), so the page
# can reuse the panel's markup and CSS.


def _library_entry(rule, display_lang):
    """
    One curated rule as a library row, display-ready.

    Every rule is included -- even the ones the matcher skips -- because a
    skipped row is still a grammar point a reader may want to look up; the
    ``skipped`` flag lets the page mark it as reference-only instead of
    hiding it.  ``examples`` stays plain strings: the library has no page
    text to highlight, so the match offsets the panel computes are
    irrelevant here.
    """
    entry = {
        "key": rule["key"],
        "name": rule["pattern"],
        "level": rule["level"],
        "desc": _desc(rule, display_lang),
        "examples": [s for s in rule.get("examples") or [] if s],
        "skipped": bool(rule.get("skipped")),
    }
    formation = _enrichment(rule, "formation", display_lang)
    if formation:
        entry["formation"] = formation
    notes = _enrichment(rule, "formation_notes", display_lang)
    if notes:
        entry["notes"] = notes
    reference = rule.get("reference")
    if reference:
        text = _reference_text(reference, rule, display_lang)
        if text:
            entry["reference"] = {"japanese": reference["japanese"], "text": text}
    formality = (rule.get("formality") or "").strip()
    if formality:
        entry["formality"] = formality
    detailed = (rule.get("meaning_detailed") or "").strip()
    if detailed and display_lang == "en":
        entry["meaning_detailed"] = detailed
    related = _related_entries(rule)
    if related:
        entry["related"] = related
    return entry


def library_entries(display_lang="en"):
    """
    The whole Japanese grammar library, in level order (N5 -> N1).

    Built on first use like the rules themselves, so an app start-up with
    no Japanese books does not pay for the Sudachi dictionary.  Within a
    level the hand-written basics come first: they are listed before the
    curated rows in the source order, and the sort is stable.
    """
    rules = _N5_RULES + _CONSTRUCTION_RULES + _get_data_rules()
    entries = [_library_entry(rule, display_lang) for rule in rules]
    ordered = sorted(
        enumerate(entries),
        key=lambda pair: (_LEVEL_RANK.get(pair[1]["level"], 9), pair[0]),
    )
    return [entry for _, entry in ordered]
