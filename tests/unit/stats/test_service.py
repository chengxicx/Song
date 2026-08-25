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
    get_jlpt_words,
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


def test_jlpt_data_endpoint(japanese, app_context, client):
    "The /stats/jlpt_data endpoint returns level data for a Japanese language."
    _save_jp_term(japanese, "食べる", 99)

    resp = client.get(f"/stats/jlpt_data?lang_id={japanese.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] > 0
    assert len(data["levels"]) == 5
    n5 = next(l for l in data["levels"] if l["level"] == "N5")
    assert n5["mastered"] == 1


def test_jlpt_data_endpoint_requires_lang_id(app_context, client):
    "Missing or invalid lang_id returns 400."
    resp = client.get("/stats/jlpt_data")
    assert resp.status_code == 400
    resp = client.get("/stats/jlpt_data?lang_id=abc")
    assert resp.status_code == 400


def test_get_jlpt_words_filters(japanese, app_context):
    "Drilldown word lists respect the unmastered/mastered/notseen filters."
    _save_jp_term(japanese, "食べる", 99)   # mastered
    _save_jp_term(japanese, "水", 3)        # unmastered
    _save_jp_term(japanese, "任意存在しない単語", 99)  # not in list

    unmastered = get_jlpt_words(db.session, japanese.id, "N5", "unmastered")
    mastered = get_jlpt_words(db.session, japanese.id, "N5", "mastered")
    notseen = get_jlpt_words(db.session, japanese.id, "N5", "notseen")
    all_words = get_jlpt_words(db.session, japanese.id, "N5", "all")

    assert [w["word"] for w in unmastered] == ["水"]
    assert unmastered[0]["status_text"] == "Learning (3)"

    assert [w["word"] for w in mastered] == ["食べる"]
    assert mastered[0]["status_text"] == "Well Known"

    notseen_words = [w["word"] for w in notseen]
    assert "あさって" in notseen_words
    assert "食べる" not in notseen_words
    assert all(w["id"] is None for w in notseen)

    all_map = {w["word"]: w for w in all_words}
    assert set(all_map.keys()) == {"食べる", "水"} | set(notseen_words)


def test_jlpt_words_endpoint(japanese, app_context, client):
    "The /stats/jlpt_words endpoint returns paged words."
    _save_jp_term(japanese, "水", 3)
    resp = client.get(f"/stats/jlpt_words?lang_id={japanese.id}&level=N5&filter=unmastered&page=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] >= 1
    assert any(w["word"] == "水" for w in data["words"])

    resp = client.get(f"/stats/jlpt_words?lang_id={japanese.id}&level=N5&filter=bogus")
    assert resp.status_code == 400
    resp = client.get(f"/stats/jlpt_words?lang_id={japanese.id}&level=all")
    assert resp.status_code == 400


def test_jlpt_export_endpoint(japanese, app_context, client):
    "The /stats/jlpt_export endpoint returns CSV content."
    _save_jp_term(japanese, "食べる", 99)
    resp = client.get(f"/stats/jlpt_export?lang_id={japanese.id}&level=N5&filter=mastered")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    body = resp.get_data(as_text=True)
    lines = body.strip().splitlines()
    assert lines[0] == "Level,Word,Reading,Meaning,Status"
    assert any("食べる" in ln for ln in lines[1:])

    resp = client.get(f"/stats/jlpt_export?lang_id={japanese.id}&level=all&filter=all")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "N5," in body
    assert "N1," in body
