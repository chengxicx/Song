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


def test_speech_to_text_section_title_is_engine_neutral(client):
    "The section now manages several engines, so it is not named Whisper."
    body = client.get("/settings/index").get_data(as_text=True)
    assert "Speech-to-text (auto-subtitles)" in body
    assert "Whisper (auto-subtitles)" not in body
    # Engine prefixes keep the two kinds of rows apart at a glance.
    assert "faster-whisper · " in body
    assert "sherpa-onnx" in body
