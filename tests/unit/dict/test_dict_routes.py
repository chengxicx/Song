"""
Dictionary route tests.
"""

import io
import json
import time

from lute.dict import service

from tests.unit.dict.mdx_fixture import build_mdx

ENTRIES = [
    ("たのしい", "<div>楽しい<br/>愉快yúkuài</div>"),
    ("箱", "<div>はこ<br/>箱子</div>"),
]


def _post_mdx(client, content, filename="fixture.mdx"):
    return client.post(
        "/dict/import",
        data={"mdx": (io.BytesIO(content), filename)},
        content_type="multipart/form-data",
    )


def _wait_for_import(client, task_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/dict/import_status/{task_id}")
        body = json.loads(resp.data)
        if body.get("state") in ("finished", "error"):
            return body
        time.sleep(0.01)
    raise AssertionError(f"import task {task_id} never finished")


def test_status_before_any_import(client, dict_dirs):  # pylint: disable=unused-argument
    body = json.loads(client.get("/dict/status").data)

    assert body["result"] == "success"
    assert body["status"]["installed"] is False
    assert body["status"]["filename"] == service.DB_FILENAME


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


def test_import_publishes_and_reports_progress(
    client, dict_dirs
):  # pylint: disable=unused-argument
    resp = _post_mdx(client, build_mdx(ENTRIES, title="Fixture"))
    body = json.loads(resp.data)
    assert body["result"] == "success"
    assert body["source"] == "fixture.mdx"

    finished = _wait_for_import(client, body["task_id"])
    assert finished["state"] == "finished"
    assert finished["dictionary"]["entries"] == len(ENTRIES)
    assert finished["dictionary"]["title"] == "Fixture"

    status = json.loads(client.get("/dict/status").data)["status"]
    assert status["installed"] is True
    assert status["source"] == "fixture.mdx"


def test_import_status_rejects_an_unknown_task(
    client, dict_dirs
):  # pylint: disable=unused-argument
    resp = client.get("/dict/import_status/not-a-task")

    assert resp.status_code == 404
    assert "unknown or has expired" in json.loads(resp.data)["message"]


def test_remove_deletes_the_dictionary(
    client, dict_dirs
):  # pylint: disable=unused-argument
    task_id = json.loads(_post_mdx(client, build_mdx(ENTRIES)).data)["task_id"]
    _wait_for_import(client, task_id)

    body = json.loads(client.post("/dict/remove").data)
    assert body["result"] == "success"
    assert body["removed"] is True
    assert body["status"]["installed"] is False


def test_the_settings_page_offers_the_import_card(
    client, dict_dirs
):  # pylint: disable=unused-argument
    html = client.get("/settings/index").data.decode("utf-8")

    assert "Local dictionary (MDX)" in html
    assert 'id="dict_settings_import"' in html
    assert 'id="dict_settings_remove"' in html
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
    assert json.loads(client.get("/dict/status").data)["status"]["installed"] is False
