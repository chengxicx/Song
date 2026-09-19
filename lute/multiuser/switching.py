"""
Switching multi-user mode on and off, migrating data in place.

Enable (single -> multi):
    the base datapath's db and user media are MOVED (same filesystem
    rename) into users/<admin>/, and users.db is created with the
    initial admin account.  No copies are kept, so there is exactly
    one source of truth.

Disable (multi -> single):
    the acting admin's db and media are moved back to the datapath
    root.  Other users' data stays frozen under users/ and their
    accounts remain in users.db; re-enabling restores everything.

Every move is rolled back if a later step fails.
"""

import os
import sqlite3
import threading

from lute.multiuser import paths, store

MIGRATION_LOCK = threading.RLock()


class SwitchError(Exception):
    "User-facing error during mode switching."


def _move_into_user_dir(base_config, username, entries, moved):
    """
    Move the given entries (names relative to the datapath root) into
    the user's directory.  Appends every completed (src, dst) to
    *moved* immediately, so a failure mid-way can still be rolled
    back.  Missing source entries are skipped.
    """
    udir = paths.user_dir(base_config, username)
    os.makedirs(udir, exist_ok=True)
    for name in entries:
        src = os.path.join(base_config.datapath, name)
        if not os.path.exists(src):
            continue
        dst = os.path.join(udir, name)
        if os.path.exists(dst):
            raise SwitchError(f"Cannot migrate: '{name}' already exists in {udir}.")
        try:
            os.rename(src, dst)
        except OSError as e:
            raise SwitchError(f"Cannot migrate '{name}': {e}") from e
        moved.append((src, dst))


def _move_to_root(base_config, username, entries, moved):
    """
    Move the given entries from the user's directory back to the
    datapath root (disable).  Appends completed (src, dst) to *moved*.
    Fails if a root slot is already occupied.
    """
    udir = paths.user_dir(base_config, username)
    for name in entries:
        src = os.path.join(udir, name)
        if not os.path.exists(src):
            continue
        dst = os.path.join(base_config.datapath, name)
        if os.path.exists(dst):
            raise SwitchError(
                f"Cannot disable: '{name}' already exists at the data path root."
            )
        try:
            os.rename(src, dst)
        except OSError as e:
            raise SwitchError(f"Cannot move back '{name}': {e}") from e
        moved.append((src, dst))


def _rollback(moved):
    for src, dst in reversed(moved):
        os.rename(dst, src)


def _set_mode_in_db(on):
    conn = sqlite3.connect(store.users_dbfile())
    try:
        conn.execute(
            "INSERT INTO appconfig (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (store.MODE_KEY, "1" if on else "0"),
        )
        conn.commit()
    finally:
        conn.close()


def enable_fresh(base_config, admin_username, password):
    """
    First-time enable: create the admin account and migrate the
    single-user data into the admin's directory.
    """
    with MIGRATION_LOCK:
        if store.enabled():
            raise SwitchError("Multi-user mode is already on.")
        if not paths.valid_username(admin_username):
            raise SwitchError(
                "Username must be 1-32 chars: letters, digits, . _ - only."
            )
        if len(password or "") < 4:
            raise SwitchError("Password must be at least 4 characters.")
        if os.path.exists(store.users_dbfile()):
            raise SwitchError("users.db already exists; use re-enable.")

        moved = []
        try:
            _move_into_user_dir(
                base_config,
                admin_username,
                [
                    base_config.dbname,
                    *paths.MIGRATABLE_ENTRIES,
                ],
                moved,
            )
            if not store.create_users_db(admin_username, store.hash_password(password)):
                raise SwitchError("users.db already exists.")
        except Exception:
            _rollback(moved)
            raise

        paths.ensure_user_dirs(paths.user_config(base_config, admin_username))
        store.set_enabled(True)
        return admin_username


def re_enable(base_config, admin_username, password):
    """
    Re-enable after a disable: verify the admin's credentials against
    the existing users.db, then migrate the current single-user db
    (the data of whoever disabled last) into that admin's directory.
    """
    with MIGRATION_LOCK:
        if store.enabled():
            raise SwitchError("Multi-user mode is already on.")
        if not store.user_exists(admin_username):
            raise SwitchError(f"Unknown user '{admin_username}'.")
        if not store.authenticate(admin_username, password):
            raise SwitchError("Wrong username or password.")
        if not store.is_admin(admin_username):
            raise SwitchError("Only an admin can enable multi-user mode.")

        moved = []
        try:
            _move_into_user_dir(
                base_config,
                admin_username,
                [
                    base_config.dbname,
                    *paths.MIGRATABLE_ENTRIES,
                ],
                moved,
            )
            _set_mode_in_db(True)
        except Exception:
            _rollback(moved)
            raise

        paths.ensure_user_dirs(paths.user_config(base_config, admin_username))
        store.set_enabled(True)
        return admin_username


def disable(base_config, admin_username):
    """
    Turn multi-user mode off: the acting admin's data moves back to
    the datapath root; other users' data stays frozen under users/.
    """
    with MIGRATION_LOCK:
        if not store.enabled():
            raise SwitchError("Multi-user mode is already off.")

        target_db = os.path.join(base_config.datapath, base_config.dbname)
        if os.path.exists(target_db):
            raise SwitchError(
                f"Cannot disable: '{base_config.dbname}' already exists "
                "at the data path root."
            )

        moved = []
        try:
            _move_to_root(
                base_config,
                admin_username,
                [
                    base_config.dbname,
                    *paths.MIGRATABLE_ENTRIES,
                ],
                moved,
            )
            _set_mode_in_db(False)
        except Exception:
            _rollback(moved)
            raise

        store.set_enabled(False)
        return admin_username
