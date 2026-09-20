"""
The criteria builder exists twice -- once in Python
(lute/review/criteria_builder.py, which validates and stores) and once
in JS (lute/static/js/lute-review-criteria.js, which drives the form).
If they disagree, the page silently rewrites the user's criteria on
save, so pin them together here.

Skips when node isn't on PATH (CI's pytest job doesn't guarantee it).
"""

import json
import os
import shutil
import subprocess

import pytest

from lute.review import criteria_builder as cb

NODE = shutil.which("node")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_HARNESS = os.path.join(_HERE, "criteria_builder_js_harness.js")
_MODULE = os.path.join(_ROOT, "lute", "static", "js", "lute-review-criteria.js")

# Inputs both implementations must agree on, including the ones they
# must both refuse.
CASES = [
    "",
    "   ",
    "status >= 2",
    "status >= 1 and status <= 5",
    "status = 2",
    "status <> 2",
    "status == 99",
    "status >= 2 or status <= 1",
    'language == "Japanese"',
    'language != "English"',
    'language:"Japanese"',
    "parents.count >= 1",
    "has:image",
    'tags:"vocab"',
    'tags:["vocab", "n2"]',
    'parents.tags:["a"]',
    'all.tags:["a", "b"]',
    'tags:["bread and butter"]',
    "status >= 2 and",
    "status >",
    'tags:["a"',
    'tags:"unclosed',
    "garbage == 1",
    "has:audio",
    "parents.count >= x",
    'status >= 2 and tags:["a"] or language == "X"',
]


@pytest.fixture(name="js_results", scope="module")
def fixture_js_results():
    "Run the JS implementation over CASES in node."
    if NODE is None:
        pytest.skip("node is not on PATH")
    if not os.path.exists(_HARNESS) or not os.path.exists(_MODULE):
        pytest.skip("criteria builder JS not found")

    payload = {
        "meta": {
            "fields": cb.FIELD_SPECS,
            # has_options is what the JS needs to accept/reject has:X.
            "has_options": ["image"],
        },
        "cases": CASES,
    }
    proc = subprocess.run(
        [NODE, _HARNESS, _MODULE],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("criteria", CASES)
def test_js_and_python_parsers_agree(criteria, js_results):
    "Both implementations must accept and reject exactly the same strings."
    py = cb.parse_criteria(criteria)
    js = js_results[criteria]
    if py is None:
        assert js is None, f"JS accepted what Python refused: {criteria!r} -> {js}"
        return
    assert js is not None, f"JS refused what Python accepted: {criteria!r}"
    assert js["parsed"] == py, criteria
    assert js["rebuilt"] == cb.build_criteria(py["joiner"], py["rows"]), criteria


DEMO_LANGUAGES = ["Japanese"]
DEMO_TAGS = ["vocab"]


def _run_harness(payload):
    "Feed a payload to the node harness and return its JSON output."
    if NODE is None:
        pytest.skip("node is not on PATH")
    proc = subprocess.run(
        [NODE, _HARNESS, _MODULE],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _demo_meta():
    "Metadata shaped like the route's builder_meta, for the harness."
    return {
        "fields": cb.FIELD_SPECS,
        "op_labels": cb._OP_LABELS,
        "has_options": ["image"],
        "statuses": [
            {"value": str(k), "label": f"{k} - {v}"}
            for k, v in sorted(cb.STATUS_LABELS.items())
        ],
        "languages": DEMO_LANGUAGES,
        "tags": DEMO_TAGS,
        "presets": cb.presets(DEMO_LANGUAGES, DEMO_TAGS),
        "default_preset": cb.DEFAULT_PRESET_ID,
    }


def test_js_init_renders_the_default_spec():
    "The page's init() must turn the default criteria into real controls."
    out = _run_harness(
        {
            "meta": _demo_meta(),
            "init": cb.parse_criteria(cb.default_criteria()),
        }
    )
    assert out["row_count"] == 2
    assert [r["field"] for r in out["rows"]] == ["status", "status"]
    assert [r["op"] for r in out["rows"]] == [">=", "<="]
    # The values must actually be selected in their dropdowns, not blank.
    assert [r["value"] for r in out["rows"]] == ["1", "5"]
    # The operator labels are words, not symbols.
    assert out["rows"][0]["op_labels"][0] == "at least"
    # The hidden field and the live preview agree with the spec.
    assert out["textarea"] == cb.default_criteria()
    assert out["preview"] == cb.default_criteria()
    # Nothing has gone wrong, so the raw-mode warning stays hidden.
    assert out["warning_hidden"] is True


def test_js_preset_list_is_grouped_and_complete():
    "Every preset the server sends must reach the dropdown."
    out = _run_harness(
        {"meta": _demo_meta(), "init": cb.parse_criteria(cb.default_criteria())}
    )
    groups = [g.get("label") for g in out["preset_groups"]]
    assert groups == [None, "Common", "By language", "By tag"]
    expected = cb.presets(DEMO_LANGUAGES, DEMO_TAGS)
    assert out["preset_count"] == len(expected)


def test_js_preset_selection_rewrites_the_textarea():
    "Picking a preset is what writes the stored criteria string."
    out = _run_harness(
        {
            "meta": _demo_meta(),
            "init": cb.parse_criteria(cb.default_criteria()),
            "act": [{"id": "criteria_preset", "value": "lang:Japanese"}],
        }
    )
    assert out["row_count"] == 1
    assert out["rows"][0]["field"] == "language"
    assert out["rows"][0]["op"] == "=="
    assert out["rows"][0]["value"] == "Japanese"
    assert out["textarea"] == 'language == "Japanese"'
    assert out["preview"] == 'language == "Japanese"'


def test_js_empty_criteria_shows_the_all_terms_note():
    "A blank criteria is not an error state; it says what it will do."
    out = _run_harness({"meta": _demo_meta(), "init": {"joiner": "and", "rows": []}})
    assert out["row_count"] == 0
    assert "every learning term is included" in out["empty_note"]
    assert out["textarea"] == ""
