"""
"Parse as" dropdown tests.

The dropdown is built by _dropdown_parser_choices().  Two rules matter
here:

- a legacy parser (ref registry.__LUTE_LEGACY_PARSERS__) is never
  offered for a new language;
- the parser a language is *currently* set to must always stay visible,
  otherwise opening and saving the edit page would silently change the
  parser.
"""

from lute.language.routes import _dropdown_parser_choices
from lute.models.language import Language


def _keys(choices):
    return [k for k, _ in choices]


def test_new_language_does_not_offer_the_legacy_parser():
    "MeCab is the backup parser now, not a choice for a new language."
    keys = _keys(_dropdown_parser_choices(None))
    assert "japanese" not in keys, "legacy parser hidden"
    assert "japanese_sudachi" in keys, "preferred Japanese parser offered"
    assert "spacedel" in keys, "generic parser still offered"


def test_japanese_language_is_offered_sudachi_only():
    "A Japanese language on the preferred parser isn't offered MeCab."
    lang = Language()
    lang.name = "Japanese"
    lang.parser_type = "japanese_sudachi"

    keys = _keys(_dropdown_parser_choices(lang))
    assert "japanese_sudachi" in keys
    assert "japanese" not in keys


def test_existing_mecab_language_keeps_its_parser_selectable():
    """
    An existing language still on MeCab must be able to stay on MeCab --
    the current value is force-included even though it's legacy.
    """
    lang = Language()
    lang.name = "Japanese"
    lang.parser_type = "japanese"

    keys = _keys(_dropdown_parser_choices(lang))
    assert "japanese" in keys, "current value must never disappear"
    assert "japanese_sudachi" in keys


def test_non_japanese_language_is_not_offered_japanese_parsers():
    "Turkish shouldn't see the Japanese parsers."
    lang = Language()
    lang.name = "Turkish"
    lang.parser_type = "turkish"

    keys = _keys(_dropdown_parser_choices(lang))
    assert "japanese" not in keys
    assert "japanese_sudachi" not in keys
    assert "turkish" in keys
