"""
The reading pane's font picker is per language, and the picker's first
entry always means "leave the theme alone".

Those two properties are what the sidebar depends on and what breaks
silently if the tables are edited: an empty key that drifts away from
position 0 leaves the reader unable to get back to the theme's own
typography, and a language falling out of FONTS_BY_LANG hands a
Japanese book the Latin serif/sans pair, which looks like the browser
ignoring the choice rather than a missing table entry.
"""

import pytest

from lute.db import db
from lute.read.reader_fonts import (
    FONTS_BY_LANG,
    LATIN_FONTS,
    THEME_DEFAULT,
    font_choices,
)
from tests.utils import make_book


@pytest.mark.parametrize(
    "lang_code", ["ja", "zh", "yue", "ko", "ar", "th", "es", "fr", "de", "it", "pt", "en", "ru"]
)
def test_first_choice_is_the_theme_default(lang_code):
    choices = font_choices(lang_code)
    assert choices[0] == THEME_DEFAULT
    assert choices[0]["key"] == ""
    assert choices[0]["stack"] == ""


@pytest.mark.parametrize("lang_code", ["ja", "zh", "yue", "ko", "ar", "th", "es"])
def test_every_other_choice_carries_a_font_stack(lang_code):
    "An empty stack would render the option as another 'theme default'."
    for choice in font_choices(lang_code)[1:]:
        assert choice["stack"], choice
        assert choice["label"], choice
        # A family with spaces has to be quoted, or CSS reads the value
        # as a list of single words and matches nothing.
        assert '"' in choice["stack"], choice


def test_keys_are_unique_within_a_language():
    "The <option> value is the key, and it is what localStorage stores."
    for lang_code in FONTS_BY_LANG:
        keys = [c["key"] for c in font_choices(lang_code)]
        assert len(keys) == len(set(keys)), lang_code


def test_japanese_offers_mincho_gothic_and_round():
    keys = [c["key"] for c in font_choices("ja")]
    assert keys == ["", "mincho", "gothic", "maru"]


def test_chinese_and_cantonese_share_the_han_faces():
    "Cantonese is written with the same characters, and the same faces."
    assert font_choices("yue") == font_choices("zh")
    assert "songti" in [c["key"] for c in font_choices("yue")]


def test_unknown_language_falls_back_to_the_latin_pair():
    assert font_choices("xx") == [THEME_DEFAULT, *LATIN_FONTS]


def test_a_bcp47_tag_uses_its_primary_subtag():
    "tts_lang may carry a region (pt-BR) or a script (zh-Hant)."
    assert font_choices("pt-BR") == font_choices("pt")
    assert font_choices("zh-Hant") == font_choices("zh")
    assert font_choices("zh-Hant") != font_choices("ja")


def test_missing_or_empty_language_does_not_raise():
    assert font_choices(None) == [THEME_DEFAULT, *LATIN_FONTS]
    assert font_choices("") == [THEME_DEFAULT, *LATIN_FONTS]
    assert font_choices("  ") == [THEME_DEFAULT, *LATIN_FONTS]


def test_callers_cannot_mutate_the_tables():
    """
    The template and the tests both get a list; editing it must not
    change what the next request sees.
    """
    first = font_choices("ja")
    first.append({"key": "bogus"})
    assert "bogus" not in [c["key"] for c in font_choices("ja")]


def test_the_sidebar_offers_the_book_languages_faces(japanese, app_context, client):  # pylint: disable=unused-argument
    """
    read/routes.py resolves the list from the *book's* language, so the
    rendered page must carry those faces and the stack the JS applies.
    """
    book = make_book("Font picker", "たのしい", japanese)
    db.session.add(book)
    db.session.commit()

    body = client.get(f"/read/{book.id}").get_data(as_text=True)
    assert 'id="reader-font"' in body
    assert 'value="mincho"' in body
    assert "Hiragino Mincho ProN" in body
    # The Chromium-only loader ships hidden; text-options.js reveals it.
    assert 'id="reader-font-system"' in body


def test_a_latin_language_book_gets_the_latin_pair(english, app_context, client):  # pylint: disable=unused-argument
    "No Mincho in a Spanish/English book: the list follows the language."
    book = make_book("Font picker", "Hello there.", english)
    db.session.add(book)
    db.session.commit()

    body = client.get(f"/read/{book.id}").get_data(as_text=True)
    assert 'value="serif"' in body
    assert 'value="mincho"' not in body
