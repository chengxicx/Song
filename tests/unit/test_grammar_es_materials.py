"""Guards for the Spanish grammar-point library and its derived matchers.

Two layers, mirroring the French / German / Russian materials tests:

* the **library** (``lute/jlpt_data/grammar_es.json``) must be complete and
  key-unique, and every row must carry the fields the panel renders;
* every **derived rule** must fire on at least one of its own examples, run
  through the same matcher the panel uses -- so a mis-derived focus can never
  ship a matcher that marks nothing.

The **golden set** (``grammar_es_golden.json``) is the parallel store for the
hand-written rules: the test re-runs every curated sentence through its own
rule, so the /grammar page can never show a point whose matcher does not
actually recognise it.

The materials themselves were extracted from a local book, so the tests skip
when the artefacts are absent, and the provenance check only inspects the
``source`` tag, which must stay an opaque token: the extraction source is
deliberately not recorded in code.
"""

import json
import os
import re

import pytest

pytest.importorskip("spacy")
pytest.importorskip("es_core_news_md")

from lute.read.render import grammar_analysis_es as es  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
LIBRARY = os.path.join(_ROOT, "lute", "jlpt_data", "grammar_es.json")
GOLDEN = os.path.join(_ROOT, "lute", "jlpt_data", "grammar_es_golden.json")
MATERIALS_DIR = os.path.join(_ROOT, "scripts", "grammar_materials_es")
MATERIALS = [
    os.path.join(MATERIALS_DIR, name)
    for name in ("units.json", "units_ref.json", "units_prac.json")
]

needs_library = pytest.mark.skipif(
    not os.path.exists(LIBRARY), reason="no es grammar library yet"
)
needs_golden = pytest.mark.skipif(
    not os.path.exists(GOLDEN), reason="no es grammar golden set yet"
)
needs_materials = pytest.mark.skipif(
    not any(os.path.exists(p) for p in MATERIALS), reason="no es grammar materials yet"
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
        assert row.get("focus"), f"no focus: {row.get('key')}"


@needs_library
def test_library_keys_are_unique():
    keys = [r["key"] for r in _library()]
    assert len(keys) == len(set(keys))


@needs_library
def test_library_keys_do_not_shadow_hand_written_rules():
    written = {r["key"] for r in es._ES_RULES}  # pylint: disable=protected-access
    assert not written & {r["key"] for r in _library()}


@needs_library
def test_library_names_do_not_duplicate_hand_written_rules():
    """
    A merged row whose name folds onto a hand-written rule would show the
    same point twice, so the merge skips it -- the library must not carry
    one anyway.
    """
    norms = {
        es._norm_name(r["name"]) for r in es._ES_RULES
    }  # pylint: disable=protected-access
    for row in _library():
        assert es._norm_name(row["name"]) not in norms, row[
            "name"
        ]  # pylint: disable=protected-access


@needs_materials
def test_material_sources_are_opaque_tags():
    "The extraction source must never be recorded as a readable name."
    for path in MATERIALS:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh)
        for row in rows:
            src = (row.get("source") or "").strip()
            assert src, f"missing source tag: {row.get('pattern')} in {path}"
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
    golden = es._golden_examples()  # pylint: disable=protected-access
    rules = {r["key"]: r for r in es._ES_RULES}
    missing = [k for k in golden if k not in rules]
    assert not missing, f"golden entries with no rule: {missing}"
    for key, examples in golden.items():
        spec = rules[key]["match"]
        for example in examples:
            text = example["es"]
            assert es.match_rule(
                {"match": spec}, es._tokens_for(text), text
            ), f"golden sentence does not fire on {key}: {example['es']}"


@needs_library
def test_data_rules_fire_on_their_own_examples():
    "Each derived matcher must mark at least one of the row's own sentences."
    rules = es._get_data_rules()  # pylint: disable=protected-access
    assert rules, "no data rules were derived"
    for rule in rules:
        examples = [
            e["es"] if isinstance(e, dict) else e for e in rule.get("examples") or []
        ]
        assert examples, f"rule {rule['key']} carries no examples"
        assert any(
            es.match_rule(rule, es._tokens_for(text), text) for text in examples
        ), f"rule {rule['key']} fires on none of its examples"


@needs_library
def test_data_rows_pair_their_zh_gloss():
    "Rows merged with parallel zh lines expose examples carrying a translation."
    entries = {e["key"]: e for e in es.library_entries("zh")}
    data = [e for k, e in entries.items() if k.startswith("esm_")]
    assert data, "no data rows reached library_entries"
    with_gloss = [e for e in data if any(r.get("tr") for r in e["examples"])]
    assert with_gloss, "data rows carry examples but no zh gloss"


def test_focus_gap_and_alternative_vocabulary():
    "The '...' gap and 'a/b' alternatives derive matchers that actually fire."
    gap = es._focus_specs(["desde", "...", "hasta"])  # pylint: disable=protected-access
    assert gap and "left" in gap[0] and gap[0]["max_gap"] == 4
    text = "Anduvimos desde el amanecer hasta la noche."
    assert es.match_rule({"match": gap[0]}, es._tokens_for(text), text)
    specs = es._focus_specs(["quizá/quizás"])  # pylint: disable=protected-access
    assert specs
    for sentence in ("Quizá llueva mañana.", "Quizás venga hoy."):
        assert es.match_rule({"match": specs[0]}, es._tokens_for(sentence), sentence)


