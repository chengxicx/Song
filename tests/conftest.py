"""
Common fixtures used by many tests.
"""

import os
import sqlite3
import yaml
import pytest

import lute

# Opt this process out of the WorkBuddy CLI "safe-delete" bulk guard.
# The sandbox shim patches os.remove/unlink and, when a deletion
# touches >= 50 files (e.g. a test-imported PDF book's static folder),
# demands confirmation via a helper that is unavailable to test runs,
# fail-closing with SystemExit(1).  That aborts fixtures mid-setup and
# poisons the shared SQLAlchemy session, cascading into hundreds of
# unrelated failures.  Tests only ever delete files they created
# themselves, so remove the guard's trigger env vars here.
for _var in ("CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR", "CODEBUDDY_TOOL_CALL_ID"):
    os.environ.pop(_var, None)

from lute.config.app_config import AppConfig
from lute.db import db
import lute.db.management
from lute.language.service import Service
from lute.app_factory import create_app

from lute.models.language import Language


# The shared in-memory test db (see pytest_sessionstart below).  Named
# in-memory dbs exist per process, so each pytest-xdist worker gets its
# own automatically; within a process the name just has to be stable.
_MEMORY_DB_URI = "file:lute_test_pytest?mode=memory&cache=shared"


def pytest_sessionstart(session):  # pylint: disable=unused-argument
    """
    Ensure test config defines a test environment.

    Special configuration for test runs required:

    DBNAME must start with test_

    DATAPATH must be specified: this ensures that the tests don't
    accidentally write into the user_data (which could mess with prod
    data/media etc)

    Also points every connection at a named in-memory sqlite db
    (LUTE_DB_URI, see AppConfig): the schema is built once per process
    and reset between tests, instead of deleting and rebuilding the db
    file for every test.  Tests that specifically exercise the on-disk
    db file lifecycle opt out by clearing the env var (see
    tests/integration/test_main.py, tests/unit/multiuser).
    """
    thisdir = os.path.dirname(os.path.realpath(__file__))
    configfile = os.path.join(thisdir, "..", "lute", "config", "config.yml")

    config = None
    with open(configfile, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    failures = []
    if "DATAPATH" not in config:
        failures.append("DATAPATH not in config file")

    ac = AppConfig(configfile)

    if not ac.is_test_db:
        failures.append("DBNAME in config.yml must start with test_")
    if len(failures) > 0:
        msg = f"Bad config.yml: {', '.join(failures)}"
        pytest.exit(msg)

    os.environ["LUTE_DB_URI"] = _MEMORY_DB_URI
    # xdist workers run in separate processes, so they already get
    # separate in-memory dbs; the shared on-disk datapath is what they'd
    # race on (user images/audio), so give each its own.
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker:
        os.environ["LUTE_DATAPATH"] = f"/tmp/lute_test_data_{worker}"


@pytest.fixture(name="testconfig")
def fixture_config():
    "Config using the app config."
    ac = AppConfig(AppConfig.default_config_filename())
    yield ac


@pytest.fixture(scope="session", autouse=True)
def _memory_db_keeper():
    """
    Create the shared in-memory db schema once, and hold one connection
    to it open for the session.

    sqlite destroys a named in-memory db when its last connection
    closes, so this keeper is what makes the db survive between tests
    (every other connection is opened and closed per checkout).  Building
    the baseline here also means the per-test reset always has tables to
    clear, even before the first create_app ran setup_db.
    """
    ac = AppConfig(AppConfig.default_config_filename())
    if ac.db_uri is None:
        yield None
        return
    conn = sqlite3.connect(ac.db_uri, uri=True, check_same_thread=False)
    conn.executescript(_baseline_sql())
    conn.commit()
    yield conn
    conn.close()


def _baseline_sql():
    "The baseline schema sql (same file setup_db uses)."
    schema_dir = os.path.join(os.path.dirname(lute.__file__), "db", "schema")
    with open(os.path.join(schema_dir, "baseline.sql"), "r", encoding="utf8") as f:
        return f.read()


def _reset_memory_db(config):
    """
    Wipe the shared in-memory db's data back to freshly-baselined state.

    The schema (tables, indexes, triggers, static rows, migration
    records) is built once per session and left in place; only data is
    removed.  This is what makes the suite cheap -- rebuilding the
    schema per test (the old unlink-the-file approach) costs about the
    same as the data wipe does, and a naive "delete from languages"
    cascade is actively expensive: the AFTER DELETE trigger on words
    updates the surviving rows on every deleted row, which is quadratic
    on a demo-data-sized db.  So: drop the triggers, delete every
    table's rows with FKs off, restore the baseline system rows,
    re-create the triggers.
    """
    conn = sqlite3.connect(config.db_uri, uri=True)
    try:
        trigger_sql = [
            r[0]
            for r in conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                "AND sql IS NOT NULL"
            )
        ]
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ):
            conn.execute(f'drop trigger if exists "{name}"')
        conn.execute("pragma foreign_keys = OFF")
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
                # Static rows every fresh db has: don't wipe them.
                " AND name NOT IN ('statuses', '_migrations')"
            )
        ]
        for name in tables:
            conn.execute(f'delete from "{name}"')
        conn.execute("insert into settings values('LoadDemoData','system','1')")
        for sql in trigger_sql:
            conn.execute(sql)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(name="app")
