"""Tests for the English grammar-analysis engine (spaCy en_core_web_sm)."""

import pytest

pytest.importorskip("spacy")
pytest.importorskip("en_core_web_sm")

from lute.read.render.grammar_analysis_en import _EN_RULES, analyze_english
from lute.read.render.grammar_analysis import is_english_language


def _keys(text, lang="en"):
    return {e["key"] for e in analyze_english(text, lang)}


# One short, natural sentence per rule.
_RULE_SENTENCES = {
    "en_there_be": "There is a book on the table.",
    "en_lets": "Let's eat something.",
    "en_can_could": "She can swim very well.",
    "en_want_to": "He wants to go home.",
    "en_must_should": "We have to hurry.",
    "en_past_simple": "They went home early.",
    "en_present_continuous": "I am reading a novel.",
    "en_past_continuous": "She was reading when I arrived.",
    "en_will_future": "I will call you tomorrow.",
    "en_going_to": "We are going to travel in May.",
    "en_comparative": "This book is bigger than that one.",
    "en_superlative": "It is the biggest city in the country.",
    "en_like_ing": "I enjoy reading novels.",
    "en_too_to": "He is too tired to walk.",
    "en_as_as": "She is as tall as her sister.",
    "en_either_or": "You can have either tea or coffee.",
    "en_present_perfect": "I have finished my work.",
    "en_past_perfect": "She had left before I arrived.",
    "en_passive": "The book was written by a famous author.",
    "en_first_conditional": "If it rains, I will stay home.",
    "en_second_conditional": "If I had time, I would go.",
    "en_so_that": "It was so dark that I couldn't see.",
    "en_used_to": "I used to play tennis.",
    "en_relative_pronouns": "The man who lives next door is friendly.",
    "en_reported_speech": "She said that she was tired.",
    "en_third_conditional": "If I had known, I would have come.",
    "en_wish_past": "I wish I knew the answer.",
    "en_must_have": "She must have finished already.",
    "en_have_sth_done": "I had it repaired yesterday.",
    "en_despite": "Despite the rain, we went out.",
    "en_unless": "Unless it rains, we will go out.",
}


def test_each_rule_matches_its_sentence():
    "Every rule must fire on its illustrative sentence."
    for key, sentence in _RULE_SENTENCES.items():
        hits = _keys(sentence)
        assert key in hits, f"rule {key} did not fire on: {sentence}"


def test_match_offsets_are_precise():
    "Matched offsets point at the construction, not the whole sentence."
    for e in analyze_english("He wants to go home."):
        if e["key"] == "en_want_to":
            example = e["examples"][0]
            m = example["matches"][0]
            assert example["sentence"][m["start"] : m["end"]] == "wants to go"


def test_same_rule_multiple_hits_in_one_sentence():
    "Two occurrences of one pattern land as two match ranges."
    for e in analyze_english("I am reading and she is writing."):
        if e["key"] == "en_present_continuous":
            assert len(e["examples"]) == 1
            assert len(e["examples"][0]["matches"]) == 2


def test_display_language_switches_desc():
    "Chinese display uses the hand-written zh description."
    zh = {e["key"]: e for e in analyze_english("Let's eat something.", "zh")}
    en = {e["key"]: e for e in analyze_english("Let's eat something.", "en")}
    assert "提议" in zh["en_lets"]["desc"]
    assert "suggestion" in en["en_lets"]["desc"]


def test_levels_are_cefr():
    "The English engine grades points with CEFR levels."
    levels = {e["key"]: e["level"] for e in analyze_english(" ".join(_RULE_SENTENCES.values()))}
    assert levels["en_there_be"] == "A1"
    assert levels["en_past_simple"] == "A2"


def test_analyze_output_structure():
    "Each entry exposes name/desc/examples with sentence and matches."
    for entry in analyze_english("They went home early. I am reading a novel."):
        assert entry["name"]
        assert entry["desc"]
        assert entry["examples"]
        ex = entry["examples"][0]
        assert ex["sentence"]
        assert len(ex["matches"]) >= 1
        for m in ex["matches"]:
            assert m["start"] < m["end"]


class StubLanguage:
    def __init__(self, parser_type=None, name=None):
        self.parser_type = parser_type
        self.name = name


def test_is_english_language_detects_by_name():
    "English detection works on language names (parser_type is generic spacedel)."
    assert is_english_language(StubLanguage(name="English"))
    assert is_english_language(StubLanguage(name="British English"))
    assert is_english_language(StubLanguage(name="英语"))
    assert not is_english_language(StubLanguage(name="Spanish"))
    assert not is_english_language(StubLanguage(parser_type="spacedel"))
    assert not is_english_language(None)


def test_rules_are_loaded():
    "The rule set is populated."
    assert len(_EN_RULES) >= 10
