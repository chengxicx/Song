"""
Per-user filesystem paths.

Each user gets a self-contained directory under <datapath>/users/<name>/
holding their own database and media, so language IDs and filenames
never collide between users:

    users/<name>/lute.db
    users/<name>/userimages/
    users/<name>/useraudio/
    users/<name>/userthemes/
    users/<name>/backups/
    users/<name>/.system_db_backups/
    users/<name>/temp/

Shared, non-user data (parser plugin data) stays at the base datapath.
"""

import os
import threading

_config_cache = {}
_cache_lock = threading.Lock()

# User-scoped snapshot attributes of AppConfig.
USER_SCOPED_ATTRS = (
    "datapath",
    "dbname",
    "dbfilename",
    "userimagespath",
    "useraudiopath",
    "userthemespath",
    "temppath",
    "system_backup_path",
    "default_user_backup_path",
    "sqliteconnstring",
)


def valid_username(name):
    "Usernames are filesystem directory names; keep them strict."
    if not isinstance(name, str):
        return False
    if not (1 <= len(name) <= 32):
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
    return set(name) <= allowed and name not in (".", "..")


def users_root(base_config):
    "Root directory holding one subdirectory per user."
    return os.path.join(base_config.datapath, "users")


def user_dir(base_config, username):
    "The given user's data directory."
    if not valid_username(username):
        raise ValueError(f"Invalid username: {username!r}")
    return os.path.join(users_root(base_config), username)


def user_dbfilename(base_config, username):
    "The user's own sqlite db file (same file name as the base db)."
    return os.path.join(user_dir(base_config, username), base_config.dbname)


class UserAppConfig:
    """
    Concrete, request-independent AppConfig-like snapshot for one user.

    Mirrors the attribute surface of AppConfig that the app touches;
    parser plugin data stays shared at the base datapath.
    """

    def __init__(self, base_config, username):
        udir = user_dir(base_config, username)
        self.username = username
        self.env = base_config.env
        self.is_docker = base_config.is_docker
        self.is_test_db = base_config.is_test_db
        self.dbname = base_config.dbname
        self.datapath = udir
        self.plugin_datapath = base_config.plugin_datapath
        self.userimagespath = os.path.join(udir, "userimages")
        self.useraudiopath = os.path.join(udir, "useraudio")
        self.userthemespath = os.path.join(udir, "userthemes")
        self.temppath = os.path.join(udir, "temp")
        self.dbfilename = os.path.join(udir, base_config.dbname)
        self.system_backup_path = os.path.join(udir, ".system_db_backups")
        self.default_user_backup_path = os.path.join(udir, "backups")

    @property
    def sqliteconnstring(self):
        "Full sqlite connection string."
        return f"sqlite:///{self.dbfilename}"


def user_config(base_config, username):
    "Cached UserAppConfig snapshot for the given user."
    key = (base_config.dbfilename, username)
    with _cache_lock:
        cfg = _config_cache.get(key)
        if cfg is None:
            cfg = UserAppConfig(base_config, username)
            _config_cache[key] = cfg
    return cfg


def ensure_user_dirs(user_cfg):
    "Create the user's directory tree if missing."
    for d in (
        user_cfg.datapath,
        user_cfg.userimagespath,
        user_cfg.useraudiopath,
        user_cfg.userthemespath,
        user_cfg.temppath,
        user_cfg.system_backup_path,
        user_cfg.default_user_backup_path,
    ):
        os.makedirs(d, exist_ok=True)


def base_setup_dirs(base_config):
    """
    Base-level dirs needed in multi-user mode (user dirs are created
    per user by ensure_user_dirs).
    """
    return [
        base_config.datapath,
        base_config.plugin_datapath,
        base_config.temppath,
    ]


# Directories/files moved between the datapath root and a user's dir
# when multi-user mode is switched on or off.
MIGRATABLE_ENTRIES = (
    "userimages",
    "useraudio",
    "userthemes",
    "backups",
    ".system_db_backups",
)
