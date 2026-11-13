"""
Restoring a backup must not lose AI explanations.

A backup is the whole sqlite file, so ``airesults`` is already in it.
The restore replaces the database, though: explanations generated after
the backup was taken have to be merged back from the pre-restore copy.
"""

import gzip
import json
import os
import shutil
import sqlite3

import pytest

from lute.ai import service as ai_service
from lute.backup.service import Service
from lute.db import db
from lute.models.book import Sentence
from lute.models.repositories import UserSettingRepository
from tests.utils import make_book

SENTENCES = [
    "Hello there, friend.",
    "A much longer sentence that stays similar.",
    "Goodbye.",
]
ORPHAN = "Sentence that is not in any restored book."


@pytest.fixture(autouse=True)
def _use_file_db(monkeypatch):
    """
    The restore flow swaps the on-disk db file, so this module opts out
    of the shared in-memory test db.
    """
    monkeypatch.delenv("LUTE_DB_URI", raising=False)


def _make_book_with_sentences(english, sentences=SENTENCES):
    "A book whose sentence rows are written directly, so text is stable."
    book = make_book("AI restore book", "placeholder", english)
    db.session.add(book)
    db.session.commit()
    text = book.texts[0]
    for index, sentence in enumerate(sentences, start=1):
        db.session.add(Sentence(tx_id=text.id, order=index, text_content=sentence))
    db.session.commit()
    return book


def _save(sentence, translation, book, lang_id):
    "Store one sentence explanation the same way an import would."
    return ai_service.save_result(
        "sentence",
        sentence,
        {"translation": translation},
        provider_id="import",
        model="mix",
        target_lang="zh",
        book_id=book.id,
        lang_id=lang_id,
        source="import",
    )


def _make_backup_file(dbfilename, path, without_ai_results=False):
    """
    A gzipped backup copy of the current db.

    without_ai_results simulates a backup made before AI explanations
    existed: the table and its migration record are removed.
    """
    plain = f"{path}.db"
    shutil.copy2(dbfilename, plain)
    if without_ai_results:
        conn = sqlite3.connect(plain)
        try:
            conn.execute("DROP TABLE IF EXISTS airesults")
            conn.execute(
                "DELETE FROM _migrations WHERE filename = ?",
                ("20261105_create_ai_results.sql",),
            )
            conn.commit()
        finally:
            conn.close()
    gzpath = f"{plain}.gz"
    with open(plain, "rb") as f_in, gzip.open(gzpath, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(plain)
    return gzpath


def _ai_rows(dbfilename):
    "Every stored AI result row in the db file."
    conn = sqlite3.connect(dbfilename)
    try:
        return conn.execute(
            'SELECT "key", sentence, result_json FROM airesults'
        ).fetchall()
    finally:
        conn.close()


def _table_exists(dbfilename, table):
    conn = sqlite3.connect(dbfilename)
    try:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            is not None
        )
    finally:
        conn.close()


def _migration_recorded(dbfilename, name):
    conn = sqlite3.connect(dbfilename)
    try:
        return (
            conn.execute(
                "SELECT 1 FROM _migrations WHERE filename = ?", (name,)
            ).fetchone()
            is not None
        )
    finally:
        conn.close()


def _restore(client, backup_path):
    with open(backup_path, "rb") as f:
        resp = client.post(
            "/backup/restore",
            data={"backup_file": (f, os.path.basename(backup_path))},
            follow_redirects=True,
        )
    assert resp.status_code == 200, f"restore request failed: {resp.status_code}"
    return resp


def test_created_backup_contains_ai_results(
    app, testconfig, tmp_path, english  # pylint: disable=unused-argument
):
    book = _make_book_with_sentences(english)
    assert _save(SENTENCES[0], "stored translation", book, english.id)

    repo = UserSettingRepository(db.session)
    settings = repo.get_backup_settings()
    settings.backup_dir = str(tmp_path / "bkp")
    settings.backup_enabled = True
    settings.backup_count = 100

    backup_path = Service(db.session).create_backup(testconfig, settings)
    plain = str(tmp_path / "unpacked.db")
    with gzip.open(backup_path, "rb") as f_in, open(plain, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    rows = _ai_rows(plain)
    assert len(rows) == 1
    assert rows[0][1] == SENTENCES[0]
    assert json.loads(rows[0][2])["translation"] == "stored translation"


def test_restore_merges_current_ai_results(
    app, client, testconfig, tmp_path, english  # pylint: disable=unused-argument
):
    book = _make_book_with_sentences(english)
    # A row that the backup also contains, in an older version.
    assert _save(SENTENCES[0], "old translation", book, english.id)
    backup_path = _make_backup_file(
        testconfig.dbfilename, str(tmp_path / "lute_backup_20260101_000000")
    )
    # Rows generated after the backup was taken.
    assert _save(SENTENCES[0], "new translation", book, english.id)
    assert _save(SENTENCES[2], "post-backup translation", book, english.id)
    assert _save(ORPHAN, "orphan translation", book, english.id)

    _restore(client, backup_path)

    rows = _ai_rows(testconfig.dbfilename)
    by_sentence = {r[1]: json.loads(r[2])["translation"] for r in rows}
    # The pre-restore (newer) version wins for the same result key.
    assert by_sentence[SENTENCES[0]] == "new translation"
    # Explanations added after the backup are merged in.
    assert by_sentence[SENTENCES[2]] == "post-backup translation"
    # A sentence the restored library does not have is skipped, not an error.
    assert ORPHAN not in by_sentence
    # One row per key: no duplicate insert of the same result.
    key = ai_service.result_key("sentence", SENTENCES[0], "zh", "import", "mix")
    assert [r[0] for r in rows].count(key) == 1


def test_restore_recreates_ai_results_table_in_old_backup(
    app, client, testconfig, tmp_path, english  # pylint: disable=unused-argument
):
    book = _make_book_with_sentences(english)
    backup_path = _make_backup_file(
        testconfig.dbfilename,
        str(tmp_path / "lute_backup_20250101_000000"),
        without_ai_results=True,
    )
    assert _save(SENTENCES[0], "kept translation", book, english.id)

    _restore(client, backup_path)

    assert _table_exists(testconfig.dbfilename, "airesults")
    rows = _ai_rows(testconfig.dbfilename)
    assert [r[1] for r in rows] == [SENTENCES[0]]
    assert json.loads(rows[0][2])["translation"] == "kept translation"
    # The post-restore migrations still record the table's migration.
    assert _migration_recorded(testconfig.dbfilename, "20261105_create_ai_results.sql")
