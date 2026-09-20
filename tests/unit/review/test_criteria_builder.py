"""
Criteria builder tests: the structured UI must round-trip the DSL
exactly, and must refuse (rather than mangle) anything it can't show.
"""

import pytest

from lute.db import db
from lute.models.term import TermTag
from lute.review import criteria_builder as cb


def test_blank_criteria_is_an_empty_builder():
    assert cb.parse_criteria("") == {"joiner": "and", "rows": []}
    assert cb.parse_criteria(None) == {"joiner": "and", "rows": []}
    assert cb.parse_criteria("   ") == {"joiner": "and", "rows": []}


@pytest.mark.parametrize(
    "criteria",
    [
        "status >= 2",
        "status >= 1 and status <= 5",
        'language == "Japanese"',
        'language != "English"',
        "parents.count >= 1",
        "has:image",
        'tags:"vocab"',
        'tags:["vocab", "n2"]',
        'all.tags:["a", "b"]',
        "status >= 2 or status <= 1",
        "status == 99",
    ],
)
def test_round_trip_is_stable(criteria):
    """
    build(parse(x)) == x, and parsing that again gives the same rows --
    so opening and saving a spec never rewrites the user's criteria.
    """
    parsed = cb.parse_criteria(criteria)
    assert parsed is not None, criteria
    rebuilt = cb.build_criteria(parsed["joiner"], parsed["rows"])
    assert rebuilt == criteria
    assert cb.parse_criteria(rebuilt) == parsed


@pytest.mark.parametrize(
    "criteria,expected",
    [
        # A one-element tag list is the same as a bare quoted tag; the
        # builder always emits the short form.  Semantically identical.
        ('parents.tags:["a"]', 'parents.tags:"a"'),
        ("status = 2", "status == 2"),
        ("status <> 2", "status != 2"),
    ],
)
def test_parse_normalizes_aliases(criteria, expected):
    "Aliases collapse to one canonical spelling."
    parsed = cb.parse_criteria(criteria)
    assert cb.build_criteria(parsed["joiner"], parsed["rows"]) == expected


@pytest.mark.parametrize(
    "criteria",
    [
        # Mixed and/or: the flat builder has one joiner, so it cannot
        # represent this and must hand back None (raw textarea).
        'status >= 2 and tags:["a"] or language == "X"',
        "garbage == 1",
        "status >",
        "status >=",
        'tags:"unclosed',
        'tags:["a"',
        "status >= 2 and",
        "has:audio",
        "parents.count >= x",
    ],
)
def test_unrepresentable_criteria_return_none(criteria):
    "Anything the builder can't show must fall back, never guess."
    assert cb.parse_criteria(criteria) is None, criteria


def test_keywords_inside_quotes_do_not_split():
    "A tag containing 'and' must not be mistaken for a joiner."
    parsed = cb.parse_criteria('tags:["bread and butter"]')
    assert parsed == {
        "joiner": "and",
        "rows": [{"field": "tags", "op": ":", "values": ["bread and butter"]}],
    }
    assert cb.build_criteria("and", parsed["rows"]) == 'tags:"bread and butter"'


def test_build_skips_incomplete_rows():
    "Half-filled rows are normal while typing; they emit nothing."
    rows = [
        cb._row("status", ">=", [""]),
        cb._row("status", "==", ["abc"]),
        cb._row("status", ">=", ["2"]),
        cb._row("language", "==", []),
        cb._row("nonsense", "==", ["1"]),
    ]
    assert cb.build_criteria("and", rows) == "status >= 2"


def test_build_normalizes_operators_and_joiners():
    "Only the DSL's own spellings are emitted."
    assert cb.build_criteria("AND", [cb._row("status", "==", ["2"])]) == "status == 2"
    assert cb.build_criteria("or", [cb._row("status", "==", ["2"])]) == "status == 2"
    assert (
        cb.build_criteria(
            "or", [cb._row("status", "==", ["2"]), cb._row("status", "==", ["1"])]
        )
        == "status == 2 or status == 1"
    )
    # '=' is normalized to '=='; a bogus op falls back to the field's first,
    # which is the useful '>=' rather than a surprising '<'.
    assert cb.build_criteria("and", [cb._row("status", "=", ["2"])]) == "status == 2"
    assert cb.build_criteria("and", [cb._row("status", "<>", ["2"])]) == "status != 2"
    assert cb.build_criteria("and", [cb._row("status", "!!", ["2"])]) == "status >= 2"
    assert cb.field_spec("status")["ops"][0] == ">="


def test_tags_are_deduped_and_quotes_stripped():
    rows = [cb._row("tags", ":", ['a"b', "a", "a"])]
    assert cb.build_criteria("and", rows) == 'tags:["ab", "a"]'


def test_default_criteria_is_usable():
    "The new-spec default must parse back into the builder."
    criteria = cb.default_criteria()
    assert criteria != ""
    parsed = cb.parse_criteria(criteria)
    assert parsed is not None
    assert cb.build_criteria(parsed["joiner"], parsed["rows"]) == criteria


def test_presets_cover_languages_and_tags():
    "Per-language and per-tag presets are generated from real data."
    presets = cb.presets(["Japanese", "Spanish"], ["vocab"])
    by_id = {p["id"]: p for p in presets}
    assert by_id["lang:Japanese"]["criteria"] == 'language == "Japanese"'
    assert by_id["tag:vocab"]["criteria"] == 'tags:"vocab"'
    # Every preset must survive a round trip through the builder.
    for p in presets:
        parsed = cb.parse_criteria(p["criteria"])
        if p["criteria"] == "":
            assert parsed == {"joiner": "and", "rows": []}
            continue
        assert parsed is not None, p["id"]
        assert cb.build_criteria(parsed["joiner"], parsed["rows"]) == p["criteria"]
    assert cb.DEFAULT_PRESET_ID in by_id


def test_builder_meta_shape(empty_db, spanish):
    "The template gets JSON-able metadata with the user's own data in it."
    db.session.add(TermTag("vocab"))
    db.session.commit()

    meta = cb.builder_meta(db.session, ["Spanish"])
    assert [f["name"] for f in meta["fields"]][0] == "status"
    assert "Spanish" in meta["languages"]
    assert "vocab" in meta["tags"]
    assert meta["has_options"] == ["image"]
    assert meta["default_preset"] == cb.DEFAULT_PRESET_ID
    # Statuses come from the statuses table, labelled "id - name".
    assert any(s["value"] == "99" for s in meta["statuses"])
    assert all(set(s) == {"value", "label"} for s in meta["statuses"])
    # Everything must be JSON-serializable (it is embedded in the page).
    import json  # pylint: disable=import-outside-toplevel

    json.dumps(meta)
