"""
ThaiParser tests.
"""


import subprocess
import sys

import pytest

# pylint: disable=wrong-import-order
from lute.models.term import Term
from lute.parse.base import ParsedToken

from lute_thai_parser.parser import ThaiParser


def test_token_count(thai):
    """
    token_count checks.
    """
    cases = [
        ("สวัสดี", 1),
        ("ลาก่อน", 1),
        ("ฉันรักคุณ", 3),
        ("ฉันกำลังเรียนภาษาไทย", 4),
    ]
    for text, expected_count in cases:
        t = Term(thai, text)
        assert t.token_count == expected_count, text
        assert t.text_lc == t.text, "case"


def assert_tokens_equals(text, lang, expected):
    """
    Parsing a text using a language should give the expected parsed tokens.

    expected is given as array of:
    [ original_text, is_word, is_end_of_sentence ]
    """
    p = ThaiParser()
    actual = p.get_parsed_tokens(text, lang)
    expected = [ParsedToken(*a) for a in expected]
    assert [str(a) for a in actual] == [str(e) for e in expected]


def test_end_of_sentence_stored_in_parsed_tokens(thai):
    """
    ParsedToken is marked as EOS=True at ends of sentences.
    """
    s = "สวัสดีทุกคน! ฉันเรียนภาษาไทยมา2เดือนแล้วฯ"

    expected = [
        ("สวัสดี", True),
        ("ทุกคน", True),
        ("!", False, True),
        (" ", False, True),
        ("ฉัน", True),
        ("เรียน", True),
        ("ภาษาไทย", True),
        ("มา", True),
        ("2", False),
        ("เดือน", True),
        ("แล้ว", True, False),
        ("ฯ", False, True),
    ]
    assert_tokens_equals(s, thai, expected)


def test_carriage_returns_treated_as_reverse_p_character(thai):
    """
    Returns need to be marked with the backwards P for rendering etc.
    """
    s = "สวัสดีทุกคน!\nฉันเรียนภาษาไทยมา2เดือนแล้ว"

    expected = [
        ("สวัสดี", True),
        ("ทุกคน", True),
        ("!", False, True),
        ("¶", False, True),
        ("ฉัน", True),
        ("เรียน", True),
        ("ภาษาไทย", True),
        ("มา", True),
        ("2", False),
        ("เดือน", True),
        ("แล้ว", True, False),
    ]
    assert_tokens_equals(s, thai, expected)


def test_get_reading_rtgs_cases():
    """
    get_reading returns the RTGS romanization used as the reading
    above a Thai word.  Syllable boundaries are not marked: the
    reading is drawn as one rt per word.
    """
    p = ThaiParser()
    cases = [
        ("สวัสดี", "sawatdi"),
        ("ครับ", "khrap"),
        ("น้ำตก", "namtok"),
        ("ประเทศไทย", "prathetthai"),
        ("กรุงเทพมหานคร", "krungthepmahankhon"),
        ("โรงเรียน", "rongrian"),
        ("หนังสือ", "nangsue"),
        ("คอมพิวเตอร์", "khomphiwtoe"),
        ("วิทยาศาสตร์", "witthayasat"),
        ("มหาวิทยาลัย", "mahawitthayalai"),
        ("อาหาร", "ahan"),
        ("ผู้หญิง", "phuying"),
        ("เร็ว", "reo"),
        ("แมว", "maeo"),
        ("เดือน", "duean"),
        ("เครื่องบิน", "khrueangbin"),
        ("เกาะ", "ko"),
        ("หนึ่ง", "nueng"),
        ("ยืน", "yuen"),
        ("ตื่น", "tuen"),
        ("ข้าวเหนียว", "khaoniao"),
        ("สวัสดีปีใหม่", "sawatdipimai"),
    ]
    for text, expected in cases:
        assert p.get_reading(text) == expected, text


def test_get_reading_returns_none_for_non_thai():
    """
    Text with no romanizable Thai character (latin script, digits,
    punctuation) has no reading: the caller shows the word plain,
    exactly as a language without readings.
    """
    p = ThaiParser()
    for text in ["", "abc", "123", "!!!", "こんにちは", "3.14", "  "]:
        assert p.get_reading(text) is None, text


def test_get_reading_returns_none_for_uncovered_shapes():
    """
    A shape the rules do not cover yields None rather than a wrong
    reading, so the word is shown plain instead of misleading: ๆ
    repeats the syllable before it and there is none here.
    """
    p = ThaiParser()
    assert p.get_reading("ๆ") is None


def test_importing_the_parser_does_not_import_pythainlp():
    code = (
        "import sys;"
        "import lute_thai_parser.parser;"
        "assert 'pythainlp' not in sys.modules, "
        "'importing the parser pulled in pythainlp'"
    )
    subprocess.check_call([sys.executable, "-c", code])
