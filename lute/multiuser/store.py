"""
The users master store: <datapath>/users.db.

A small standalone sqlite db (plain sqlite3, separate from the
SQLAlchemy engine) holding user accounts and the multi-user mode flag:

    users(username PK, password_hash, role, created_at)
    appconfig(key PK, value)          -- "multiuser_mode" = "1"/"0"

Authentication and user lookups read users.db live, so CLI changes
(e.g. password resets) take effect without a server restart.  Only the
mode flag is cached in process memory; it changes via enable()/disable()
in lute.multiuser.switching.
"""

import hashlib
import os
import sqlite3
import threading

from werkzeug.security import check_password_hash, generate_password_hash

from lute.multiuser import paths

USERS_DB_FILENAME = "users.db"
MODE_KEY = "multiuser_mode"

# scrypt is unavailable on python builds linked against LibreSSL;
# fall back to pbkdf2 there.  check_password_hash reads the method
# from the stored hash, so this only affects new hashes.
PASSWORD_HASH_METHOD = "scrypt" if hasattr(hashlib, "scrypt") else "pbkdf2:sha256"


def hash_password(password):
    "Hash a password for storage in users.db."
    return generate_password_hash(password, method=PASSWORD_HASH_METHOD)


_write_lock = threading.RLock()

_users_dbfile = None
_enabled = False

_ROLE_ADMIN = "admin"
_ROLE_USER = "user"


def users_dbfile():
    "Path to users.db (set by load()); None before load."
    return _users_dbfile


def enabled():
    "True when multi-user mode is currently on."
    return _enabled


def is_loaded():
    return _users_dbfile is not None


def load(base_config):
    """
    Read users.db state at app boot.  Must be called before the auth
    gate or db creator are used.  Never creates users.db.
    """
    global _users_dbfile, _enabled  # pylint: disable=global-statement
    _users_dbfile = os.path.join(base_config.datapath, USERS_DB_FILENAME)
    _enabled = False
    if not os.path.exists(_users_dbfile):
        return
    conn = sqlite3.connect(_users_dbfile)
    try:
        row = conn.execute(
            "SELECT value FROM appconfig WHERE key = ?", (MODE_KEY,)
        ).fetchone()
    finally:
        conn.close()
    _enabled = bool(row and row[0] == "1")


def set_enabled(value):
    global _enabled  # pylint: disable=global-statement
    _enabled = bool(value)