def test_token_class_vocabulary():
    """
    A "§" token names a word class the literal vocabulary cannot spell.

    ``estar + gerundio`` and the tense-adjacent rows depend on this, and an
    unknown class must match nothing rather than everything.
    """
    spec = es._focus_specs(["está/estoy", "§ger"])  # pylint: disable=protected-access
    assert spec, "§ger derived no spec"
    assert es.match_rule(
        {"match": spec[0]}, es._tokens_for("Estoy comiendo."), "Estoy comiendo."
    )
    assert not es.match_rule(
        {"match": spec[0]}, es._tokens_for("Estoy cansado."), "Estoy cansado."
    )
    # A typo'd class is a spec that matches nothing -- never a wildcard.
    empty = es._focus_specs(["verbo", "§nope"])  # pylint: disable=protected-access
    assert empty
    assert not es.match_rule(
        {"match": empty[0]}, es._tokens_for("verbo verbo"), "verbo verbo"
    )


def test_short_bare_focus_never_becomes_a_rule():
    """
    A one-word focus under the length floor yields no spec at all.

    Spanish function words (para, como, que, por, con, sin) appear on a large
    share of any page; a rule built on one of them teaches nothing.
    """
    for word in ("para", "como", "que", "por", "con", "sin", "de"):
        assert (
            es._focus_specs([word]) == []
        ), f"{word!r} should not stand alone"  # pylint: disable=protected-access


@needs_library
def test_library_entries_are_ordered_by_cefr():
    "The page lists A1 -> C2; an out-of-order level would sort to the end."
    order = ["A1", "A2", "B1", "B2", "C1", "C2"]
    seen = [e["level"] for e in es.library_entries("en")]
    ranks = [order.index(level) for level in seen if level in order]
    assert ranks == sorted(ranks)


@needs_library
def test_library_serves_the_data_rows_with_formation_and_notes():
    "The detail card renders formation / notes when the row carries them."
    entries = es.library_entries("en")
    with_notes = [e for e in entries if e.get("notes") and e.get("formation")]
    assert with_notes, "no library entry carries formation + notes"


def test_hand_written_rules_are_the_baseline():
    "The hand-written rules are the baseline; the data layer only adds to them."
    assert len(es._ES_RULES) >= 30
    assert len(es._get_all_rules()) >= len(es._ES_RULES)


@needs_library
def test_library_covers_the_advanced_bands():
    """
    A1..B2 alone is not a library: the merged rows must reach C1 (and C2).

    The advanced material batch exists to fill the empty top of the scale --
    without these rows the /grammar page stops at B2.
    """
    levels = {r["level"] for r in _library()}
    assert {"C1", "C2"} <= levels, f"advanced bands missing: {sorted(levels)}"


def test_focus_alternatives_match_inflected_forms():
    """
    A slash list carrying the base form also matches inflections via lemma.

    ``volver/vuelvo/vuelve + a + §inf`` has to fire on *Volvió a llamar*,
    where none of the listed surfaces appear -- the lemma variant is what
    makes a form list usable at all.
    """
    specs = es._focus_specs(  # pylint: disable=protected-access
        ["volver/vuelvo/vuelve", "a", "§inf"]
    )
    assert specs
    for text in ("Volvió a llamar.", "Volverá a intentarlo."):
        assert any(
            es.match_rule({"match": spec}, es._tokens_for(text), text) for spec in specs
        ), f"no spec fires on {text!r}"


def test_negative_command_class():
    """
    ``§subj2`` picks the one command shape the model tags reliably.

    es_core_news_md labels the imperative mood inconsistently (Escriba as
    Imp, Hable as Sub, Vengan as Ind), so the negative command -- no +
    present subjunctive, second person -- is the anchor that works.
    """
    specs = es._focus_specs(["no", "...", "§subj2"])  # pylint: disable=protected-access
    assert specs
    text = "No me lo digas."
    assert es.match_rule({"match": specs[0]}, es._tokens_for(text), text)
    # A plain negated statement must not look like a command.
    other = "No quiero ir."
    assert not es.match_rule({"match": specs[0]}, es._tokens_for(other), other)


@needs_library
def test_library_renders_without_the_tokeniser(monkeypatch):
    """
    No spaCy / no Spanish model: the data layer drops out, the page stays up.

    The library page renders on a base install, so an ImportError from the
    lazy tokenizer must not turn into a 500 -- it costs the merged rows only.
    """

    def _boom(_sentence):
        raise ImportError("no Spanish model")

    monkeypatch.setattr(es, "_tokens_for", _boom)
    monkeypatch.setattr(es, "_data_rules_cache", None)
    entries = es.library_entries("en")
    assert entries, "the hand-written corpus disappeared with the tokenizer"
    assert not [e for e in entries if e["key"].startswith("esm_")]
