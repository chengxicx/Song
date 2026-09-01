"""
Data for the series overview page (/book/series/<tag>).

A "series" is simply all books carrying a given book tag, listed in
title order with a continue-reading shortcut.
"""

import json

from lute.book.stats import get_difficulty_label
from lute.db import db

_SERIES_BOOKS_SQL = """
SELECT
    b.BkID AS BkID,
    b.BkTitle AS BkTitle,
    b.BkArchived AS BkArchived,
    LgName,
    tags.taglist AS TagList,
    COALESCE(textcounts.pagecount, 1) AS PageCount,
    currtext.TxOrder AS PageNum,
    COALESCE(readpages.readpagecount, 0) AS ReadPageCount,
    textcounts.wc AS WordCount,
    booklastopened.lastopeneddate AS LastOpenedDate,
    c.new_word_percent AS NewWordPercent,
    c.unknownpercent AS UnknownPercent,
    c.status_distribution AS StatusDistribution,
    CASE WHEN completed_books.BkID IS NULL THEN 0 ELSE 1 END AS IsCompleted,
    b.BkBookType AS BookType
FROM books b
INNER JOIN languages ON LgID = b.BkLgID
LEFT OUTER JOIN texts currtext ON currtext.TxID = BkCurrentTxID
LEFT OUTER JOIN (
    select TxBkID, max(TxStartDate) as lastopeneddate from texts group by TxBkID
) booklastopened on booklastopened.TxBkID = b.BkID
LEFT OUTER JOIN (
    SELECT TxBkID, SUM(TxWordCount) as wc, COUNT(TxID) AS pagecount
    FROM texts
    GROUP BY TxBkID
) textcounts on textcounts.TxBkID = b.BkID
LEFT OUTER JOIN (
    select TxBkID as BkID,
           sum(case when TxReadDate is null then 0 else 1 end) as readpagecount
    from texts
    group by TxBkID
) readpages on readpages.BkID = b.BkID
LEFT OUTER JOIN bookstats c on c.BkID = b.BkID
LEFT OUTER JOIN (
    SELECT BtBkID as BkID, GROUP_CONCAT(T2Text, ', ') AS taglist
    FROM
    (
        SELECT BtBkID, T2Text
        FROM booktags bt
        INNER JOIN tags2 t2 ON t2.T2ID = bt.BtT2ID
        ORDER BY T2Text
    ) tagssrc
    GROUP BY BtBkID
) AS tags ON tags.BkID = b.BkID
LEFT OUTER JOIN (
    select texts.TxBkID as BkID
    from texts
    inner join (
        select TxBkID, max(TxOrder) as maxTxOrder from texts group by TxBkID
    ) last_page on last_page.TxBkID = texts.TxBkID and last_page.maxTxOrder = texts.TxOrder
    where TxReadDate is not null
) completed_books on completed_books.BkID = b.BkID
WHERE b.BkID in (
    select bt.BtBkID from booktags bt
    inner join tags2 t2 on t2.T2ID = bt.BtT2ID
    where t2.T2Text = :tagtext
)
ORDER BY b.BkArchived, b.BkTitle COLLATE NOCASE
"""


def progress_percent(page_num, page_count, read_page_count, is_completed):
    """
    Percentage of a book that has been read, 0-100.

    Mirrors the SQL expression the home book table uses
    (lute/book/datatables.py): a book is 100% as soon as its last page
    carries a read date, otherwise it's the greater of "pages marked
    read" and "pages before the current one" — navigating with the
    arrows marks pages read, but jumping straight to a page only moves
    the current page.
    """
    pages = max(1, page_count or 1)
    if is_completed:
        return 100
    done = max(read_page_count or 0, (page_num or 1) - 1)
    return min(100, max(0, int(done * 100 / pages)))


def get_series_overview(session, tagtext):
    """
    View model for the series page, or None if no books carry the tag.

    Each entry in `books` doubles as a row for the client-side
    DataTable on the overview page (BkID/BkTitle/... keys, mirroring
    the home book table's row shape), plus a few display-only fields.
    """
    rows = session.execute(
        db.text(_SERIES_BOOKS_SQL), {"tagtext": tagtext}
    ).fetchall()
    if len(rows) == 0:
        return None

    books = []
    total_words = 0
    new_word_pcts = []
    read_count = 0
    active_count = 0
    continue_book = None

    for r in rows:
        wordcount = r.WordCount or 0
        pagenum = r.PageNum or 1
        pagecount = r.PageCount or 1
        is_completed = bool(r.IsCompleted)
        progress = progress_percent(pagenum, pagecount, r.ReadPageCount, is_completed)
        label, color_class, description = get_difficulty_label(r.NewWordPercent)

        if not r.BkArchived:
            active_count += 1
        if is_completed:
            read_count += 1
        else:
            # Prefer non-archived books when picking the continue target.
            if continue_book is None or (continue_book["archived"] and not r.BkArchived):
                continue_book = {
                    "id": r.BkID,
                    "title": r.BkTitle,
                    "archived": bool(r.BkArchived),
                }

        total_words += wordcount
        if r.NewWordPercent is not None:
            new_word_pcts.append(r.NewWordPercent)

        books.append(
            {
                # Keys used by the client-side DataTable (same shape as
                # the home book listing rows).
                "BkID": r.BkID,
                "BkTitle": r.BkTitle,
                "LgName": r.LgName,
                "TagList": r.TagList or "",
                "WordCount": wordcount,
                "UnknownPercent": r.UnknownPercent,
                "NewWordPercent": r.NewWordPercent,
                "LastOpenedDate": r.LastOpenedDate,
                "StatusDistribution": r.StatusDistribution,
                "DifficultyLabel": label,
                "DifficultyColor": color_class,
                "DifficultyDescription": description,
                "IsCompleted": 1 if is_completed else 0,
                "ProgressPercent": progress,
                "BookType": r.BookType or "",
                "BkArchived": 1 if r.BkArchived else 0,
                "PageNum": pagenum,
                "PageCount": pagecount,
                # Display-only fields for the template fallback rows.
                "archived": bool(r.BkArchived),
                "is_completed": is_completed,
            }
        )

    # The continue target falls back to the first book when every
    # episode has been read.
    if continue_book is None:
        continue_book = {"id": books[0]["BkID"], "title": books[0]["BkTitle"]}

    avg_new_word = (
        int(round(sum(new_word_pcts) / len(new_word_pcts))) if new_word_pcts else None
    )
    label, color_class, description = get_difficulty_label(avg_new_word)

    return {
        "tag": tagtext,
        "book_count": active_count,
        "total_count": len(books),
        "read_count": read_count,
        "total_words": total_words,
        "avg_new_word_percent": avg_new_word,
        "difficulty_label": label,
        "difficulty_color": color_class,
        "difficulty_description": description,
        "continue_id": continue_book["id"],
        "continue_title": continue_book["title"],
        "books": books,
    }
