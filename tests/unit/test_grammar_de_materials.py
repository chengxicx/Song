"""Guards for the German grammar-point library and its derived matchers.

Two layers, mirroring the French / Russian materials tests:

* the **library** (``lute/jlpt_data/grammar_de.json``) must be complete and
  key-unique, and every row must carry the fields the panel renders;
* every **derived rule** must fire on at least one of its own examples, run
  through the same matcher the panel uses -- so a mis-derived focus can never
  ship a matcher that marks nothing.

The **golden set** (``grammar_de_golden.json``) is the parallel store for the
hand-written rules: the test re-runs every curated sentence through its own
rule, so the /grammar page can never show a point whose matcher does not
actually recognise it.

The materials themselves come from a local book extraction that is not in
the repository, so the tests skip when the artefacts are absent, and the
provenance check only inspects the ``source`` tag, which must stay an
opaque token: the extraction source is deliberately not recorded in code.
"""

import json
import os
import re

import pytest

pytest.importorskip("spacy")
pytest.importorskip("de_core_news_sm")

from lute.read.render import grammar_analysis_de as de  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
LIBRARY = os.path.join(_ROOT, "lute", "jlpt_data", "grammar_de.json")
GOLDEN = os.path.join(_ROOT, "lute", "jlpt_data", "grammar_de_golden.json")
MATERIALS = os.path.join(_ROOT, "scripts", "grammar_materials_de", "units.json")

needs_library = pytest.mark.skipif(
    not os.path.exists(LIBRARY), reason="no de grammar library yet"
)
needs_golden = pytest.mark.skipif(
    not os.path.exists(GOLDEN), reason="no de grammar golden set yet"
)
needs_materials = pytest.mark.skipif(
    not os.path.exists(MATERIALS), reason="no de grammar materials yet"
)


def _library():
    with open(LIBRARY, encoding="utf-8") as fh:
        return json.load(fh)


@needs_library
def test_library_rows_have_the_render_fields():
    rows = _library()
    assert rows, "library is empty"
    for row in rows:
        assert row.get("name"), row
        assert row.get("level"), row
        assert (row.get("meaning") or "").strip(), row
        assert (row.get("zh") or "").strip(), f"no zh gloss: {row.get('key')}"
        assert row.get("examples"), f"no examples: {row.get('key')}"


@needs_library
def test_library_keys_are_unique():
    keys = [r["key"] for r in _library()]
    assert len(keys) == len(set(keys))


@needs_library
def test_library_keys_do_not_shadow_hand_written_rules():
    written = {r["key"] for r in de._DE_RULES}
    assert not written & {r["key"] for r in _library()}


@needs_materials
def test_material_sources_are_opaque_tags():
    "The extraction source must never be recorded as a readable name."
    with open(MATERIALS, encoding="utf-8") as fh:
        rows = json.load(fh)
    for row in rows:
        src = (row.get("source") or "").strip()
        assert src, f"missing source tag: {row.get('pattern')}"
        assert re.fullmatch(
            r"[a-z0-9][a-z0-9_-]*", src
        ), f"source tag is not an opaque token: {src!r}"


@needs_golden
def test_golden_sentences_fire_on_their_own_rule():
    """
    Every curated sentence must be recognised by the rule it is filed under.

    This is the invariant that keeps the /grammar page honest: a point whose
    own example does not match its own matcher is a point the reader can
    click through to and find broken.
    """
    golden = de._golden_examples()
    rules = {r["key"]: r for r in de._DE_RULES}
    missing = [k for k in golden if k not in rules]
    assert not missing, f"golden entries with no rule: {missing}"
    for key, examples in golden.items():
        spec = rules[key]["match"]
        for example in examples:
            text = de._norm_text(example["de"])
            assert de.match_rule({"match": spec}, de._tokens_for(text), text), (
                f"golden sentence does not fire on {key}: {example['de']}"
            )


