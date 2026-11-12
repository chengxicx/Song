"""
Tests for getting TextItems.
"""

from lute.models.term import Term
from lute.parse.base import ParsedToken
from lute.read.render.calculate_textitems import get_textitems


def make_tokens(token_data):
    "Make ParsedTokens for the data."
    return [ParsedToken(t, 1) for t in token_data]


def assert_renderable_equals(
    language, token_data, term_data, expected, expected_displayed=None
):
    """
    Run the given scenario:

    Given tokens in language, and terms saved in that language,
    the calculator should render the expected terms,
    only displaying the appropriate parts of each.
    """
    tokens = make_tokens(token_data)
    terms = [Term(language, t) for t in term_data]

    tis = get_textitems(tokens, terms, language)
    res = ""
    for ti in tis:
        res += f"[{ti.text}-{ti.token_count}]"

    zws = chr(0x200B)
    res = res.replace(zws, "")

    assert res == expected

    if expected_displayed is not None:
        res = ""
        for ti in tis:
            res += f"[{ti.display_text}-{ti.token_count}]"

        res = res.replace(zws, "")
        assert res == expected_displayed


def test_simple_render(english):
    "Tokens with no defined terms are rendered as-is."
    data = ["some", " ", "data", " ", "here", "."]
    expected = "[some-1][ -1][data-1][ -1][here-1][.-1]"
    assert_renderable_equals(english, data, [], expected)


def test_non_matching_terms_are_ignored(english):
    "Non-match ignored."
    data = ["some", " ", "data", " ", "here", "."]
    expected = "[some-1][ -1][data-1][ -1][here-1][.-1]"
    assert_renderable_equals(english, data, ["ignoreme"], expected)


def test_partial_matching_terms_are_ignored(english):
    "Partial match is not the same as a match."
    data = ["some", " ", "data", " ", "here", "."]
    expected = "[some-1][ -1][data-1][ -1][here-1][.-1]"
    assert_renderable_equals(english, data, ["data he"], expected)


def test_multiword_items_cover_other_items(english):
    """
    Given a multiword term, some of the other terms are hidden.
    """
    data = ["some", " ", "data", " ", "here", "."]
    words = [
        "data here",
    ]
    expected = "[some-1][ -1][data here-3][.-1]"
    assert_renderable_equals(english, data, words, expected)


def test_case_not_considered_for_matches(english):
    "Case doesn't matter."
    data = ["some", " ", "data", " ", "here", "."]
    expected = "[some-1][ -1][data here-3][.-1]"
    assert_renderable_equals(english, data, ["DATA HERE"], expected)


def test_term_found_in_multiple_places(english):
    "Term can be in a few places."
    data = [
        "some",
        " ",
        "data",
        " ",
        "here",
        " ",
        "more",
        " ",
        "data",
        " ",
        "here",
        ".",
    ]
    expected = "[some-1][ -1][data here-3][ -1][more-1][ -1][data here-3][.-1]"
    assert_renderable_equals(english, data, ["DATA HERE"], expected)


def test_overlapping_multiwords(english):
    """
    Given two overlapping terms, they're both displayed,
    but some of the second is cut off.
    """
    data = ["some", " ", "data", " ", "here", "."]
    words = [
        "some data",
        "data here",
    ]
    expected = "[some data-3][data here-3][.-1]"
    expected_displayed = "[some data-3][ here-3][.-1]"
    assert_renderable_equals(english, data, words, expected, expected_displayed)


def test_multiword_selection_uses_display_text_when_terms_overlap(english):
    """
    Regression for the reading pane's multiword selection.

    lute-tooltip.js joins the selected spans to build the /read/termform
    text.  A span's data-text holds the FULL term ("data here"), but an
    overlapped span renders only its un-covered tail (" here").  Joining
    the full texts counted the shared "data" twice and created a term
    that could never match the page.  Joining display_text (what the new
    data-display-text attribute carries) must give the visible tokens.
    """
    data = ["some", " ", "data", " ", "here", "."]
    terms = [Term(english, t) for t in ("some data", "data here")]
    # The two multiword term spans.  The trailing "." is a single-token
    # item that is not part of the overlap under test.
    tis = [
        ti
        for ti in get_textitems(make_tokens(data), terms, english)
        if ti.token_count > 1
    ]

    zws = chr(0x200B)

    def selected_tokens(attr):
        "Tokens the JS would send, split back out at the ZWS boundaries."
        return [tok for ti in tis for tok in getattr(ti, attr).split(zws)]

    assert selected_tokens("display_text") == data[:-1]
    assert "".join(selected_tokens("display_text")) == "some data here"
    # Sanity: the on-screen text really is what we just reconstructed.
    assert "".join(ti.html_display_text for ti in tis) == "some data here"
    # The old data('text') join duplicated the shared token.
    assert selected_tokens("text") == ["some", " ", "data", "data", " ", "here"]
    # The template only emits data-display-text for the cut-off span.
    assert [ti.display_text != ti.text for ti in tis] == [False, True]


def test_multiwords_starting_at_same_location(english):
    """
    Test overlapping matches.
    Given two terms that contain the same chars at the start,
    the longer term overwrites the shorter.
    """
    data = ["A", " ", "B", " ", "C", " ", "D"]
    words = [
        "A B",
        "A B C",
    ]
    expected = "[A B C-5][ -1][D-1]"
    assert_renderable_equals(english, data, words, expected)


def test_crazy_case(english):
    """
    Crazy test case covering the scenario in the class docstring.
    """
    data = [
        "A",
        " ",
        "B",
        " ",
        "C",
        " ",
        "D",
        " ",
        "E",
        " ",
        "F",
        " ",
        "G",
        " ",
        "H",
        " ",
        "I",
    ]
    words = [
        "B C",  # J
        "E F G H I",  # K
        "F G",  # L
        "C D E",  # M
    ]
    expected = "[A-1][ -1][B C-3][C D E-5][E F G H I-9]"
    expected_displayed = "[A-1][ -1][B C-3][ D E-5][ F G H I-9]"
    assert_renderable_equals(english, data, words, expected, expected_displayed)


def test_zero_width_space_tokens_do_not_crash(english):
    """
    A token that is itself a zero-width space (the Korean Kiwi tokenizer
    emits these for the reader's empty-paragraph placeholder) used to
    produce out-of-range indexes in the multi-word-term search -- the zws
    is both the term separator and a token -- and crash with IndexError.
    The bogus items are skipped instead.
    """
    zws = chr(0x200B)
    tokens = [zws, zws, zws]
    tis = get_textitems(make_tokens(tokens), [], english)
    assert len(tis) > 0
    assert all(0 <= ti.index and ti.index + ti.token_count <= len(tokens) for ti in tis)
