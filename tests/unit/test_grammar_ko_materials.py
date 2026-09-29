"""
Tests for the Korean grammar materials pipeline.

Covers three things the "private-book" merge added:

  * the merged rows in lute/jlpt_data/grammar_ko.json are complete enough to
    render in the panel (name / meaning / zh / ko / level / examples);
  * the audit gate still maps every material entry exactly once, so nothing
    is silently dropped between the books and the library;
  * the pattern-derived matchers the engine builds for those rows are
    validated against the rows' own examples -- a row that got a matcher
    must fire on at least one of its own sentences.
"""

import json
import os
import re

import pytest

pytest.importorskip("kiwipiepy")

from lute.read.render import grammar_analysis_ko as ko  # noqa: E402
from scripts.audit_grammar_ko_coverage import run as audit_run  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIBRARY = os.path.join(_HERE, "..", "..", "lute", "jlpt_data", "grammar_ko.json")
_MATERIALS = os.path.join(_HERE, "..", "..", "scripts", "grammar_materials_ko")


def _library():
    with open(_LIBRARY, encoding="utf-8") as fh:
        return json.load(fh)


def _merged_rows():
    return [e for e in _library() if e["key"].startswith("kgm_")]


# ---- library integrity ------------------------------------------------


def test_library_rows_have_the_render_fields():
    "Every row must carry what the panel reads (name, level, zh, ko)."
    bad = [
        e["key"]
        for e in _library()
        if not (e.get("name") or "").strip()
        or not (e.get("level") or "").strip()
        or not (e.get("zh") or "").strip()
        or not (e.get("ko") or "").strip()
    ]
    assert not bad, f"rows missing render fields: {bad[:10]}"


def test_library_keys_are_unique():
    "Keys are the merge's identity; a duplicate would shadow a row."
    keys = [e["key"] for e in _library()]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f"duplicate keys: {dupes}"


def test_merged_rows_are_complete():
    "The materials rows must be as complete as the vendored kimchi rows."
    rows = _merged_rows()
    assert len(rows) >= 30, f"expected the merged rows, found {len(rows)}"
    for e in rows:
        assert (e.get("meaning") or "").strip(), e["key"]
        assert (e.get("level") or "").startswith("TOPIK"), e["key"]
        assert (e.get("zh") or "").strip(), e["key"]
        assert (e.get("ko") or "").strip(), e["key"]
        assert e.get("examples"), e["key"]


def test_merged_row_examples_are_hangul():
    "The examples are the books' Korean sentences, kept verbatim."
    for e in _merged_rows():
        for sentence in e["examples"]:
            assert any("\uac00" <= c <= "\ud7a3" for c in sentence), e["key"]


# ---- enrichment (enrich_grammar_ko.py) --------------------------------


def test_key_level_glosses_are_wired_up():
    """
    A hand-written lookup table fails silently when a key is misspelled, so
    every _ZH_BY_KEY / _KO_BY_KEY entry must name a real row *and* be the
    value that row actually carries.
    """
    from lute.jlpt_data.enrich_grammar_ko import _KO_BY_KEY, _ZH_BY_KEY

    by_key = {e["key"]: e for e in _library()}
    for table in (_ZH_BY_KEY, _KO_BY_KEY):
        stale = sorted(k for k in table if k not in by_key)
        assert not stale, f"table names no such row: {stale}"
    for key, want in _ZH_BY_KEY.items():
        assert by_key[key]["zh"] == want, f"{key}: {by_key[key]['zh']!r} != {want!r}"
    for key, want in _KO_BY_KEY.items():
        assert by_key[key]["ko"] == want, f"{key}: {by_key[key]['ko']!r} != {want!r}"


def test_enrich_is_non_destructive():
    """
    Re-running the enrichment must be a no-op: it may not blank a gloss it
    does not know, and may not downgrade a level that came from a better
    source -- the merged rows carry the TOPIK band printed in their own book,
    and the coarse type-based fallback used to overwrite all 29 of them.
    """
    import copy

    from lute.jlpt_data.enrich_grammar_ko import enrich

    rows = _library()
    again = enrich(copy.deepcopy(rows))
    assert [e["key"] for e in again] == [e["key"] for e in rows]
    for before, after in zip(rows, again):
        assert after["level"] == before["level"], before["key"]
        assert after["zh"] == before["zh"], before["key"]
        assert after["ko"] == before["ko"], before["key"]


