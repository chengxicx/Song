"""
Book sharing: an admin's book is copied into another account's
database, with the reading state reset.

These tests work on real per-user sqlite files (created by setup_db, so
the schema and migrations match production) and never touch the ORM:
book_share writes with plain sqlite3 on purpose.
"""

import os
import sqlite3

import pytest

from lute.db.setup.main import setup_db
from lute.multiuser import book_share, paths, store


class FakeBase:  # pylint: disable=too-few-public-methods
    "Minimal AppConfig stand-in for per-user path resolution."

    def __init__(self, datapath):
        self.datapath = datapath
        self.dbname = "lute.db"
        self.plugin_datapath = os.path.join(datapath, "plugins")
        self.env = "unit_test"
        self.is_docker = False
        self.is_test_db = True
        self.dbfilename = os.path.join(datapath, "lute.db")


@pytest.fixture(name="base")
def fixture_base(tmp_path):
    "An admin and a child account, each with a real (empty) database."
    datapath = tmp_path / "data"
    os.makedirs(datapath, exist_ok=True)
    base = FakeBase(str(datapath))
    store.load(base)
    store.create_user("admin", "secret1", role="admin")
    store.create_user("kid", "secret1")
    for name in ("admin", "kid"):
        cfg = paths.user_config(base, name)
        paths.ensure_user_dirs(cfg)
        setup_db(cfg)
    yield base


def _conn(base, username):
    conn = sqlite3.connect(paths.user_dbfilename(base, username))
    conn.row_factory = sqlite3.Row
    return conn


def _add_language(conn, lgid, name):
    conn.execute(
        """
        INSERT INTO languages (LgID, LgName, LgCharacterSubstitutions,
            LgRegexpSplitSentences, LgExceptionsSplitSentences,
            LgRegexpWordCharacters, LgRightToLeft, LgShowRomanization,
            LgParserType)
        VALUES (?, ?, '', '', '', '', 0, 0, 'spacedel')
        """,
        (lgid, name),
    )


def _seed_admin_book(base, audio="clip.mp3"):
    "A two-page book the admin has already started reading."
    cfg = paths.user_config(base, "admin")
    if audio:
        os.makedirs(cfg.useraudiopath, exist_ok=True)
        with open(os.path.join(cfg.useraudiopath, audio), "wb") as f:
            f.write(b"fake-mp3-bytes")
    conn = _conn(base, "admin")
    try:
        with conn:
            _add_language(conn, 1, "English")
            conn.execute(
                """
                INSERT INTO books (BkID, BkLgID, BkTitle, BkCurrentTxID,
                    BkArchived, BkAudioFilename, BkAudioCurrentPos,
                    BkAudioBookmarks, BkBookType)
                VALUES (1, 1, 'Shared Book', 2, 1, ?, 12.5, '[]', '')
                """,
                (audio,),
            )
            conn.execute(
                """
                INSERT INTO texts (TxID, TxBkID, TxOrder, TxText, TxReadDate,
                    TxStartDate, TxWordCount)
                VALUES (1, 1, 1, 'first page', '2026-01-01 10:00:00',
                        '2026-01-01 09:00:00', 2)
                """,
            )
            conn.execute(
                """
                INSERT INTO texts (TxID, TxBkID, TxOrder, TxText, TxReadDate,
                    TxStartDate, TxWordCount)
                VALUES (2, 1, 2, 'second page', NULL, '2026-01-02 09:00:00', 2)
                """,
            )
            conn.execute("INSERT INTO tags2 (T2ID, T2Text) VALUES (1, 'series-a')")
            conn.execute("INSERT INTO booktags (BtBkID, BtT2ID) VALUES (1, 1)")
            conn.execute(
                "INSERT INTO bookstats (BkID, new_word_percent) VALUES (1, 42)"
            )
    finally:
        conn.close()
    return cfg


