"""Tests for the pure Eudic mapping rules."""

import pytest

from lute.eudic.mapping import (
    clean_exp,
    clean_phon,
    is_pushable,
    normalize_word,
    push_form,
    split_reading,
    star_for_status,
    status_for_star,
    strip_reading,
    truncate,
    word_key,
)


# --- normalize_word -------------------------------------------------------


def test_zws_becomes_space_for_space_delimited_words():
    "Lute stores 'give up' as 'give\\u200Bup'; Eudic needs a real space."
    assert normalize_word("give\u200Bup") == "give up"


def test_zws_vanishes_inside_cjk_words():
    "A Japanese multi-token term must not gain spaces in transit."
    assert normalize_word("気\u200Bが\u200Bつく") == "気がつく"


def test_normalize_word_collapses_whitespace_and_exotic_spaces():
    assert normalize_word("  a \u00A0 b \u3000 c  ") == "a b c"


def test_normalize_word_handles_empty():
    assert normalize_word("") == ""
    assert normalize_word(None) == ""


# --- split_reading --------------------------------------------------------


@pytest.mark.parametrize(
    "raw,word,reading",
    [
        ("俺 [おれ]", "俺", "おれ"),
        ("まつる", "まつる", ""),
        ("食べる【たべる】", "食べる", "たべる"),
        ("hello", "hello", ""),
        (" 俺 [おれ] ", "俺", "おれ"),
    ],
)
def test_split_reading(raw, word, reading):
    assert split_reading(raw) == (word, reading)


def test_strip_reading_keeps_a_fully_bracketed_word():
    "Better to keep a weird word than to produce an empty one."
    assert strip_reading("[おれ]") == "[おれ]"


# --- word_key -------------------------------------------------------------


def test_word_key_ignores_the_reading_marker():
    "This is what stops every sync from adding a duplicate entry."
    assert word_key("俺 [おれ]") == word_key("俺") == "俺"
    assert word_key("俺 [おら]") == word_key("俺 [おれ]")


def test_word_key_ignores_case():
    assert word_key("Anthrax") == "anthrax"


def test_word_key_ignores_cjk_whitespace():
    assert word_key("気 が つく") == word_key("気がつく") == "気がつく"


def test_word_key_keeps_spaces_between_latin_words():
    assert word_key("West Virginia") == "west virginia"


def test_word_key_handles_zws_from_a_lute_term():
    assert word_key("give\u200Bup") == "give up"


# --- push_form ------------------------------------------------------------


def test_push_form_rebuilds_eudic_reading_notation():
    assert push_form("俺", "おれ") == "俺 [おれ]"


def test_push_form_without_reading_is_the_bare_word():
    assert push_form("まつる", "") == "まつる"


def test_push_form_reuses_the_remote_string_verbatim():
    """
    Keeps the remote key stable even when the local reading differs or
    is missing, so the word is updated instead of duplicated.
    """
    assert push_form("俺", "", remote_word="俺 [おれ]") == "俺 [おれ]"
    assert push_form("俺", "おら", remote_word="俺 [おれ]") == "俺 [おれ]"


def test_push_form_joins_multi_token_terms_correctly():
    assert push_form("give\u200Bup", "") == "give up"
    assert push_form("気\u200Bが", "きが") == "気が [きが]"


# --- status <-> star ------------------------------------------------------


@pytest.mark.parametrize("status,star", [(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)])
def test_statuses_one_to_five_map_directly(status, star):
    assert star_for_status(status) == star
    assert status_for_star(star) == status


def test_well_known_degrades_to_the_top_star():
    "Eudic's mastered list is GET/DELETE only, so it cannot be written."
    assert star_for_status(99) == 5


@pytest.mark.parametrize("status", [0, 98, None, "", 7, -1, "abc"])
def test_statuses_that_must_not_be_pushed(status):
    assert star_for_status(status) is None


@pytest.mark.parametrize("star", [0, 6, -1, None, "", "x"])
def test_stars_that_are_not_a_status(star):
    assert status_for_star(star) is None


def test_is_pushable():
    assert is_pushable(1) and is_pushable(99)
    assert not is_pushable(0) and not is_pushable(98)


# --- text cleanups --------------------------------------------------------


def test_clean_phon_strips_the_slashes():
    assert clean_phon("/'ækʃn/") == "'ækʃn"
    assert clean_phon("") == ""
    assert clean_phon(None) == ""


def test_clean_exp_flattens_line_breaks():
    assert clean_exp("n. 甲\r\n乙\n丙") == "n. 甲 乙 丙"


def test_truncate():
    assert truncate("abcdef", 3) == "abc"
    assert truncate("ab", 3) == "ab"
    assert truncate(None, 3) is None
