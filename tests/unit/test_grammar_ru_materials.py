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


@needs_library
@needs_materials
def test_duplicate_rule_list_matches_the_overlap_audit():
    """
    The panel's silence list must equal what the overlap audit derives.

    A merged row whose generalisation reports the same span as another rule
    would print one construction twice, so the row is dropped from the panel
    (it stays in the library).  Pinning the list to the audit -- rather than
    to a hand-maintained guess -- means growing the library re-derives it, and
    a stale entry fails here instead of silently suppressing a row that is no
    longer a duplicate.
    """
    from scripts import audit_grammar_ru_overlaps as audit

    _by_key, cands = audit.candidates()
    derived = {key for key, _decision in cands}
    assert derived == set(ru._DUPLICATE_RULE_IDS)


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


# ---- the inflected spec family -----------------------------------------
#
# A literal spec is pinned to one surface form, so a row about "между +
# instrumental" stated as "между обедом" could never fire on "между
# друзьями".  The engine therefore also derives a case-only spec that keeps
# function words literal.  These tests pin that behaviour down, because two
# separate mistakes in the first attempt made the whole data layer unusable
# and neither showed up as a failure -- only as a panel that fired on
# everything.


def _seq_lists(match):
    """
    Every spec list inside a match shape, recursing into any_of.

    Returns the seq lists as-is (each a list of leaf specs); a gapped
    left/right shape contributes its two anchor lists joined, since both
    sides are subject to the same "must be specific" argument.
    """
    if "any_of" in match:
        out = []
        for alt in match["any_of"]:
            out.extend(_seq_lists(alt))
        return out
    if "seq" in match:
        return [list(match["seq"])]
    if "left" in match or "right" in match:
        return [list(match.get("left") or []) + list(match.get("right") or [])]
    return []


def _leaf_specs(match):
    "Every leaf spec in a (possibly any_of-nested) match shape."
    return [spec for seq in _seq_lists(match) for spec in seq]


def test_no_spec_carries_an_empty_morph_subset():
    """
    An empty ``{"morph": {}}`` is a subset of everything, so it matches any
    token.  A lowercase feature key silently produced exactly that, and the
    panel jumped from a median of 6 to 17 entries per page.
    """
    for rule in ru._get_all_rules():
        for spec in _leaf_specs(rule["match"]):
            assert spec.get("morph") != {}, (
                f"{rule['key']} carries an empty morph subset -- it fires on "
                "every token"
            )


def test_inflected_specs_keep_a_literal_anchor():
    """
    A case-only spec with nothing literal to pin it fires on every token of
    that case.  Rows whose focus is all content words must stay literal --
    that mistake took the panel from a median of 8 to 17 entries per page.
    """
    for rule in ru._get_data_rules():
        for seq in _seq_lists(rule["match"]):
            has_morph = any("morph" in s for s in seq)
            has_literal = any(
                "surface_in" in s or "lemma_in" in s for s in seq
            )
            if has_morph:
                assert has_literal, (
                    f"{rule['key']} has a bare case spec with no literal "
                    "anchor -- it fires on any token of that case"
                )


def test_generalised_specs_turn_on_case_only():
    """
    Only a *case* may be generalised away from the row's stated form.

    A preposition row ("между + instrumental") is the reason the inflected
    family exists, and case is what it turns on.  Generalising a verb-side
    feature instead yields specs like ``["не", {Tense: "pres"}]`` -- "не +
    any present word" -- which is not a construction but every negated
    present verb in the page.  Those rows fired on nearly the whole example
    corpus, so a non-``Case`` morph subset here is a bug, not a design choice.
    """
    for rule in ru._get_data_rules():
        for spec in _leaf_specs(rule["match"]):
            morph = spec.get("morph")
            if morph:
                assert set(morph) == {"Case"}, (
                    f"{rule['key']} generalises {sorted(morph)} -- only a case "
                    "is a construction; a verb-side feature is not"
                )


def test_compound_focus_words_are_split_for_the_tokenizer():
    """
    "из-за" is tokenised into two tokens, so a single spec for it can never
    match.  The derived spec must offer the parts.
    """
    for rule in ru._get_data_rules():
        for spec in _leaf_specs(rule["match"]):
            for surface in spec.get("surface_in", []):
                assert "-" not in surface, (
                    f"{rule['key']} keeps a hyphenated literal {surface!r} "
                    "that the tokenizer can never produce"
                )


