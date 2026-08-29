"""
Show books in datatables.
"""

from lute.utils.data_tables import DataTablesSqliteQuery, supported_parser_type_criteria
from lute.book.stats import difficulty_filter_sql, difficulty_sql_case
from lute.models.repositories import UserSettingRepository
from lute.models.repositories import MissingUserSettingKeyException


def _configured_series_tags(session):
    """
    Book tags configured as series (UserSetting 'book_series_tags',
    comma-separated tag texts).  Returns the raw tag texts, unescaped.
    """
    try:
        raw = UserSettingRepository(session).get_value("book_series_tags") or ""
    except MissingUserSettingKeyException:
        raw = ""
    return [t.strip() for t in raw.split(",") if t.strip()]


# The LEFT OUTER JOIN subqueries shared by the flat listing and the
# series aggregation, kept in one place so they can't drift apart.
_TEXTCOUNTS_SQL = """
    LEFT OUTER JOIN (
        SELECT TxBkID, SUM(TxWordCount) as wc, COUNT(TxID) AS pagecount
        FROM texts
        GROUP BY TxBkID
    ) textcounts on textcounts.TxBkID = b.BkID
"""

_LASTOPENED_SQL = """
    LEFT OUTER JOIN (
        select TxBkID, max(TxStartDate) as lastopeneddate from texts group by TxBkID
    ) booklastopened on booklastopened.TxBkID = b.BkID
"""

_COMPLETED_SQL = """
    left outer join (
      select texts.TxBkID as BkID
      from texts
      inner join (
        /* last page in each book */
        select TxBkID, max(TxOrder) as maxTxOrder from texts group by TxBkID
      ) last_page on last_page.TxBkID = texts.TxBkID and last_page.maxTxOrder = texts.TxOrder
      where TxReadDate is not null
    ) completed_books on completed_books.BkID = b.BkID
"""

_PARSER_CRITERIA_SQL = f"""
      and (languages.LgParserType in ({ supported_parser_type_criteria() })
           or b.BkBookType = 'manga')
"""


def _flat_base_sql(archived, extra_where=""):
    """
    The standard (non-aggregated) book listing, without the language,
    tag, and new-word filters, which are appended by the caller.
    `extra_where` adds conditions into the main WHERE (used by the
    series union to exclude books carrying a series tag).
    """
    difficulty_col = difficulty_sql_case("c.new_word_percent")

    return f"""
    SELECT
        b.BkID As BkID,
        LgID,
        LgName,
        BkTitle,
        case when currtext.TxID is null then 1 else currtext.TxOrder end as PageNum,
        textcounts.pagecount AS PageCount,
        booklastopened.lastopeneddate AS LastOpenedDate,
        BkArchived,
        tags.taglist AS TagList,
        COALESCE(
            CASE WHEN b.BkBookType = 'manga' THEN c.manga_word_count ELSE textcounts.wc END,
            0
        ) AS WordCount,
        c.distinctterms as DistinctCount,
        c.distinctunknowns as UnknownCount,
        c.unknownpercent as UnknownPercent,
        c.new_word_percent as NewWordPercent,
        c.status_distribution as StatusDistribution,
        {difficulty_col["label"]} AS DifficultyLabel,
        {difficulty_col["color"]} AS DifficultyColor,
        {difficulty_col["description"]} AS DifficultyDescription,
        case when completed_books.BkID is null then 0 else 1 end as IsCompleted,
        NULL as SeriesTag,
        NULL as SeriesBookCount,
        NULL as SeriesReadCount

    FROM books b
    INNER JOIN languages ON LgID = b.BkLgID
    LEFT OUTER JOIN texts currtext ON currtext.TxID = BkCurrentTxID
    {_LASTOPENED_SQL}
    {_TEXTCOUNTS_SQL}
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

    {_COMPLETED_SQL}
    WHERE b.BkArchived = {archived}
    {extra_where}
    {_PARSER_CRITERIA_SQL}
    """