def _ensure_schema(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS appconfig (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )"""
    )
    # Book sharing record: one row per book copied from an admin's db
    # into another user's db (lute/multiuser/book_share.py).  Lives here
    # rather than in the per-user dbs so the admin's book page can show
    # every share at once, and so a copy can still be found after the
    # source book was edited or deleted.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bookassignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin TEXT NOT NULL,
            target TEXT NOT NULL,
            src_bkid INTEGER NOT NULL,
            dst_bkid INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS bookassignments_target_dst "
        "ON bookassignments (target, dst_bkid)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS bookassignments_admin_src "
        "ON bookassignments (admin, src_bkid)"
    )


def create_users_db(admin_username, password_hash):
    """
    Create users.db with mode ON and the initial admin account.
    Caller holds _write_lock.  Returns False if the file exists.
    """
    if os.path.exists(_users_dbfile):
        return False
    conn = sqlite3.connect(_users_dbfile)
    try:
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (admin_username, password_hash, _ROLE_ADMIN),
        )
        conn.execute("INSERT INTO appconfig (key, value) VALUES (?, '1')", (MODE_KEY,))
        conn.commit()
    finally:
        conn.close()
    return True


def _open_users_db():
    """
    Open users.db, creating the schema if the file is new.  Used by
    the write operations; callers still hold _write_lock.
    """
    conn = sqlite3.connect(_users_dbfile)
    _ensure_schema(conn)
    return conn


def users():
    "All accounts: list of dicts (username, role, created_at)."
    if not os.path.exists(_users_dbfile):
        return []
    conn = sqlite3.connect(_users_dbfile)
    try:
        rows = conn.execute(
            "SELECT username, role, created_at FROM users ORDER BY username"
        ).fetchall()
    finally:
        conn.close()
    return [{"username": r[0], "role": r[1], "created_at": r[2]} for r in rows]


def get_user(username):
    "Account dict or None; live read."
    if not username or not os.path.exists(_users_dbfile):
        return None
    conn = sqlite3.connect(_users_dbfile)
    try:
        row = conn.execute(
            "SELECT username, password_hash, role, created_at "
            "FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "username": row[0],
        "password_hash": row[1],
        "role": row[2],
        "created_at": row[3],
    }


def user_exists(username):
    return get_user(username) is not None


def authenticate(username, password):
    "True when the credentials check out; live read."
    user = get_user(username)
    if user is None:
        return False
    return check_password_hash(user["password_hash"], password)


def create_user(username, password, role=_ROLE_USER):
    "Create an account.  Raises ValueError on bad input/duplicates."
    if not paths.valid_username(username):
        raise ValueError("Username must be 1-32 chars: letters, digits, . _ - only.")
    if role not in (_ROLE_ADMIN, _ROLE_USER):
        raise ValueError(f"Invalid role: {role}")
    if len(password or "") < 4:
        raise ValueError("Password must be at least 4 characters.")
    with _write_lock:
        if user_exists(username):
            raise ValueError(f"User '{username}' already exists.")
        conn = _open_users_db()
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) "
                "VALUES (?, ?, ?, datetime('now'))",
                (username, hash_password(password), role),
            )
            conn.commit()
        finally:
            conn.close()


def set_password(username, new_password):
    "Reset an account password.  Raises ValueError if user missing."
    if len(new_password or "") < 4:
        raise ValueError("Password must be at least 4 characters.")
    with _write_lock:
        if not user_exists(username):
            raise ValueError(f"User '{username}' does not exist.")
        conn = _open_users_db()
        try:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (hash_password(new_password), username),
            )
            conn.commit()
        finally:
            conn.close()


def set_role(username, role):
    "Change an account role; refuses to demote the last admin."
    if role not in (_ROLE_ADMIN, _ROLE_USER):
        raise ValueError(f"Invalid role: {role}")
    with _write_lock:
        if get_user(username) is None:
            raise ValueError(f"User '{username}' does not exist.")
        if role != _ROLE_ADMIN and _admin_count() <= 1 and is_admin(username):
            raise ValueError("Cannot demote the last admin.")
        conn = _open_users_db()
        try:
            conn.execute(
                "UPDATE users SET role = ? WHERE username = ?", (role, username)
            )
            conn.commit()
        finally:
            conn.close()


def delete_user(username):
    "Remove an account; refuses to delete the last admin."
    with _write_lock:
        if not user_exists(username):
            raise ValueError(f"User '{username}' does not exist.")
        if is_admin(username) and _admin_count() <= 1:
            raise ValueError("Cannot delete the last admin.")
        conn = _open_users_db()
        try:
            conn.execute("DELETE FROM users WHERE username = ?", (username,))
            conn.commit()
        finally:
            conn.close()
    # Book-share records reference the account by name; drop them so the
    # admin's book page never offers to unshare a vanished user.
    delete_assignments_for_user(username)


def _admin_count():
    if not os.path.exists(_users_dbfile):
        return 0
    conn = sqlite3.connect(_users_dbfile)
    try:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin'"
        ).fetchone()
    finally:
        conn.close()
    return count


def is_admin(username):
    user = get_user(username)
    return bool(user and user["role"] == _ROLE_ADMIN)


def admin_usernames():
    return [u["username"] for u in users() if u["role"] == _ROLE_ADMIN]


# ---------------------------------------------------------------------------
# book sharing records


def _bookassignments_conn():
    "Open users.db for book-assignment reads (schema is created on write)."
    if not _users_dbfile or not os.path.exists(_users_dbfile):
        return None
    conn = sqlite3.connect(_users_dbfile)
    _ensure_schema(conn)
    return conn


def record_assignment(admin, target, src_bkid, dst_bkid, title):
    "Remember that src_bkid was copied to dst_bkid in the target's db."
    with _write_lock:
        conn = _open_users_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO bookassignments "
                "(admin, target, src_bkid, dst_bkid, title, created_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (admin, target, src_bkid, dst_bkid, title or ""),
            )
            conn.commit()
        finally:
            conn.close()


def assignments(target=None):
    """
    Book-assignment rows, newest first.

    target=None returns every row (admins only, for the accounts page);
    a target name narrows it to that account.
    """
    conn = _bookassignments_conn()
    if conn is None:
        return []
    try:
        sql = (
            "SELECT id, admin, target, src_bkid, dst_bkid, title, created_at "
            "FROM bookassignments"
        )
        params = ()
        if target is not None:
            sql += " WHERE target = ?"
            params = (target,)
        sql += " ORDER BY id DESC"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r[0],
            "admin": r[1],
            "target": r[2],
            "src_bkid": r[3],
            "dst_bkid": r[4],
            "title": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]


def find_assignment(target, src_bkid):
    "The row for (target, source book), or None."
    for a in assignments(target):
        if a["src_bkid"] == src_bkid:
            return a
    return None


def delete_assignment(target, dst_bkid):
    "Forget a share (after its copy was removed or is already gone)."
    with _write_lock:
        conn = _open_users_db()
        try:
            conn.execute(
                "DELETE FROM bookassignments WHERE target = ? AND dst_bkid = ?",
                (target, dst_bkid),
            )
            conn.commit()
        finally:
            conn.close()


def delete_assignments_for_user(username):
    "Drop share records that reference a deleted account."
    if not _users_dbfile or not os.path.exists(_users_dbfile):
        return
    with _write_lock:
        conn = _open_users_db()
        try:
            conn.execute(
                "DELETE FROM bookassignments WHERE target = ? OR admin = ?",
                (username, username),
            )
            conn.commit()
        finally:
            conn.close()
