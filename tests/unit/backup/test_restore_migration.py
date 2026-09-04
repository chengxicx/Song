"""
Regression test: restoring a backup made by an older schema (e.g. an
upstream Lute backup) must work without restarting the app.

The post-restore before_request handler re-runs pending migrations
(via setup_db) so that Song-specific columns (LgKiwi*, manga/pdf/srt
book fields, etc.) are added to the restored database on the next
request.
"""

import gzip
import os
import shutil
import sqlite3

import pytest

# Song-specific migrations (applied after upstream 3.10.x) and the
# schema elements they add.  A "real" upstream backup would be missing
# all of these; we simulate that by dropping them from a copy of the
# current db and removing the migration records.
SONG_LANG_COLUMNS = [
    "LgKiwiTokenizerMode",
    "LgKiwiStemming",
    "LgKiwiFilterParticles",
    "LgKiwiJoinCompoundNouns",
    "LgTTSLang",
    "LgTranslateTargetLang",
]
SONG_BOOK_COLUMNS = [
    "BkBookType",
    "BkSrtData",
    "BkVideoCurrentPos",
    "BkMangaPath",
    "BkMangaData",
    "BkMediaURL",
    "BkPdfPath",
]
SONG_BOOKSTATS_COLUMNS = ["manga_word_count", "BkStatsStale"]
SONG_MIGRATIONS = [
    "20260802_add_kiwi_settings.sql",
    "20260803_add_book_youtube_fields.sql",
    "20260811_add_manga_fields.sql",
    "20260812_add_manga_word_count.sql",
    "20260820_add_bookstats_stale.sql",
    "20260827_add_media_url.sql",
    "20260828_add_book_pdf_field.sql",
    "20260829_add_language_tts_translate.sql",
]


@pytest.fixture(name="upstream_backup_file")
def fixture_upstream_backup_file(testconfig):
    """
    Build a gzipped backup that looks like it came from upstream Lute:
    current data, but without Song's added columns or migration records.
    """
    import tempfile

    temp_dir = tempfile.mkdtemp()
    plain = os.path.join(temp_dir, "lute_backup_20250101_000000.db")
    gzpath = plain + ".gz"
    shutil.copy(testconfig.dbfilename, plain)

    conn = sqlite3.connect(plain)
    try:
        cur = conn.cursor()
        for table, cols in [
            ("languages", SONG_LANG_COLUMNS),
            ("books", SONG_BOOK_COLUMNS),
            ("bookstats", SONG_BOOKSTATS_COLUMNS),
        ]:
            existing = [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]
            for col in cols:
                if col in existing:
                    cur.execute(f'ALTER TABLE {table} DROP COLUMN "{col}"')
        qmarks = ",".join("?" * len(SONG_MIGRATIONS))
        cur.execute(f"DELETE FROM _migrations WHERE filename IN ({qmarks})", SONG_MIGRATIONS)
        conn.commit()
        # Sanity: the trimmed db really is missing Song's columns.
        langs = [r[1] for r in cur.execute("PRAGMA table_info(languages)")]
        assert "LgKiwiTokenizerMode" not in langs
    finally:
        conn.close()

    with open(plain, "rb") as f_in, gzip.open(gzpath, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(plain)

    yield gzpath

    shutil.rmtree(temp_dir)


def _column_exists(dbfilename, table, column):
    conn = sqlite3.connect(dbfilename)
    try:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        return column in cols
    finally:
        conn.close()


def test_restore_old_schema_backup_runs_migrations_without_restart(
    app, client, upstream_backup_file
):
    "After restore, the next request applies pending migrations."

    dbfile = app.env_config.dbfilename

    with open(upstream_backup_file, "rb") as f:
        resp = client.post(
            "/backup/restore",
            data={"backup_file": (f, "lute_backup_20250101_000000.db.gz")},
            follow_redirects=True,
        )
    assert resp.status_code == 200, f"restore request failed: {resp.status_code}"

    # The post-restore request (the redirect to /) should have run
    # setup_db, re-adding Song's schema to the restored database.
    assert _column_exists(dbfile, "languages", "LgKiwiTokenizerMode")
    assert _column_exists(dbfile, "books", "BkPdfPath")
    assert _column_exists(dbfile, "bookstats", "BkStatsStale")

    # Migration records are back, so subsequent restarts won't re-run them.
    conn = sqlite3.connect(dbfile)
    try:
        names = [
            r[0]
            for r in conn.execute("SELECT filename FROM _migrations WHERE filename LIKE '2026%'")
        ]
    finally:
        conn.close()
    for m in SONG_MIGRATIONS:
        assert m in names, f"migration {m} not recorded after restore"

    # And the app is usable: home page renders.
    resp = client.get("/")
    assert resp.status_code == 200