def test_shared_name_families_are_disambiguated_where_needed():
    """
    kimchi gives one `name` to a whole family of senses, so a name-keyed
    gloss hands every sense the same text.  Where the senses are unrelated
    constructions they must now differ.
    """
    by_key = {e["key"]: e for e in _library()}
    for family in (
        (
            "verb_거니__not-only-but-also",
            "verb_거니__past-acknowledgment",
            "verb_거니__repetition-alternation",
        ),
        (
            "verb_으리__prediction",
            "verb_으리__intention",
            "verb_으리__informal-logical-rebuttal",
        ),
        (
            "noun_이나_나__choice",
            "noun_이나_나__no-less-than",
            "noun_이나_나__at-least",
        ),
    ):
        zh = {by_key[k]["zh"] for k in family}
        ko = {by_key[k]["ko"] for k in family}
        assert len(zh) == len(family), f"{family[0].split('__')[0]}: {zh}"
        assert len(ko) == len(family), f"{family[0].split('__')[0]}: {ko}"


# ---- materials + audit ------------------------------------------------


def _material_files():
    "The three merge-ready per-book files, in the order the audit reads them."
    return ["beginning.json", "intermediate.json", "advanced.json"]


def test_material_files_exist_and_are_well_formed():
    """
    Every top-level material entry must be merge-ready: a pattern, a TOPIK
    level, and at least one verbatim Korean example.  Entries that carry no
    examples (book practice/review pages) live in reference/ instead, which
    neither the audit nor the merge reads.
    """
    for name in _material_files():
        path = os.path.join(_MATERIALS, name)
        assert os.path.exists(path), f"missing material file {name}"
        with open(path, encoding="utf-8") as fh:
            entries = json.load(fh)
        assert entries, name
        for e in entries:
            assert (e.get("pattern") or "").strip(), name
            assert (e.get("level") or "").startswith("TOPIK"), name
            assert e.get("examples"), f"{name}: {e.get('pattern')!r} has no examples"
            for ex in e["examples"]:
                assert (ex.get("korean") or "").strip(), name


def test_reference_dir_is_not_consumed():
    """
    reference/ holds entries deliberately excluded from the merge; the audit
    globs the top level only, so its total must equal the three files exactly.
    """
    counted = sum(
        len(json.load(open(os.path.join(_MATERIALS, name), encoding="utf-8")))
        for name in _material_files()
    )
    res = audit_run(write=False)
    assert res["total"] == counted, f"audit read {res['total']}, files hold {counted}"


def test_every_material_entry_is_mapped_once():
    "The audit gate: covered + supplementable + missing == total."
    res = audit_run(write=False)
    assert res["total"] > 0
    assert res["total"] == (
        res["covered"] + res["supplementable"] + res["missing"]
    ), f"unmapped material entries: {res}"
    # Nothing left to merge: a non-empty gap report means a book entry is
    # not in the library, which makes the merge non-idempotent.
    assert res["missing"] == 0, f"unmerged material entries: {res['missing']}"


def test_no_merged_row_duplicates_an_existing_point():
    """
    A merged row must not restate a point the library already has.

    This bit once: the book writes "A/V-거니와" while the upstream row is
    named "(이)거니" with focus ["이거니와", "거니와"], so a name-based audit
    saw no match and merged a duplicate.  The audit now also indexes the
    focus literals, and this test pins that down.
    """
    from scripts.audit_grammar_ko_coverage import _alts, _canon

    upstream = set()
    for e in _library():
        if e["key"].startswith("kgm_"):
            continue
        for f in e.get("focus") or []:
            c = _canon(f)
            if len(c) >= 2:
                upstream.add(c)

    dupes = [
        (e["name"], alt)
        for e in _merged_rows()
        for alt in _alts(e["name"])
        if alt in upstream
    ]
    assert not dupes, f"merged rows restating an existing point: {dupes}"


