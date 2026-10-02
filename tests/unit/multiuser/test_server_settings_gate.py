"""
Server-level settings are admin-owned when multi-user mode is on:
hidden from other users AND unreachable by a hand-built URL or POST.

Every case is asserted twice where it matters: once on the response
(status/HTML), and once on the *side effect* (the settings row, the
language's is_active, the pip call), because a gate that only hides
the UI but forgets the endpoint is the exact failure mode here.
"""

import os
import re

import pytest

from lute.db import db
from lute.models.language import Language
from lute.models.repositories import UserSettingRepository
from lute.multiuser import context, store


@pytest.fixture(name="as_mei")
def fixture_as_mei(mu_client):
    "admin creates 'mei' (role 'user'), then the client is logged in as her."
    mu_client.login("admin", "pass1234")
    mu_client.post(
        "/users/new",
        data={"username": "mei", "password": "meipass1", "confirm": "meipass1"},
    )
    mu_client.post("/logout")
    assert mu_client.login("mei", "meipass1").status_code == 302
    return mu_client


def _setting(username, key, app):
    "Read one setting value out of the given user's own db."
    with app.app_context(), context.user_scope(username):
        return UserSettingRepository(db.session).get_value(key)


def _set_setting(username, key, value, app):
    with app.app_context(), context.user_scope(username):
        repo = UserSettingRepository(db.session)
        repo.set_value(key, value)
        db.session.commit()


def _settings_form_data(resp):
    """
    Extract the settings form's current values from the rendered HTML,
    so a POST in a test carries the same field set a browser would send.
    """
    html = resp.get_data(as_text=True)
    data = {}
    for m in re.finditer(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html):
        data[m.group(1)] = m.group(2)
    for m in re.finditer(
        r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S
    ):
        name, body = m.group(1), m.group(2)
        sel = re.search(r'<option[^>]*selected[^>]*value="([^"]*)"', body)
        if sel:
            data[name] = sel.group(1)
        else:
            first = re.search(r'<option[^>]*value="([^"]*)"', body)
            if first:
                data[name] = first.group(1)
    return data


# ---------------------------------------------------------------------------
# single-user mode: nothing may regress
#
# is_admin is False in single-user mode (app_factory short-circuits on
# mu_store.enabled()), so every gate must key off can_manage_server
# instead, or these sections vanish for the sole user.
# ---------------------------------------------------------------------------


def test_single_user_settings_page_shows_server_sections(mu_app):
    resp = mu_app.test_client().get("/settings/index")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="mecab_path"' in html
    assert 'name="japanese_sudachi_dict"' in html
    assert 'name="japanese_sudachi_mode"' in html
    assert 'href="/multiuser/switch"' in html
    assert 'id="whisper_settings_status"' in html


def test_single_user_server_sections_still_save(mu_app):
    "The SERVER_LEVEL_KEYS skip must be a no-op when there's one user."
    client = mu_app.test_client()
    form = _settings_form_data(client.get("/settings/index"))
    form["mecab_path"] = "/tmp/somewhere/libmecab.so"
    form["japanese_sudachi_dict"] = "full"
    form["japanese_sudachi_mode"] = "A"
    assert client.post("/settings/index", data=form).status_code == 302
    with mu_app.app_context():
        repo = UserSettingRepository(db.session)
        assert repo.get_value("mecab_path") == "/tmp/somewhere/libmecab.so"
        assert repo.get_value("japanese_sudachi_dict") == "full"
        assert repo.get_value("japanese_sudachi_mode") == "A"


def test_single_user_open_endpoints_unaffected(mu_app):
    "A single-user install must still be able to turn multi-user mode on."
    client = mu_app.test_client()
    assert client.get("/multiuser/switch").status_code == 200
    assert client.get("/language/index").status_code == 200
    assert client.get("/book/whisper/available").status_code == 200
    assert client.get("/settings/test_mecab?mecab_path=").status_code == 200
    assert client.get("/settings/test_sudachi").status_code == 200


# ---------------------------------------------------------------------------
# hiding
# ---------------------------------------------------------------------------


def test_settings_page_hides_server_sections_from_non_admin(as_mei):
    html = as_mei.get("/settings/index").get_data(as_text=True)
    assert 'id="mecab_path"' not in html
    assert 'name="japanese_sudachi_dict"' not in html
    assert 'name="japanese_sudachi_mode"' not in html
    assert 'id="test_mecab_btn"' not in html
    assert 'id="test_sudachi_btn"' not in html
    assert 'id="whisper_settings_status"' not in html
    assert 'href="/multiuser/switch"' not in html
    # Per-user sections stay.
    assert 'name="show_highlights"' in html
    assert 'name="backup_dir"' in html


