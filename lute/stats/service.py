"""
Calculating stats.
"""

from datetime import datetime, timedelta
from sqlalchemy import text


def _get_data_per_lang(session):
    "Return dict of lang name to dict[date_yyyymmdd}: count"
    ret = {}
    sql = """
    select lang, dt, sum(WrWordCount) as count
    from (
      select LgName as lang, strftime('%Y-%m-%d', WrReadDate, 'localtime') as dt, WrWordCount
      from wordsread
      inner join languages on LgID = WrLgID and LgIsActive = 1
    ) raw
    group by lang, dt
    """
    result = session.execute(text(sql)).all()
    for row in result:
        langname = row[0]
        if langname not in ret:
            ret[langname] = {}
        ret[langname][row[1]] = int(row[2])
    return ret


def _charting_data(readbydate):
    "Calc data and running total."
    dates = sorted(readbydate.keys())
    if len(dates) == 0:
        return []

    # The line graph needs somewhere to start from for a line
    # to be drawn on the first day.
    first_date = datetime.strptime(dates[0], "%Y-%m-%d")
    day_before_first = first_date - timedelta(days=1)
    dbf = day_before_first.strftime("%Y-%m-%d")
    data = [{"readdate": dbf, "wordcount": 0, "runningTotal": 0}]

    total = 0
    for d in dates:
        dcount = readbydate.get(d)
        total += dcount
        hsh = {"readdate": d, "wordcount": dcount, "runningTotal": total}
        data.append(hsh)
    return data


def get_chart_data(session):
    "Get data for chart for each language."
    raw_data = _get_data_per_lang(session)
    chartdata = {}
    for k, v in raw_data.items():
        chartdata[k] = _charting_data(v)
    return chartdata


def _readcount_by_date(readbydate):
    """
    Return data as array: [ today, week, month, year, all time ]

    This may be inefficient, but will do for now.
    """
    today = datetime.now().date()

    def _in_range(i):
        start_date = today - timedelta(days=i)
        dates = [
            start_date + timedelta(days=x) for x in range((today - start_date).days + 1)
        ]
        ret = 0
        for d in dates:
            df = d.strftime("%Y-%m-%d")
            ret += readbydate.get(df, 0)
        return ret

    return {
        "day": _in_range(0),
        "week": _in_range(6),
        "month": _in_range(29),
        "year": _in_range(364),
        "total": _in_range(3650),  # 10 year drop off :-P
    }


def get_table_data(session):
    "Wordcounts by lang in time intervals."
    raw_data = _get_data_per_lang(session)

    ret = []
    for langname, readbydate in raw_data.items():
        ret.append({"name": langname, "counts": _readcount_by_date(readbydate)})
    return ret


def get_reading_streak(session):
    "Calculates the current reading streak in days."
    sql = """
    SELECT DISTINCT strftime('%Y-%m-%d', WrReadDate, 'localtime') as dt
    FROM wordsread
    WHERE WrLgID IN (SELECT LgID FROM languages WHERE LgIsActive = 1)
    ORDER BY dt DESC
    """
    result = session.execute(text(sql)).all()
    if not result:
        return 0

    dates = {datetime.strptime(row[0], "%Y-%m-%d").date() for row in result}

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    if today not in dates and yesterday not in dates:
        return 0

    streak = 0
    current_date = today if today in dates else yesterday
    while current_date in dates:
        streak += 1
        current_date -= timedelta(days=1)

    return streak


# ---------------------------------------------------------------------------
# Term statistics (for overview charts)
# ---------------------------------------------------------------------------

STATUS_LABELS = {
    0: "Unknown",
    1: "Level 1",
    2: "Level 2",
    3: "Level 3",
    4: "Level 4",
    5: "Level 5",
    98: "Ignored",
    99: "Well-known",
}


def _month_start(d):
    "Return the first day of the month containing date string d."
    dt = datetime.strptime(d, "%Y-%m-%d").date()
    return dt.replace(day=1)


