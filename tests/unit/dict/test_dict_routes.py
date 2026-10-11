"""
Dictionary route tests.

The pool is a folder, so these cover what the settings card needs:
listing it, adding to it under a chosen name, and removing one -- with
the one guard that matters, refusing to delete a dictionary a language
is still reading.
"""

import io
import json
import time

from lute.db import db
from lute.dict import service

from tests.unit.dict.mdx_fixture import build_mdx

ENTRIES = [
    ("たのしい", "<div>楽しい<br/>愉快yúkuài</div>"),
    ("箱", "<div>はこ<br/>箱子</div>"),
]


def _post_mdx(client, content, filename="fixture.mdx", name=None):
    data = {"mdx": (io.BytesIO(content), filename)}
    if name is not None:
        data["name"] = name
    return client.post("/dict/import", data=data, content_type="multipart/form-data")


def _wait_for_import(client, task_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/dict/import_status/{task_id}")
        body = json.loads(resp.data)
        if body.get("state") in ("finished", "error"):
            return body
        time.sleep(0.01)
    raise AssertionError(f"import task {task_id} never finished")


def _pool(client):
    return json.loads(client.get("/dict/status").data)["dictionaries"]


def test_status_before_any_import(client, dict_dirs):  # pylint: disable=unused-argument
    body = json.loads(client.get("/dict/status").data)

    assert body["result"] == "success"
    assert body["dictionaries"] == []
    assert body["default_filename"] == service.DB_FILENAME


def test_import_requires_a_file(client, dict_dirs):  # pylint: disable=unused-argument
    resp = client.post("/dict/import", data={}, content_type="multipart/form-data")

    assert resp.status_code == 400
    assert "Choose an .mdx file" in json.loads(resp.data)["message"]


def test_import_rejects_a_non_mdx_name(
    client, dict_dirs
):  # pylint: disable=unused-argument
    resp = _post_mdx(client, build_mdx(ENTRIES), filename="dictionary.txt")

    assert resp.status_code == 400
    assert "not an .mdx file" in json.loads(resp.data)["message"]


def test_import_rejects_an_empty_file(
    client, dict_dirs
):  # pylint: disable=unused-argument
    resp = _post_mdx(client, b"")

    assert resp.status_code == 400
    assert "empty" in json.loads(resp.data)["message"]


def test_import_rejects_a_name_that_is_not_a_pool_file(
    client, dict_dirs
):  # pylint: disable=unused-argument
    resp = _post_mdx(client, build_mdx(ENTRIES), name="../../etc/passwd")

    assert resp.status_code == 400
    assert "not a usable dictionary name" in json.loads(resp.data)["message"]


def test_import_publishes_and_reports_progress(
    client, dict_dirs
):  # pylint: disable=unused-argument
    resp = _post_mdx(client, build_mdx(ENTRIES, title="Fixture"))
    body = json.loads(resp.data)
    assert body["result"] == "success"
    assert body["source"] == "fixture.mdx"
    # No name given: it comes from the .mdx file.
    assert body["filename"] == "fixture.db"

    finished = _wait_for_import(client, body["task_id"])
    assert finished["state"] == "finished"
    assert finished["dictionary"]["entries"] == len(ENTRIES)
    assert finished["dictionary"]["title"] == "Fixture"

    pool = _pool(client)
    assert [d["filename"] for d in pool] == ["fixture.db"]
    assert pool[0]["installed"] is True
    assert pool[0]["source"] == "fixture.mdx"


def test_import_publishes_under_the_given_name(
    client, dict_dirs
):  # pylint: disable=unused-argument
    "The name is what a language stores, so it must be honoured exactly."
    body = json.loads(
        _post_mdx(client, build_mdx(ENTRIES), name="ja_zh_shogakukan_v2.db").data
    )
    assert body["filename"] == "ja_zh_shogakukan_v2.db"
    _wait_for_import(client, body["task_id"])

    pool = _pool(client)
    assert [d["filename"] for d in pool] == ["ja_zh_shogakukan_v2.db"]
    # The phone's own file, so the card marks it.
    assert pool[0]["is_default"] is True


def test_the_pool_lists_the_phones_file_first(
    client, dict_dirs
):  # pylint: disable=unused-argument
    for name in ("ko_zh_basic.db", service.DB_FILENAME, "aa_other.db"):
        task_id = json.loads(_post_mdx(client, build_mdx(ENTRIES), name=name).data)[
            "task_id"
        ]
        _wait_for_import(client, task_id)

    assert [d["filename"] for d in _pool(client)] == [
        service.DB_FILENAME,
        "aa_other.db",
        "ko_zh_basic.db",
    ]


def test_import_status_rejects_an_unknown_task(
    client, dict_dirs
):  # pylint: disable=unused-argument
    resp = client.get("/dict/import_status/not-a-task")

    assert resp.status_code == 404
    assert "unknown or has expired" in json.loads(resp.data)["message"]


def test_remove_deletes_the_named_dictionary(
    client, dict_dirs
):  # pylint: disable=unused-argument
    task_id = json.loads(_post_mdx(client, build_mdx(ENTRIES), name="a.db").data)[
        "task_id"
    ]
    _wait_for_import(client, task_id)

    body = json.loads(client.post("/dict/remove", data={"name": "a.db"}).data)
    assert body["result"] == "success"
    assert body["removed"] is True
    assert body["filename"] == "a.db"
    assert body["dictionaries"] == []


def test_remove_without_a_name_is_refused(
    client, dict_dirs
):  # pylint: disable=unused-argument
    "There is no longer such a thing as 'the' dictionary to delete."
    resp = client.post("/dict/remove", data={})

    assert resp.status_code == 400
    assert "No dictionary named" in json.loads(resp.data)["message"]


def _language_reads(language, filename):
    "Give the language a Local dictionary row reading this pool file."
    from lute.models.language import LanguageDictionary

    d = LanguageDictionary()
    d.usefor = "terms"
    d.dicttype = "localdict"
    d.dicturi = filename
    d.is_active = True
    d.sort_order = 0
    language.dictionaries.append(d)
    db.session.commit()


def test_remove_refuses_a_dictionary_a_language_still_reads(
    client, app_context, dict_dirs, japanese
):  # pylint: disable=unused-argument
    task_id = json.loads(_post_mdx(client, build_mdx(ENTRIES), name="a.db").data)[
        "task_id"
    ]
    _wait_for_import(client, task_id)
    _language_reads(japanese, "a.db")

    resp = client.post("/dict/remove", data={"name": "a.db"})

    assert resp.status_code == 409
    body = json.loads(resp.data)
    assert body["result"] == "in_use"
    assert "Japanese" in body["message"]
    # Still there.
    assert [d["filename"] for d in _pool(client)] == ["a.db"]


def test_remove_with_force_deletes_it_anyway(
    client, app_context, dict_dirs, japanese
):  # pylint: disable=unused-argument
    task_id = json.loads(_post_mdx(client, build_mdx(ENTRIES), name="a.db").data)[
        "task_id"
    ]
    _wait_for_import(client, task_id)
    _language_reads(japanese, "a.db")

    body = json.loads(
        client.post("/dict/remove", data={"name": "a.db", "force": "1"}).data
    )

    assert body["result"] == "success"
    assert body["removed"] is True
    assert _pool(client) == []


def test_the_settings_page_offers_the_import_card(
    client, dict_dirs
):  # pylint: disable=unused-argument
    html = client.get("/settings/index").data.decode("utf-8")

    assert "Local dictionaries (MDX)" in html
    assert 'id="dict_settings_list"' in html
    assert 'id="dict_settings_import"' in html
    assert 'id="dict_settings_name"' in html
    assert "dict-settings.js" in html
    # The name the phone's default download URL expects.
    assert service.DB_FILENAME in html


def test_a_broken_file_reports_the_error(
    client, dict_dirs
):  # pylint: disable=unused-argument
    task_id = json.loads(
        _post_mdx(client, b"definitely not an mdx", filename="junk.mdx").data
    )["task_id"]

    finished = _wait_for_import(client, task_id)
    assert finished["state"] == "error"
    assert finished["error"]
    assert _pool(client) == []
