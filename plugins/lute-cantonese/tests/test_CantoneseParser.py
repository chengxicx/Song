"""
CantoneseParser tests.
"""

import pytest

# pylint: disable=wrong-import-order
from lute.models.term import Term
from lute.parse.base import ParsedToken

from lute_cantonese_parser.parser import CantoneseParser


@pytest.mark.parametrize(
    "text,expected_count",
    [
        ("你", 1),
        ("唔知", 1),
        ("我唔知", 2),
        ("今日天氣好好", 3),
        ("你好。吃饭了吗？", 8),
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
    assert_tokens_equals(s, cantonese, expected)


def test_carriage_returns_treated_as_reverse_p_character(cantonese):
    """
    Returns need to be marked with the backwards P for rendering etc.
    """
    s = "你好。\n现在。"

    expected = [
        ("你", True),
        ("好", True),
        ("。", False, True),
        ("¶", False, True),
        ("现", True),
        ("在", True),
        ("。", False, True),
    ]
    assert_tokens_equals(s, cantonese, expected)


def test_readings():
    """
    Parser returns jyutping readings if they add value.
    """
    p = CantoneseParser()

    no_reading = ["Hello", "。", "2024"]

    for c in no_reading:
        assert p.get_reading(c) is None, c

    cases = [
        ("你好", "nei5 hou2"),
        ("唔該", "m4goi1"),
        ("茶餐廳", "caa4caan1teng1"),
    ]

    for c in cases:
        assert p.get_reading(c[0]) == c[1], c[0]


def test_parser_declares_cantonese():
    "Parser is language-specific."
    assert CantoneseParser.languages() is not None
    assert "cantonese" in CantoneseParser.languages()