def test_loose_derived_specs_are_rejected():
    """
    A single bare-lemma spec cannot tell a construction from a word, so the
    derivation must refuse it.  Without this the row "V-되" matched the plain
    verb 되다 and flagged "그는 훌륭한 선생님이 되었다." as the grammar point 되.
    """
    bare = {"type": "tokens", "conds": [{"lemma": {"되다", "되"}}]}
    assert not ko._spec_is_specific(bare)
    assert ko._spec_is_specific({"type": "tokens", "conds": [{"pos": "EC"}]})
    assert ko._spec_is_specific(
        {"type": "tokens", "conds": [{"surface": ("지",)}, {"surface": ("만",)}]}
    )
    assert ko._spec_is_specific({"type": "regex", "re": None})
    # ... and the shipped rules obey it, except for the whitelisted fixed form.
    for rule in ko._get_pattern_rules():
        if rule["key"] in ko._BARE_LEMMA_ALLOWED:
            continue
        for spec in rule["patterns"]:
            assert ko._spec_is_specific(spec), rule["key"]


def test_plain_verb_does_not_trigger_a_grammar_point():
    "Regression: an ordinary 되다 sentence must report nothing."
    names = {e["name"] for e in ko.analyze_korean("그는 훌륭한 선생님이 되었다.", "zh")}
    assert "되" not in names, names


# ---- engine derivation ------------------------------------------------


def test_pattern_rules_are_derived_and_validated():
    """
    Every pattern-derived rule must fire on at least one of its own
    examples -- that validation is what keeps a mis-derived pattern silent.
    """
    rules = ko._get_pattern_rules()
    assert rules, "no pattern-derived rules were built"
    for rule in rules:
        examples = ko._PATTERN_RULE_EXAMPLES.get(rule["key"], [])
        assert examples, f"rule {rule['key']} carries no examples to validate"
        fired = False
        for sentence in examples:
            tokens = ko._tokens_for(sentence)
            if ko._match_spans(rule, tokens, sentence):
                fired = True
                break
        assert fired, f"rule {rule['key']} fires on none of its examples"


def test_merged_rows_get_matchers_where_derivable():
    "A healthy share of the merged rows must be matchable, not just data."
    keys = {r["key"] for r in ko._get_pattern_rules()}
    merged = {e["key"] for e in _merged_rows()}
    matched = keys & merged
    assert len(matched) >= 12, f"only {len(matched)} merged rows got a matcher"


@pytest.mark.parametrize(
    "sentence,expected",
    [
        ("길도 복잡하고 해서 지하철을 탔어요.", "고 해서"),
        ("그 회사는 무리하게 확장한 나머지 위기를 맞게 되었다.", "(으)ㄴ 나머지"),
        ("결혼하자는 말을 꺼내기가 무섭게 거절해 버렸다.", "기가 무섭게"),
        ("어려운 학생들 도와주는 셈치고 사는 거예요.", "는 셈치다"),
        ("어떤 어려움이 닥칠지라도 포기하지 않겠습니다.", "(으)ㄹ지라도"),
    ],
)
def test_new_points_are_detected_on_book_sentences(sentence, expected):
    names = {e["name"] for e in ko.analyze_korean(sentence, "zh")}
    assert expected in names, f"{expected!r} not detected in: {sentence}"


def test_matches_point_at_the_construction():
    "The reported span covers the grammar morphemes, not the whole sentence."
    results = ko.analyze_korean("결혼하자는 말을 꺼내기가 무섭게 거절해 버렸다.", "zh")
    entry = next(e for e in results if e["name"] == "기가 무섭게")
    example = entry["examples"][0]
    m = example["matches"][0]
    assert "기가 무섭게" in example["sentence"][m["start"] : m["end"]]


def test_matches_carry_no_padding_whitespace():
    """
    A span must not swallow the gap before the next token.  The token
    matcher ends a run at the *next* token's start, so a construction that
    ends before a space (기가 무섭게) would otherwise report "기가 무섭게 ".
    """
    sentences = [
        "결혼하자는 말을 꺼내기가 무섭게 거절해 버렸다.",
        "길도 복잡하고 해서 지하철을 탔어요.",
        "저는 한국어를 공부하고 있어요.",
        "어려운 학생들 도와주는 셈치고 사는 거예요.",
    ]
    for sentence in sentences:
        for entry in ko.analyze_korean(sentence, "zh"):
            for example in entry["examples"]:
                for m in example["matches"]:
                    got = example["sentence"][m["start"] : m["end"]]
                    assert got == got.strip(), f"{entry['name']}: {got!r}"


