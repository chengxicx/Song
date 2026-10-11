"""
The settings page's "Local dictionaries (MDX)" card (static/js/dict-settings.js).

The card is the only consumer of the /dict endpoints' JSON, and the field
names are a contract: the Python side sends `dictionaries[].filename`,
`.title`, `.entries`, `.size_mb`, `.source`, `.url`, `.is_default`, plus
`task_id`, `state`, `processed`, `error`, `dictionary.entries` and the
remove payload's `result`/`removed`.  Rename one on either side and
nothing throws -- the card renders blank, or the progress poll never
settles, and no Python test can see it.  So the real module is run
against a stub DOM and a stubbed network
(dict_settings_js_harness.js) and the rendered strings are pinned.

Skips when node is not on PATH.
"""

import json
import os
import shutil
import subprocess

import pytest

NODE = shutil.which("node")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_HARNESS = os.path.join(_HERE, "dict_settings_js_harness.js")
_MODULE = os.path.join(_ROOT, "lute", "static", "js", "dict-settings.js")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

INSTALLED = {
    "installed": True,
    "filename": "ja_zh_shogakukan_v2.db",
    "url": "/static/dict/ja_zh_shogakukan_v2.db",
    "is_default": True,
    "size_mb": 42.5,
    "modified": "2026-10-11 18:40",
    "source": "shogakukan_v2.mdx",
    "title": "小学馆V2日汉辞典",
    "entries": 69425,
    "terms": 70307,
    "imported_at": "2026-10-11 18:40",
}

BARE = {
    "installed": True,
    "filename": "ko_zh_basic.db",
    "url": "/static/dict/ko_zh_basic.db",
    "is_default": False,
    "size_mb": 12.0,
    "modified": "2026-10-11 18:40",
    "source": None,
    "title": None,
    "entries": None,
    "terms": None,
    "imported_at": None,
}


