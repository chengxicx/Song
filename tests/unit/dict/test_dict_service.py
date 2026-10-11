"""
Dictionary import and lookup tests.
"""

import os
import time

from lute.dict import service

from tests.unit.dict.mdx_fixture import build_mdx

ENTRIES = [
    ("たのしい", "<div>楽しい<br/>愉快yúkuài</div>"),
    ("ひさしぶり", "<div>久し振り<br/>好久不见</div>"),
    ("箱", "<div>はこ<br/>箱子</div>"),
]


def _wait(task_id, timeout=10.0):
    "Poll an import task until it reaches a terminal state."
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = service.task_status(task_id)
        if task["state"] in ("finished", "error"):
            return task
        time.sleep(0.01)
    raise AssertionError(f"import task {task_id} never finished")


def _import(app, scratch_dir, mdx_bytes, name="fixture.mdx"):
    "Write the MDX, run the import, and return the finished task."
    path = os.path.join(scratch_dir, name)
    with open(path, "wb") as f:
        f.write(mdx_bytes)
    task_id = service.start_import(app, path, name)
    return _wait(task_id)


def test_import_publishes_the_dictionary(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    assert service.is_installed() is False

    task = _import(app, scratch, build_mdx(ENTRIES, title="Fixture"))

    assert task["state"] == "finished"
    assert task["result"]["entries"] == len(ENTRIES)
    assert task["result"]["terms"] == len(ENTRIES)
    assert task["result"]["title"] == "Fixture"
    assert task["result"]["source"] == "fixture.mdx"
    assert service.is_installed() is True


def test_imported_dictionary_is_looked_up(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES))

    assert service.lookup("たのしい") == "楽しい\n愉快yúkuài"
    assert service.lookup("箱") == "はこ\n箱子"


def test_lookup_falls_back_to_the_reading_and_strips_honorifics(
    app, app_context, dict_dirs
):
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES))

    # The key is the kana reading; the kanji spelling is only found
    # through the term's reading.
    assert service.lookup("楽しい", "たのしい") == "楽しい\n愉快yúkuài"
    # おひさしぶり is keyed as ひさしぶり.
    assert service.lookup("お久しぶり", "おひさしぶり") == "久し振り\n好久不见"


def test_lookup_strips_the_zero_width_spaces_between_tokens(
    app, app_context, dict_dirs
):
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES))

    assert service.lookup("たの\u200bしい") == "楽しい\n愉快yúkuài"


def test_lookup_returns_none_without_a_dictionary(app_context, dict_dirs):
    assert service.lookup("たのしい") is None


def test_lookup_returns_none_for_an_unknown_word(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES))
    assert service.lookup("存在しない") is None


def test_identical_definitions_are_stored_once(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    entries = [
        ("たのしい", "<div>楽しい</div>"),
        ("愉しい", "<div>楽しい</div>"),
    ]
    task = _import(app, scratch, build_mdx(entries))

    assert task["result"]["entries"] == 1
    assert task["result"]["terms"] == 2
    assert service.lookup("愉しい") == "楽しい"


def test_definitions_are_truncated(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    long_definition = "あ" * 2000
    _import(app, scratch, build_mdx([("ながい", f"<div>{long_definition}</div>")]))

    assert len(service.lookup("ながい")) == service.MAX_DEFINITION_CHARS


def test_status_reports_the_installed_dictionary(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    assert service.status()["installed"] is False

    _import(app, scratch, build_mdx(ENTRIES, title="Fixture"))
    info = service.status()

    assert info["installed"] is True
    assert info["filename"] == service.DB_FILENAME
    assert info["title"] == "Fixture"
    assert info["source"] == "fixture.mdx"
    assert info["entries"] == len(ENTRIES)
    # Rounds to 0.0 for a fixture-sized dictionary; what matters is that
    # the published file is there and its size was reported.
    assert info["size_mb"] is not None
    assert os.path.getsize(service.db_path()) > 0
    assert info["url"].endswith(f"/static/dict/{service.DB_FILENAME}")


def test_remove_deletes_the_dictionary(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES))

    assert service.remove() is True
    assert service.is_installed() is False
    assert service.lookup("たのしい") is None
    assert service.remove() is False


def test_a_failed_import_leaves_the_previous_dictionary_alone(
    app, app_context, dict_dirs
):
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES, title="Good"))
    assert service.lookup("たのしい") == "楽しい\n愉快yúkuài"

    # Every definition is empty, so there is nothing to publish.
    bad = build_mdx([("x", "<div></div>")])
    task = _import(app, scratch, bad, name="bad.mdx")

    assert task["state"] == "error"
    assert "no usable entries" in task["error"].lower()
    assert service.lookup("たのしい") == "楽しい\n愉快yúkuài"
    assert service.status()["title"] == "Good"


def test_a_corrupt_file_fails_the_import(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    task = _import(app, scratch, b"this is not an MDX file at all", name="junk.mdx")

    assert task["state"] == "error"
    assert service.is_installed() is False


def test_import_deletes_the_source_file(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    path = os.path.join(scratch, "fixture.mdx")
    with open(path, "wb") as f:
        f.write(build_mdx(ENTRIES))
    _wait(service.start_import(app, path, "fixture.mdx"))

    assert os.path.exists(path) is False


def test_lookup_candidates_order():
    assert service.lookup_candidates("楽しい", "たのしい") == [
        "楽しい",
        "たのしい",
    ]
    assert service.lookup_candidates("お久しぶり", "おひさしぶり") == [
        "お久しぶり",
        "おひさしぶり",
        "ひさしぶり",
    ]
    assert service.lookup_candidates("", None) == []
    assert service.lookup_candidates("  ", "  ") == []


def test_lookup_candidates_are_deduplicated():
    assert service.lookup_candidates("たのしい", "たのしい") == ["たのしい"]