def _series_union_base_sql(archived, series_tags):
    """
    One row per series tag, UNION ALL the books without a series tag.

    A book carrying several series tags is counted under the first one
    (MIN tag text), so it appears exactly once.
    """
    difficulty_col = difficulty_sql_case("agg.newwordpercent")
    taglist = ", ".join(f"'{t.replace(chr(39), chr(39) * 2)}'" for t in series_tags)

    not_series = f"""
      and b.BkID not in (
        select bt.BtBkID from booktags bt
        inner join tags2 t2 on t2.T2ID = bt.BtT2ID
        where t2.T2Text in ({taglist})
      )
    """

    series_branch = f"""
    SELECT
        NULL AS BkID,
        agg.lgid AS LgID,
        L.LgName AS LgName,
        agg.tagtext AS BkTitle,
        1 AS PageNum,
        agg.bookcount AS PageCount,
        agg.lastopened AS LastOpenedDate,
        0 AS BkArchived,
        agg.tagtext AS TagList,
        agg.wordcount AS WordCount,
        agg.distinctcount AS DistinctCount,
        agg.unknowncount AS UnknownCount,
        agg.unknownpercent AS UnknownPercent,
        agg.newwordpercent AS NewWordPercent,
        NULL AS StatusDistribution,
        {difficulty_col["label"]} AS DifficultyLabel,
        {difficulty_col["color"]} AS DifficultyColor,
        {difficulty_col["description"]} AS DifficultyDescription,
        CASE WHEN agg.readcount >= agg.bookcount THEN 1 ELSE 0 END AS IsCompleted,
        agg.tagtext AS SeriesTag,
        agg.bookcount AS SeriesBookCount,
        agg.readcount AS SeriesReadCount
    FROM (
        SELECT
            st.seriestag AS tagtext,
            b.BkLgID AS lgid,
            COUNT(DISTINCT b.BkID) AS bookcount,
            COALESCE(SUM(
                CASE WHEN b.BkBookType = 'manga'
                     THEN c.manga_word_count ELSE textcounts.wc END
            ), 0) AS wordcount,
            SUM(CASE WHEN completed_books.BkID IS NULL THEN 0 ELSE 1 END) AS readcount,
            MAX(booklastopened.lastopeneddate) AS lastopened,
            SUM(COALESCE(c.distinctterms, 0)) AS distinctcount,
            SUM(COALESCE(c.distinctunknowns, 0)) AS unknowncount,
            /* Rounded to integers to match the per-book display. */
            CAST(ROUND(AVG(c.unknownpercent), 0) AS INTEGER) AS unknownpercent,
            CAST(ROUND(AVG(c.new_word_percent), 0) AS INTEGER) AS newwordpercent
        FROM books b
        INNER JOIN (
            /* Each book is grouped under exactly one series tag. */
            SELECT bt.BtBkID AS BtBkID, MIN(t2.T2Text) AS seriestag
            FROM booktags bt
            INNER JOIN tags2 t2 ON t2.T2ID = bt.BtT2ID
            WHERE t2.T2Text in ({taglist})
            GROUP BY bt.BtBkID
        ) st ON st.BtBkID = b.BkID
        INNER JOIN languages ON LgID = b.BkLgID
        {_LASTOPENED_SQL}
        {_TEXTCOUNTS_SQL}
        LEFT OUTER JOIN bookstats c on c.BkID = b.BkID
        {_COMPLETED_SQL}
        WHERE b.BkArchived = {archived}
        {_PARSER_CRITERIA_SQL}
        GROUP BY st.seriestag, b.BkLgID
    ) agg
    INNER JOIN languages L ON L.LgID = agg.lgid
    """

    flat_branch = _flat_base_sql(archived, extra_where=not_series)

    return f"""
    SELECT * FROM (
        {series_branch}
        UNION ALL
        {flat_branch}
    ) seriesbase
    WHERE 1=1
    """


def get_data_tables_list(parameters, is_archived, session):
    "Book json data for datatables."

    # Default sort: Last read (LastOpenedDate) descending, so the most
    # recently opened books appear first on the home page.  This is
    # applied when the frontend request carries no explicit order
    # (first visit, or state was cleared).  We look up the column
    # index by name so reordering the columns array in the template
    # won't silently break the default.
    if not parameters.get("order"):
        for col in parameters.get("columns", []):
            if col.get("name") == "LastOpenedDate" and col.get("orderable"):
                parameters["order"] = [{"column": col["index"], "dir": "desc"}]
                break

    # Newly-created books have never been opened, so their LastOpenedDate
    # (max(TxStartDate)) is NULL.  Treat NULL as "most recently added":
    # when sorting by Last read descending, such books should rank at the
    # top (newest) instead of the bottom where SQLite normally puts NULLs.
    # We replace the sort field with an explicit null-aware expression;
    # descending sorts NULLs first, ascending sorts NULLs last.
    for order in parameters.get("order", []):
        col_index = int(order["column"])
        columns = parameters.get("columns", [])
        if col_index >= len(columns):
            continue
        col = columns[col_index]
        if col.get("name") != "LastOpenedDate":
            continue
        if order.get("dir") == "desc":
            col["name"] = "LastOpenedDate is null desc, LastOpenedDate"
        else:
            col["name"] = "LastOpenedDate is null, LastOpenedDate"
        break

    archived = "true" if is_archived else "false"

    # Series aggregation: books carrying a configured series tag are
    # collapsed into one row per tag.  Any active search or tag filter
    # switches back to the flat listing, so every book stays findable.
    series_tags = _configured_series_tags(session)
    search_value = (parameters.get("search") or {}).get("value") or ""
    tag_filter = (parameters.get("filtTag") or "").strip()
    use_series_aggregation = (
        len(series_tags) > 0 and not search_value.strip() and not tag_filter
    )

    # Add "where" criteria for all the filters.
    language_id = parameters["filtLanguage"]
    if language_id == "null" or language_id == "undefined" or language_id is None:
        language_id = "0"
    language_id = int(language_id)
    language_filter = ""
    if language_id != 0:
        language_filter = f" and LgID = {language_id}"

    new_word_filter = parameters.get("filtNewWord")
    new_word_sql = ""
    if new_word_filter and new_word_filter.strip():
        level = new_word_filter.strip().upper()
        sql_frag = difficulty_filter_sql("NewWordPercent", level)
        if sql_frag:
            new_word_sql = f" and {sql_frag}"

    if use_series_aggregation:
        base_sql = _series_union_base_sql(archived, series_tags)
        base_sql += language_filter + new_word_sql
    else:
        base_sql = _flat_base_sql(archived)
        # The flat query filters on the joined columns directly.
        if language_id != 0:
            base_sql += f" and LgID = {language_id}"
        if new_word_filter and new_word_filter.strip():
            level = new_word_filter.strip().upper()
            sql_frag = difficulty_filter_sql("c.new_word_percent", level)
            if sql_frag:
                base_sql += f" and {sql_frag}"
        if tag_filter:
            tag = tag_filter.replace("'", "''")
            base_sql += (
                f" and b.BkID in ("
                f"select BtBkID from booktags bt "
                f"inner join tags2 t2 on t2.T2ID = bt.BtT2ID "
                f"where t2.T2Text = '{tag}'"
                f")"
            )

    connection = session.connection()
    return DataTablesSqliteQuery.get_data(base_sql, parameters, connection)