def _daily_counts(session, date_col, lang_id, status_filter=None):
    """
    Return list of {date, count} (daily granularity) for the given date
    column.  status_filter restricts to a specific WoStatus value.
    Only counts terms from active (non-frozen) languages.
    """
    params = {}
    where = []
    where.append(
        "WoLgID in (select LgID from languages where LgIsActive = 1)"
    )
    if lang_id is not None:
        where.append("WoLgID = :lid")
        params["lid"] = lang_id
    if status_filter is not None:
        where.append("WoStatus = :st")
        params["st"] = status_filter
    where_clause = "where " + " and ".join(where)

    sql = (
        f"select strftime('%Y-%m-%d', {date_col}, 'localtime') as dt, count(*) as cnt "
        f"from words {where_clause} "
        "group by dt order by dt"
    )
    rows = session.execute(text(sql), params).all()
    return [{"date": r[0], "count": int(r[1])} for r in rows if r[0]]


def _filter_days(daily_data, days):
    "Return only the last N days of daily data (including today)."
    today = datetime.now().date()
    cutoff = today - timedelta(days=days - 1)
    cutoff_str = cutoff.isoformat()
    today_str = today.isoformat()
    return [d for d in daily_data if cutoff_str <= d["date"] <= today_str]


def _filter_today(daily_data):
    "Return only today's data."
    today_str = datetime.now().date().isoformat()
    return [d for d in daily_data if d["date"] == today_str]


def _monthly_aggregate(daily_data, months=12):
    "Aggregate daily data into monthly buckets, keeping last N months."
    if not daily_data:
        return []
    buckets = {}
    for item in daily_data:
        key = _month_start(item["date"])
        buckets[key] = buckets.get(key, 0) + item["count"]
    sorted_months = sorted(buckets.items())
    if len(sorted_months) > months:
        sorted_months = sorted_months[-months:]
    return [{"date": k.isoformat(), "count": v} for k, v in sorted_months]


def get_new_terms(session, period, lang_id):
    "New terms trend grouped by creation date."
    daily = _daily_counts(session, "WoCreated", lang_id)
    if period == "today":
        return _filter_today(daily)
    if period == "7days":
        return _filter_days(daily, 7)
    if period == "monthly":
        return _monthly_aggregate(daily, 12)
    return daily


def get_mastered_terms(session, period, lang_id):
    "Fully-mastered (status 99) terms grouped by status-changed date."
    daily = _daily_counts(session, "WoStatusChanged", lang_id, status_filter=99)
    if period == "today":
        return _filter_today(daily)
    if period == "7days":
        return _filter_days(daily, 7)
    if period == "monthly":
        return _monthly_aggregate(daily, 12)
    return daily


def get_heatmap_data(session, lang_id):
    "Daily term-adjustment volume for the GitHub-style heatmap."
    return _daily_counts(session, "WoStatusChanged", lang_id)


