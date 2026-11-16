"""
Sharing a book with another account.

In multi-user mode every account has its own sqlite file, so a book
cannot be referenced from two accounts at once: "sharing" means copying
the book's *content* into the other account's database.  That copy is
also what keeps the reading records independent -- progress, read dates,
terms and practice results live in tables that are simply not copied.

Copied
    books row            attributes, media paths, book type
    texts                page text / order / word count, read dates cleared
    tags2 + booktags     matched by tag text, created when missing
    languages            only when the target lacks that language
    audio file           useraudio is a per-user directory

Deliberately not copied
    sentences            derived; generated on first read with the
                         target's own parser/language settings
    bookstats            depends on the target's term statuses; a stale
                         row is inserted so it gets recomputed
    bookgrammar          derived from the text, recomputed on demand
    words / wordtags /   the reader's term vocabulary, never the book's
      wordimages
    wordsread,           reading history, bookmarks, read-aloud takes
      textbookmarks,     and AI results
      shadowattempts,
      airesults
    BkCurrentTxID,       reading position -- the copy starts at page 1
      read dates, and
      audio/video pos

Epub / manga / PDF files need no copying: those live under
``<datapath>/static/<uuid>/``, which every account shares, so the copy
reuses the same paths.
"""

import os
import shutil
import sqlite3
import threading

from lute.multiuser import paths, store


class ShareError(Exception):
    "Raised when a book cannot be shared."


# Shares are rare, administrative actions: serialize them so two admins
# can't pick the same next BkID for the same target database.
_SHARE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# sqlite helpers


def _connect(dbfile):
    "Open a user database for reading/writing outside the ORM."
    conn = sqlite3.connect(dbfile)
    conn.row_factory = sqlite3.Row
    # Deleting a book relies on the schema's ON DELETE CASCADE rules.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def _table_columns(conn, table):
    "Column names of the given table in this database."
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _table_exists(conn, table):
    "True when the table is present in this database."
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _next_id(conn, table, pk):
    "Next integer id for a table whose pk column is NOT NULL without autoincrement."
    sql = f"SELECT COALESCE(MAX({pk}), 0) + 1 FROM {table}"
    return conn.execute(sql).fetchone()[0]


def _insert_row(conn, table, values, pk=None):
    """
    Insert values, keeping only the columns the target database has.

    Two databases from different app versions don't have to agree on the
    column set; copying the intersection is always safe, and missing
    columns fall back to their schema defaults.
    """
    values = dict(values)
    if pk and values.get(pk) is None:
        values[pk] = _next_id(conn, table, pk)
    columns = [c for c in values if c in _table_columns(conn, table)]
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    cur = conn.execute(sql, [values[c] for c in columns])
    return cur.lastrowid


def _user_dbfile(base_config, username):
    "Path to the user's database, or a ShareError if it isn't there."
    dbfile = paths.user_dbfilename(base_config, username)
    if not os.path.exists(dbfile):
        raise ShareError(f"User '{username}' has no database yet.")
    return dbfile


# ---------------------------------------------------------------------------
# listing


def admin_books(base_config, admin):
    """
    The admin's own books, for the sharing page.

    Returns a list of dicts: id, title, language, pages, words,
    book_type, archived.
    """
    dbfile = _user_dbfile(base_config, admin)
    conn = _connect(dbfile)
    try:
        rows = conn.execute(
            """
            SELECT b.BkID, b.BkTitle, l.LgName,
                   COALESCE(b.BkBookType, '') AS BkBookType,
                   COALESCE(b.BkArchived, 0) AS BkArchived,
                   COALESCE(tc.pagecount, 0) AS PageCount,
                   COALESCE(tc.wc, 0) AS WordCount
            FROM books b
            INNER JOIN languages l ON l.LgID = b.BkLgID
            LEFT OUTER JOIN (
                SELECT TxBkID, COUNT(TxID) AS pagecount, SUM(TxWordCount) AS wc
                FROM texts GROUP BY TxBkID
            ) tc ON tc.TxBkID = b.BkID
            ORDER BY b.BkTitle COLLATE NOCASE
            """
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r["BkID"],
            "title": r["BkTitle"],
            "language": r["LgName"],
            "pages": r["PageCount"],
            "words": r["WordCount"],
            "book_type": r["BkBookType"],
            "archived": bool(r["BkArchived"]),
        }
        for r in rows
    ]