def test_admin_still_sees_everything(mu_client):
    mu_client.login("admin", "pass1234")
    html = mu_client.get("/settings/index").get_data(as_text=True)
    for needle in (
        'id="mecab_path"',
        'name="japanese_sudachi_dict"',
        'id="whisper_settings_status"',
        'href="/multiuser/switch"',
    ):
        assert needle in html


def test_languages_menu_hidden_from_non_admin(as_mei):
    assert 'href="/language/index"' not in as_mei.get("/").get_data(as_text=True)


def test_admin_sees_languages_menu(mu_client):
    mu_client.login("admin", "pass1234")
    assert 'href="/language/index"' in mu_client.get("/").get_data(as_text=True)


def test_empty_db_home_has_no_dead_language_links(as_mei):
    html = as_mei.get("/").get_data(as_text=True)
    assert "/language/list_predefined" not in html
    assert "/language/new" not in html


# ---------------------------------------------------------------------------
# the clobber test: hidden fields must not be reset to their defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,value",
    [
        ("mecab_path", "/opt/homebrew/lib/libmecab.dylib"),
        ("japanese_sudachi_dict", "full"),
        ("japanese_sudachi_mode", "A"),
    ],
)
def test_non_admin_settings_post_does_not_clobber_server_keys(
    as_mei, mu_enabled_app, key, value
):
    """
    A field that isn't rendered isn't in the POST, and WTForms then hands
    the write loop the field *default* (None / "core" / "C").  Without
    the SERVER_LEVEL_KEYS skip, saving an unrelated preference silently
    resets the admin's server-level setting.
    """
    _set_setting("mei", key, value, mu_enabled_app)

    form = _settings_form_data(as_mei.get("/settings/index"))
    form["show_highlights"] = "y"
    assert as_mei.post("/settings/index", data=form).status_code == 302

    assert _setting("mei", key, mu_enabled_app) == value


def test_non_admin_cannot_craft_the_post(as_mei, mu_enabled_app):
    "Server-side enforcement, not just a hidden input."
    _set_setting("mei", "mecab_path", "/legit/libmecab.so", mu_enabled_app)

    form = _settings_form_data(as_mei.get("/settings/index"))
    form["mecab_path"] = "/tmp/evil/libmecab.so"
    assert as_mei.post("/settings/index", data=form).status_code == 302

    assert _setting("mei", "mecab_path", mu_enabled_app) == "/legit/libmecab.so"


# ---------------------------------------------------------------------------
# the two bypass paths around the form
# ---------------------------------------------------------------------------


def test_set_key_endpoint_refuses_server_keys(as_mei, mu_enabled_app):
    _set_setting("mei", "mecab_path", "/legit/libmecab.so", mu_enabled_app)

    resp = as_mei.post("/settings/set/mecab_path/evil.so")
    assert resp.status_code == 403
    assert "admin" in resp.json["message"]
    assert _setting("mei", "mecab_path", mu_enabled_app) == "/legit/libmecab.so"

    # A normal per-user key must still work.
    assert as_mei.post("/settings/set/show_highlights/0").status_code == 200


def test_admin_may_use_set_key_endpoint(mu_client):
    mu_client.login("admin", "pass1234")
    assert mu_client.post("/settings/set/mecab_path/legit.so").status_code == 200


def test_shortcuts_endpoint_cannot_write_server_keys(as_mei, mu_enabled_app):
    """
    edit_shortcuts writes request.form verbatim and UserShortcutsForm has
    no fields, so any extra POST field used to land in the settings table.
    """
    _set_setting("mei", "japanese_sudachi_mode", "A", mu_enabled_app)

    as_mei.post(
        "/settings/shortcuts",
        data={
            "mecab_path": "/tmp/evil.so",
            "japanese_sudachi_mode": "C",
            "hotkey_save": "ctrl-s",
        },
    )

    assert _setting("mei", "japanese_sudachi_mode", mu_enabled_app) == "A"
    assert _setting("mei", "mecab_path", mu_enabled_app) != "/tmp/evil.so"


# ---------------------------------------------------------------------------
# process-wide side effects
# ---------------------------------------------------------------------------


def test_test_mecab_is_admin_only(as_mei, mu_enabled_app):
    _set_setting("mei", "mecab_path", "/legit/libmecab.so", mu_enabled_app)
    before = os.environ.get("MECAB_PATH")

    resp = as_mei.get("/settings/test_mecab?mecab_path=/tmp/evil.so")
    assert resp.status_code == 403
    assert resp.json["result"] == "failure"

    assert _setting("mei", "mecab_path", mu_enabled_app) == "/legit/libmecab.so"
    assert os.environ.get("MECAB_PATH") == before, "process MECAB_PATH was touched"