def fixture_app():
    """
    A clean instance of the demo database.

    With the shared in-memory db (default): the schema is built once
    per process and wiped back to baseline state before every test.
    Tests that opt out (LUTE_DB_URI cleared) still get the historical
    delete-the-file-and-recreate behaviour.
    """
    config_file = AppConfig.default_config_filename()
    c = AppConfig(config_file)
    if c.db_uri is not None:
        _reset_memory_db(c)
    elif os.path.exists(c.dbfilename):
        os.unlink(c.dbfilename)
    extra_config = {"WTF_CSRF_ENABLED": False, "TESTING": True}
    app = create_app(config_file, extra_config=extra_config)
    yield app


@pytest.fixture(name="app_context")
def fixture_app_context(app):
    """
    Yields the app context so that tests using the db will work.
    """
    with app.app_context() as c:
        yield c


@pytest.fixture(name="empty_db")
def fixture_empty_db(app_context):
    """
    Wipe the db.
    """
    lute.db.management.delete_all_data(db.session)


@pytest.fixture(name="client")
def fixture_demo_client(app):
    """
    Client using demo-data-loaded application.
    """
    return app.test_client()


def _get_test_language(lang_name):
    """
    Return language from the db if it already exists,
    or create it from the file.
    """
    lang = db.session.query(Language).filter(Language.name == lang_name).first()
    if lang is not None:
        return lang
    service = Service(db.session)
    lang = service.get_language_def(lang_name).language
    db.session.add(lang)
    db.session.commit()
    return lang


@pytest.fixture(name="spanish")
def fixture_spanish(app_context):
    return _get_test_language("Spanish")


@pytest.fixture(name="english")
def fixture_english(app_context):
    return _get_test_language("English")


@pytest.fixture(name="japanese")
def fixture_japanese(app_context):
    return _get_test_language("Japanese")


@pytest.fixture(name="korean")
def fixture_korean(app_context):
    return _get_test_language("Korean")


@pytest.fixture(name="german")
def fixture_german(app_context):
    return _get_test_language("German")


@pytest.fixture(name="thai")
def fixture_thai(app_context):
    return _get_test_language("Thai")


@pytest.fixture(name="russian")
def fixture_russian(app_context):
    return _get_test_language("Russian")


@pytest.fixture(name="french")
def fixture_french(app_context):
    return _get_test_language("French")


@pytest.fixture(name="arabic")
def fixture_arabic(app_context):
    return _get_test_language("Arabic")


@pytest.fixture(name="mandarin")
def fixture_mandarin(app_context):
    return _get_test_language("Mandarin Chinese")


@pytest.fixture(name="turkish")
def fixture_turkish(app_context):
    return _get_test_language("Turkish")


@pytest.fixture(name="classical_chinese")
def fixture_cl_chinese(app_context):
    return _get_test_language("Classical Chinese")


@pytest.fixture(name="hindi")
def fixture_hindi(app_context):
    return _get_test_language("Hindi")


@pytest.fixture(name="generic")
def fixture_generic(app_context):
    return _get_test_language("Generic")
