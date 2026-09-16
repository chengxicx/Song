"""
N5 Japanese grammar point detection.

Uses SudachiPy to tokenize each sentence with full POS and a
lemma (dictionary form), then a token-aware matcher recognises the
grammar constructions.  Using lemmas means conjugated forms (e.g.
食べている / 食べていました) match the same rule.

Data source (meanings / examples): OpenJLPT  (CC BY-SA 4.0)
https://github.com/evanclan/OpenJLPT  -- data/json/grammar/n5.json
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
    "Split a page of text into sentences on Japanese punctuation."
    parts = re.split(r"(?<=[。．！？!?])", text.replace("\n", " "))
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
    if "lemma" in cond and token["lemma"] not in cond["lemma"]:
        return False
    if "pos1" in cond and token["pos"][0] != cond["pos1"]:
        return False
    if "pos2" in cond and token["pos"][1] != cond["pos2"]:
        return False
    return True


def _try_match(conds, tokens, i, j):
    "Backtracking match of the condition sequence against the token list."
    if i == len(conds):
        return True
    cond = conds[i]
    if cond.get("optional"):
        if j < len(tokens) and _match_condition(cond, tokens[j]) and _try_match(conds, tokens, i + 1, j + 1):
            return True
        return _try_match(conds, tokens, i + 1, j)
    if j >= len(tokens):
        return False
    if not _match_condition(cond, tokens[j]):
        return False
    return _try_match(conds, tokens, i + 1, j + 1)


def _match_tokens(conds, tokens):
    "True if the condition sequence appears anywhere in the token list."
    for start in range(len(tokens)):
        if _try_match(conds, tokens, 0, start):
            return True
    return False


def _matches(rule, tokens, sentence_text):
    "True if any pattern-spec of the rule matches this sentence."
    for spec in rule["patterns"]:
        if spec["type"] == "regex":
            if spec["re"].search(sentence_text):
                return True
        else:  # tokens
            if _match_tokens(spec["conds"], tokens):
                return True
    return False


# ---- rules -----------------------------------------------------------

_P = lambda *ks: {k: v for k, v in zip(("pos1", "pos2", "pos3"), ks)}
# short helpers: fixed surface, fixed lemma(set), a plain particle token
_SURF = lambda s: {"surface": s}
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
        "〜から（reason）",
        "because; since",
        ["雨が降っているから、出かけません。", "眠いから、早く寝ます。"],
        [
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
            {"type": "tokens", "conds": [_SURF("て"), _LEMMA("いる")]},
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


# ---- data-driven rules (N4-N1) ----------------------------------------

# Levels loaded from JSON data files under lute/jlpt_data/grammar/.  N5 keeps
# its hand-written, debugged rules in _N5_RULES above.
_ALL_LEVELS = ["N4", "N3", "N2", "N1"]

# Where the grammar JSON lives, relative to this module:
#   lute/read/render/grammar_analysis_ja.py  ->  lute/jlpt_data/grammar/
_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "jlpt_data", "grammar")
)

# Simple kana -> romaji map used only to build readable, stable rule keys.
_ROMAJI = {}
for _src, _dst in [
    ("あいうえお", "a i u e o"),
    ("かきくけこ", "ka ki ku ke ko"),
    ("さしすせそ", "sa shi su se so"),
    ("たちつてと", "ta chi tsu te to"),
    ("なにぬねの", "na ni nu ne no"),
    ("はひふへほ", "ha hi fu he ho"),
    ("まみむめも", "ma mi mu me mo"),
    ("やゆよ", "ya yu yo"),
    ("らりるれろ", "ra ri ru re ro"),
    ("わをん", "wa wo n"),
    ("がぎぐげご", "ga gi gu ge go"),
    ("ざじずぜぞ", "za ji zu ze zo"),
    ("だぢづでど", "da ji du de do"),
    ("ばびぶべぼ", "ba bi bu be bo"),
    ("ぱぴぷぺぽ", "pa pi pu pe po"),
    ("ゃゅょっ", "xya xyu xyo xtsu"),
    ("アイウエオ", "a i u e o"),
    ("カキクケコ", "ka ki ku ke ko"),
    ("サシスセソ", "sa shi su se so"),
    ("タチツテト", "ta chi tsu te to"),
    ("ナニヌネノ", "na ni nu ne no"),
    ("ハヒフヘホ", "ha hi fu he ho"),
    ("マミムメモ", "ma mi mu me mo"),
    ("ヤユヨ", "ya yu yo"),
    ("ラリルレロ", "ra ri ru re ro"),
    ("ワヲン", "wa wo n"),
    ("ガギグゲゴ", "ga gi gu ge go"),
    ("ザジズゼゾ", "za ji zu ze zo"),
    ("ダヂヅデド", "da ji du de do"),
    ("バビブベボ", "ba bi bu be bo"),
    ("パピプペポ", "pa pi pu pe po"),
    ("ャュョッ", "xya xyu xyo xtsu"),
]:
    for _k, _v in zip(_src, _dst.split()):
        _ROMAJI[_k] = _v


def _extract_core(pattern):
    "Strip leading 〜/～ and truncate at parenthetical qualifiers; keep the core literal."
    s = pattern.strip().lstrip("〜～")
    s = re.split(r"[（）()]", s)[0].strip()
    return s


def _data_key(pattern, level, idx):
    "Stable, unique ASCII key derived from the pattern text."
    core = _extract_core(pattern) or ""
    slug = "".join(_ROMAJI.get(ch, "u%x" % ord(ch)) for ch in core)
    slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
    if not slug:
        slug = "base"
    return f"cl_{slug}_{level.lower()}_{idx}"


def _make_data_rule(level, item, idx, skipped):
    example_sentences = [e["ja"] for e in item["examples"]]
    return {
        "key": _data_key(item["pattern"], level, idx),
        "pattern": item["pattern"],
        "level": level,
        "meaning": item["meaning"],
        "examples": example_sentences,
        "patterns": [],
        "kind": "construction",
        "skipped": skipped,
    }


def _load_level(level):
    """
    Load one level's JSON file and turn each entry into a rule dict.

    Match specs are auto-derived from the pattern text:
      * a literal-substring regex when the core string matches the rule's own
        examples (the common, low-noise case);
      * otherwise a lemma-based token spec, which lets conjugated forms match
        (e.g. ようにする matching ようにします);
      * rules whose pattern is too vague to match reliably (single bare kanji,
        or constructions that do not appear in their own examples) are marked
        ``skipped`` so they never fire and never cause false positives.
    """
    path = os.path.join(_DATA_DIR, f"{level.lower()}.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        entries = json.load(fh)
    rules = []
    for idx, item in enumerate(entries):
        core = _extract_core(item["pattern"])
        joined = "".join(e["ja"] for e in item["examples"])
        # A single bare character (中 / 方 / 時 / 間 / 日 ...) is far too
        # generic to match on directly - skip it to avoid false positives.
        if len(core) < 2:
            rules.append(_make_data_rule(level, item, idx, skipped=True))
            continue
        if re.search(re.escape(core), joined):
            # Literal substring match: precise, matches the rule's own examples.
            rule = _make_data_rule(level, item, idx, skipped=False)
            rule["patterns"] = [{"type": "regex", "re": re.compile(re.escape(core))}]
            rules.append(rule)
            continue
        # Try a lemma-based token spec so inflected forms still match.
        conds = [{"lemma": {t["lemma"]}} for t in _tokens_for(core)] if core else []
        if conds and _match_tokens(conds, _tokens_for(joined)):
            rule = _make_data_rule(level, item, idx, skipped=False)
            rule["patterns"] = [{"type": "tokens", "conds": conds}]
            rules.append(rule)
            continue
        # Too generic / unreliable: skip (kept out of the panel entirely).
        rules.append(_make_data_rule(level, item, idx, skipped=True))
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
    "〜から（reason）": "因为；由于",
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


def _desc(rule, display_lang):
    "Description for a rule in the requested display language."
    if display_lang == "zh":
        return _ZH_DESC.get(rule["pattern"], rule["meaning"])
    return rule["meaning"]


def analyze_japanese(page_text, display_lang="en"):
    """
    Analyze a page of Japanese text for grammar points.

    Constructive grammar rules become individual entries; basic particle
    rules are folded into one trailing "Particles" entry so the panel
    isn't dominated by を/に/で/と hits.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence": ...}]}
    Rules appearing multiple times are merged; examples are deduped.
    """
    sentences = _split_sentences(page_text)
    matched = []
    particle_examples = {}  # particle key -> [sentences]
    for sentence in sentences:
        if not sentence:
            continue
        tokens = _tokens_for(sentence)
        for rule in _ALL_RULES:
            if not _matches(rule, tokens, sentence):
                continue
            if rule.get("kind") == "particle":
                ex = particle_examples.setdefault(rule["key"], [])
                if sentence not in ex:
                    ex.append(sentence)
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
            if sentence not in [ex["sentence"] for ex in entry["examples"]]:
                entry["examples"].append({"sentence": sentence})

    if particle_examples:
        symbols = [
            _PARTICLE_SYMBOLS.get(rule["key"], rule["key"])
            for rule in _ALL_RULES
            if rule["key"] in particle_examples
        ]
        shown = []
        for ex_list in particle_examples.values():
            for s in ex_list:
                if len(shown) >= 6:
                    break
                if s not in shown:
                    shown.append(s)
            if len(shown) >= 6:
                break
        matched.append(
            {
                "key": "basic_particles",
                "name": "Particles: " + "・".join(symbols),
                "level": "N5",
                "desc": _ZH_PARTICLE if display_lang == "zh" else "Basic N5 particles detected",
                "examples": [{"sentence": s} for s in shown],
            }
        )
    return matched