def test_test_sudachi_is_admin_only(as_mei):
    assert as_mei.get("/settings/test_sudachi").status_code == 403


# ---------------------------------------------------------------------------
# mode switch
# ---------------------------------------------------------------------------


def test_switch_page_is_admin_only(as_mei):
    resp = as_mei.get("/multiuser/switch")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_non_admin_cannot_disable_multiuser(as_mei):
    assert as_mei.post("/multiuser/disable").status_code == 302
    assert store.enabled(), "mode was disabled by a non-admin"


def test_non_admin_cannot_enable_or_reenable(as_mei):
    assert (
        as_mei.post(
            "/multiuser/enable",
            data={
                "username": "mei",
                "password": "meipass1",
                "confirm": "meipass1",
            },
        ).status_code
        == 302
    )
    assert store.enabled()
    assert store.is_admin("mei") is False


# ---------------------------------------------------------------------------
# language endpoints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/language/index",
        "/language/new",
        "/language/new/Japanese",
        "/language/list_predefined",
    ],
)
def test_language_pages_admin_only(as_mei, path):
    resp = as_mei.get(path)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_language_writes_admin_only(as_mei):
    for path in (
        "/language/toggle_active/1",
        "/language/delete/1",
        "/language/edit/1",
        "/language/grammar_engine/install/ja_core",
    ):
        assert as_mei.post(path).status_code == 302, path


def test_non_admin_cannot_flip_language_active(as_mei, mu_enabled_app):
    from lute.language.service import Service as LanguageService

    with mu_enabled_app.app_context(), context.user_scope("mei"):
        lang = LanguageService(db.session).get_language_def("English").language
        db.session.add(lang)
        db.session.commit()
        lang_id, was_active = lang.id, lang.is_active

    as_mei.post(f"/language/toggle_active/{lang_id}")

    with mu_enabled_app.app_context(), context.user_scope("mei"):
        assert db.session.get(Language, lang_id).is_active == was_active


def test_grammar_engine_install_never_runs_pip(as_mei, monkeypatch):
    "The important assertion: the subprocess is not reached."
    from lute.read.render import grammar_analysis

    called = []
    monkeypatch.setattr(
        grammar_analysis,
        "install_grammar_engine",
        lambda extra: (called.append(extra), (True, "nope"))[1],
    )
    as_mei.post("/language/grammar_engine/install/ja_core")
    assert called == [], "pip install reached for a non-admin"


# ---------------------------------------------------------------------------
# whisper: installing is admin work, using it is not
# ---------------------------------------------------------------------------


def test_whisper_install_is_admin_only(as_mei, monkeypatch):
    from lute.book import whisper_transcribe

    called = []
    monkeypatch.setattr(
        whisper_transcribe, "install_whisper", lambda: (called.append(1), (True, "x"))[1]
    )
    resp = as_mei.post("/book/whisper/install")
    assert resp.status_code == 403
    assert called == [], "pip install reached for a non-admin"


def test_whisper_model_download_is_admin_only(as_mei, monkeypatch):
    from lute.book import whisper_transcribe

    called = []
    monkeypatch.setattr(whisper_transcribe, "has_running_task", lambda: False)
    monkeypatch.setattr(
        whisper_transcribe, "whisper_status", lambda: {"installed": True}
    )
    monkeypatch.setattr(
        whisper_transcribe,
        "start_model_download",
        lambda *a, **k: called.append(1),
    )
    resp = as_mei.post("/book/whisper/download_model", data={"whisper_model": "small"})
    assert resp.status_code == 403
    assert called == []


def test_whisper_model_delete_is_admin_only(as_mei, monkeypatch):
    from lute.book import whisper_transcribe

    called = []
    monkeypatch.setattr(whisper_transcribe, "has_running_task", lambda: False)
    monkeypatch.setattr(
        whisper_transcribe,
        "delete_model",
        lambda size: (called.append(size), (True, "x"))[1],
    )
    resp = as_mei.post("/book/whisper/delete_model", data={"whisper_model": "small"})
    assert resp.status_code == 403
    assert called == []


def test_admin_can_still_manage_whisper(mu_client, monkeypatch):
    from lute.book import whisper_transcribe

    monkeypatch.setattr(whisper_transcribe, "has_running_task", lambda: False)
    monkeypatch.setattr(
        whisper_transcribe, "delete_model", lambda size: (True, "Deleted.")
    )
    mu_client.login("admin", "pass1234")
    resp = mu_client.post("/book/whisper/delete_model", data={"whisper_model": "small"})
    assert resp.status_code == 200


def test_non_admin_can_still_transcribe(as_mei):
    "Over-blocking check: availability stays public for everyone."
    assert as_mei.get("/book/whisper/available").status_code == 200
    assert as_mei.get("/book/whisper/models").status_code == 200