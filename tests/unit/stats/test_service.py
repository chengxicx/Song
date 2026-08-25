"Stats service test."

from datetime import datetime, timedelta
from lute.models.book import WordsRead
from lute.models.term import Term
from lute.db import db
from lute.stats.service import (
    get_chart_data,
    get_table_data,
    get_reading_streak,
    get_jlpt_data,
)
from tests.utils import make_text


def make_read_text(lang, content, readdate):
    "Make and save a text."
    t = make_text(content, content, lang)
    # t.read_date = readdate
    db.session.add(t)
    db.session.commit()

    if readdate is None:
        return

    wr = WordsRead(t, readdate, t.word_count)
    db.session.add(wr)
    db.session.commit()


def test_get_chart_data(spanish, english, app_context):
    "Smoke test."
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    daybefore = today - timedelta(days=2)

    make_read_text(spanish, "Yo tengo un gato.", today)
    make_read_text(spanish, "Ella esta aqui.", yesterday)
    make_read_text(spanish, "Nuevo text no leido.", None)
    make_read_text(english, "Yo yo.", today)

    expected = {
        "Spanish": [
            {
                "readdate": daybefore.strftime("%Y-%m-%d"),
                "wordcount": 0,
                "runningTotal": 0,
            },
            {
                "readdate": yesterday.strftime("%Y-%m-%d"),
                "wordcount": 3,
                "runningTotal": 3,
            },
            {"readdate": today.strftime("%Y-%m-%d"), "wordcount": 4, "runningTotal": 7},
        ],
        "English": [
            {
                "readdate": yesterday.strftime("%Y-%m-%d"),
                "wordcount": 0,
                "runningTotal": 0,
            },
            {"readdate": today.strftime("%Y-%m-%d"), "wordcount": 2, "runningTotal": 2},
        ],
    }
    assert get_chart_data(db.session) == expected


def test_get_table_data(spanish, english, app_context):
    "Smoke test."
    today = datetime.now()
    yesterday = today - timedelta(days=1)

    make_read_text(spanish, "Yo tengo un gato.", today)
    make_read_text(spanish, "Ella esta aqui.", yesterday)
    make_read_text(spanish, "Nuevo text no leido.", None)
    make_read_text(english, "Yo yo.", today)

    expected = [
        {
            "name": "English",
            "counts": {"day": 2, "week": 2, "month": 2, "year": 2, "total": 2},
        },
        {
            "name": "Spanish",
            "counts": {"day": 4, "week": 7, "month": 7, "year": 7, "total": 7},
        },
    ]
    actual = get_table_data(db.session)
    assert actual == expected


def test_get_data_works_when_nothing_read(app_context):
    "Nothing read should still be ok, empty chart."
    assert not get_chart_data(db.session), "nothing present"
    assert not get_table_data(db.session), "nothing"


def test_get_reading_streak(spanish, app_context):
    "Test reading streak calculation."
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    day_before_yesterday = today - timedelta(days=2)

    assert get_reading_streak(db.session) == 0

    make_read_text(spanish, "Ella esta aqui.", today)
    assert get_reading_streak(db.session) == 1

    make_read_text(spanish, "Nuevo text.", yesterday)
    assert get_reading_streak(db.session) == 2

    make_read_text(spanish, "Otro text.", day_before_yesterday)
    assert get_reading_streak(db.session) == 3


def _save_jp_term(lang, text, status):
    "Save a Japanese term with a given status."
    t = Term.create_term_no_parsing(lang, text)
    t.status = status
    db.session.add(t)
    db.session.commit()


def test_get_jlpt_data_counts_by_level(japanese, app_context):
    "Seen/mastered counts attributed to JLPT levels."
    # N5 words (from OpenJLPT)
    _save_jp_term(japanese, "食べる", 99)   # mastered
    _save_jp_term(japanese, "水", 3)        # seen (not mastered)
    _save_jp_term(japanese, "空", 98)       # ignored -> not seen
    # N1 word
    _save_jp_term(japanese, "赴く", 5)      # seen
    # not in word list
    _save_jp_term(japanese, "任意存在しない単語", 99)

    data = get_jlpt_data(db.session, japanese.id)

    lv = {l["level"]: l for l in data["levels"]}
    assert lv["N5"]["total"] == 662
    assert lv["N5"]["mastered"] == 1
    assert lv["N5"]["seen"] == 2  # 食べる + 水
    assert lv["N1"]["seen"] == 1
    assert data["total_mastered"] == 1
    assert data["total_seen"] == 3


def test_get_jlpt_data_empty_custom_word(japanese, app_context):
    "Words not in the JLPT list are ignored."
    _save_jp_term(japanese, "任意存在しない単語", 99)
    data = get_jlpt_data(db.session, japanese.id)
    assert data["total_mastered"] == 0
    assert data["total_seen"] == 0
