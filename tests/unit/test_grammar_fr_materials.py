"""Guards for the French grammar-point library and its derived matchers.

Two layers, mirroring the Russian materials tests:

* the **library** (``lute/jlpt_data/grammar_fr.json``) must be complete and
  key-unique, and every row must carry the fields the panel renders;
* every **derived rule** must fire on at least one of its own examples, run
  through the same matcher the panel uses -- so a mis-derived focus can never
  ship a matcher that marks nothing.

The materials themselves come from local book extractions that are not in
the repository, so the tests skip when the artefacts are absent, and the
provenance check only inspects the ``source`` tag, which must stay an
opaque token: the extraction sources are deliberately not recorded in code.
"""

import json
import os
import re

import pytest

pytest.importorskip("spacy")
pytest.importorskip("fr_core_news_sm")

from lute.read.render import grammar_analysis_fr as fr  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
LIBRARY = os.path.join(_ROOT, "lute", "jlpt_data", "grammar_fr.json")
MATERIALS = os.path.join(_ROOT, "scripts", "grammar_materials_fr", "units.json")

needs_library = pytest.mark.skipif(
    not os.path.exists(LIBRARY), reason="no fr grammar library yet"
)
needs_materials = pytest.mark.skipif(
    not os.path.exists(MATERIALS), reason="no fr grammar materials yet"
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
    written = {r["key"] for r in fr._FR_RULES}
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


def test_focus_gap_and_alternative_vocabulary():
    "The '...' gap and 'a/b' alternatives derive matchers that actually fire."
    # gap: "trop ... pour" fires across an adjective, not on a bare pair.
    gap = fr._focus_specs(["trop", "...", "pour"])
    assert gap and "left" in gap[0] and gap[0]["max_gap"] == 4
    text = fr._norm_text("C'est un bébé trop fragile pour aller à la crèche.")
    assert fr.match_rule({"match": gap[0]}, fr._tokens_for(text), text)
    # slash alternatives: "aucun/aucune" matches both surfaces.
    alt = fr._focus_specs(["aucun/aucune"])
    assert len(alt) == 1  # surface-only: no lemma variant for alternatives
    for sentence in ("Je n'ai aucun ami.", "Elle n'a aucune raison."):
        text = fr._norm_text(sentence)
        assert fr.match_rule({"match": alt[0]}, fr._tokens_for(text), text)
    # hyphenated words: fires on both spaCy tokenisations, never on the
    # verb phrase "peut être".
    hyphen = fr._focus_specs(["peut-être"])
    for sentence in (
        "Peut-être viendra-t-il demain.",
        "Peut-être qu'elle est à vendre.",
    ):
        text = fr._norm_text(sentence)
        assert any(
            fr.match_rule({"match": s}, fr._tokens_for(text), text) for s in hyphen
        )
    text = fr._norm_text("Il peut être là demain.")
    assert not any(
        fr.match_rule({"match": s}, fr._tokens_for(text), text) for s in hyphen
    )


@needs_library
def test_data_rules_fire_on_their_own_examples():
    "Each derived matcher must mark at least one of the row's own sentences."
    assert fr._get_data_rules(), "no data rules were derived"
    for rule in fr._get_data_rules():
        examples = fr._FR_RULE_EXAMPLES.get(rule["key"], [])
        assert examples, f"rule {rule['key']} carries no examples"
        fired = False
        for sentence in examples:
            text = fr._norm_text(sentence)
            if fr.match_rule(rule, fr._tokens_for(text), text):
                fired = True
                break
        assert fired, f"rule {rule['key']} fires on none of its examples"


@needs_library
def test_data_rows_pair_their_zh_gloss():
    "Rows merged with parallel zh lines expose example dicts with the gloss."
    entries = {e["key"]: e for e in fr.library_entries("zh")}
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


def test_hand_written_rules_are_the_baseline():
    "The hand-written rules are the baseline; the data layer only adds to them."
    assert len(fr._FR_RULES) >= 34
    assert len(fr._get_all_rules()) >= len(fr._FR_RULES)
