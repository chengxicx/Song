"""Guards for the Japanese grammar golden set.

The Japanese hand-written rules report constructions the curated library
already carries, so their golden set is *derived* from those rows by
``scripts/generate_grammar_ja_golden.py`` rather than written by hand.  The
invariants are the same as the ones the Spanish / French / German / Russian
materials tests enforce for their golden sets:

* every sentence in the set is recognised by the rule it is filed under,
  run through the same matcher the panel uses -- so a point the reader can
  click through to never turns out to match nothing;
* every hand-written rule is covered, and no golden key names a rule that
  does not exist.

Two more guards exist because the set is generated:

* a sentence may be filed under one rule only, since a hit for
  〜もいいです is not an illustration of 〜てはいけません even though both
  fire on it;
* the per-sentence translations come from the enrichment tables, so a
  Korean or Chinese panel must find a gloss on most sentences -- the check
  is a floor, not a coverage assertion, and it fails loudly if the
  enrichment files stop matching the sentences they translate.
"""

import json
import os

import pytest

from lute.read.render import grammar_analysis_ja as ja  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
GOLDEN = os.path.join(_ROOT, "lute", "jlpt_data", "grammar_ja_golden.json")

pytest.importorskip("sudachipy")

needs_golden = pytest.mark.skipif(
    not os.path.exists(GOLDEN), reason="no ja grammar golden set yet"
)


def _rules():
    return {rule["key"]: rule for rule in ja._N5_RULES + ja._CONSTRUCTION_RULES}


@needs_golden
def test_golden_keys_name_real_hand_written_rules():
    rules = _rules()
    golden = ja._golden_examples()
    unknown = sorted(set(golden) - set(rules))
    assert not unknown, f"golden entries with no rule: {unknown}"


@needs_golden
def test_every_hand_written_rule_has_a_golden_example():
    "A rule with no golden sentence is a rule no test ever exercises."
    golden = ja._golden_examples()
    missing = sorted(set(_rules()) - set(golden))
    assert not missing, f"hand-written rules with no golden example: {missing}"


@needs_golden
def test_golden_sentences_fire_on_their_own_rule():
    """
    Every curated sentence must be recognised by the rule it is filed under.

    This is the invariant that keeps the /grammar page honest: a point
    whose own example does not match its own matcher is a point the reader
    can click through to and find broken.
    """
    golden = ja._golden_examples()
    for key, examples in golden.items():
        rule = _rules()[key]
        for example in examples:
            text = example["japanese"]
            assert ja._match_spans(  # pylint: disable=protected-access
                rule, ja._tokens_for(text), text
            ), f"golden sentence does not fire on {key}: {text}"


@needs_golden
def test_golden_sentences_are_not_filed_under_two_rules():
    """
    One sentence illustrates one point.

    ``〜もいいです`` and ``〜てはいけません`` both fire on 撮ってもいいですか,
    but only one of them is what the sentence is *about*; the generator
    keeps each sentence for a single rule and the test keeps it that way.
    """
    golden = ja._golden_examples()
    seen = {}
    for key, examples in golden.items():
        for example in examples:
            text = example["japanese"]
            assert text not in seen, f"{text} filed under {seen[text]} and {key}"
            seen[text] = key


@needs_golden
def test_golden_sentences_carry_a_display_gloss():
    """
    The Chinese and Korean panels read a gloss under the example.

    ``zh_enrichment.json`` / ``ko_enrichment.json`` translate the curated
    rows per sentence; a golden sentence that reaches neither has to come
    from a row neither file covers, so a small remainder is expected but a
    sudden collapse means the enrichment keys drifted from the library.
    """
    golden = ja._golden_examples()
    sentences = [e for examples in golden.values() for e in examples]
    assert sentences, "golden set is empty"
    with_zh = sum(1 for e in sentences if (e.get("chinese") or "").strip())
    with_ko = sum(1 for e in sentences if (e.get("ko") or "").strip())
    assert with_zh == len(sentences), "golden sentences without a Chinese gloss"
    # ko_enrichment.json covers the vendored (English) rows, which is about
    # half the library; the merged material rows are Chinese-only there.
    assert (
        with_ko >= len(sentences) * 0.4
    ), f"only {with_ko}/{len(sentences)} golden sentences carry a Korean gloss"


@needs_golden
def test_golden_file_matches_the_generator_output():
    """
    The file is generated, so it has to stay what the generator produces.

    Guards against an edit that a regeneration would silently undo.
    """
    from scripts.generate_grammar_ja_golden import collect

    with open(GOLDEN, encoding="utf-8") as fh:
        on_disk = json.load(fh)
    generated, _missing = collect()
    assert on_disk == generated, (
        "grammar_ja_golden.json differs from the generator output; "
        "re-run python -m scripts.generate_grammar_ja_golden --write"
    )