def test_matches_are_not_repeated():
    """
    A rule normally carries several specs for one construction -- a token
    path and a surface-alias path.  When two of them land on the same words
    the panel must still receive that range once, or it highlights twice.
    """
    for rule in ko._get_pattern_rules():
        key = rule["key"]
        for sentence in ko._PATTERN_RULE_EXAMPLES.get(key) or []:
            for entry in ko.analyze_korean(sentence, "zh"):
                for example in entry["examples"]:
                    spans = [(m["start"], m["end"]) for m in example["matches"]]
                    assert len(spans) == len(set(spans)), (key, sentence, spans)


def test_audit_does_not_mistake_a_short_library_name_for_coverage():
    """
    The audit's containment test must look only one way: the book's pattern
    may sit inside a longer library name, never the reverse.

    Judging the reverse -- "the library's name appears inside the book's
    pattern" -- hid 39 real gaps behind a "0 missing" report, because Korean
    points are composites of short function words: ``-기 일쑤이다`` counted as
    covered by the copula ``이다``, ``-(으)ㄹ 법하다`` by ``하다`` ("to do"),
    and ``-을/를 비롯해서`` by the object particle ``을/를``.  A genuine
    same-point-different-name case is a judgement, so it belongs in
    ``_EXPLICIT_MATCH``, not in a substring test.
    """
    import collections

    from scripts.audit_grammar_ko_coverage import (
        _index_by_core,
        _index_by_focus,
        _load_library,
        _match,
    )

    entries = _load_library()
    by_name = collections.defaultdict(list)
    for key, e in entries.items():
        by_name[e.get("name", "")].append(key)
    idx = _index_by_core(entries)
    focus_idx = _index_by_focus(entries)

    # `하다` really is a library row; that alone must not "cover" an invented
    # point that merely happens to end in it.
    assert "하다" in by_name, "fixture drifted: no 하다 row in the library"
    kind, keys = _match("V-가나다라하다", entries, idx, by_name, focus_idx)
    assert kind != "covered", f"reverse containment still counts: {keys}"


def test_material_meanings_describe_the_point_not_the_page():
    """
    A material entry's meaning_* must gloss the grammar point, not describe
    the page it was lifted from.

    Five entries in advanced.json were labelled as their page's
    "더 알아볼까요?" extension box -- ``meaning_en`` literally began
    "extension/notes page ..." -- while the book's actual definition went
    unrecorded.  The merge copies ``meaning_en`` into the library's
    ``meaning``, so the page description would have been shown to learners
    as the point's definition.
    """
    page_talk = re.compile(
        r"(extension/notes page|notes page|review page|practice page|"
        r"the remainder is|which is skipped|더 알아볼까요)",
        re.I,
    )
    bad = []
    for name in _material_files():
        with open(os.path.join(_MATERIALS, name), encoding="utf-8") as fh:
            for e in json.load(fh):
                blob = " ".join(
                    str(e.get(f) or "")
                    for f in ("meaning_en", "meaning_zh", "meaning_ko")
                )
                if page_talk.search(blob):
                    bad.append((name, e.get("pattern")))
    assert not bad, f"material meanings describe the page, not the point: {bad}"


def test_enrich_keeps_a_gloss_the_row_already_has():
    """
    The name-keyed gloss table is a *fallback*: it fills a gap and must not
    replace a gloss the row already carries, because a merged row's gloss
    comes from the book and is more specific than the family's.

    This bit once: the stale name entry "도록 하다" overwrote the merged
    ``kgm_도록하다`` row's own gloss the moment the merge created a row with
    that name.  Only the per-key tables (``_ZH_BY_KEY`` / ``_KO_BY_KEY``) are
    authoritative.
    """
    from lute.jlpt_data.enrich_grammar_ko import enrich

    row = {
        "key": "kgm_도록하다__f0c5de",
        "name": "도록 하다",
        "type": "verb",
        "zh": "book wording",
        "ko": "책 설명",
    }
    out = enrich([row])[0]
    assert out["zh"] == "book wording", out["zh"]
    assert out["ko"] == "책 설명", out["ko"]
