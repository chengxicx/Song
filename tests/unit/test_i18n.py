"""
lute.i18n unit tests: dictionary loading, fallback, and language switching.
"""

import json

import pytest

from lute import i18n
from lute.db import db
from lute.models.repositories import UserSettingRepository
from lute.settings.current import current_settings, refresh_global_settings


def test_ui_language_codes():
    "Valid ui_language values, en first."
    assert i18n.language_codes() == ["en", "zh", "ja", "ko"]


def test_t_returns_english_source_for_en():
    "English is the key itself (fast path)."
    assert i18n.t("Settings", lang="en") == "Settings"


def test_t_translates_known_key():
    "Known keys come back in the requested language."
    assert i18n.t("Settings", lang="zh") == "设置"
    assert i18n.t("Settings", lang="ja") == "設定"
    assert i18n.t("Settings", lang="ko") == "설정"


def test_t_falls_back_to_english_for_unknown_key():
    "An unknown key degrades to the English source instead of erroring."
    key = "A key that definitely does not exist in any dictionary"
    assert i18n.t(key, lang="zh") == key
    assert i18n.t(key, lang="ja") == key
    assert i18n.t(key, lang="ko") == key


def test_t_keeps_html_in_keys_renderable():
    "Dictionary values may carry the templates' original inline HTML."
    val = i18n.t("<b>A (shortest)</b> — finest granularity, breaks compounds into smallest units.", lang="zh")
    assert "<b>" in val


def test_translations_for_js_is_valid_json():
    "The injected window.LUTE_I18N payload is a JSON object."
    payload = i18n.translations_for_js(lang="zh")
    parsed = json.loads(payload)
    assert parsed["Settings"] == "设置"
    assert i18n.translations_for_js(lang="en") == "{}"


def test_dictionaries_cover_the_same_keys():
    "zh/ja/ko ship the same key set, so no language silently misses strings."
    zh = i18n._load("zh")
    for lang in ("ja", "ko"):
        d = i18n._load(lang)
        assert set(d.keys()) == set(zh.keys()), f"{lang} keys differ from zh"


def test_current_language_defaults_to_en(app_context):
    "Without a stored ui_language the UI stays English."
    current_settings().pop("ui_language", None)
    assert i18n.current_language() == "en"


def test_current_language_follows_setting(app_context):
    "Setting ui_language switches the language used by t()."
    try:
        current_settings()["ui_language"] = "ja"
        assert i18n.current_language() == "ja"
        assert i18n.t("Settings") == "設定"
        current_settings()["ui_language"] = "bogus"
        assert i18n.current_language() == "en", "invalid value falls back to en"
    finally:
        current_settings().pop("ui_language", None)


def test_settings_form_persists_ui_language(app, app_context):
    "The settings form stores ui_language, and t() picks it up after refresh."
    client = app.test_client()
    client.get("/")
    from lute.settings.forms import UserSettingsForm

    repo = UserSettingRepository(db.session)
    repo.set_value("ui_language", "zh")
    db.session.commit()
    refresh_global_settings(db.session)
    assert i18n.t("Settings") == "设置"

    # The settings page renders the UI language selector.
    resp = client.get("/settings/index")
    assert resp.status_code == 200
    assert b"ui_language" in resp.data

    db.session.begin_nested()
    try:
        repo.set_value("ui_language", "en")
        db.session.commit()
        refresh_global_settings(db.session)
        assert i18n.t("Settings") == "Settings"
    finally:
        from sqlalchemy import exc as sa_exc

        try:
            db.session.rollback()
        except sa_exc.SQLAlchemyError:
            pass


def test_rendered_page_uses_translations(app, app_context):
    "With ui_language=zh the home page renders translated chrome."
    client = app.test_client()
    client.get("/")
    from lute.settings.current import current_settings

    current_settings()["ui_language"] = "zh"
    try:
        resp = client.get("/settings/index")
        html = resp.data.decode("utf-8")
        assert "设置" in html, "translated settings heading present"
        assert "Backup" not in html or "备份" in html, "translated section heading"
    finally:
        current_settings().pop("ui_language", None)
