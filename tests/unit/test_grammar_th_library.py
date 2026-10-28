"""
The Thai grammar library: shape, and each matcher rule against its own
examples.

The corpus is data, so the tests are data checks: unique keys, a level
every row agrees on, descriptions in the display language, and -- the
useful one -- every row that claims a matcher actually fires on one of the
sentences the corpus stores for it.  A rule derived from the wrong literal
still renders a card, so only this check catches it.
"""

import json
import os
import re

import pytest

from lute.read.render import grammar_analysis_th as th
from lute.read.render.grammar_analysis_matcher import match_rule, make_rule

LEVELS = {"A1", "A2", "B1", "B2", "C1", "C2"}
TOPICS = {
    "noun-phrase",
    "pronoun",
    "verb",
    "adjective",
    "adverb",
    "preposition",
    "clause",
    "particle",
    "negation",
    "question",
    "quantity",
    "time",
    "speech",
}


def corpus():
    "The raw rows."
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "lute",
        "jlpt_data",
        "grammar_th.json",
    )
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_corpus_rows_are_well_formed():
    rows = corpus()
    assert len(rows) > 250, "the corpus should cover the whole outline"
    keys = [r["key"] for r in rows]
    assert len(keys) == len(set(keys)), "duplicate keys break the detail pages"
    for row in rows:
        assert row["level"] in LEVELS, row["key"]
        assert row["topic"] in TOPICS, row["key"]
        assert row["section"], row["key"]
        assert row["name"].strip(), row["key"]
        assert row["en"].strip(), row["key"]
        assert row["zh"].strip(), row["key"]
        assert len(row.get("examples") or []) <= 3, row["key"]


def test_every_example_carries_a_chinese_gloss():
    """
    Each example is "thai · roman — english ／ 中文".  The gloss is what the
    detail page puts on its own line, so a missing one shows up as a bare
    English sentence and the reader loses the translation.
    """
    bad = []
    for row in corpus():
        for ex in row.get("examples") or []:
            parts = ex.split(" ／ ")
            if len(parts) != 2 or not parts[1].strip():
                bad.append(f'{row["key"]}: {ex[:40]}')
    assert bad == [], f"examples without a Chinese gloss: {bad}"


def test_library_entries_use_the_display_language():
    zh = th.library_entries("zh")
    en = th.library_entries("en")
    corpus_keys = {row["key"] for row in corpus()}
    hand_keys = {r["key"] for r in th._TH_RULES}  # pylint: disable=protected-access
    # The library is the corpus plus the hand-written rules the corpus does
    # not cover -- never a silent extra card, and never a missing one.
    extra = {entry["key"] for entry in en} - corpus_keys
    assert extra == hand_keys - corpus_keys
    assert corpus_keys <= {entry["key"] for entry in en}
    assert len(zh) == len(en) == len(corpus()) + len(extra)
    assert zh[0]["desc"] != en[0]["desc"]
    assert any("\u4e00" <= ch <= "\u9fff" for ch in zh[0]["desc"])
    assert all(entry["level"] in LEVELS for entry in zh)


def test_levels_are_ordered_easiest_first():
    order = [entry["level"] for entry in th.library_entries("en")]
    ranks = [["A1", "A2", "B1", "B2", "C1", "C2"].index(level) for level in order]
    assert ranks == sorted(ranks)


def _token_offsets(sentence):
    "Character offsets where tokens start and end, from the engine's tokens."
    starts, ends = set(), set()
    for token in th._tokens_for(sentence):  # pylint: disable=protected-access
        starts.add(token["idx"])
        ends.add(token["idx"] + len(token["surface"]))
    return starts, ends


def _occurs_at_boundary(sentence, literal, mode):
    """
    Whether the sentence uses the literal in a way the row may match.

    A row's literal is only evidence of the construction when it sits where
    the row says it may: as a whole word, at the start of one, or opening
    one that continues past it ("prefix" -- นัก- inside นักเรียน).
    """
    starts, ends = _token_offsets(sentence)
    for hit in re.finditer(re.escape(literal), sentence):
        if hit.start() not in starts:
            continue
        if mode == "start" or hit.end() in ends:
            return True
        if mode == "prefix" and hit.end() not in ends:
            return True
    return False


