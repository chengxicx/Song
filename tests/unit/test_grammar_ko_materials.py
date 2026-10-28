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


# ---- overlap audit / panel silence --------------------------------------


def test_duplicate_rule_list_matches_the_overlap_audit():
    """
    The panel's silence list must equal what the overlap audit derives.

    A row whose matcher reports the same span as another rule on the same
    sentence would print one construction twice, so the panel drops it (it
    stays in the library).  Pinning the list to the audit -- rather than to
    a hand-maintained guess -- means growing the library re-derives it, and
    a stale entry fails here instead of silently suppressing a row that is
    no longer a duplicate.
    """
    from scripts import audit_grammar_ko_overlaps as audit

    _by_key, cands = audit.candidates()
    derived = {key for key, _decision in cands}
    assert derived == set(ko._DUPLICATE_RULE_IDS)


def test_silenced_rows_leave_the_panel_but_stay_in_the_library():
    "Silence is a panel decision, not a content removal."
    assert ko._DUPLICATE_RULE_IDS <= {e["key"] for e in _library()}
    panel = {rule["key"] for rule in ko._panel_rules()}
    assert not (ko._DUPLICATE_RULE_IDS & panel)
    assert ko._DUPLICATE_RULE_IDS <= {r["key"] for r in ko.library_entries("zh")}


# ---- focus-literal precision --------------------------------------------


def _data_rule(key):
    return next(r for r in ko._get_data_rules() if r["key"] == key)


def _fires(rule, sentence):
    return bool(ko._match_spans(rule, ko._tokens_for(sentence), sentence))


def test_a_literal_never_fires_as_the_tail_of_a_longer_one():
    """
    Korean stacks morphemes at the end of an 어절, so 거나 is both -거나 and
    the tail of -다거나, and 에다가's marked tail 다가 is also the -다가 of
    -려다가.  A plain alternation reports both rows on the same span, so a
    literal is guarded by a lookbehind for every longer literal that ends
    with it -- the pool is the whole library, so a merged row tightens the
    rows it nests inside without anyone editing them.
    """
    assert _fires(_data_rule("verb_거나__choice"), "저는 주말에 책을 읽거나 영화를 봐요.")
    tail = "다솜은 친구랑 통화한다거나 좋아하는 웹툰을 정주행한다거나 하는 것으로 스트레스를 푼다."
    assert not _fires(_data_rule("verb_거나__choice"), tail)
    assert _fires(_data_rule("verb_거니__past-acknowledgment"), "내가 잘못했거니 후회가 남았어요.")
    figured = "첫번째 책이 좋아서 두번째 책도 좋겠거니 하고 샀는데, 읽어보니까 별로더라고요."
    assert not _fires(_data_rule("verb_거니__past-acknowledgment"), figured)
    assert _fires(_data_rule("noun_에다가__onto-into"), "그릇에다가 밥을 담았어요.")
    about_to = "그 영화를 DVD 대여점에서 빌려 보려다가 텔레비전에서 오늘 방영한다는 것을 알았다."
    assert not _fires(_data_rule("noun_에다가__onto-into"), about_to)


def test_라서_fires_on_the_copula_but_not_on_a_verb_ending():
    """
    라서 is the copula's ending (이 + 라서) and also the tail of 모르 + 어서
    and 따르 + 어서.  Kiwi reports a contracted stem's ending as starting
    inside the stem, so the literal is only trusted on a morpheme boundary.
    """
    rule = _data_rule("verb_이라서_라서__reason-or-cause")
    for sentence in (
        "어머니 생신이라서 고향에 가야 돼요.",
        "제가 임산부라서 그러는데 이 자리에 앉아도 될까요?",
        "달리기 시합이 아니라서 제가 달리고 싶은 속도로 달릴 수 있어요.",
    ):
        assert _fires(rule, sentence), sentence
    for sentence in (
        "한국말을 몰라서 실수한 적이 있어요.",
        "가격에 따라서 사람들의 선택이 달라져요.",
    ):
        assert not _fires(rule, sentence), sentence