def _row_by_prefix(prefix):
    "The one data rule whose key starts with prefix, or an assertion error."
    keys = [r["key"] for r in ru._get_data_rules() if r["key"].startswith(prefix)]
    assert keys, f"no row with key prefix {prefix!r} in the library"
    for rule in ru._get_all_rules():
        if rule["key"] == keys[0]:
            return rule
    raise AssertionError(f"derived rule {keys[0]} is missing")


def test_inflected_spec_generalises_across_noun_forms():
    """
    The point of the family: one construction, many noun forms.  "между +
    instrumental" must fire on a plural and on a pronoun, not only on the
    noun form the book happened to use.
    """
    rule = _row_by_prefix("rgm_между__")
    for sentence in ("между обедом", "между друзьями", "между нами"):
        text = ru._strip_stress(sentence)
        assert ru.match_rule(rule, ru._tokens_for(text), text), (
            f"{rule['key']} does not fire on {sentence!r}"
        )


def test_literal_specs_still_fire_on_the_book_phrasing():
    "The narrow literal family is kept alongside the generalised one."
    rule = _row_by_prefix("rgm_между__")
    text = ru._strip_stress("между обедом")
    assert ru.match_rule(rule, ru._tokens_for(text), text)


def test_generalisation_does_not_spill_to_other_constructions():
    """
    The counterpart guard: generalising must not make one construction fire
    on a different one.  "между + instrumental" is instrumental; a genitive
    phrase must not be caught by it.
    """
    rule = _row_by_prefix("rgm_между__")
    for sentence in ("между друзей", "около дома", "перед обедом"):
        text = ru._strip_stress(sentence)
        assert not ru.match_rule(rule, ru._tokens_for(text), text), (
            f"{rule['key']} fires on {sentence!r} -- over-generalised"
        )


# ---- the panel's folded reference block ---------------------------------
#
# The panel shows a rule's own curated example with its translation, so a
# reader sees a translated sentence instead of only the description.  The
# Japanese engine had this and Russian did not; the shared matcher now
# carries it for any engine whose rules ship one.


def test_russian_rules_carry_a_reference_with_a_gloss():
    "Every rule that has a translated example exposes one for the panel."
    rules = ru._get_all_rules()
    with_ref = [r for r in rules if r.get("reference")]
    assert with_ref, "no rule carries a reference block"
    # The data rows all have one; the hand-written rules that have a golden
    # example have one too.  Only a rule with no example anywhere lacks it.
    for rule in with_ref:
        reference = rule["reference"]
        assert (reference.get("sentence") or "").strip(), rule["key"]
        assert any(
            (reference.get(f) or "").strip() for f in ("zh", "en", "ko")
        ), f"{rule['key']} has a reference with no translation"


def test_reference_gloss_follows_the_display_language():
    "The gloss is resolved in the panel's language, never back-filled."
    from lute.read.render.grammar_analysis_matcher import reference_gloss

    reference = {
        "sentence": "Деньги не могут тебя сделать счастливым.",
        "zh": "金钱并不能使你幸福。",
        "en": "Money can't make you happy.",
        "ko": "돈이 너를 행복하게 만들 수는 없다.",
    }
    assert reference_gloss(reference, "zh").startswith("金钱")
    assert reference_gloss(reference, "en").startswith("Money")
    assert reference_gloss(reference, "ko").startswith("돈이")


def test_reference_gloss_stays_empty_when_the_language_is_missing():
    "A missing translation is empty, not the other language's text."
    from lute.read.render.grammar_analysis_matcher import reference_gloss

    zh_only = {"sentence": "Весна. Ночь. Тишина.", "zh": "春天。夜。一片寂静。"}
    assert reference_gloss(zh_only, "zh") == "春天。夜。一片寂静。"
    assert reference_gloss(zh_only, "en") == ""
    assert reference_gloss(zh_only, "ko") == ""


def test_panel_emits_the_reference_block():
    "The panel entry carries the reference so the front end can fold it."
    sentence = "Карина на работе несчастна, потому что у неё есть кошка."
    for entry in ru.analyze_russian(sentence, display_lang="zh"):
        if entry["key"] == "ru_u_menya":
            reference = entry.get("reference")
            assert reference, "panel entry carries no reference block"
            assert reference["sentence"].strip()
            assert reference["text"].strip()
            return
    raise AssertionError("ru_u_menya did not fire on the probe sentence")
