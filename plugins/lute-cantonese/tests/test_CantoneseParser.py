"""
CantoneseParser tests.

Expectations are keyed on the pycantonese major version: 3.x and 5.x
use different segmentation models and disagree on some word
boundaries (and 5.x may group sentence punctuation into a word
token, which the parser normalizes away).
"""

import pytest

# pylint: disable=wrong-import-order
import pycantonese
from lute.models.term import Term
from lute.parse.base import ParsedToken

from lute_cantonese_parser.parser import CantoneseParser

PYC_MAJOR = int(pycantonese.__version__.split(".")[0])


@pytest.mark.parametrize(
    "text,expected_count",
    [
        ("你", 1),
        ("你好", 2),
        ("今日", 1),
        ("唔該", 1),
        ("阿明", 1),
        ("茶餐廳", 1),
        ("你好。吃饭了吗？现在是2024年。", 14 if PYC_MAJOR < 5 else 9),
    ],
)
def test_token_count(text, expected_count, cantonese):
    """
    token_count checks.
    """
    t = Term(cantonese, text)
    assert t.token_count == expected_count, text
    assert t.text_lc == t.text


def assert_tokens_equals(text, lang, expected):
    """
    Parsing a text using a language should give the expected parsed tokens.

    expected is given as array of:
    [ original_text, is_word, is_end_of_sentence ]
    """
    p = CantoneseParser()
    actual = p.get_parsed_tokens(text, lang)
    expected = [ParsedToken(*a) for a in expected]
    assert [str(a) for a in actual] == [str(e) for e in expected]


def test_end_of_sentence_stored_in_parsed_tokens(cantonese):
    """
    ParsedToken is marked as EOS=True at ends of sentences.
    """
    s = "你好。吃饭了吗？现在是2024年。"

    if PYC_MAJOR < 5:
        expected = [
            ("你", True),
            ("好", True),
            ("。", False, True),
            ("吃", True),
            ("饭", True),
            ("了", True),
            ("吗", True),
            ("？", False, True),
            ("现", True),
            ("在", True),
            ("是", True),
            ("2024", False, False),
            ("年", True),
            ("。", False, True),
        ]
    else:
        # 5.x groups "吃饭了吗？现在是" into one token; the parser
        # splits punctuation back out.
        expected = [
            ("你", True),
            ("好", True),
            ("。", False, True),
            ("吃饭了吗", True),
            ("？", False, True),
            ("现在是", True),
            ("2024", False, False),
            ("年", True),
            ("。", False, True),
        ]
    assert_tokens_equals(s, cantonese, expected)


def test_no_word_token_contains_sentence_punctuation(cantonese):
    """
    Word tokens never contain sentence punctuation, whatever the
    pycantonese version does.
    """
    s = "吃饭了吗？现在是2024年。天氣好好！"
    for t in CantoneseParser().get_parsed_tokens(s, cantonese):
        if t.is_word:
            assert not any(c in cantonese.regexp_split_sentences for c in t.token), t


def test_carriage_returns_treated_as_reverse_p_character(cantonese):
    """
    Returns need to be marked with the backwards P for rendering etc.
    """
    s = "你好。\n现在。"

    if PYC_MAJOR < 5:
        tail = [("现", True), ("在", True), ("。", False, True)]
    else:
        tail = [("现在", True), ("。", False, True)]
    expected = [("你", True), ("好", True), ("。", False, True), ("¶", False, True)] + tail
    assert_tokens_equals(s, cantonese, expected)


def test_readings():
    """
    Parser returns jyutping readings if they add value.

    pycantonese 3.x returns concatenated syllables ("m4goi1"),
    5.x space-separated ("m4 goi1"), so multi-syllable words are
    compared without spaces.
    """
    p = CantoneseParser()

    no_reading = ["Hello", "。", "2024"]

    for c in no_reading:
        assert p.get_reading(c) is None, c

    assert p.get_reading("你好") == "nei5 hou2"

    for text, expected in [("唔該", "m4goi1"), ("茶餐廳", "caa4caan1teng1")]:
        actual = p.get_reading(text)
        assert actual is not None, text
        assert actual.replace(" ", "") == expected, text


def test_parser_declares_cantonese():
    "Parser is language-specific."
    assert CantoneseParser.languages() is not None
    assert "cantonese" in CantoneseParser.languages()
