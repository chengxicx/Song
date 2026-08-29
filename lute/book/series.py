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
    COALESCE(textcounts.pagecount, 1) AS PageCount,
    currtext.TxOrder AS PageNum,
    textcounts.wc AS WordCount,
    booklastopened.lastopeneddate AS LastOpenedDate,
    c.new_word_percent AS NewWordPercent,
    c.status_distribution AS StatusDistribution,
    CASE WHEN completed_books.BkID IS NULL THEN 0 ELSE 1 END AS IsCompleted
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
LEFT OUTER JOIN bookstats c on c.BkID = b.BkID
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

_STATUS_LABELS = {
    "0": "Unknown",
    "1": "Level 1",
    "2": "Level 2",
    "3": "Level 3",
    "4": "Level 4",
    "5": "Level 5",
    "99": "Well Known or Ignored",
}

_STATUS_ORDER = ["0", "1", "2", "3", "4", "5", "99"]


def _status_bar_html(status_distribution):
    """
    Server-rendered status distribution bar, mirroring the
    render_stats_graph() output used in the book table.
    """
    if not status_distribution:
        return '<div class="status-bar-container"><div class="status-bar-empty">—</div></div>'
    try:
        counts = dict(json.loads(status_distribution))
    except (ValueError, TypeError):
        return '<div class="status-bar-container"><div class="status-bar-empty">—</div></div>'

    counts["99"] = counts.get("98", 0) + counts.get("99", 0)
    counts.pop("98", None)
    total = sum(counts.values())
    if total == 0:
        return '<div class="status-bar-container"><div class="status-bar-empty">—</div></div>'

    parts = []
    for key in _STATUS_ORDER:
        if key not in counts:
            continue
        pct = counts[key] * 100.0 / total
        display = "inline-flex" if pct >= 1 else "none"
        label = _STATUS_LABELS[key]
        title = f"{label}: {pct:.0f}% ({counts[key]} words)"
        parts.append(
            f'<div class="status-bar{key} status-bar" title="{title}" '
            f'style="flex: {pct}; display: {display}"></div>'
        )
    return '<div class="status-bar-container">' + "".join(parts) + "</div>"


def get_series_overview(session, tagtext):
    """
    View model for the series page, or None if no books carry the tag.
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
        label, color_class, description = get_difficulty_label(r.NewWordPercent)
        progress = (
            "✓"
            if is_completed
            else (f"({pagenum}/{pagecount})" if pagenum > 1 else "")
        )

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
                "id": r.BkID,
                "title": r.BkTitle,
                "archived": bool(r.BkArchived),
                "word_count": wordcount,
                "progress": progress,
                "is_completed": is_completed,
                "last_read": r.LastOpenedDate,
                "new_word_percent": r.NewWordPercent,
                "difficulty_label": label,
                "difficulty_color": color_class,
                "difficulty_description": description,
                "status_bar_html": _status_bar_html(r.StatusDistribution),
            }
        )

    # The continue target falls back to the first book when every
    # episode has been read.
    if continue_book is None:
        continue_book = {"id": books[0]["id"], "title": books[0]["title"]}

    avg_new_word = (
        round(sum(new_word_pcts) / len(new_word_pcts), 1) if new_word_pcts else None
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