def shared_books(base_config, target):
    """
    Books shared *into* the target account, newest first.

    Each dict is the users.db assignment row plus ``missing``: True when
    the copy is gone from the target's database (the user deleted their
    copy), in which case only the bookkeeping row is left.
    """
    rows = store.assignments(target)
    if not rows:
        return []
    dbfile = paths.user_dbfilename(base_config, target)
    present = set()
    if os.path.exists(dbfile):
        conn = _connect(dbfile)
        try:
            present = {r[0] for r in conn.execute("SELECT BkID FROM books").fetchall()}
        finally:
            conn.close()
    for r in rows:
        r["missing"] = r["dst_bkid"] not in present
    return rows


# ---------------------------------------------------------------------------
# copy helpers


def _map_language(src, dst, src_lgid):
    "Target database's LgID for the source language, copying the row if needed."
    row = src.execute("SELECT * FROM languages WHERE LgID = ?", (src_lgid,)).fetchone()
    if row is None:
        raise ShareError("The book's language row is missing.")
    name = row["LgName"]
    hit = dst.execute("SELECT LgID FROM languages WHERE LgName = ?", (name,)).fetchone()
    if hit is not None:
        return hit[0]
    values = {k: row[k] for k in row.keys() if k != "LgID"}
    return _insert_row(dst, "languages", values, pk="LgID")


def _copy_audio(base_config, admin, target, filename):
    """
    Copy a per-user audio file into the target's useraudio dir.

    Returns the filename the target's book row should use.  Absolute
    paths (CLI imports) are shared as-is, and an existing target file of
    the same size is assumed to be the same recording.
    """
    name = (filename or "").strip()
    if not name or os.path.isabs(name):
        return name
    src_dir = paths.user_config(base_config, admin).useraudiopath
    src_path = os.path.join(src_dir, name)
    if not os.path.isfile(src_path):
        return name
    dst_dir = paths.user_config(base_config, target).useraudiopath
    os.makedirs(dst_dir, exist_ok=True)
    dst_path = os.path.join(dst_dir, name)
    if os.path.exists(dst_path):
        if os.path.getsize(dst_path) == os.path.getsize(src_path):
            return name
        stem, ext = os.path.splitext(name)
        n = 1
        while os.path.exists(os.path.join(dst_dir, f"{stem}_{n}{ext}")):
            n += 1
        name = f"{stem}_{n}{ext}"
        dst_path = os.path.join(dst_dir, name)
    shutil.copy2(src_path, dst_path)
    return name


def _copy_book_rows(src, dst, src_bkid, admin, base_config, target):
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    # pylint: disable=too-many-locals
    "Insert the book and its pages into the target db.  Returns (new id, title)."
    book_row = src.execute("SELECT * FROM books WHERE BkID = ?", (src_bkid,)).fetchone()
    if book_row is None:
        raise ShareError("That book no longer exists in your library.")
    values = {k: book_row[k] for k in book_row.keys() if k != "BkID"}
    values["BkLgID"] = _map_language(src, dst, book_row["BkLgID"])
    # Reading state belongs to the reader, not the book: the copy starts
    # from the first page with no audio/video position and no bookmarks.
    values["BkCurrentTxID"] = 0
    values["BkArchived"] = 0
    values["BkAudioCurrentPos"] = None
    values["BkAudioBookmarks"] = None
    values["BkVideoCurrentPos"] = None
    values["BkSharedBy"] = admin
    audio_name = (
        book_row["BkAudioFilename"] if "BkAudioFilename" in book_row.keys() else None
    )
    values["BkAudioFilename"] = _copy_audio(base_config, admin, target, audio_name)
    new_bkid = _insert_row(dst, "books", values, pk="BkID")

    page_rows = src.execute(
        "SELECT * FROM texts WHERE TxBkID = ? ORDER BY TxOrder, TxID", (src_bkid,)
    ).fetchall()
    next_txid = _next_id(dst, "texts", "TxID")
    for r in page_rows:
        page = {k: r[k] for k in r.keys() if k != "TxID"}
        page["TxID"] = next_txid
        next_txid += 1
        page["TxBkID"] = new_bkid
        page["TxReadDate"] = None
        page["TxStartDate"] = None
        _insert_row(dst, "texts", page)

    tag_rows = src.execute(
        """
        SELECT t2.T2Text, t2.T2Comment
        FROM tags2 t2
        INNER JOIN booktags bt ON bt.BtT2ID = t2.T2ID
        WHERE bt.BtBkID = ?
        """,
        (src_bkid,),
    ).fetchall()
    for t in tag_rows:
        hit = dst.execute(
            "SELECT T2ID FROM tags2 WHERE T2Text = ?", (t["T2Text"],)
        ).fetchone()
        if hit is not None:
            t2id = hit[0]
        else:
            t2id = _insert_row(
                dst,
                "tags2",
                {"T2Text": t["T2Text"], "T2Comment": t["T2Comment"] or ""},
                pk="T2ID",
            )
        dst.execute(
            "INSERT OR IGNORE INTO booktags (BtBkID, BtT2ID) VALUES (?, ?)",
            (new_bkid, t2id),
        )

    # The new BkID is max(BkID)+1, so a book deleted earlier can hand its
    # id to this copy.  Drop any per-book cache rows left under that id
    # (a raw delete on a connection without foreign keys, or a Lute
    # version that doesn't cascade these, leaves them behind).
    for table, column in (
        ("bookstats", "BkID"),
        ("bookgrammar", "BgBkID"),
        ("airesults", "book_id"),
        ("shadowattempts", "SaBkID"),
    ):
        if _table_exists(dst, table):
            dst.execute(f"DELETE FROM {table} WHERE {column} = ?", (new_bkid,))

    # Stats are relative to the target's own known terms; ask for a
    # recompute instead of carrying the admin's numbers over.
    _insert_row(dst, "bookstats", {"BkID": new_bkid, "BkStatsStale": 1})

    return new_bkid, book_row["BkTitle"]