def test_a_focus_literal_the_name_does_not_explain_is_dropped():
    """
    The Kimchi <f> marks are the authors'; the name is the point.  The 에
    rows mark whole verbs (갑니다 / 가서 / 있어요) beside the particle, and
    a matcher built from those marks fires on every sentence ending in
    them -- the 에다가 row likewise marks its own tail 다가.  A literal is
    kept only when the row's own name explains it, in the raw string or in
    one of the literal's Kiwi morphemes (which is what keeps 수정됐어요
    alive for 되다 and 찍을까요 for (으)ㄹ까).
    """
    keys = {rule["key"] for rule in ko._get_data_rules()}
    assert "noun_에__direction" not in keys
    assert "noun_에__location" not in keys
    assert _data_rule("noun_에다가__onto-into")["patterns"][0]["re"].pattern == (
        r"에다가(?!\S)"
    )
    # The contracted forms the name spells survive the filter.
    for key in ("noun_되다__to-become", "verb_을까_ㄹ까__suggestion"):
        assert _data_rule(key)["patterns"][0]["re"].pattern, key


def test_whole_word_focus_no_longer_reports_a_grammar_point():
    """
    The rows the filter dropped used to fire on sentences that carry no
    grammar point at all: any sentence ending in -어요 (the 에-location
    row marked 있어요) was reported as the 에 point.
    """
    for sentence in (
        "오늘 날씨가 정말 좋아요.",
        "친구가 아파서 학교에 안 왔어요.",
        "내일은 친구를 만나러 서울에 갑니다.",
    ):
        names = {e["name"] for e in ko.analyze_korean(sentence, "zh")}
        assert "에" not in names, (sentence, names)


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


# ---- panel fields (接续 / 注意点) -------------------------------------


def _material_rows_by_name():
    "The books' rows, keyed by the name the merge gives them."
    from scripts.merge_grammar_ko_materials import load_materials

    return load_materials()


def test_merged_rows_carry_the_books_panel_fields():
    """
    The books state a 接续 line for every point and 注意点 for most, and the
    panel renders both.  The merge used to hard-code ``notes: ""`` and drop
    ``formation`` entirely, which left the Korean panel showing a description
    where the Japanese one showed 接续 / 参考例句 / 注意点.
    """
    by_name = _material_rows_by_name()
    rows = _merged_rows()
    assert rows, "expected merged rows"
    no_formation = [e["key"] for e in rows if not (e.get("formation") or "").strip()]
    assert not no_formation, f"merged rows without a 接续 line: {no_formation[:10]}"
    for e in rows:
        meta = by_name.get(e["name"])
        assert meta is not None, e["key"]
        assert e["formation"] == (meta.get("formation") or "").strip(), e["key"]
        assert (e.get("notes") or "") == (meta.get("notes") or "").strip(), e["key"]


def test_panel_enrichment_covers_every_book_field():
    """
    A Chinese panel shows the 接续 / 注意点 only when the wording exists, so
    the enrichment file must cover every field the rows carry -- a missing
    entry hides a block silently instead of failing.
    """
    from lute.read.render.grammar_analysis_ko import _KO_ENRICH

    by_key = {e["key"]: e for e in _library()}
    stale = sorted(k for k in _KO_ENRICH if k not in by_key)
    assert not stale, f"enrichment names no such row: {stale}"
    for e in _merged_rows():
        entry = _KO_ENRICH.get(e["key"]) or {}
        for field in ("formation", "notes"):
            if not (e.get(field) or "").strip():
                continue
            want = (entry.get(field) or "").strip()
            assert want, f"{e['key']}: no Chinese {field}"
            assert re.search(r"[\u4e00-\u9fff]", want), f"{e['key']} {field}"
            # The engine's zh filter drops any field carrying a Latin word,
            # so an English word here would hide the block it was meant to
            # fill in.
            assert not re.search(r"[A-Za-z]{3,}", want), f"{e['key']} {field}: {want}"


def test_panel_backfill_is_idempotent_and_non_destructive():
    """
    Re-running the merge must not touch a row it has already filled, and must
    never overwrite a value it does not own -- the rule the zh/ko glosses
    follow.
    """
    import copy

    from scripts.merge_grammar_ko_materials import backfill, load_materials

    rows = _library()
    again = copy.deepcopy(rows)
    assert backfill(again, load_materials()) == []
    assert again == rows
