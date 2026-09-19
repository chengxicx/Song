"""
Parser registry tests.
"""

import pytest

from lute.parse.registry import (
    __LUTE_PARSERS__,
    get_parser,
    is_legacy_parser,
    selectable_parsers,
    supported_parsers,
    supported_parser_types,
    is_supported,
)
from lute.parse.mecab_parser import JapaneseParser
from lute.parse.space_delimited_parser import SpaceDelimitedParser


def test_get_parser_by_name():
    p = get_parser("spacedel")
    assert isinstance(p, SpaceDelimitedParser)


def test_get_parser_throws_if_not_found():
    "Check error message thrown."
    e = None
    try:
        _ = get_parser("trash")
    except ValueError as ex:
        e = ex
    assert e is not None, "Have ValueError"
    assert str(e) == "Unknown parser type 'trash'", "message"


def test_supported_parsers():
    "Sanity check only."
    d = supported_parsers()
    assert isinstance(d, list), "returns a list"

    p = [n for n in d if n[0] == "spacedel"][0]
    assert [p[0], p[1].name()] == ["spacedel", "Space Delimited"], "sanity check"


def test_supported_parser_types():
    "Sanity check only."
    d = supported_parser_types()
    assert isinstance(d, list), "returns a list"
    assert "spacedel" in d, "sanity check"


class DummyParser:
    "Dummy unsupported parser."

    @classmethod
    def is_supported(cls):
        return False

    @classmethod
    def name(cls):
        return "DUMMY"


@pytest.fixture(name="_load_dummy")
def fixture_load_dummy():
    "Add the dummy parser for the test."
    __LUTE_PARSERS__["dummy"] = DummyParser
    yield
    del __LUTE_PARSERS__["dummy"]


def test_unavailable_parser_not_included_in_lists(_load_dummy):
    "An unsupported parser shouldn't be available."
    d = supported_parsers()
    assert "dummy" not in d, "not present"
    assert is_supported("dummy") is False, "no"
    with pytest.raises(ValueError):
        get_parser("dummy")


def test_get_parser_throws_if_parser_not_supported(_load_dummy):
    "Check throw."
    e = None
    try:
        _ = get_parser("dummy")
    except ValueError as ex:
        e = ex
    assert e is not None, "Have ValueError"
    assert str(e) == "Unsupported parser type 'dummy'", "message"


# ---- legacy parsers ----


def test_legacy_parser_stays_a_supported_type():
    """
    A legacy parser must keep showing up in supported_parser_types():
    the book and term lists filter on that, so dropping it would hide
    the books and terms of every language still using it.
    """
    if not JapaneseParser.is_supported():
        pytest.skip("mecab is not installed")

    assert is_legacy_parser("japanese") is True
    assert is_supported("japanese") is True
    assert "japanese" in supported_parser_types(), "old data stays visible"
    assert "japanese" in [k for k, _ in supported_parsers()]


@pytest.mark.skipif(
    not is_supported("japanese_sudachi"),
    reason="sudachi extra not installed; the parser is not selectable",
)
def test_legacy_parser_is_not_selectable():
    "It isn't offered for a new language."
    keys = [k for k, _ in selectable_parsers()]
    assert "japanese" not in keys
    assert "japanese_sudachi" in keys


def test_non_legacy_parsers_are_all_selectable():
    "Everything else is still offered."
    selectable = [k for k, _ in selectable_parsers()]
    for key, klass in supported_parsers():
        if is_legacy_parser(key):
            assert key not in selectable, key
        else:
            assert key in selectable, key
        assert klass.name(), key