# ---------------------------------------------------------------------------
# public operations


def share_book(base_config, admin, target, src_bkid):
    """
    Copy one of the admin's books into the target account's database.

    Returns {"skipped": bool, "dst_bkid": int, "title": str, "pages": int}.
    A book that was already shared with this account is skipped rather
    than re-copied: overwriting would throw away the target's reading
    progress.  If the target deleted their copy, the stale bookkeeping
    row is dropped and the book is shared again.
    """
    if not admin or not target:
        raise ShareError("A source and a target account are required.")
    if admin == target:
        raise ShareError("You cannot share a book with your own account.")

    src_bkid = int(src_bkid)
    with _SHARE_LOCK:
        previous = store.find_assignment(target, src_bkid)
        if previous is not None:
            dst_file = paths.user_dbfilename(base_config, target)
            still_there = False
            if os.path.exists(dst_file):
                probe = _connect(dst_file)
                try:
                    still_there = (
                        probe.execute(
                            "SELECT 1 FROM books WHERE BkID = ?",
                            (previous["dst_bkid"],),
                        ).fetchone()
                        is not None
                    )
                finally:
                    probe.close()
            if still_there:
                return {
                    "skipped": True,
                    "dst_bkid": previous["dst_bkid"],
                    "title": previous["title"],
                    "pages": 0,
                }
            store.delete_assignment(target, previous["dst_bkid"])

        src_file = _user_dbfile(base_config, admin)
        dst_file = _user_dbfile(base_config, target)
        src = _connect(src_file)
        try:
            dst = _connect(dst_file)
            try:
                with dst:
                    new_bkid, title = _copy_book_rows(
                        src, dst, src_bkid, admin, base_config, target
                    )
            finally:
                dst.close()
        finally:
            src.close()

        store.record_assignment(admin, target, src_bkid, new_bkid, title)
        pages = 0
        conn = _connect(dst_file)
        try:
            pages = conn.execute(
                "SELECT COUNT(*) FROM texts WHERE TxBkID = ?", (new_bkid,)
            ).fetchone()[0]
        finally:
            conn.close()
        return {"skipped": False, "dst_bkid": new_bkid, "title": title, "pages": pages}


def unshare_book(base_config, target, dst_bkid):
    """
    Remove a shared copy from the target account, including its reading
    records (they belong to that copy).  The audio file is removed only
    when no other book of that account still points at it.
    """
    dst_bkid = int(dst_bkid)
    dst_file = _user_dbfile(base_config, target)
    audio_to_check = None
    conn = _connect(dst_file)
    try:
        row = conn.execute(
            "SELECT BkAudioFilename FROM books WHERE BkID = ?", (dst_bkid,)
        ).fetchone()
        if row is None:
            store.delete_assignment(target, dst_bkid)
            return
        audio_to_check = (row["BkAudioFilename"] or "").strip() or None
        with conn:
            # books cascades to texts/sentences/bookmarks/stats/grammar
            # because we turned foreign keys on; airesults has no fk.
            conn.execute("DELETE FROM books WHERE BkID = ?", (dst_bkid,))
            conn.execute("DELETE FROM airesults WHERE book_id = ?", (dst_bkid,))
        still_used = False
        if audio_to_check and not os.path.isabs(audio_to_check):
            still_used = (
                conn.execute(
                    "SELECT 1 FROM books WHERE BkAudioFilename = ? LIMIT 1",
                    (audio_to_check,),
                ).fetchone()
                is not None
            )
    finally:
        conn.close()

    if audio_to_check and not still_used and not os.path.isabs(audio_to_check):
        path = os.path.join(
            paths.user_config(base_config, target).useraudiopath, audio_to_check
        )
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass

    store.delete_assignment(target, dst_bkid)