def test_share_copies_content_and_resets_reading_state(base):
    _seed_admin_book(base)
    result = book_share.share_book(base, "admin", "kid", 1)

    assert result["skipped"] is False
    assert result["pages"] == 2

    conn = _conn(base, "kid")
    try:
        book = conn.execute("SELECT * FROM books").fetchone()
        assert book["BkTitle"] == "Shared Book"
        assert book["BkSharedBy"] == "admin"
        # Reading state must start fresh.
        assert book["BkCurrentTxID"] == 0
        assert book["BkArchived"] == 0
        assert book["BkAudioCurrentPos"] is None
        assert book["BkAudioBookmarks"] is None

        pages = conn.execute("SELECT * FROM texts ORDER BY TxOrder").fetchall()
        assert [p["TxText"] for p in pages] == ["first page", "second page"]
        assert [p["TxWordCount"] for p in pages] == [2, 2]
        assert all(p["TxReadDate"] is None for p in pages)
        assert all(p["TxStartDate"] is None for p in pages)

        # Series tags carry over so the book set still works.
        tags = conn.execute(
            "SELECT t2.T2Text FROM tags2 t2 "
            "INNER JOIN booktags bt ON bt.BtT2ID = t2.T2ID "
            "WHERE bt.BtBkID = ?",
            (book["BkID"],),
        ).fetchall()
        assert [t[0] for t in tags] == ["series-a"]

        # Stats are recomputed against the target's own known terms.
        stats = conn.execute(
            "SELECT * FROM bookstats WHERE BkID = ?", (book["BkID"],)
        ).fetchone()
        assert stats["BkStatsStale"] == 1
        assert stats["new_word_percent"] is None
    finally:
        conn.close()


def test_share_leaves_the_admin_book_untouched(base):
    _seed_admin_book(base)
    book_share.share_book(base, "admin", "kid", 1)
    conn = _conn(base, "admin")
    try:
        assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
        book = conn.execute("SELECT * FROM books").fetchone()
        assert book["BkCurrentTxID"] == 2
        assert book["BkArchived"] == 1
        assert book["BkSharedBy"] is None
    finally:
        conn.close()


def test_share_reuses_an_existing_language_of_the_target(base):
    _seed_admin_book(base)
    conn = _conn(base, "kid")
    try:
        with conn:
            _add_language(conn, 7, "English")
    finally:
        conn.close()

    book_share.share_book(base, "admin", "kid", 1)

    conn = _conn(base, "kid")
    try:
        assert conn.execute("SELECT COUNT(*) FROM languages").fetchone()[0] == 1
        assert conn.execute("SELECT BkLgID FROM books").fetchone()[0] == 7
    finally:
        conn.close()


def test_share_creates_the_language_when_missing(base):
    _seed_admin_book(base)
    book_share.share_book(base, "admin", "kid", 1)
    conn = _conn(base, "kid")
    try:
        assert conn.execute("SELECT COUNT(*) FROM languages").fetchone()[0] == 1
        lgname = conn.execute(
            "SELECT l.LgName FROM books b INNER JOIN languages l ON l.LgID = b.BkLgID"
        ).fetchone()[0]
        assert lgname == "English"
    finally:
        conn.close()


def test_share_copies_the_audio_file(base):
    admin_cfg = _seed_admin_book(base)
    book_share.share_book(base, "admin", "kid", 1)

    kid_cfg = paths.user_config(base, "kid")
    assert os.path.isfile(os.path.join(kid_cfg.useraudiopath, "clip.mp3"))
    assert os.path.isfile(os.path.join(admin_cfg.useraudiopath, "clip.mp3"))

    conn = _conn(base, "kid")
    try:
        assert conn.execute("SELECT BkAudioFilename FROM books").fetchone()[0] == (
            "clip.mp3"
        )
    finally:
        conn.close()


