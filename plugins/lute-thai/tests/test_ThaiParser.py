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


def _sentence_groups(text, lang):
    """
    Parse text and return the tokens grouped into sentences
    (a new group starts after each end-of-sentence token).
    """
    p = ThaiParser()
    groups, cur = [], []
    for t in p.get_parsed_tokens(text, lang):
        cur.append(t)
        if t.is_end_of_sentence:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def test_word_spaced_text_one_sentence_per_line(thai):
    """
    LingQ-style "word-spaced" Thai (every word separated by a space,
    one line = one sentence) must NOT become one sentence per word:
    the space density marks spaces as word separators, so only the
    newline ends a sentence.  Regression: book 545 rendered one
    .textsentence (and one 🔊 button) per word.
    """
    # First two lines of LingQ Mini Stories 1a as stored in the DB.
    s = "ไมค์ ตื่นนอน ตอน หก โมงเช้า ทุกวัน\nเขา ทำ อาหารเช้า และ ดื่ม กาแฟ หนึ่ง ถ้วย"

    groups = _sentence_groups(s, thai)

    assert len(groups) == 2, [str(t) for g in groups for t in g]
    # Line 1 ends with the paragraph mark; line 2 has no trailing
    # newline, so it stays an open (no-EOS) straggler group.
    assert groups[0][-1].token == "¶", [str(t) for t in groups[0]]
    assert groups[1][-1].token == "ถ้วย", [str(t) for t in groups[1]]
    for group in groups:
        for t in group:
            assert not (t.token.strip() == "" and t.is_end_of_sentence), (
                f"space marked as EOS: {[str(x) for x in group]}"
            )


def test_traditional_spaced_text_still_splits_on_spaces(thai):
    """
    Traditionally written Thai (spaces only between sentences) keeps
    the old behaviour: a space ends a sentence, because the space
    density is far below the word-separator threshold.
    """
    s = "สวัสดีทุกคน ฉันเรียนภาษาไทยมา2เดือนแล้ว"

    groups = _sentence_groups(s, thai)

    assert len(groups) == 2, [str(t) for g in groups for t in g]
    assert groups[0][-1].token == " "
    assert groups[0][-1].is_end_of_sentence