def get_term_languages(session):
    "Active languages that have at least one term, for the selector."
    sql = (
        "select l.LgID, l.LgName, l.LgParserType, count(w.WoID) as cnt "
        "from languages l "
        "inner join words w on w.WoLgID = l.LgID "
        "where l.LgIsActive = 1 "
        "group by l.LgID, l.LgName, l.LgParserType "
        "order by l.LgName"
    )
    rows = session.execute(text(sql)).all()
    return [
        {
            "id": r[0],
            "name": r[1],
            "count": int(r[3]),
            "is_japanese": r[2] == "japanese",
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# JLPT progress report
# ---------------------------------------------------------------------------

# Excel/text table won't handle the load; match by normalized term text.
# "Seen" = WoStatus in {1,2,3,4,5,99}; "Mastered" = WoStatus == 99.
_SEEN_STATUSES = (1, 2, 3, 4, 5, 99)


def _normalize_jp(text):
    "Normalize a Japanese term for matching against the JLPT word list."
    return (text or "").strip().lower()


def get_jlpt_data(session, lang_id):
    """
    JLPT progress for the given Japanese language.

    Returns counts per JLPT level against the OpenJLPT vocab list:
      - levels: [{level, total, seen, mastered}, ...] ordered N5..N1
      - total: total N5-N1 vocab size
      - total_seen / total_mastered: aggregate counts
    """
    from lute.stats.jlpt_data import LEVELS, jlpt_level, level_totals, vocab_total

    # For active words in the JLPT list, attribute by level.
    sql = """
        select WoText, WoStatus
        from words
        where WoLgID = :lid
          and WoStatus in (1,2,3,4,5,98,99)
    """
    rows = session.execute(text(sql), {"lid": lang_id}).all()

    seen_counts = {level: 0 for level in LEVELS}
    mastered_counts = {level: 0 for level in LEVELS}
    total_seen = 0
    total_mastered = 0

    for word_text, status in rows:
        word = _normalize_jp(word_text)
        if not word:
            continue
        level = jlpt_level(word)
        if level is None:
            continue
        if status == 99:
            mastered_counts[level] += 1
            total_mastered += 1
        if status in _SEEN_STATUSES:
            seen_counts[level] += 1
            total_seen += 1

    totals = level_totals()
    levels = []
    for level in LEVELS:
        levels.append(
            {
                "level": level,
                "total": totals[level],
                "seen": seen_counts[level],
                "mastered": mastered_counts[level],
            }
        )
    return {
        "levels": levels,
        "total": vocab_total(),
        "total_seen": total_seen,
        "total_mastered": total_mastered,
    }


def get_last_read_language_id(session):
    "Language ID of the most recently read book, or None."
    sql = (
        "select b.BkLgID "
        "from books b "
        "inner join languages l on l.LgID = b.BkLgID and l.LgIsActive = 1 "
        "where b.BkArchived = 0 "
        "order by b.BkCurrentTxID desc "
        "limit 1"
    )
    row = session.execute(text(sql)).first()
    if row is None:
        # Fallback: language of most recently added term
        sql2 = (
            "select w.WoLgID from words w "
            "inner join languages l on l.LgID = w.WoLgID and l.LgIsActive = 1 "
            "order by w.WoID desc limit 1"
        )
        row = session.execute(text(sql2)).first()
    return int(row[0]) if row else None


def get_term_summary(session, lang_id, period="7days"):
    """
    Summary data for the summary panel:
      - total_terms: total number of terms
      - recent_by_status: {status_code: count} of terms created in current period
      - cumulative_by_status: {status_code: count} of all terms by status
    Only counts terms from active (non-frozen) languages.
    """
    params = {}
    where_parts = [
        "WoLgID in (select LgID from languages where LgIsActive = 1)"
    ]
    if lang_id is not None:
        where_parts.append("WoLgID = :lid")
        params["lid"] = lang_id

    where_clause = "where " + " and ".join(where_parts)

    total_sql = f"select count(*) from words {where_clause}"
    total = session.execute(text(total_sql), params).scalar() or 0

    # Use Python date comparison instead of SQLite date('now', 'localtime')
    # to avoid timezone mismatches between SQLite and the application.
    today = datetime.now().date()
    if period == "today":
        recent_start = today.isoformat()
        recent_end = today.isoformat()
    elif period == "7days":
        cutoff = today - timedelta(days=6)
        recent_start = cutoff.isoformat()
        recent_end = today.isoformat()
    else:
        # monthly: last 12 months of new terms
        cutoff = today - timedelta(days=365)
        recent_start = cutoff.isoformat()
        recent_end = today.isoformat()

    recent_params = dict(params)
    recent_params["start_date"] = recent_start
    recent_params["end_date"] = recent_end

    recent_where = where_clause + " and date(WoCreated, 'localtime') >= :start_date and date(WoCreated, 'localtime') <= :end_date"
    recent_sql = (
        f"select WoStatus, count(*) from words {recent_where} "
        "group by WoStatus"
    )
    recent_rows = session.execute(text(recent_sql), recent_params).all()
    recent_by_status = {int(r[0]): int(r[1]) for r in recent_rows}

    cum_sql = f"select WoStatus, count(*) from words {where_clause} group by WoStatus"
    cum_rows = session.execute(text(cum_sql), params).all()
    cumulative_by_status = {int(r[0]): int(r[1]) for r in cum_rows}

    return {
        "total_terms": int(total),
        "recent_by_status": recent_by_status,
        "recent_label": "Today" if period == "today" else ("Last 7 days" if period == "7days" else "Last 12 months"),
        "cumulative_by_status": cumulative_by_status,
    }