def _run(payload):
    "Run one harness scenario and return its JSON output."
    proc = subprocess.run(
        [NODE, _HARNESS, _MODULE],
        input=json.dumps(payload).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    stdout = proc.stdout.decode("utf-8")
    assert stdout, proc.stderr.decode("utf-8")
    result = json.loads(stdout)
    assert "error" not in result, result.get("stack")
    return result


# --- the one-line summary ---------------------------------------------------


def test_status_line_describes_the_dictionary():
    result = _run({"scenario": "status_text", "status": INSTALLED})

    assert "小学馆V2日汉辞典" in result["text"]
    assert "69,425 entries" in result["text"]
    assert "42.5 MB" in result["text"]
    assert "shogakukan_v2.mdx" in result["text"]
    assert "2026-10-11 18:40" in result["text"]


def test_status_line_says_so_when_there_is_no_dictionary():
    assert _run({"scenario": "status_text"})["text"] == "No dictionary imported yet."


def test_status_line_falls_back_to_the_file_name_for_a_cli_built_dictionary():
    # A dictionary built with tools/mdx_to_sqlite.py and dropped into
    # the folder has no metadata sidecar: no title, no counts.
    result = _run({"scenario": "status_text", "status": BARE})

    assert result["text"] == "ko_zh_basic.db \u00b7 12 MB"
    assert result["described"] == "12 MB"


def test_status_line_copes_with_a_bare_status():
    # A missing key must not throw and blank the whole card.
    assert _run({"scenario": "status_text", "status": {}})["text"]


# --- the list ---------------------------------------------------------------


def test_render_lists_every_dictionary_in_the_pool():
    dom = _run({"scenario": "render", "dictionaries": [INSTALLED, BARE]})["dom"]
    html = dom["dict_settings_list"]["html"]

    assert "小学馆V2日汉辞典" in html
    assert "ja_zh_shogakukan_v2.db" in html
    assert "ko_zh_basic.db" in html
    assert "69,425 entries" in html
    # The download URL is what a browser -- or the phone -- fetches.
    assert 'href="/static/dict/ja_zh_shogakukan_v2.db"' in html


def test_render_marks_the_phones_default_file():
    dom = _run({"scenario": "render", "dictionaries": [INSTALLED, BARE]})["dom"]
    html = dom["dict_settings_list"]["html"]

    assert html.count("phone default") == 1
    assert "phone default" in html.split("ko_zh_basic.db")[0]


def test_render_says_so_when_the_pool_is_empty():
    dom = _run({"scenario": "render", "dictionaries": []})["dom"]

    assert "No dictionary imported yet." in dom["dict_settings_list"]["html"]


def test_each_row_removes_its_own_dictionary():
    html = _run({"scenario": "row_html", "status": BARE})["html"]

    assert "DictSettings.remove('ko_zh_basic.db')" in html


def test_the_row_escapes_the_title_from_the_dictionary_file():
    # The title is whatever the .mdx carried, and it is written into the
    # list with innerHTML.
    hostile = dict(INSTALLED, title='<img src=x onerror="alert(1)">')
    html = _run({"scenario": "row_html", "status": hostile})["html"]

    assert "<img" not in html
    assert "&lt;img" in html


# --- the conversion poll ----------------------------------------------------


def test_poll_resolves_when_the_conversion_finishes():
    result = _run(
        {
            "scenario": "poll",
            "importStatus": [
                {"state": "queued", "processed": 0},
                {"state": "running", "processed": 4000},
                {
                    "state": "finished",
                    "dictionary": {"entries": 69425, "title": "小学馆V2日汉辞典"},
                },
            ],
        }
    )

    assert result["resolved"]["state"] == "finished"
    assert result["resolved"]["dictionary"]["entries"] == 69425
    assert "4,000 entries read" in result["messages"]


def test_poll_rejects_with_the_server_message():
    result = _run(
        {
            "scenario": "poll",
            "importStatus": [
                {"state": "running", "processed": 100},
                {
                    "state": "error",
                    "error": "MDX: encrypted dictionary is not supported",
                },
            ],
        }
    )

    assert result["rejected"] == "MDX: encrypted dictionary is not supported"


# --- import -----------------------------------------------------------------


def test_import_reports_the_upload_then_the_finished_count():
    result = _run(
        {
            "scenario": "import",
            "importStatus": [
                {"state": "running", "processed": 2000},
                {"state": "finished", "dictionary": {"entries": 69425}},
            ],
        }
    )

    # The file goes up over XHR (that is where upload progress comes
    # from), then the conversion is polled, then the card refreshes.
    assert result["uploads"] == [{"method": "POST", "url": "/dict/import"}]
    assert [c["url"] for c in result["calls"]] == [
        "/dict/import_status/t1",
        "/dict/import_status/t1",
        "/dict/status",
    ]
    assert result["dom"]["dict_settings_msg"]["text"] == "Imported 69,425 entries."
    # Upload progress was surfaced before the poll started.
    assert any("25%" in m for m in result["progress"])
    # The card is interactive again when the import is over.
    assert result["dom"]["dict_settings_import"]["disabled"] is False


def test_import_sends_the_chosen_name():
    result = _run({"scenario": "import", "name": "ko_zh_basic.db"})

    assert result["fields"] == [
        [["mdx", {"name": "fixture.mdx"}], ["name", "ko_zh_basic.db"]]
    ]
    # The card says which file it is building, not just "converting".
    assert any("into ko_zh_basic.db" in m for m in result["messageLog"])


def test_import_without_a_name_lets_the_server_derive_one():
    result = _run({"scenario": "import"})

    assert result["fields"] == [[["mdx", {"name": "fixture.mdx"}]]]
    # The response carries the name the server chose.
    assert any("into fixture.db" in m for m in result["messageLog"])


def test_import_selected_takes_the_name_from_the_text_box():
    result = _run(
        {
            "scenario": "import_selected",
            "selectedFile": {"name": "fixture.mdx"},
            "typedName": "my_dict.db",
        }
    )

    assert result["fields"] == [
        [["mdx", {"name": "fixture.mdx"}], ["name", "my_dict.db"]]
    ]


def test_import_surfaces_an_upload_rejection():
    result = _run(
        {
            "scenario": "import",
            "xhr": {
                "status": 400,
                "body": {
                    "result": "failure",
                    "message": '"a.txt" is not an .mdx file.',
                },
            },
        }
    )

    assert result["dom"]["dict_settings_msg"]["text"] == '"a.txt" is not an .mdx file.'
    assert result["calls"] == []
    assert result["uploads"] == [{"method": "POST", "url": "/dict/import"}]


def test_import_without_a_file_asks_for_one():
    result = _run({"scenario": "import_without_a_file"})

    assert result["dom"]["dict_settings_msg"]["text"] == "Choose an .mdx file first."


# --- remove -----------------------------------------------------------------


def test_remove_posts_the_file_name_and_refreshes():
    result = _run({"scenario": "remove", "removeName": "ko_zh_basic.db"})

    assert [c["url"] for c in result["calls"]] == ["/dict/remove", "/dict/status"]
    assert result["calls"][0]["method"] == "POST"
    assert result["calls"][0]["body"] == [["name", "ko_zh_basic.db"]]
    assert result["dom"]["dict_settings_msg"]["text"] == "Dictionary removed."


def test_remove_does_nothing_when_cancelled():
    result = _run({"scenario": "remove_cancelled", "confirm": False})

    assert result["calls"] == []
    assert result["dom"]["dict_settings_msg"]["text"] == ""


def test_remove_asks_again_when_a_language_still_uses_it():
    "The server refuses; confirming the refusal deletes with force."
    result = _run(
        {
            "scenario": "remove",
            "removeName": "ja_zh_shogakukan_v2.db",
            "removeBody": [
                {
                    "status": 409,
                    "body": {
                        "result": "in_use",
                        "message": "ja_zh_shogakukan_v2.db is still the "
                        "dictionary for: Japanese.",
                    },
                },
                {"body": {"result": "success", "removed": True}},
            ],
            "confirm": [True, True],
        }
    )

    assert [c["url"] for c in result["calls"]] == [
        "/dict/remove",
        "/dict/remove",
        "/dict/status",
    ]
    assert result["calls"][0]["body"] == [["name", "ja_zh_shogakukan_v2.db"]]
    assert result["calls"][1]["body"] == [
        ["name", "ja_zh_shogakukan_v2.db"],
        ["force", "1"],
    ]
    assert result["dom"]["dict_settings_msg"]["text"] == "Dictionary removed."


def test_remove_keeps_the_dictionary_when_the_refusal_is_declined():
    result = _run(
        {
            "scenario": "remove",
            "removeName": "ja_zh_shogakukan_v2.db",
            "removeBody": {
                "status": 409,
                "body": {"result": "in_use", "message": "still in use."},
            },
            "confirm": [True, False],
        }
    )

    assert [c["url"] for c in result["calls"]] == ["/dict/remove", "/dict/status"]
    assert result["dom"]["dict_settings_msg"]["text"] == ""
