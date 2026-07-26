"""
JapaneseParser tests.
"""

from lute.parse.mecab_parser import JapaneseParser
from lute.models.term import Term
from lute.settings.current import current_settings
from lute.parse.base import ParsedToken


def test_token_count(japanese):
    "token_count checks."
    cases = [("私", 1), ("元気", 1), ("です", 1), ("元気です", 2), ("元気です私", 3)]
    for text, expected_count in cases:
        t = Term(japanese, text)
        assert t.token_count == expected_count, text
        assert t.text_lc == t.text, "case"


def assert_tokens_equals(text, lang, expected):
    """
    Parsing a text using a language should give the expected parsed tokens.

    expected is given as array of:
    [ original_text, is_word, is_end_of_sentence ]
    """
    p = JapaneseParser()
    actual = p.get_parsed_tokens(text, lang)
    expected = [ParsedToken(*a) for a in expected]
    assert [str(a) for a in actual] == [str(e) for e in expected]


def test_end_of_sentence_stored_in_parsed_tokens(japanese):
    "ParsedToken is marked as EOS=True at ends of sentences."
    s = "元気.元気?元気!\n元気。元気？元気！"

    expected = [
        ("元気", True),
        (".", False, True),
        ("元気", True),
        ("?", False, True),
        ("元気", True),
        ("!", False, True),
        ("¶", False, True),
        ("元気", True),
        ("。", False, True),
        ("元気", True),
        ("？", False, True),
        ("元気", True),
        ("！", False, True),
        ("¶", False, True),
    ]
    assert_tokens_equals(s, japanese, expected)


def test_issue_488_repeat_character_handled(japanese):
    "Repeat sometimes needs explicit check, can be returned as own word."
    s = "聞こえる行く先々。少々お待ちください。"

    expected = [
        ("聞こえる", True),
        ("行く先", True),
        ("々", True),
        ("。", False, True),
        ("少々", True),
        ("お待ち", True),
        ("ください", True),
        ("。", False, True),
        ("¶", False, True),
    ]
    assert_tokens_equals(s, japanese, expected)


def test_readings(app_context):
    """
    Parser returns readings if they add value.
    """
    p = JapaneseParser()

    # Don't bother giving reading for a few cases
    no_reading = ["NHK", "ツヨイ", "どちら"]  # roman  # only katakana  # only hiragana

    for c in no_reading:
        assert p.get_reading(c) is None, c

    zws = "\u200B"
    cases = [
        ("強い", "つよい"),
        ("二人", "ににん"),  # ah well, not perfect :-)
        ("強いか", "つよいか"),
        (f"強い{zws}か", "つよいか"),  # zws stripped before processing
    ]

    for c in cases:
        assert p.get_reading(c[0]) == c[1], c[0]


def test_reading_setting(app_context):
    "Return reading matching user setting."
    cases = {
        "katakana": "ツヨイ",
        "hiragana": "つよい",
        "alphabet": "tsuyoi",
    }
    p = JapaneseParser()
    for k, v in cases.items():
        current_settings["japanese_reading"] = k
        assert p.get_reading("強い") == v, k


def test_dict_type_auto_detects_ipadic_by_default(app_context):
    "Auto-detection should pick up whatever the system has installed."
    # Clear the cache to force a fresh detection.
    JapaneseParser._dict_type = None
    JapaneseParser._old_dict_setting = None
    current_settings["japanese_dict"] = "auto"
    detected = JapaneseParser._detect_dict_type()
    # We don't know which dict the dev machine has, but the
    # detection should always return one of the two valid values
    # without throwing.
    assert detected in ("ipadic", "unidic")


def test_dict_type_respects_explicit_ipadic_setting(app_context):
    "Explicit ipadic setting skips detection and returns ipadic."
    JapaneseParser._dict_type = None
    JapaneseParser._old_dict_setting = None
    current_settings["japanese_dict"] = "ipadic"
    assert JapaneseParser._detect_dict_type() == "ipadic"


def test_dict_type_respects_explicit_unidic_setting(app_context):
    "Explicit unidic setting skips detection and returns unidic."
    JapaneseParser._dict_type = None
    JapaneseParser._old_dict_setting = None
    current_settings["japanese_dict"] = "unidic"
    assert JapaneseParser._detect_dict_type() == "unidic"


def test_dict_type_is_cached(app_context):
    "Second call with same settings returns cached value."
    JapaneseParser._dict_type = None
    JapaneseParser._old_dict_setting = None
    current_settings["japanese_dict"] = "auto"
    first = JapaneseParser._detect_dict_type()
    second = JapaneseParser._detect_dict_type()
    assert first == second


def test_dict_type_cache_invalidates_on_setting_change(app_context):
    "Changing the dict setting invalidates the cache."
    JapaneseParser._dict_type = None
    JapaneseParser._old_dict_setting = None
    current_settings["japanese_dict"] = "ipadic"
    assert JapaneseParser._detect_dict_type() == "ipadic"
    # Change to unidic -- cache should be invalidated.
    current_settings["japanese_dict"] = "unidic"
    assert JapaneseParser._detect_dict_type() == "unidic"


def test_get_reading_with_unidic_returns_reading(app_context):
    """
    When set to unidic mode, get_reading uses feature field index 9
    (仮名形 / kana form of the surface) to extract the reading.
    With an actual Unidic install this would be the proper reading;
    with IPADIC field 9 is not the right field, so we just verify
    the code path doesn't crash and returns None or a string.
    """
    current_settings["japanese_dict"] = "unidic"
    current_settings["japanese_reading"] = "hiragana"
    # Reset cache so detection picks up the explicit setting.
    JapaneseParser._dict_type = None
    JapaneseParser._old_dict_setting = None
    p = JapaneseParser()
    # Should not throw; may return None or a string depending on
    # the actual dictionary installed.
    result = p.get_reading("強い")
    assert result is None or isinstance(result, str)


def test_get_lemma_basic_form_returns_none(app_context):
    "For words already in dictionary form, get_lemma returns None."
    p = JapaneseParser()
    # 強い is already the basic/dictionary form.
    result = p.get_lemma("強い")
    assert result is None


def test_get_lemma_inflected_form_returns_base(app_context):
    "For inflected verbs/adjectives, get_lemma returns the base form."
    p = JapaneseParser()
    # 広がっ (te-form) -> 広がる (base form)
    result = p.get_lemma("広がっ")
    assert result == "広がる"


def test_get_lemma_hiragana_returns_none(app_context):
    "For all-hiragana text, get_lemma returns None."
    p = JapaneseParser()
    result = p.get_lemma("わたし")
    assert result is None


def test_get_lemma_unidic_mode_does_not_crash(app_context):
    "When set to unidic mode, get_lemma doesn't crash."
    current_settings["japanese_dict"] = "unidic"
    JapaneseParser._dict_type = None
    JapaneseParser._old_dict_setting = None
    p = JapaneseParser()
    # With IPADIC this may return None or a wrong value, but
    # should not throw an exception.
    result = p.get_lemma("広がっ")
    assert result is None or isinstance(result, str)
