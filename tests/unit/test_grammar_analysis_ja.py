"""Tests for the N5 Japanese grammar-analysis engine."""

import re

import pytest

from lute.read.render.grammar_analysis_ja import (
    _ALL_LEVELS,
    _ALL_RULES,
    _DATA_RULES,
    _N5_RULES,
    _ZH_DESC,
    analyze_japanese,
)
from lute.read.render.grammar_analysis import is_japanese_language


@pytest.fixture(name="rules")
def rules_fixture():
    return {r["key"]: r for r in _N5_RULES}


def _keys(text):
    return {e["key"] for e in analyze_japanese(text)}


def test_english_regex_demo_unchanged():
    "The original English regex engine should still exist and work on English."
    from lute.read.render.grammar_analysis import analyze

    result = analyze(["I used to play tennis."])
    assert any("used to" in r["name"] for r in result)


def test_n5_rules_each_have_data(rules):
    "Every N5 rule carries a level, meaning, and at least one example."
    for rule in rules.values():
        assert rule["level"] == "N5"
        assert rule["meaning"]
        assert rule["examples"]


def test_each_rule_matches_its_own_examples(rules):
    "A rule must fire on its OpenJLPT example sentences."
    for key, rule in rules.items():
        text = "".join(rule["examples"])
        hits = _keys(text)
        if rule.get("kind") == "particle":
            # Pure particles are folded into one aggregated entry.
            assert "basic_particles" in hits, f"particle rule {key} not aggregated"
        else:
            assert key in hits, f"rule {key} did not match its own examples"


def test_particles_are_aggregated():
    "Pure particles (を/に/で/と) collapse into a single trailing entry."
    results = analyze_japanese("本を読みます。学校で勉強します。7時に起きます。")
    keys = [e["key"] for e in results]
    assert "wo_object" not in keys
    assert "de_place_means" not in keys
    assert "ni_time_destination" not in keys
    assert "basic_particles" in keys
    particles_entry = next(e for e in results if e["key"] == "basic_particles")
    assert particles_entry["name"] == "Particles: で・に・を"
    assert particles_entry["examples"]
    # Aggregated entry is trailing (lowest priority).
    assert keys[-1] == "basic_particles"


def test_particles_still_detected_when_alone():
    "Even a lone particle hit still yields a Particles entry."
    results = analyze_japanese("本を読みます。")
    assert any(e["key"] == "basic_particles" for e in results)


@pytest.mark.parametrize(
    "key, sentence",
    [
        # A fixed particle/conjugation should NOT be reported by an unrelated rule.
        ("te_iru", "窓を開けました。"),  # past, no progressive ている
        ("te_kudasai", "窓を開けました。"),  # no ください
        ("tai", "これはたいへんです。"),  # たいへん, not desire 〜たい
        ("ta_koto_ga_arimasu", "日本に行きました。"),  # no こと
        ("mashou", "一緒に行きます。"),  # plain ます, not ましょう
        ("masen_ka", "一緒に行きましょう。"),  # ましょう, not ませんか
        ("te_wa_ikemasen", "写真を撮ってもいいです。"),  # permission, not prohibition
        ("te_mo_ii_desu", "ここで写真を撮ってはいけません。"),  # prohibition, not permission
    ],
)
def test_negative_examples_do_not_false_positive(key, sentence):
    "Sentences that lack a construction must not report that rule."
    hits = _keys(sentence)
    assert key not in hits, f"rule {key} should NOT match: {sentence}"


def test_subtitle_line_ending_in_match_does_not_overflow():
    """
    A subtitle/transcript line with no trailing 。 may end exactly on a
    matched construction (e.g. "〜から").  The token run then reaches the
    end of the token list; the character span must clamp to the sentence
    end instead of indexing a non-existent offset.
    """
    results = analyze_japanese("これが一番おいしいですから\nええ 友達が来ますから")
    assert any(e["key"] == "kara_reason" for e in results)
    entry = next(e for e in results if e["key"] == "kara_reason")
    for ex in entry["examples"]:
        s = ex["sentence"]
        for m in ex["matches"]:
            assert m["start"] >= 0 and m["end"] <= len(s), f"match out of range: {m}"
            assert s[m["start"] : m["end"]] == "から"


def test_analysis_return_shape():
    "Return entries have the fields the front-end panel renders."
    results = analyze_japanese("日本に行ったことがあります。")
    entry = next(e for e in results if e["key"] == "ta_koto_ga_arimasu")
    assert entry["level"] == "N5"
    assert entry["name"]
    assert entry["desc"]
    assert entry["examples"][0]["sentence"]


def test_is_japanese_language_detection():
    "Language detection routes Japanese books to the Sudachi engine."

    class FakeLang:
        def __init__(self, parser_type, name):
            self.parser_type = parser_type
            self.name = name

    assert is_japanese_language(FakeLang("japanese", "Japanese")) is True
    assert is_japanese_language(FakeLang("japanese_sudachi", "日本語")) is True
    assert is_japanese_language(FakeLang("spacedel", "Japanese")) is True  # by name
    assert is_japanese_language(FakeLang("spacedel", "Spanish")) is False
    assert is_japanese_language(None) is False


def test_data_rules_loaded_for_all_levels():
    "Data-driven rules are loaded for N4/N3/N2/N1 with the needed fields."
    for level in _ALL_LEVELS:
        rules = [r for r in _DATA_RULES if r["level"] == level]
        assert rules, f"no data rules loaded for {level}"
        for r in rules:
            assert r["level"] == level
            assert r["meaning"]
            assert r["examples"]


def test_each_data_rule_matches_its_own_examples():
    "Every non-skipped data rule fires on a text built from its own examples."
    active = [r for r in _DATA_RULES if not r["skipped"]]
    assert active, "expected some active data rules"
    for rule in active:
        text = "".join(rule["examples"])
        hits = _keys(text)
        assert rule["key"] in hits, f"data rule {rule['key']} did not match its own examples"


def test_no_particles_in_levels():
    "N4-N1 data rules are all constructions; none is folded into a particle entry."
    for rule in _DATA_RULES:
        assert rule.get("kind") != "particle", (
            f"data rule {rule['key']} should not be a particle"
        )


def test_chinese_display_language():
    "display_lang='zh' yields Chinese desc for hits; default stays English."
    zh = analyze_japanese("毎日運動することにした。", display_lang="zh")
    hit = next(e for e in zh if e["key"] == "cl_kotonisuru_n4_0")
    assert re.search(r"[\u4e00-\u9fff]", hit["desc"]), (
        f"expected Chinese desc, got: {hit['desc']!r}"
    )

    en = analyze_japanese("毎日運動することにした。")
    en_hit = next(e for e in en if e["key"] == "cl_kotonisuru_n4_0")
    rule = next(r for r in _ALL_RULES if r["key"] == "cl_kotonisuru_n4_0")
    assert en_hit["desc"] == rule["meaning"] == "decide to do"


def test_zh_table_covers_all_patterns():
    "Every rule pattern in _ALL_RULES has a Chinese translation entry."
    assert _ZH_DESC, "translation table must not be empty"
    missing = [r["pattern"] for r in _ALL_RULES if r["pattern"] not in _ZH_DESC]
    assert missing == []
    for value in _ZH_DESC.values():
        assert re.search(r"[\u4e00-\u9fff]", value), f"non-Chinese desc: {value!r}"