def test_each_row_fires_on_an_example_that_contains_its_literal():
    """
    A row whose literal is visibly present in one of the sentences stored
    beside it must be reported on that sentence.  Guards the loader: a
    rule built from the wrong field, or dropped as a duplicate key, would
    leave the library card in place but never fire it.

    "Present" respects the row's boundary mode: a literal that only turns
    up inside a longer word (ขา inside ขาย) is not an occurrence, and the
    rule is right not to fire on it.
    """
    try:
        th._tokenizer()  # pylint: disable=protected-access
    except ImportError:
        pytest.skip("pythainlp is not installed")

    misses = []
    hand_keys = {r["key"] for r in th._TH_RULES}  # pylint: disable=protected-access
    for row in corpus():
        if row["key"] in hand_keys:
            # Fired by a hand-written rule instead of a corpus rule; the
            # engine tests cover those.
            continue
        if not row.get("focus") or not row.get("examples"):
            continue
        mode = row.get("boundary") or "token"
        sentences = [
            ex.split(" · ")[0]
            for ex in row["examples"]
            if any(
                _occurs_at_boundary(ex.split(" · ")[0], literal, mode)
                for literal in row["focus"]
            )
        ]
        if not sentences:
            continue
        if not any(
            row["key"] in {c["key"] for c in th.analyze_thai(s, "en")}
            for s in sentences
        ):
            misses.append(row["key"])
    assert misses == [], f"literal in the text but no card: {misses}"


def test_unrelated_text_does_not_raise_the_noisy_rows():
    """
    Precision spot-checks for the literals that double as ordinary words:
    หนัก must not fire the นัก- prefix card, and a plain statement must
    not fire the passive or yes/no cards.
    """
    try:
        th._tokenizer()  # pylint: disable=protected-access
    except ImportError:
        pytest.skip("pythainlp is not installed")

    keys = {c["key"] for c in th.analyze_thai("ฝนตกหนักมากเมื่อคืนนี้", "en")}
    assert "th-prefix-nak" not in keys
    keys = {c["key"] for c in th.analyze_thai("เขาเป็นครูที่โรงเรียน", "en")}
    assert "th_passive" not in keys
    assert "th_yesno" not in keys


# ---- word-boundary mode ------------------------------------------------
#
# The corpus regex runs over raw text, so a short literal also turns up
# inside longer words.  Every rule now says where its literal may sit:
# whole token for particles (ขา, สิ, ที), token start for prefixes and
# compound heads (ความ, ฤดู, เท่า), and a stricter "prefix" -- start a
# token *and* run on into it -- for morphemes that never stand alone
# (นัก-, ผู้-, ความ-, โรง-).


def test_boundary_values_are_known():
    "Only the documented modes, and only where the row has a literal."
    for row in corpus():
        assert row.get("boundary") in (None, "token", "start", "prefix"), row["key"]
        if row.get("boundary"):
            assert row.get("focus"), row["key"]


def test_particle_literals_do_not_match_inside_longer_words():
    """
    Each pair is the same characters: once as the particle, once buried in
    a longer word that merely contains it.  Only the first may fire.
    """
    try:
        th._tokenizer()  # pylint: disable=protected-access
    except ImportError:
        pytest.skip("pythainlp is not installed")

    buried = [
        ("ผมขายของที่ตลาด", "th-particle-khaa", "ขา inside ขาย"),
        ("สิบสองโมง", "th-imperative", "สิ inside สิบ"),
        ("ทีมนี้เก่งมาก", "th-times-count", "ที inside ทีม"),
        ("ของฉันอยู่ไหน", "th-request-thing", "ขอ inside ของ"),
    ]
    for sentence, key, why in buried:
        keys = {c["key"] for c in th.analyze_thai(sentence, "en")}
        assert key not in keys, f"{why} fired {key} on {sentence}"

    assert "th-particle-khaa" in {c["key"] for c in th.analyze_thai("คุณแม่ขา", "en")}
    assert "th-imperative" in {c["key"] for c in th.analyze_thai("นั่งลงหน่อย", "en")}


def test_prefix_rows_still_match_the_first_element_of_a_compound():
    """
    The boundary must not cost recall where the literal legitimately opens
    a longer word: ความรัก, นักเรียน and ฤดูฝน are all one token.
    """
    try:
        th._tokenizer()  # pylint: disable=protected-access
    except ImportError:
        pytest.skip("pythainlp is not installed")

    cases = [
        ("ความรักเป็นสิ่งสวยงาม", "th-prefix-khwam"),
        ("นักเรียนคนนี้ขยัน", "th-prefix-nak"),
        ("ฤดูฝนฝนตกทุกวัน", "th-seasons"),
        ("เขาเก่งเท่ากับพี่ชาย", "th-equal-tao-gup"),
    ]
    for sentence, key in cases:
        keys = {c["key"] for c in th.analyze_thai(sentence, "en")}
        assert key in keys, f"{key} lost on {sentence}"