def test_get_reading_rtgs_cases():
    """
    get_reading returns the RTGS romanization used as the reading
    above a Thai word.  Syllable boundaries are not marked: the
    reading is drawn as one rt per word.  Each syllable carries a
    Paiboon-style tone mark (grave low, circumflex falling, acute
    high, caron rising, none mid).
    """
    p = ThaiParser()
    cases = [
        ("สวัสดี", "sa\u0300wa\u0300tdi"),
        ("ครับ", "khra\u0301p"),
        ("น้ำตก", "na\u0301mto\u0300k"),
        ("ประเทศไทย", "pra\u0300the\u0302tthai"),
        ("กรุงเทพมหานคร", "krungthe\u0302pma\u0301ha\u030cnkhra\u0301"),
        ("โรงเรียน", "rongrian"),
        ("หนังสือ", "na\u030cngsu\u030ce"),
        ("คอมพิวเตอร์", "khomphiwtoe"),
        ("วิทยาศาสตร์", "wi\u0301tthayasa\u0300t"),
        ("มหาวิทยาลัย", "ma\u0301ha\u030cwi\u0301tthayalai"),
        ("อาหาร", "aha\u030cn"),
        ("ผู้หญิง", "phu\u0302yi\u030cng"),
        ("เร็ว", "reo"),
        ("แมว", "maeo"),
        ("เดือน", "duean"),
        ("เครื่องบิน", "khru\u0302engbin"),
        ("เกาะ", "ko\u0300"),
        ("หนึ่ง", "nu\u0300eng"),
        ("ยืน", "yuen"),
        ("ตื่น", "tu\u0302en"),
        ("ข้าวเหนียว", "kha\u0302oni\u030cao"),
        ("สวัสดีปีใหม่", "sa\u0300wa\u0300tdipima\u0300i"),
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


def test_get_reading_tone_marks():
    """
    The five tones each get their own mark: unmarked is mid, grave is
    low, circumflex falling, acute high, caron rising.  The same
    initial shows the contrast (kin vs kì, mâk vs lék).
    """
    p = ThaiParser()
    cases = [
        ("\u0e01\u0e34\u0e19", "kin"),  # กิน   kin    mid
        ("\u0e01\u0e35\u0e48", "ki\u0300"),  # กี่   ki\u0300    low
        ("\u0e21\u0e32\u0e01", "ma\u0302k"),  # มาก   ma\u0302k   falling
        ("\u0e40\u0e25\u0e47\u0e01", "le\u0301k"),  # เล็ก   le\u0301k   high
        ("\u0e2b\u0e19\u0e32", "na\u030c"),  # หนา   na\u030c   rising
    ]
    for text, expected in cases:
        assert p.get_reading(text) == expected, text


def test_glide_final_is_live():
    """
    A syllable ending in a glide stays live whatever the vowel length:
    the semivowel is a sonorant ending.  เร็ว /rew\u02d0\u00b3/ stays mid
    on its short diphthong (เร็ว, คิว, ปลิว), and the glide closes
    ลาย in มหาวิทยาลัย the same way.  A glide written as a plain
    final behaves identically (คิ้ว).
    """
    p = ThaiParser()
    cases = [
        ("\u0e40\u0e23\u0e47\u0e27", "reo"),  # เร็ว   re\u0301o->reo
        ("\u0e04\u0e34\u0e27", "khiw"),  # คิว
        ("\u0e1b\u0e25\u0e34\u0e27", "pliw"),  # ปลิว
        ("\u0e15\u0e31\u0e27", "tua"),  # ตัว
        ("\u0e1a\u0e31\u0e27", "bua"),  # บัว
        (
            "\u0e21\u0e2b\u0e32\u0e27\u0e34\u0e17\u0e22\u0e32\u0e25\u0e31\u0e22",
            "ma\u0301ha\u030cwi\u0301tthayalai",
        ),  # มหาวิทยาลัย
    ]
    for text, expected in cases:
        assert p.get_reading(text) == expected, text


def test_leading_h_tone():
    """
    A ห before a low-class onset moves the syllable to the high class:
    หนู and หมู read rising where นู and มู alone would be mid, and
    หนึ่ง is low with mai ek like a high-class initial (หนึ่ง nu\u0300eng).
    """
    p = ThaiParser()
    cases = [
        ("\u0e2b\u0e19\u0e39", "nu\u030c"),  # หนู   nu\u030c
        ("\u0e2b\u0e21\u0e39", "mu\u030c"),  # หมู   mu\u030c
        ("\u0e2b\u0e19\u0e36\u0e48\u0e07", "nu\u0300eng"),  # หนึ่ง
    ]
    for text, expected in cases:
        assert p.get_reading(text) == expected, text


def test_tone_overrides():
    """
    The override table forces irregular spellings that the chart would
    misread: the ี่ and -าย families (ใหม่ ma\u0300i, ไหม ma\u030ci,
    ใช่ cha\u0300i, อย่าง ya\u0300ng, อยู่ yu\u0300), while the chart
    still applies where the table is silent (ที่นี่ thi\u0302-ni\u0300).
    """
    p = ThaiParser()
    cases = [
        ("\u0e43\u0e2b\u0e21", "ma\u0300i"),  # ใหม่
        ("\u0e44\u0e2b\u0e21", "ma\u030ci"),  # ไหม
        ("\u0e43\u0e0a\u0e48", "cha\u0300i"),  # ใช่
        ("\u0e2d\u0e22\u0e48\u0e32\u0e07", "ya\u0300ng"),  # อย่าง
        ("\u0e2d\u0e22\u0e39\u0e48", "yu\u0300"),  # อยู่
        ("\u0e17\u0e35\u0e48\u0e19\u0e35\u0e48", "thi\u0302ni\u0300"),  # ที่นี่
    ]
    for text, expected in cases:
        assert p.get_reading(text) == expected, text


def test_get_reading_key():
    """
    get_reading_key appends each syllable's tone as a digit (mid 1
    ... rising 5) after the syllable, the shape the shadowing fuzzy
    rescue splits on.  Mid keeps its 1 so no syllable disappears.
    """
    p = ThaiParser()
    cases = [
        ("\u0e2a\u0e27\u0e31\u0e2a\u0e14\u0e35", "sa2wat2di1"),  # สวัสดี
        ("\u0e04\u0e23\u0e31\u0e1a", "khrap4"),  # ครับ
        ("\u0e2b\u0e19\u0e31\u0e07\u0e2a\u0e37\u0e2d", "nang5sue5"),  # หนังสือ
        ("\u0e2b\u0e19\u0e36\u0e48\u0e07", "nueng2"),  # หนึ่ง
        ("\u0e2d\u0e32\u0e2b\u0e32\u0e23", "a1han5"),  # อาหาร
    ]
    for text, expected in cases:
        assert p.get_reading_key(text) == expected, text


def test_vowel_length_table_complete():
    """
    Every romanizable vowel spelling carries a length, and the length
    is one of the two the tone chart branches on: the chart cannot be
    right for a vowel whose length is unknown.
    """
    from lute_thai_parser import romanize as r

    assert set(r._VOWELS) == set(r._VOWEL_LENGTH), "length table"
    for key, length in r._VOWEL_LENGTH.items():
        assert length in ("short", "long"), key
        assert r._VOWELS[key], key


def test_get_reading_golden():
    """
    Golden set: every word's reading (and key, where checked) against
    the form given by Wiktionary's Paiboon transcription.  Pins the
    whole rule surface -- tone chart, glide liveness, leading ห,
    overrides, cluster onsets.
    """
    p = ThaiParser()
    cases = [
        ("\u0e01\u0e34\u0e19", "kin", "kin1"),  # kin
        ("\u0e01\u0e35\u0e48", "ki\u0300", "ki2"),  # kì
        ("\u0e21\u0e32\u0e01", "ma\u0302k", "mak3"),  # mâk
        ("\u0e04\u0e23\u0e31\u0e1a", "khra\u0301p", "khrap4"),  # khráp
        ("\u0e40\u0e25\u0e47\u0e01", "le\u0301k", "lek4"),  # lék
        ("\u0e2a\u0e49\u0e33", "sa\u0302m", "sam3"),  # sâm
        ("\u0e42\u0e15\u0e4a\u0e40\u0e30", "to\u0301", "to4"),  # tó
        ("\u0e40\u0e01\u0e32\u0e30", "ko\u0300", "ko2"),  # kò
        ("\u0e40\u0e01\u0e31\u0e1a", "ke\u0300p", "kep2"),  # kèp
        ("\u0e04\u0e32\u0e1a", "kha\u0302p", "khap3"),  # khâp
        ("\u0e04\u0e1a", "kho\u0301p", "khop4"),  # khóp
        ("\u0e0a\u0e37\u0e48", "chu\u0302e", "chue3"),  # chûe
        ("\u0e04\u0e23\u0e39", "khru", "khru1"),  # khru
        ("\u0e19\u0e01", "no\u0301k", "nok4"),  # nók
        ("\u0e19\u0e49\u0e33", "na\u0301m", "nam4"),  # nám
        ("\u0e04\u0e34\u0e14", "khi\u0301t", "khit4"),  # khít
        ("\u0e23\u0e31\u0e01", "ra\u0301k", "rak4"),  # rák
        ("\u0e14\u0e35", "di", "di1"),  # di
        ("\u0e17\u0e31\u0e49\u0e07", "tha\u0301ng", "thang4"),  # tháng
        ("\u0e1b\u0e49\u0e32", "pa\u0302", "pa3"),  # pâ
        ("\u0e15\u0e31\u0e49\u0e07", "ta\u0302ng", "tang3"),  # tâng
        (
            "\u0e2a\u0e27\u0e31\u0e2a\u0e14\u0e35",
            "sa\u0300wa\u0300tdi",
            "sa2wat2di1",
        ),  # sàwàtdi
        (
            "\u0e1b\u0e23\u0e40\u0e17\u0e28\u0e44\u0e17\u0e22",
            "pra\u0300the\u0302tthai",
            None,
        ),  # pràthêtthai
        ("\u0e2b\u0e19\u0e32", "na\u030c", None),  # nǎ
        ("\u0e2b\u0e19\u0e36\u0e48\u0e07", "nu\u0300eng", None),  # nùeng
        ("\u0e2b\u0e21\u0e32", "ma\u030c", None),  # mǎ
        ("\u0e2d\u0e22\u0e39\u0e48", "yu\u0300", None),  # yù
        ("\u0e43\u0e2b\u0e21", "ma\u0300i", None),  # mài
        ("\u0e2d\u0e22\u0e48\u0e32", "ya\u0302", None),  # yâ
        ("\u0e44\u0e21\u0e49", "ma\u0301i", "mai4"),  # mái
        ("\u0e44\u0e2b\u0e21", "ma\u030ci", None),  # mǎi
        ("\u0e43\u0e0a\u0e48", "cha\u0300i", None),  # chài
        ("\u0e40\u0e01\u0e25\u0e31\u0e22", "kla\u0302i", None),  # klâi
        ("\u0e44\u0e01\u0e48", "ka\u0300i", None),  # kài
        (
            "\u0e40\u0e04\u0e23\u0e37\u0e48\u0e2d\u0e07",
            "khru\u0302eng",
            None,
        ),  # khrûeng
        ("\u0e1b\u0e25\u0e35\u0e48\u0e22\u0e19", "pli\u0300an", None),  # plìan
        ("\u0e40\u0e25\u0e48\u0e19", "le\u0302n", None),  # lên
        ("\u0e40\u0e1e\u0e23\u0e32\u0e30", "phro\u0301", None),  # phró
        ("\u0e19\u0e49\u0e2d\u0e07", "no\u0301ng", None),  # nóng
        ("\u0e2d\u0e32\u0e2b\u0e32\u0e23", "aha\u030cn", None),  # ahǎn
        (
            "\u0e2b\u0e19\u0e31\u0e07\u0e2a\u0e37\u0e2d",
            "na\u030cngsu\u030ce",
            None,
        ),  # nǎngsǔe
        ("\u0e17\u0e35\u0e48\u0e19\u0e35\u0e48", "thi\u0302ni\u0300", None),  # thînì
        ("\u0e2d\u0e30\u0e44\u0e23", "a\u0300rai", None),  # àrai
        ("\u0e19\u0e31\u0e19\u0e4c", "na\u0301", None),  # ná
        ("\u0e15\u0e31\u0e27", "tua", None),  # tua
        ("\u0e19\u0e32\u0e19", "nan", "nan1"),  # nan
    ]
    for text, expected, expected_key in cases:
        assert p.get_reading(text) == expected, text
        if expected_key is not None:
            assert p.get_reading_key(text) == expected_key, text
