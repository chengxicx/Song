"""Guards for the Russian grammar-point library and its derived matchers.

Two layers, mirroring the Korean materials tests:

* the **library** (``lute/jlpt_data/grammar_ru.json``) must be complete and
  key-unique, and every row must carry the fields the panel renders;
* every **derived rule** must fire on at least one of its own examples, run
  through the same matcher the panel uses -- so a mis-derived focus can never
  ship a matcher that marks nothing.

The materials themselves come from a local extraction that is not in the
repository, so the tests skip when the artefacts are absent, and the
provenance check only inspects the ``source`` tag, which must stay an opaque
token: the extraction source is deliberately not recorded in code.
"""

import json
import os
import re

import pytest

pytest.importorskip("pymorphy3")

from lute.read.render import grammar_analysis_ru as ru  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
LIBRARY = os.path.join(_ROOT, "lute", "jlpt_data", "grammar_ru.json")
MATERIALS = os.path.join(_ROOT, "scripts", "grammar_materials_ru", "units.json")

needs_library = pytest.mark.skipif(
    not os.path.exists(LIBRARY), reason="no ru grammar library yet"
)
needs_materials = pytest.mark.skipif(
    not os.path.exists(MATERIALS), reason="no ru grammar materials yet"
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
    written = {r["key"] for r in ru._RU_RULES}
    assert not written & {r["key"] for r in _library()}


@needs_materials
def test_material_sources_are_opaque_tags():
    "The extraction source must never be recorded as a readable name."
    with open(MATERIALS, encoding="utf-8") as fh:
        rows = json.load(fh)
    for row in rows:
        src = (row.get("source") or "").strip()
        assert src, f"missing source tag: {row.get('pattern')}"
        assert re.fullmatch(r"[a-z0-9][a-z0-9_-]*", src), (
            f"source tag is not an opaque token: {src!r}"
        )


@needs_materials
def test_example_lines_carry_no_combining_marks():
    "OCR keeps stress marks; they must be stripped before anything stores them."
    with open(MATERIALS, encoding="utf-8") as fh:
        rows = json.load(fh)
    for row in rows:
        for ex in row.get("examples") or []:
            text = ex.get("russian") if isinstance(ex, dict) else ex
            assert not re.search(r"[\u0300-\u036f]", text or ""), (
                f"combining accent left in: {text!r}"
            )


@needs_materials
def test_every_material_entry_is_mapped_once():
    "Audit invariant: nothing may fall outside covered/supplementable/missing."
    from scripts import audit_grammar_ru_coverage as audit

    res = audit.run(write=False)
    assert res["total"] == (
        res["covered"] + res["supplementable"] + res["missing"]
    ), res


@needs_library
def test_data_rules_fire_on_their_own_examples():
    "Each derived matcher must mark at least one of the row's own sentences."
    assert ru._get_data_rules(), "no data rules were derived"
    for rule in ru._get_data_rules():
        examples = ru._RU_RULE_EXAMPLES.get(rule["key"], [])
        assert examples, f"rule {rule['key']} carries no examples"
        fired = False
        for sentence in examples:
            text = ru._strip_stress(sentence)
            if ru.match_rule(rule, ru._tokens_for(text), text):
                fired = True
                break
        assert fired, f"rule {rule['key']} fires on none of its examples"


def test_hand_written_rules_are_the_baseline():
    "The hand-written rules are the baseline; the data layer only adds to them."
    assert len(ru._RU_RULES) >= 29
    assert len(ru._get_all_rules()) >= len(ru._RU_RULES)