def test_agent_prefix_ignores_the_same_spelling_standing_alone():
    """
    บ่อยนัก "not at all" is one word นัก on its own; นักเรียน is the prefix
    inside a longer one.  Found in production: a real page fired the
    นัก- card on บ่อยนัก, where the prefix reading is the wrong one.
    """
    try:
        th._tokenizer()  # pylint: disable=protected-access
    except ImportError:
        pytest.skip("pythainlp is not installed")

    for sentence in ("ผมไม่ค่อยไปงานเลี้ยงบ่อยนัก", "ไม่ค่อยดีนัก"):
        keys = {c["key"] for c in th.analyze_thai(sentence, "en")}
        assert "th-prefix-nak" not in keys, sentence
    assert "th-prefix-nak" in {
        c["key"] for c in th.analyze_thai("เขาเป็นนักร้องที่มีชื่อเสียง", "en")
    }


def test_boundary_is_opt_in_for_regex_specs():
    """
    Other engines share the matcher and pass no boundary, so their raw
    substring behaviour has to stay exactly as it was.
    """
    rule = make_rule(
        "probe", "probe", "A1", "", {"type": "regex", "re": re.compile("ขา")}
    )
    spans = match_rule(rule, th._tokens_for("ผมขายของ"), "ผมขายของ")
    assert spans == [(2, 4)], spans


def test_a_short_sentence_stays_readable():
    """
    The panel lists every hit, so a plain sentence must not explode into a
    wall of cards -- the corpus has ~290 rows and most of them carry a
    literal that occurs somewhere.
    """
    try:
        th._tokenizer()  # pylint: disable=protected-access
    except ImportError:
        pytest.skip("pythainlp is not installed")

    cards = th.analyze_thai(
        "ผมไม่ไปทำงานเพราะฝนตกหนัก ฉันกำลังอ่านหนังสือที่ซื้อไว้เมื่อวานนี้", "en"
    )
    assert 3 <= len(cards) <= 15, [c["key"] for c in cards]


def test_every_library_card_shows_an_example():
    """
    A card with no sentence teaches the name and nothing else.  The corpus
    shipped fifty-five such rows until they were written; this keeps the
    next row from arriving bare.
    """
    blank = [
        entry["key"] for entry in th.library_entries("zh") if not entry["examples"]
    ]
    assert not blank, f"cards with no example: {blank}"


# ---- panel reference block ---------------------------------------------
#
# The panel's examples are the page's own sentences, so the translation a
# reader sees comes from the folded reference block.  Thai builds it by
# parsing the corpus line rather than storing the glosses twice.


def test_reference_line_is_split_into_its_three_parts():
    "The reference sentence is the Thai text alone, not the whole line."
    reference = th._reference_from_line(  # pylint: disable=protected-access
        "ผมไปกับเพื่อน · pǒm bpai gùp pêu-un — I went with a friend. ／ 我和朋友一起去了"
    )
    assert reference == {
        "sentence": "ผมไปกับเพื่อน",
        "en": "I went with a friend.",
        "zh": "我和朋友一起去了",
    }


def test_every_panel_rule_carries_a_translated_reference():
    """
    Corpus rows cite their first example; hand-written rules cite the
    golden one.  Every card now has one of the two, so none may go without.
    """
    missing = []
    for rule in (
        th._hand_rules() + th._get_corpus_rules()
    ):  # pylint: disable=protected-access
        reference = rule.get("reference") or {}
        if not (
            reference.get("sentence") and reference.get("en") and reference.get("zh")
        ):
            missing.append(rule["key"])
    assert missing == [], f"cards with no translated example: {missing}"


def test_panel_shows_the_gloss_in_the_display_language():
    "Chinese panel gets the Chinese gloss, English panel the English one."
    zh = {c["key"]: c for c in th.analyze_thai("ผมชอบเขา", "zh")}
    en = {c["key"]: c for c in th.analyze_thai("ผมชอบเขา", "en")}
    assert zh["th_pronouns"]["reference"]["text"] == "我喜欢他"
    assert en["th_pronouns"]["reference"]["text"] == "I like him."
