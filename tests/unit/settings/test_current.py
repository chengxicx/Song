"""
Current settings tests.
"""

from lute.db import db
from lute.settings.current import (
    refresh_global_settings,
    current_settings,
    current_hotkeys,
)


def test_refresh_refreshes_current_settings(app_context):
    "Current settings are loaded."
    if "backup_dir" in current_settings():
        del current_settings()["backup_dir"]
    refresh_global_settings(db.session)
    assert "backup_dir" in current_settings(), "loaded"


def test_hotkey_strings_mapped_to_name(app_context):
    "Hotkey key combo to name."
    refresh_global_settings(db.session)
    hotkey_names = current_hotkeys().values()
    assert "hotkey_Status5" in hotkey_names, "this is set by default"
    assert current_hotkeys()["Digit5"] == "hotkey_Status5", "mapped"
    assert "" not in current_hotkeys(), "No blank keyboard shortcuts"


def test_settings_are_scoped_per_user(app_context, spanish):
    "Each user scope gets its own settings bucket."
    from lute.multiuser.context import user_scope

    refresh_global_settings(db.session)
    with user_scope("someuser"):
        assert "backup_dir" not in current_settings(), "fresh bucket"
        current_settings()["custom_key"] = "user value"
    assert "custom_key" not in current_settings(), "default bucket untouched"
    with user_scope("someuser"):
        assert current_settings()["custom_key"] == "user value", "scoped bucket"
