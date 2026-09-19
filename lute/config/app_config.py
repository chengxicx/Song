"""
App configuration.
"""

import os
import sqlite3
import yaml
from platformdirs import PlatformDirs


class AppConfig:  # pylint: disable=too-many-instance-attributes
    """
    Configuration wrapper around yaml file.

    Adds various properties for lint-time checking.
    """

    def __init__(self, config_file_path):
        """
        Load the required configuration file.
        """
        self._load_config(config_file_path)

    def _load_config(self, config_file_path):
        """
        Load and validate the config file.
        """
        with open(config_file_path, "r", encoding="utf-8") as cf:
            config = yaml.safe_load(cf)

        if not isinstance(config, dict):
            raise RuntimeError(
                f"File at {config_file_path} is invalid or is not a yaml dictionary."
            )

        self.env = config.get("ENV", None)
        if self.env not in ["prod", "dev"]:
            raise ValueError(f"ENV must be prod or dev, was {self.env}.")

        self.is_docker = bool(config.get("IS_DOCKER", False))

        # Database name.
        self.dbname = config.get("DBNAME", None)
        if self.dbname is None:
            raise ValueError("Config file must have 'DBNAME'")

        # Various invoke tasks in /tasks.py check if the database is a
        # test_ db prior to running some destructive action.
        self.is_test_db = self.dbname.startswith("test_")

        # Path to user data.
        # LUTE_DATAPATH lets a test harness redirect every file the app
        # writes (user images/audio/themes) to a per-process location --
        # e.g. one dir per pytest-xdist worker -- without editing config
        # files.  Set by tests/conftest.py only.
        self.datapath = os.environ.get("LUTE_DATAPATH") or config.get(
            "DATAPATH", self._get_appdata_dir()
        )
        self.plugin_datapath = os.path.join(self.datapath, "plugins")
        self.userimagespath = os.path.join(self.datapath, "userimages")
        self.useraudiopath = os.path.join(self.datapath, "useraudio")
        self.userthemespath = os.path.join(self.datapath, "userthemes")
        self.temppath = os.path.join(self.datapath, "temp")
        self.dbfilename = os.path.join(self.datapath, self.dbname)

        # Test harness hook (tests/conftest.py): when LUTE_DB_URI is set,
        # it is used as the actual sqlite target for every connection --
        # e.g. "file:lute_test_pytest?mode=memory&cache=shared", a named
        # in-memory db built once per process.  dbfilename keeps
        # describing the on-disk location used by file-based operations
        # (backups, startup checks, tests of the file lifecycle itself).
        # The empty string counts as unset, so `LUTE_DB_URI= pytest ...`
        # forces file-db mode.
        self.db_uri = os.environ.get("LUTE_DB_URI") or None

        # Path to db backup.
        # When Lute starts up, it backs up the db
        # if migrations are going to be applied, just in case.
        # Hidden directory as a hint to the the user that
        # this is a system dir.
        self.system_backup_path = os.path.join(self.datapath, ".system_db_backups")

        # Default backup path for user, can be overridden in settings.
        self.default_user_backup_path = config.get(
            "BACKUP_PATH", os.path.join(self.datapath, "backups")
        )

    def _get_appdata_dir(self):
        "Get user's appdata directory from platformdirs."
        dirs = PlatformDirs("Lute3", "Lute3")
        return dirs.user_data_dir

    @property
    def sqliteconnstring(self):
        "Full sqlite connection string."
        if self.db_uri:
            # SQLAlchemy's sqlite dialect passes a file: URL through to
            # sqlite3 when the "uri=true" query flag is present, so the
            # named in-memory db is reachable through the ORM too.
            return f"sqlite:///{self.db_uri}&uri=true"
        return f"sqlite:///{self.dbfilename}"

    def sqlite3_connect(self, **kwargs):
        """
        Open a raw connection to this config's database: the LUTE_DB_URI
        override when set (opened with uri=True), else the db file.
        """
        return sqlite3.connect(
            self.db_uri or self.dbfilename, uri=self.db_uri is not None, **kwargs
        )

    @staticmethod
    def configdir():
        "Return the path to the configuration file directory."
        return os.path.dirname(os.path.realpath(__file__))

    @staticmethod
    def default_config_filename():
        "Return the path to the default configuration file."
        thisdir = AppConfig.configdir()
        return os.path.join(thisdir, "config.yml")
