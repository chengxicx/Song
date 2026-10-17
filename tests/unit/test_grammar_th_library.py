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

import pytest

from lute.read.render import grammar_analysis_th as th

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
    assert len(zh) == len(corpus())
    assert zh[0]["desc"] != en[0]["desc"]
    assert any("\u4e00" <= ch <= "\u9fff" for ch in zh[0]["desc"])
    assert all(entry["level"] in LEVELS for entry in zh)


def test_levels_are_ordered_easiest_first():
    order = [entry["level"] for entry in th.library_entries("en")]
    ranks = [["A1", "A2", "B1", "B2", "C1", "C2"].index(level) for level in order]
    assert ranks == sorted(ranks)


def test_each_row_fires_on_an_example_that_contains_its_literal():
    """
    A row whose literal is visibly present in one of the sentences stored
    beside it must be reported on that sentence.  Guards the loader: a
    rule built from the wrong field, or dropped as a duplicate key, would
    leave the library card in place but never fire it.
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
        sentences = [
            ex.split(" · ")[0]
            for ex in row["examples"]
            if any(literal in ex.split(" · ")[0] for literal in row["focus"])
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
