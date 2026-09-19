"""
User store tests: accounts, passwords, last-admin guard.
"""

import os
import sqlite3

import pytest

from lute.multiuser import store


@pytest.fixture(name="loaded_store", autouse=True)
def fixture_loaded_store(tmp_path):
    "Point the store at a fresh (nonexistent) users.db."

    class _FakeBase:  # pylint: disable=too-few-public-methods
        datapath = str(tmp_path / "data")

    os.makedirs(_FakeBase.datapath, exist_ok=True)
    store.load(_FakeBase)
    yield


def test_create_and_authenticate():
    store.create_user("alice", "secret1")
    assert store.user_exists("alice")
    assert store.authenticate("alice", "secret1")
    assert not store.authenticate("alice", "wrong")
    assert not store.authenticate("bob", "secret1")


def test_create_user_bootstraps_users_db():
    "create_user works even before users.db exists (schema auto-created)."
    assert not os.path.exists(store.users_dbfile())
    store.create_user("alice", "secret1")
    assert os.path.exists(store.users_dbfile())
    assert store.user_exists("alice")


def test_invalid_usernames_rejected():
    for bad in ["../etc", "a/b", "", "x" * 33, "..", "."]:
        with pytest.raises(ValueError):
            store.create_user(bad, "secret1")


def test_duplicate_user_rejected():
    store.create_user("alice", "secret1")
    with pytest.raises(ValueError, match="already exists"):
        store.create_user("alice", "secret2")


def test_short_password_rejected():
    with pytest.raises(ValueError, match="4 characters"):
        store.create_user("alice", "abc")


def test_set_password():
    store.create_user("alice", "secret1")
    store.set_password("alice", "secret2")
    assert store.authenticate("alice", "secret2")
    assert not store.authenticate("alice", "secret1")


def test_delete_user_and_last_admin_guard():
    store.create_user("root", "secret1", role="admin")
    store.create_user("alice", "secret1")
    store.delete_user("alice")
    assert not store.user_exists("alice")
    with pytest.raises(ValueError, match="last admin"):
        store.delete_user("root")


def test_set_role_and_last_admin_guard():
    store.create_user("root", "secret1", role="admin")
    store.create_user("alice", "secret1")
    store.set_role("alice", "admin")
    store.set_role("alice", "user")
    with pytest.raises(ValueError, match="last admin"):
        store.set_role("root", "user")


def test_mode_persisted_across_reload(tmp_path):
    "The mode flag lives in users.db and survives a boot-time reload."
    assert not os.path.exists(store.users_dbfile())
    assert store.create_users_db("root", "somehash")
    assert store.enabled() is False, "fresh users.db does not auto-enable"

    class _FakeBase:  # pylint: disable=too-few-public-methods
        datapath = str(tmp_path / "data")

    conn = sqlite3.connect(store.users_dbfile())
    conn.execute(
        "INSERT OR REPLACE INTO appconfig (key, value) VALUES (?, '1')",
        (store.MODE_KEY,),
    )
    conn.commit()
    conn.close()

    store.load(_FakeBase)
    assert store.enabled() is True