def test_sharing_twice_is_skipped_so_progress_is_kept(base):
    _seed_admin_book(base)
    first = book_share.share_book(base, "admin", "kid", 1)
    assert first["skipped"] is False

    # The target reads a page.
    conn = _conn(base, "kid")
    try:
        with conn:
            conn.execute("UPDATE texts SET TxReadDate = '2026-02-02' WHERE TxOrder = 1")
    finally:
        conn.close()

    second = book_share.share_book(base, "admin", "kid", 1)
    assert second["skipped"] is True
    assert second["dst_bkid"] == first["dst_bkid"]

    conn = _conn(base, "kid")
    try:
        assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
        read = conn.execute(
            "SELECT TxReadDate FROM texts WHERE TxOrder = 1"
        ).fetchone()[0]
        assert read == "2026-02-02"
    finally:
        conn.close()


def test_sharing_again_after_the_target_deleted_the_copy(base):
    _seed_admin_book(base)
    first = book_share.share_book(base, "admin", "kid", 1)
    conn = _conn(base, "kid")
    try:
        with conn:
            conn.execute("DELETE FROM books WHERE BkID = ?", (first["dst_bkid"],))
    finally:
        conn.close()

    again = book_share.share_book(base, "admin", "kid", 1)
    assert again["skipped"] is False
    assert len(store.assignments("kid")) == 1


def test_admin_books_lists_titles(base):
    _seed_admin_book(base)
    rows = book_share.admin_books(base, "admin")
    assert [(r["id"], r["title"], r["pages"]) for r in rows] == [(1, "Shared Book", 2)]


def test_shared_books_reports_missing_copies(base):
    _seed_admin_book(base)
    result = book_share.share_book(base, "admin", "kid", 1)
    rows = book_share.shared_books(base, "kid")
    assert len(rows) == 1
    assert rows[0]["missing"] is False
    assert rows[0]["title"] == "Shared Book"

    conn = _conn(base, "kid")
    try:
        with conn:
            conn.execute("DELETE FROM books WHERE BkID = ?", (result["dst_bkid"],))
    finally:
        conn.close()
    assert book_share.shared_books(base, "kid")[0]["missing"] is True


def test_unshare_removes_the_copy_and_its_records(base):
    _seed_admin_book(base)
    result = book_share.share_book(base, "admin", "kid", 1)

    book_share.unshare_book(base, "kid", result["dst_bkid"])

    conn = _conn(base, "kid")
    try:
        assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM texts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM booktags").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM bookstats").fetchone()[0] == 0
    finally:
        conn.close()
    assert store.assignments("kid") == []
    # The language row stays: it belongs to the account, not the book.
    assert not os.path.isfile(
        os.path.join(paths.user_config(base, "kid").useraudiopath, "clip.mp3")
    )


def test_unshare_keeps_audio_still_used_by_another_book(base):
    _seed_admin_book(base)
    result = book_share.share_book(base, "admin", "kid", 1)
    conn = _conn(base, "kid")
    try:
        with conn:
            conn.execute(
                "UPDATE books SET BkAudioFilename = 'other.mp3' WHERE BkID = ?",
                (result["dst_bkid"],),
            )
            conn.execute(
                """
                INSERT INTO books (BkID, BkLgID, BkTitle, BkAudioFilename)
                VALUES (99, 1, 'Own book', 'clip.mp3')
                """
            )
    finally:
        conn.close()

    book_share.unshare_book(base, "kid", result["dst_bkid"])
    assert os.path.isfile(
        os.path.join(paths.user_config(base, "kid").useraudiopath, "clip.mp3")
    )


def test_cannot_share_with_yourself(base):
    _seed_admin_book(base)
    with pytest.raises(book_share.ShareError):
        book_share.share_book(base, "admin", "admin", 1)


def test_missing_target_database_is_reported(base):
    _seed_admin_book(base)
    store.create_user("nodb", "secret1")
    with pytest.raises(book_share.ShareError, match="no database"):
        book_share.share_book(base, "admin", "nodb", 1)


def test_missing_source_book_is_reported(base):
    _seed_admin_book(base)
    with pytest.raises(book_share.ShareError):
        book_share.share_book(base, "admin", "kid", 12345)
