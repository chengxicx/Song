"""
Settings route tests.
"""


def test_set_known_key_succeeds(client):
    "A key defined in the db can be set."
    resp = client.post("/settings/set/show_highlights/0")
    assert resp.status_code == 200
    assert resp.json == {"result": "success", "message": "OK"}


def test_set_unknown_key_reports_failure_instead_of_500(client):
    """
    A client built against another version (e.g. a stale cached page)
    posts a key this build does not define; it must get a plain
    failure, not a 500 from the lookup.
    """
    resp = client.post("/settings/set/shadowing_show_buttons/1")
    assert resp.status_code == 404
    assert resp.json["result"] == "failure"
    assert "shadowing_show_buttons" in resp.json["message"]
