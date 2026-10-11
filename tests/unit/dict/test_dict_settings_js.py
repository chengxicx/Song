"""
The settings page's "Local dictionary (MDX)" card (static/js/dict-settings.js).

The card is the only consumer of the /dict endpoints' JSON, and the field
names are a contract: the Python side sends `installed`, `entries`,
`size_mb`, `source`, `url`, `task_id`, `state`, `processed`, `error` and
`dictionary.entries`.  Rename one on either side and nothing throws -- the
card renders blank, or the progress poll never settles, and no Python test
can see it.  So the real module is run against a stub DOM and a stubbed
network (dict_settings_js_harness.js) and the rendered strings are pinned.

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
    "size_mb": 42.5,
    "modified": "2026-10-11 18:40",
    "source": "shogakukan_v2.mdx",
    "title": "小学馆V2日汉辞典",
    "entries": 69425,
    "terms": 70307,
    "imported_at": "2026-10-11 18:40",
}

NOT_INSTALLED = {
    "installed": False,
    "filename": "ja_zh_shogakukan_v2.db",
    "url": "/static/dict/ja_zh_shogakukan_v2.db",
    "size_mb": None,
    "source": None,
    "title": None,
    "entries": None,
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


def test_status_line_describes_the_installed_dictionary():
    text = _run({"scenario": "status_text", "status": INSTALLED})["text"]

    assert "小学馆V2日汉辞典" in text
    assert "69,425 entries" in text
    assert "42.5 MB" in text
    assert "shogakukan_v2.mdx" in text
    assert "2026-10-11 18:40" in text


def test_status_line_says_so_when_nothing_is_installed():
    text = _run({"scenario": "status_text", "status": NOT_INSTALLED})["text"]
    assert text == "No dictionary imported yet."


def test_status_line_falls_back_to_the_file_name_for_a_cli_built_dictionary():
    # A dictionary built with tools/mdx_to_sqlite.py and dropped into
    # place has no metadata sidecar: no title, no counts.
    bare = {
        "installed": True,
        "filename": "ja_zh_shogakukan_v2.db",
        "url": "/static/dict/ja_zh_shogakukan_v2.db",
        "size_mb": 42.5,
        "source": None,
        "title": None,
        "entries": None,
        "imported_at": None,
    }

    assert _run({"scenario": "status_text", "status": bare})["text"] == (
        "ja_zh_shogakukan_v2.db \u00b7 42.5 MB"
    )


def test_status_line_copes_with_a_bare_status():
    # A missing key must not throw and blank the whole card.
    assert _run({"scenario": "status_text", "status": {}})["text"]


def test_render_shows_the_download_url_when_installed():
    dom = _run({"scenario": "render", "status": INSTALLED})["dom"]

    assert dom["dict_settings_installed"]["style"]["display"] == ""
    assert dom["dict_settings_url"]["text"] == "/static/dict/ja_zh_shogakukan_v2.db"
    assert dom["dict_settings_url"]["href"] == "/static/dict/ja_zh_shogakukan_v2.db"
    assert dom["dict_settings_remove"]["disabled"] is False


def test_render_hides_the_url_and_disables_remove_when_not_installed():
    dom = _run({"scenario": "render", "status": NOT_INSTALLED})["dom"]

    assert dom["dict_settings_installed"]["style"]["display"] == "none"
    assert dom["dict_settings_remove"]["disabled"] is True
    assert dom["dict_settings_status"]["text"] == "No dictionary imported yet."


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


def test_remove_posts_and_refreshes():
    result = _run({"scenario": "remove"})

    assert [c["url"] for c in result["calls"]] == ["/dict/remove", "/dict/status"]
    assert result["calls"][0]["method"] == "POST"
    assert result["dom"]["dict_settings_msg"]["text"] == "Dictionary removed."


def test_remove_does_nothing_when_cancelled():
    result = _run({"scenario": "remove_cancelled", "confirm": False})

    assert result["calls"] == []
    assert result["dom"]["dict_settings_msg"]["text"] == ""