@needs_golden
def test_every_hand_written_rule_has_golden_examples():
    "A rule with no example would render an empty card on the library page."
    golden = de._golden_examples()
    for rule in de._DE_RULES:
        assert golden.get(rule["key"]), f"no golden examples for {rule['key']}"


@needs_golden
def test_golden_examples_carry_every_translation():
    """
    The golden set is shown under a display-language heading.

    A missing translation would print an English line under a Chinese or
    Korean one, so every example must carry all three.
    """
    for key, examples in de._golden_examples().items():
        for example in examples:
            for lang in ("de", "en", "zh", "ko"):
                assert (example.get(lang) or "").strip(), (
                    f"{key}: example missing {lang}: {example.get('de')}"
                )


@needs_library
def test_data_rules_fire_on_their_own_examples():
    "Each derived matcher must mark at least one of the row's own sentences."
    assert de._get_data_rules(), "no data rules were derived"
    for rule in de._get_data_rules():
        examples = de._DE_RULE_EXAMPLES.get(rule["key"], [])
        assert examples, f"rule {rule['key']} carries no examples"
        fired = False
        for sentence in examples:
            text = de._norm_text(sentence)
            if de.match_rule(rule, de._tokens_for(text), text):
                fired = True
                break
        assert fired, f"rule {rule['key']} fires on none of its examples"


@needs_library
def test_data_rows_pair_their_zh_gloss():
    "Rows merged with parallel zh lines expose example dicts with the gloss."
    entries = {e["key"]: e for e in de.library_entries("zh")}
    paired = [
        e
        for e in entries.values()
        if e["examples"]
        and isinstance(e["examples"][0], dict)
        and e["examples"][0].get("text")
    ]
    assert paired, "no data rows reached library_entries"
    with_gloss = [e for e in paired if any(r.get("tr") for r in e["examples"])]
    assert with_gloss, "data rows carry examples but no zh gloss"


def test_focus_gap_and_alternative_vocabulary():
    "The '...' gap and 'a/b' alternatives derive matchers that actually fire."
    # gap: "in ... die" spans the noun between the preposition and article.
    gap = de._focus_specs(["in", "...", "die"])
    assert gap and "left" in gap[0] and gap[0]["max_gap"] == 4
    text = de._norm_text("Ich fahre morgen in die Berge.")
    assert de.match_rule({"match": gap[0]}, de._tokens_for(text), text)
    # slash alternatives: "kein/keine" matches both surfaces.
    alt = de._focus_specs(["kein/keine"])
    assert len(alt) == 1  # surface-only: no lemma variant for alternatives
    for sentence in ("Ich habe kein Geld.", "Ich habe keine Zeit."):
        text = de._norm_text(sentence)
        assert de.match_rule({"match": alt[0]}, de._tokens_for(text), text)


def test_short_bare_focus_never_becomes_a_rule():
    """
    A one-word focus under the length floor yields no spec at all.

    German capitalises every noun and every sentence start, so a bare "zu" or
    "die" would fire on a large share of the corpus and teach nothing.
    """
    for word in ("zu", "um", "je", "die", "der", "es"):
        assert de._focus_specs([word]) == [], f"{word!r} should not stand alone"


@needs_library
def test_library_entries_are_ordered_by_cefr():
    "The page lists A1 -> C2; an out-of-order level would sort to the end."
    order = ["A1", "A2", "B1", "B2", "C1", "C2"]
    seen = [e["level"] for e in de.library_entries("en")]
    ranks = [order.index(level) for level in seen if level in order]
    assert ranks == sorted(ranks)


@needs_library
def test_library_covers_the_whole_cefr_scale():
    "Every hand-written rule carries a CEFR level the library can sort."
    for rule in de._DE_RULES:
        assert rule["level"] in ("A1", "A2", "B1", "B2", "C1", "C2"), rule["key"]


def test_hand_written_rules_are_the_baseline():
    "The hand-written rules are the baseline; the data layer only adds to them."
    assert len(de._DE_RULES) >= 30
    assert len(de._get_all_rules()) >= len(de._DE_RULES)
