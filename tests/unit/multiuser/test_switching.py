"""
Mode switching tests: data migration on enable/disable/re-enable,
including rollback on failure.
"""

import os

import pytest

from lute.multiuser import paths, store, switching


def _ucfg(app, username):
    "User config snapshot for the app's base config."
    return paths.user_config(app.env_config.base_config, username)


def _write_file(path, content="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def test_enable_fresh_moves_data(mu_app):
    base = mu_app.env_config.base_config
    dbfile = _write_file(base.dbfilename, "dbcontent")
    img = _write_file(os.path.join(base.userimagespath, "1", "a.jpeg"))

    switching.enable_fresh(base, "admin", "pass1234")

    assert store.enabled() is True
    assert os.path.exists(store.users_dbfile())
    ucfg = _ucfg(mu_app, "admin")
    assert os.path.exists(ucfg.dbfilename), "db moved into user dir"
    with open(ucfg.dbfilename, encoding="utf-8") as f:
        assert f.read() == "dbcontent", "content preserved"
    assert not os.path.exists(dbfile), "no copy left at the root"
    assert os.path.exists(os.path.join(ucfg.userimagespath, "1", "a.jpeg"))
    assert not os.path.exists(img)


def test_enable_fresh_validates_input(mu_app):
    base = mu_app.env_config.base_config
    with pytest.raises(switching.SwitchError, match="1-32 chars"):
        switching.enable_fresh(base, "bad/name", "pass1234")
    with pytest.raises(switching.SwitchError, match="4 characters"):
        switching.enable_fresh(base, "admin", "abc")
    assert store.enabled() is False
    assert not os.path.exists(store.users_dbfile())


def test_enable_rolls_back_on_failure(mu_app):
    base = mu_app.env_config.base_config
    _write_file(base.dbfilename, "dbcontent")
    _write_file(os.path.join(base.userimagespath, "1", "a.jpeg"))
    # Pre-create the user dir's userimages to make the move fail partway.
    # (entries move in MIGRATABLE_ENTRIES order; db moves first, then
    # userimages -- blocking userimages exercises the rollback.)
    pre = _write_file(
        os.path.join(paths.user_dir(base, "admin"), "userimages", "1", "b.jpeg")
    )

    with pytest.raises(switching.SwitchError):
        switching.enable_fresh(base, "admin", "pass1234")

    assert not store.enabled()
    assert not os.path.exists(store.users_dbfile())
    assert os.path.exists(base.dbfilename), "db moved back by rollback"
    assert os.path.exists(os.path.join(base.userimagespath, "1", "a.jpeg"))
    assert os.path.exists(pre)


def test_disable_then_reenable_roundtrip(mu_app):
    base = mu_app.env_config.base_config
    _write_file(base.dbfilename, "admin-db")
    _write_file(os.path.join(base.userimagespath, "1", "a.jpeg"))

    switching.enable_fresh(base, "admin", "pass1234")
    ucfg = _ucfg(mu_app, "admin")
    other_img = _write_file(
        os.path.join(paths.user_config(base, "mei").userimagespath, "1", "m.jpeg")
    )

    switching.disable(base, "admin")
    assert store.enabled() is False
    assert os.path.exists(base.dbfilename), "admin db back at the root"
    with open(base.dbfilename, encoding="utf-8") as f:
        assert f.read() == "admin-db"
    assert os.path.exists(os.path.join(base.userimagespath, "1", "a.jpeg"))
    assert os.path.exists(other_img), "other user's data stays frozen"

    # Re-enable with the existing users.db: verify admin credentials.
    with pytest.raises(switching.SwitchError, match="Wrong username or password"):
        switching.re_enable(base, "admin", "wrongpass")
    switching.re_enable(base, "admin", "pass1234")
    assert store.enabled() is True
    assert not os.path.exists(base.dbfilename)
    assert os.path.exists(os.path.join(ucfg.userimagespath, "1", "a.jpeg"))
    assert os.path.exists(other_img), "frozen data survives re-enable"


def test_disable_requires_no_conflicting_base_db(mu_app):
    base = mu_app.env_config.base_config
    _write_file(base.dbfilename, "db")
    switching.enable_fresh(base, "admin", "pass1234")
    ucfg = _ucfg(mu_app, "admin")
    # Someone drops a db file at the root while mode is on.
    _write_file(base.dbfilename, "stray")

    with pytest.raises(switching.SwitchError, match="already exists"):
        switching.disable(base, "admin")
    assert store.enabled() is True, "mode unchanged on failure"
    assert os.path.exists(ucfg.dbfilename), "admin data untouched"
