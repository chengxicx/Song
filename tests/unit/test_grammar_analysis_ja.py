"""Tests for the N5 Japanese grammar-analysis engine."""

import pytest

from lute.read.render.grammar_analysis_ja import (
    _ALL_LEVELS,
    _DATA_RULES,
    _N5_RULES,
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