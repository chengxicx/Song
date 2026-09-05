"Stats service test."

from datetime import datetime, timedelta
from lute.models.book import WordsRead
from lute.models.term import Term
from lute.db import db
from lute.stats.service import (
    get_chart_data,
    get_table_data,
    get_term_languages,
    get_reading_streak,
    get_jlpt_data,
    get_jlpt_words,
    get_cefr_data,
    get_cefr_words,
    get_topik_data,
    get_topik_words,
    get_dele_data,
    get_dele_words,
    get_russian_data,
    get_russian_words,
    get_german_data,
    get_german_words,
    get_thai_data,
    get_thai_words,
    get_french_data,
    get_french_words,
    get_arabic_data,
    get_arabic_words,
    get_hsk2_data,
    get_hsk2_words,
    get_hsk3_data,
    get_hsk3_words,
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
    _save_jp_term(japanese, "食べる", 99)  # mastered
    _save_jp_term(japanese, "水", 3)  # seen (not mastered)
    _save_jp_term(japanese, "空", 98)  # ignored -> not seen
    # N1 word
    _save_jp_term(japanese, "赴く", 5)  # seen
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
    _save_jp_term(japanese, "食べる", 99)  # mastered
    _save_jp_term(japanese, "水", 3)  # unmastered
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
    resp = client.get(
        f"/stats/jlpt_words?lang_id={japanese.id}&level=N5&filter=unmastered&page=1"
    )
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
    resp = client.get(
        f"/stats/jlpt_export?lang_id={japanese.id}&level=N5&filter=mastered"
    )
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


def test_get_cefr_data_counts_by_level(english, app_context):
    "Seen/mastered counts attributed to CEFR levels, incl. inflection expansion."
    _save_jp_term(english, "run", 99)  # mastered (A1)
    _save_jp_term(english, "running", 3)  # expands to run -> seen (A1)
    _save_jp_term(english, "dogs", 98)  # ignored -> not seen
    _save_jp_term(english, "extraordinary", 5)  # seen (B1)
    _save_jp_term(english, "zzzqqqxyz", 99)  # not in word list

    data = get_cefr_data(db.session, english.id)

    lv = {l["level"]: l for l in data["levels"]}
    assert len(lv) == 6
    assert lv["A1"]["mastered"] == 1
    assert lv["A1"]["seen"] == 1  # run ("dogs" ignored)
    assert lv["A2"]["seen"] == 1  # running is its own A2 headword
    assert lv["B1"]["seen"] == 1  # extraordinary
    assert data["total_mastered"] == 1
    assert data["total_seen"] == 3


def test_cefr_short_inflected_words(english, app_context):
    "Short words like red/ring must not crash expansion and match directly."
    _save_jp_term(english, "red", 99)
    _save_jp_term(english, "ring", 3)

    data = get_cefr_data(db.session, english.id)
    lv = {l["level"]: l for l in data["levels"]}
    assert lv["A1"]["mastered"] == 1
    assert lv["A1"]["seen"] == 2


def test_cefr_data_endpoint(english, app_context, client):
    "The /stats/cefr_data endpoint returns level data for an English language."
    _save_jp_term(english, "run", 99)

    resp = client.get(f"/stats/cefr_data?lang_id={english.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] > 0
    assert len(data["levels"]) == 6
    a1 = next(l for l in data["levels"] if l["level"] == "A1")
    assert a1["mastered"] == 1

    resp = client.get("/stats/cefr_data")
    assert resp.status_code == 400
    resp = client.get("/stats/cefr_data?lang_id=abc")
    assert resp.status_code == 400


def test_get_cefr_words_filters(english, app_context):
    "Drilldown respects filters; expanded headwords excluded from notseen."
    _save_jp_term(english, "run", 99)
    _save_jp_term(english, "running", 3)
    _save_jp_term(english, "zzzqqqxyz", 99)

    unmastered = get_cefr_words(db.session, english.id, "A1", "unmastered")
    a2_unmastered = get_cefr_words(db.session, english.id, "A2", "unmastered")
    mastered = get_cefr_words(db.session, english.id, "A1", "mastered")
    notseen = get_cefr_words(db.session, english.id, "A1", "notseen")
    all_words = get_cefr_words(db.session, english.id, "A1", "all")

    assert [w["word"] for w in unmastered] == []
    assert [w["word"] for w in a2_unmastered] == ["running"]
    assert [w["word"] for w in mastered] == ["run"]
    assert mastered[0]["status_text"] == "Well Known"

    notseen_words = [w["word"] for w in notseen]
    assert "water" in notseen_words
    assert "dog" in notseen_words  # dogs was ignored -> dog still unseen
    assert "run" not in notseen_words
    assert all(w["id"] is None for w in notseen)

    all_map = {w["word"]: w for w in all_words}
    assert set(all_map.keys()) == {"run"} | set(notseen_words)


def test_cefr_words_and_export_endpoints(english, app_context, client):
    "The cefr_words and cefr_export endpoints behave like the JLPT ones."
    _save_jp_term(english, "run", 99)
    _save_jp_term(english, "running", 3)

    resp = client.get(
        f"/stats/cefr_words?lang_id={english.id}&level=A2&filter=unmastered&page=1"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] >= 1

    resp = client.get(f"/stats/cefr_words?lang_id={english.id}&level=A1&filter=bogus")
    assert resp.status_code == 400
    resp = client.get(f"/stats/cefr_words?lang_id={english.id}&level=all")
    assert resp.status_code == 400

    resp = client.get(
        f"/stats/cefr_export?lang_id={english.id}&level=A1&filter=mastered"
    )
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    lines = resp.get_data(as_text=True).strip().splitlines()
    assert lines[0] == "Level,Word,Reading,Meaning,Status"
    assert any("run" in ln for ln in lines[1:])

    resp = client.get(f"/stats/cefr_export?lang_id={english.id}&level=all&filter=all")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "A1," in body
    assert "C2," in body


def test_get_topik_data_counts_by_level(korean, app_context):
    "Seen/mastered counts attributed to TOPIK A/B/C bands."
    _save_jp_term(korean, "가게", 99)  # mastered (A)
    _save_jp_term(korean, "한국", 3)  # seen (A)
    _save_jp_term(korean, "가난", 98)  # ignored -> not seen (band C)
    _save_jp_term(korean, "임의단어zzz", 99)  # not in word list

    data = get_topik_data(db.session, korean.id)

    lv = {l["level"]: l for l in data["levels"]}
    assert len(lv) == 3
    assert lv["A"]["mastered"] == 1
    assert lv["A"]["seen"] == 2
    assert lv["C"]["seen"] == 0
    assert data["total_mastered"] == 1
    assert data["total_seen"] == 2


def test_topik_data_endpoint(korean, app_context, client):
    "The /stats/topik_data endpoint returns band data for a Korean language."
    _save_jp_term(korean, "가게", 99)

    resp = client.get(f"/stats/topik_data?lang_id={korean.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] > 0
    assert len(data["levels"]) == 3
    a = next(l for l in data["levels"] if l["level"] == "A")
    assert a["mastered"] == 1

    resp = client.get("/stats/topik_data")
    assert resp.status_code == 400
    resp = client.get("/stats/topik_data?lang_id=abc")
    assert resp.status_code == 400


def test_get_topik_words_filters(korean, app_context):
    "Drilldown respects the unmastered/mastered/notseen filters."
    _save_jp_term(korean, "가게", 99)
    _save_jp_term(korean, "한국", 3)
    _save_jp_term(korean, "임의단어zzz", 99)

    unmastered = get_topik_words(db.session, korean.id, "A", "unmastered")
    mastered = get_topik_words(db.session, korean.id, "A", "mastered")
    notseen = get_topik_words(db.session, korean.id, "A", "notseen")

    assert [w["word"] for w in unmastered] == ["한국"]
    assert [w["word"] for w in mastered] == ["가게"]

    notseen_words = [w["word"] for w in notseen]
    assert all(w["id"] is None for w in notseen)
    assert "가게" not in notseen_words
    assert "한국" not in notseen_words


def test_topik_words_and_export_endpoints(korean, app_context, client):
    "The topik_words and topik_export endpoints behave like the JLPT ones."
    _save_jp_term(korean, "가게", 99)
    _save_jp_term(korean, "한국", 3)

    resp = client.get(
        f"/stats/topik_words?lang_id={korean.id}&level=A&filter=unmastered&page=1"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] >= 1

    resp = client.get(f"/stats/topik_words?lang_id={korean.id}&level=A&filter=bogus")
    assert resp.status_code == 400
    resp = client.get(f"/stats/topik_words?lang_id={korean.id}&level=all")
    assert resp.status_code == 400

    resp = client.get(
        f"/stats/topik_export?lang_id={korean.id}&level=A&filter=mastered"
    )
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    lines = resp.get_data(as_text=True).strip().splitlines()
    assert lines[0] == "Level,Word,Reading,Meaning,Status"
    assert any("가게" in ln for ln in lines[1:])

    resp = client.get(f"/stats/topik_export?lang_id={korean.id}&level=all&filter=all")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "A," in body
    assert "C," in body


def test_get_dele_data_counts_by_level(spanish, app_context):
    "Seen/mastered counts attributed to DELE levels, incl. inflection expansion."
    _save_jp_term(spanish, "hablar", 99)  # mastered (A1)
    _save_jp_term(spanish, "casas", 3)  # expands to casa -> seen (A1)
    _save_jp_term(spanish, "felices", 98)  # ignored -> not seen (B1)
    _save_jp_term(spanish, "zzzqqqxyz", 99)  # not in word list

    data = get_dele_data(db.session, spanish.id)

    lv = {l["level"]: l for l in data["levels"]}
    assert len(lv) == 6
    assert lv["A1"]["mastered"] == 1
    assert lv["A1"]["seen"] == 2  # hablar + casas
    assert lv["B1"]["seen"] == 0  # felices ignored
    assert data["total_mastered"] == 1
    assert data["total_seen"] == 2


def test_dele_data_endpoint(spanish, app_context, client):
    "The /stats/dele_data endpoint returns level data for a Spanish language."
    _save_jp_term(spanish, "hablar", 99)

    resp = client.get(f"/stats/dele_data?lang_id={spanish.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] > 0
    assert len(data["levels"]) == 6
    a1 = next(l for l in data["levels"] if l["level"] == "A1")
    assert a1["mastered"] == 1

    resp = client.get("/stats/dele_data")
    assert resp.status_code == 400
    resp = client.get("/stats/dele_data?lang_id=abc")
    assert resp.status_code == 400


def test_get_dele_words_filters(spanish, app_context):
    "Drilldown respects filters; expanded headwords excluded from notseen."
    _save_jp_term(spanish, "hablar", 99)
    _save_jp_term(spanish, "casas", 3)

    unmastered = get_dele_words(db.session, spanish.id, "A1", "unmastered")
    mastered = get_dele_words(db.session, spanish.id, "A1", "mastered")
    notseen = get_dele_words(db.session, spanish.id, "A1", "notseen")
    all_words = get_dele_words(db.session, spanish.id, "A1", "all")

    assert [w["word"] for w in unmastered] == ["casas"]
    assert [w["word"] for w in mastered] == ["hablar"]
    assert mastered[0]["status_text"] == "Well Known"

    notseen_words = [w["word"] for w in notseen]
    assert "abrir" in notseen_words  # in the DELE list, never learned
    assert "hablar" not in notseen_words
    assert all(w["id"] is None for w in notseen)

    all_map = {w["word"]: w for w in all_words}
    assert set(all_map.keys()) == {"casas", "hablar"} | set(notseen_words)


def test_dele_words_and_export_endpoints(spanish, app_context, client):
    "The dele_words and dele_export endpoints behave like the JLPT ones."
    _save_jp_term(spanish, "hablar", 99)
    _save_jp_term(spanish, "casas", 3)

    resp = client.get(
        f"/stats/dele_words?lang_id={spanish.id}&level=A1&filter=unmastered&page=1"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] >= 1

    resp = client.get(f"/stats/dele_words?lang_id={spanish.id}&level=A1&filter=bogus")
    assert resp.status_code == 400
    resp = client.get(f"/stats/dele_words?lang_id={spanish.id}&level=all")
    assert resp.status_code == 400

    resp = client.get(
        f"/stats/dele_export?lang_id={spanish.id}&level=A1&filter=mastered"
    )
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    lines = resp.get_data(as_text=True).strip().splitlines()
    assert lines[0] == "Level,Word,Reading,Meaning,Status"
    assert any("hablar" in ln for ln in lines[1:])

    resp = client.get(f"/stats/dele_export?lang_id={spanish.id}&level=all&filter=all")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "A1," in body
    assert "C2," in body


def test_get_russian_data_counts_by_level(russian, app_context):
    "Seen/mastered counts attributed to Russian CEFR levels."
    _save_jp_term(russian, "брать", 99)  # mastered (A1)
    _save_jp_term(russian, "верить", 3)  # seen, not mastered (A2)
    _save_jp_term(russian, "бояться", 98)  # ignored -> not seen (B1)
    _save_jp_term(russian, "ввести", 5)  # seen (C1)
    _save_jp_term(russian, "xyzпроизвольныйглаголzzz", 99)  # not in list

    data = get_russian_data(db.session, russian.id)

    lv = {l["level"]: l for l in data["levels"]}
    assert len(lv) == 6
    assert lv["A1"]["mastered"] == 1
    assert lv["A1"]["seen"] == 1
    assert lv["A2"]["seen"] == 1
    assert lv["B1"]["seen"] == 0  # бояться ignored
    assert lv["C1"]["seen"] == 1
    assert data["total_mastered"] == 1
    assert data["total_seen"] == 3


def test_get_russian_data_inflected_form(russian, app_context):
    "A conjugated stored form expands to its infinitive headword."
    _save_jp_term(russian, "думает", 99)  # думает -> думать (A1)

    data = get_russian_data(db.session, russian.id)
    lv = {l["level"]: l for l in data["levels"]}
    assert lv["A1"]["mastered"] == 1


def test_russian_data_endpoint(russian, app_context, client):
    "The /stats/russian_data endpoint returns level data for a Russian language."
    _save_jp_term(russian, "брать", 99)

    resp = client.get(f"/stats/russian_data?lang_id={russian.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] > 0
    assert len(data["levels"]) == 6
    a1 = next(l for l in data["levels"] if l["level"] == "A1")
    assert a1["mastered"] == 1

    resp = client.get("/stats/russian_data")
    assert resp.status_code == 400
    resp = client.get("/stats/russian_data?lang_id=abc")
    assert resp.status_code == 400


def test_get_russian_words_filters(russian, app_context):
    "Drilldown respects the unmastered/mastered/notseen filters."
    _save_jp_term(russian, "брать", 99)
    _save_jp_term(russian, "верить", 3)

    unmastered = get_russian_words(db.session, russian.id, "A1", "unmastered")
    mastered = get_russian_words(db.session, russian.id, "A1", "mastered")
    a2_unmastered = get_russian_words(db.session, russian.id, "A2", "unmastered")
    notseen = get_russian_words(db.session, russian.id, "A1", "notseen")

    assert [w["word"] for w in unmastered] == []
    assert [w["word"] for w in mastered] == ["брать"]
    assert mastered[0]["status_text"] == "Well Known"
    assert [w["word"] for w in a2_unmastered] == ["верить"]

    notseen_words = [w["word"] for w in notseen]
    assert "писать" in notseen_words  # in the A1 list, never learned
    assert "брать" not in notseen_words
    assert all(w["id"] is None for w in notseen)


def test_russian_words_and_export_endpoints(russian, app_context, client):
    "The russian_words and russian_export endpoints behave like the JLPT ones."
    _save_jp_term(russian, "брать", 99)
    _save_jp_term(russian, "верить", 3)

    resp = client.get(
        f"/stats/russian_words?lang_id={russian.id}&level=A2&filter=unmastered&page=1"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] >= 1

    resp = client.get(
        f"/stats/russian_words?lang_id={russian.id}&level=A1&filter=bogus"
    )
    assert resp.status_code == 400
    resp = client.get(f"/stats/russian_words?lang_id={russian.id}&level=all")
    assert resp.status_code == 400

    resp = client.get(
        f"/stats/russian_export?lang_id={russian.id}&level=A1&filter=mastered"
    )
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    lines = resp.get_data(as_text=True).strip().splitlines()
    assert lines[0] == "Level,Word,Reading,Meaning,Status"
    assert any("брать" in ln for ln in lines[1:])

    resp = client.get(
        f"/stats/russian_export?lang_id={russian.id}&level=all&filter=all"
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "A1," in body
    assert "C2," in body


def test_get_german_data_counts_by_level(german, app_context):
    "Seen/mastered counts attributed to German CEFR levels."
    _save_jp_term(german, "machen", 99)  # mastered (A1)
    _save_jp_term(german, "Behörde", 3)  # seen, not mastered (B1)
    _save_jp_term(german, "Melancholie", 98)  # ignored -> not seen (C2)
    _save_jp_term(german, "xyzfakegermanwordzzz", 99)  # not in list

    data = get_german_data(db.session, german.id)

    lv = {l["level"]: l for l in data["levels"]}
    assert len(lv) == 6
    assert lv["A1"]["mastered"] == 1
    assert lv["A1"]["seen"] == 1
    assert lv["B1"]["seen"] == 1
    assert lv["C1"]["seen"] == 0
    assert lv["C2"]["seen"] == 0  # Melancholie ignored
    assert data["total_mastered"] == 1
    assert data["total_seen"] == 2


def test_get_german_data_inflected_form(german, app_context):
    "A conjugated stored form expands to its base-word headword."
    _save_jp_term(german, "machst", 99)  # machst -> machen (A1)

    data = get_german_data(db.session, german.id)
    lv = {l["level"]: l for l in data["levels"]}
    assert lv["A1"]["mastered"] == 1


def test_german_data_endpoint(german, app_context, client):
    "The /stats/german_data endpoint returns level data for a German language."
    _save_jp_term(german, "machen", 99)

    resp = client.get(f"/stats/german_data?lang_id={german.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] > 0
    assert len(data["levels"]) == 6
    a1 = next(l for l in data["levels"] if l["level"] == "A1")
    assert a1["mastered"] == 1

    resp = client.get("/stats/german_data")
    assert resp.status_code == 400
    resp = client.get("/stats/german_data?lang_id=abc")
    assert resp.status_code == 400


def test_get_german_words_filters(german, app_context):
    "Drilldown respects the unmastered/mastered/notseen filters."
    _save_jp_term(german, "machen", 99)

    unmastered = get_german_words(db.session, german.id, "A1", "unmastered")
    mastered = get_german_words(db.session, german.id, "A1", "mastered")
    notseen = get_german_words(db.session, german.id, "A1", "notseen")

    assert [w["word"] for w in unmastered] == []
    assert [w["word"] for w in mastered] == ["machen"]
    assert mastered[0]["status_text"] == "Well Known"

    notseen_words = [w["word"] for w in notseen]
    assert "frei" in notseen_words  # in the A1 list, never learned
    assert "machen" not in notseen_words
    assert all(w["id"] is None for w in notseen)


def test_german_words_and_export_endpoints(german, app_context, client):
    "The german_words and german_export endpoints behave like the JLPT ones."
    _save_jp_term(german, "machen", 99)

    resp = client.get(
        f"/stats/german_words?lang_id={german.id}&level=A1&filter=mastered&page=1"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] >= 1

    resp = client.get(f"/stats/german_words?lang_id={german.id}&level=A1&filter=bogus")
    assert resp.status_code == 400
    resp = client.get(f"/stats/german_words?lang_id={german.id}&level=all")
    assert resp.status_code == 400

    resp = client.get(
        f"/stats/german_export?lang_id={german.id}&level=A1&filter=mastered"
    )
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    lines = resp.get_data(as_text=True).strip().splitlines()
    assert lines[0] == "Level,Word,Reading,Meaning,Status"
    assert any("machen" in ln for ln in lines[1:])

    resp = client.get(f"/stats/german_export?lang_id={german.id}&level=all&filter=all")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "A1," in body
    assert "C2," in body


def test_get_thai_data_counts_by_level(thai, app_context):
    "Seen/mastered counts attributed to Thai frequency buckets."
    _save_jp_term(thai, "และ", 99)  # mastered (1-500)
    _save_jp_term(thai, "ภาษา", 3)  # seen, not mastered (501-1000)
    _save_jp_term(thai, "ขอบคุณ", 98)  # ignored -> not seen (1001-2000)
    _save_jp_term(thai, "zzzเฟคคำzzz", 99)  # not in any bucket

    data = get_thai_data(db.session, thai.id)

    lv = {l["level"]: l for l in data["levels"]}
    assert len(lv) == 5
    assert lv["1-500"]["mastered"] == 1
    assert lv["1-500"]["seen"] == 1
    assert lv["501-1000"]["seen"] == 1
    assert lv["1001-2000"]["seen"] == 0  # ขอบคุณ ignored
    assert data["total_mastered"] == 1
    assert data["total_seen"] == 2


def test_thai_data_endpoint(thai, app_context, client):
    "The /stats/thai_data endpoint returns bucket data for a Thai language."
    _save_jp_term(thai, "และ", 99)

    resp = client.get(f"/stats/thai_data?lang_id={thai.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] > 0
    assert len(data["levels"]) == 5
    b = next(l for l in data["levels"] if l["level"] == "1-500")
    assert b["mastered"] == 1

    resp = client.get("/stats/thai_data")
    assert resp.status_code == 400
    resp = client.get("/stats/thai_data?lang_id=abc")
    assert resp.status_code == 400


def test_get_thai_words_filters(thai, app_context):
    "Drilldown respects the unmastered/mastered/notseen filters."
    _save_jp_term(thai, "และ", 99)
    _save_jp_term(thai, "ภาษา", 3)

    unmastered = get_thai_words(db.session, thai.id, "1-500", "unmastered")
    mastered = get_thai_words(db.session, thai.id, "1-500", "mastered")
    b_unmastered = get_thai_words(db.session, thai.id, "501-1000", "unmastered")
    notseen = get_thai_words(db.session, thai.id, "1-500", "notseen")

    assert [w["word"] for w in unmastered] == []
    assert [w["word"] for w in mastered] == ["และ"]
    assert mastered[0]["status_text"] == "Well Known"
    assert [w["word"] for w in b_unmastered] == ["ภาษา"]

    notseen_words = [w["word"] for w in notseen]
    assert "การ" in notseen_words  # in the 1-500 bucket, never learned
    assert "และ" not in notseen_words
    assert all(w["id"] is None for w in notseen)


def test_thai_words_and_export_endpoints(thai, app_context, client):
    "The thai_words and thai_export endpoints behave like the JLPT ones."
    _save_jp_term(thai, "และ", 99)
    _save_jp_term(thai, "ภาษา", 3)

    resp = client.get(
        f"/stats/thai_words?lang_id={thai.id}&level=501-1000&filter=unmastered&page=1"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 1

    resp = client.get(f"/stats/thai_words?lang_id={thai.id}&level=1-500&filter=bogus")
    assert resp.status_code == 400
    resp = client.get(f"/stats/thai_words?lang_id={thai.id}&level=all")
    assert resp.status_code == 400

    resp = client.get(
        f"/stats/thai_export?lang_id={thai.id}&level=1-500&filter=mastered"
    )
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    lines = resp.get_data(as_text=True).strip().splitlines()
    assert lines[0] == "Level,Word,Reading,Meaning,Status"
    assert any("และ" in ln for ln in lines[1:])

    resp = client.get(f"/stats/thai_export?lang_id={thai.id}&level=all&filter=all")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "1-500," in body
    assert "5001-10000," in body


def test_get_french_data_counts_by_level(french, app_context):
    "Seen/mastered counts attributed to French CEFR levels."
    _save_jp_term(french, "accent", 99)  # mastered (A1)
    _save_jp_term(french, "abricot", 3)  # seen, not mastered (A2)
    _save_jp_term(french, "abbé", 98)  # ignored -> not seen (B1)
    _save_jp_term(french, "abaisser", 5)  # seen (B2)
    _save_jp_term(french, "zzzazertyzzz", 99)  # not in list

    data = get_french_data(db.session, french.id)

    lv = {l["level"]: l for l in data["levels"]}
    assert len(lv) == 6
    assert lv["A1"]["mastered"] == 1
    assert lv["A1"]["seen"] == 1
    assert lv["A2"]["seen"] == 1
    assert lv["B1"]["seen"] == 0  # abbé ignored
    assert lv["B2"]["seen"] == 1
    assert data["total_mastered"] == 1
    assert data["total_seen"] == 3


def test_get_french_data_elided_form(french, app_context):
    "An elided stored form expands to its noun headword."
    _save_jp_term(french, "l'homme", 99)  # l'homme -> homme (A1)

    data = get_french_data(db.session, french.id)
    lv = {l["level"]: l for l in data["levels"]}
    assert lv["A1"]["mastered"] == 1


def test_french_data_endpoint(french, app_context, client):
    "The /stats/french_data endpoint returns level data for a French language."
    _save_jp_term(french, "accent", 99)

    resp = client.get(f"/stats/french_data?lang_id={french.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] > 0
    assert len(data["levels"]) == 6
    a1 = next(l for l in data["levels"] if l["level"] == "A1")
    assert a1["mastered"] == 1

    resp = client.get("/stats/french_data")
    assert resp.status_code == 400
    resp = client.get("/stats/french_data?lang_id=abc")
    assert resp.status_code == 400


def test_get_french_words_filters(french, app_context):
    "Drilldown respects the unmastered/mastered/notseen filters."
    _save_jp_term(french, "accent", 99)
    _save_jp_term(french, "abricot", 3)

    unmastered = get_french_words(db.session, french.id, "A1", "unmastered")
    mastered = get_french_words(db.session, french.id, "A1", "mastered")
    a2_unmastered = get_french_words(db.session, french.id, "A2", "unmastered")
    notseen = get_french_words(db.session, french.id, "A1", "notseen")

    assert [w["word"] for w in unmastered] == []
    assert [w["word"] for w in mastered] == ["accent"]
    assert mastered[0]["status_text"] == "Well Known"
    assert [w["word"] for w in a2_unmastered] == ["abricot"]

    notseen_words = [w["word"] for w in notseen]
    assert "accepter" in notseen_words  # in the A1 list, never learned
    assert "accent" not in notseen_words
    assert all(w["id"] is None for w in notseen)


def test_french_words_and_export_endpoints(french, app_context, client):
    "The french_words and french_export endpoints behave like the JLPT ones."
    _save_jp_term(french, "accent", 99)
    _save_jp_term(french, "abricot", 3)

    resp = client.get(
        f"/stats/french_words?lang_id={french.id}&level=A2&filter=unmastered&page=1"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] >= 1

    resp = client.get(f"/stats/french_words?lang_id={french.id}&level=A1&filter=bogus")
    assert resp.status_code == 400
    resp = client.get(f"/stats/french_words?lang_id={french.id}&level=all")
    assert resp.status_code == 400

    resp = client.get(
        f"/stats/french_export?lang_id={french.id}&level=A1&filter=mastered"
    )
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    lines = resp.get_data(as_text=True).strip().splitlines()
    assert lines[0] == "Level,Word,Reading,Meaning,Status"
    assert any("accent" in ln for ln in lines[1:])

    resp = client.get(f"/stats/french_export?lang_id={french.id}&level=all&filter=all")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "A1," in body
    assert "C2," in body


def test_get_arabic_data_counts_by_level(arabic, app_context):
    "Seen/mastered counts attributed to Arabic CEFR levels."
    _save_jp_term(arabic, "أب", 99)  # mastered (A1)
    _save_jp_term(arabic, "آلة", 3)  # seen, not mastered (A2)
    _save_jp_term(arabic, "آثار", 98)  # ignored -> not seen (B1)
    _save_jp_term(arabic, "آثم", 5)  # seen (B2)
    _save_jp_term(arabic, "فهدققزززzzz", 99)  # not in list

    data = get_arabic_data(db.session, arabic.id)

    lv = {l["level"]: l for l in data["levels"]}
    assert len(lv) == 6
    assert lv["A1"]["mastered"] == 1
    assert lv["A1"]["seen"] == 1
    assert lv["A2"]["seen"] == 1
    assert lv["B1"]["seen"] == 0  # آثار ignored
    assert lv["B2"]["seen"] == 1
    assert data["total_mastered"] == 1
    assert data["total_seen"] == 3


def test_get_arabic_data_prefixed_form(arabic, app_context):
    "A definite-article prefixed form expands to its bare headword."
    _save_jp_term(arabic, "الكتاب", 99)  # الكتاب -> كتاب (A1)

    data = get_arabic_data(db.session, arabic.id)
    lv = {l["level"]: l for l in data["levels"]}
    assert lv["A1"]["mastered"] == 1


def test_arabic_data_endpoint(arabic, app_context, client):
    "The /stats/arabic_data endpoint returns level data for an Arabic language."
    _save_jp_term(arabic, "أب", 99)

    resp = client.get(f"/stats/arabic_data?lang_id={arabic.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] > 0
    assert len(data["levels"]) == 6
    a1 = next(l for l in data["levels"] if l["level"] == "A1")
    assert a1["mastered"] == 1

    resp = client.get("/stats/arabic_data")
    assert resp.status_code == 400
    resp = client.get("/stats/arabic_data?lang_id=abc")
    assert resp.status_code == 400


def test_get_arabic_words_filters(arabic, app_context):
    "Drilldown respects the unmastered/mastered/notseen filters."
    _save_jp_term(arabic, "أب", 99)
    _save_jp_term(arabic, "آلة", 3)

    unmastered = get_arabic_words(db.session, arabic.id, "A1", "unmastered")
    mastered = get_arabic_words(db.session, arabic.id, "A1", "mastered")
    a2_unmastered = get_arabic_words(db.session, arabic.id, "A2", "unmastered")
    notseen = get_arabic_words(db.session, arabic.id, "A1", "notseen")

    assert [w["word"] for w in unmastered] == []
    assert [w["word"] for w in mastered] == ["أب"]
    assert mastered[0]["status_text"] == "Well Known"
    assert [w["word"] for w in a2_unmastered] == ["آلة"]

    notseen_words = [w["word"] for w in notseen]
    assert "مدرسة" in notseen_words  # in the A1 list, never learned
    assert "أب" not in notseen_words
    assert all(w["id"] is None for w in notseen)


def test_arabic_words_and_export_endpoints(arabic, app_context, client):
    "The arabic_words and arabic_export endpoints behave like the JLPT ones."
    _save_jp_term(arabic, "أب", 99)
    _save_jp_term(arabic, "آلة", 3)

    resp = client.get(
        f"/stats/arabic_words?lang_id={arabic.id}&level=A2&filter=unmastered&page=1"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] >= 1

    resp = client.get(f"/stats/arabic_words?lang_id={arabic.id}&level=A1&filter=bogus")
    assert resp.status_code == 400
    resp = client.get(f"/stats/arabic_words?lang_id={arabic.id}&level=all")
    assert resp.status_code == 400

    resp = client.get(
        f"/stats/arabic_export?lang_id={arabic.id}&level=A1&filter=mastered"
    )
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    lines = resp.get_data(as_text=True).strip().splitlines()
    assert lines[0] == "Level,Word,Reading,Meaning,Status"
    assert any("أب" in ln for ln in lines[1:])

    resp = client.get(f"/stats/arabic_export?lang_id={arabic.id}&level=all&filter=all")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "A1," in body
    assert "C2," in body


def test_get_hsk2_data_counts_by_level(mandarin, app_context):
    "Seen/mastered counts attributed to HSK 2.0 levels."
    # HSK 2.0 levels: 猫=1, 运动=2, 环境=3, 成熟=4.
    _save_jp_term(mandarin, "猫", 99)  # mastered (1)
    _save_jp_term(mandarin, "运动", 3)  # seen, not mastered (2)
    _save_jp_term(mandarin, "环境", 98)  # ignored -> not seen (3)
    _save_jp_term(mandarin, "成熟", 5)  # seen (4)
    _save_jp_term(mandarin, "不存在的词zzz", 99)  # not in list

    data = get_hsk2_data(db.session, mandarin.id)

    lv = {l["level"]: l for l in data["levels"]}
    assert len(lv) == 6
    assert lv["1"]["mastered"] == 1
    assert lv["1"]["seen"] == 1
    assert lv["2"]["seen"] == 1
    assert lv["3"]["seen"] == 0  # 环境 ignored
    assert lv["4"]["seen"] == 1
    assert data["total_mastered"] == 1
    assert data["total_seen"] == 3


def test_get_hsk3_data_counts_and_7():
    "HSK 3.0 reports levels 1-9 as 1-7 with 7 = the 7-9 band."
    from lute.stats import hsk_data

    word_levels = hsk_data.level_totals("3")
    assert list(word_levels.keys()) == ["1", "2", "3", "4", "5", "6", "7"]
    assert word_levels["7"] > 0  # the 7-9 band has entries


def test_get_hsk3_data_counts_by_level(mandarin, app_context):
    "HSK 3.0 level attribution on stored terms."
    # HSK 3.0 levels: 爱=1, 运动=2, 安全=3, 性格=4.
    _save_jp_term(mandarin, "爱", 99)
    _save_jp_term(mandarin, "运动", 3)
    _save_jp_term(mandarin, "不存在的词zzz", 99)

    data = get_hsk3_data(db.session, mandarin.id)

    lv = {l["level"]: l for l in data["levels"]}
    assert len(lv) == 7
    assert lv["1"]["mastered"] == 1
    assert lv["1"]["seen"] == 1
    assert lv["2"]["seen"] == 1
    assert data["total_mastered"] == 1
    assert data["total_seen"] == 2


def test_hsk_data_endpoint(mandarin, app_context, client):
    "The hsk2_data/hsk3_data endpoints return level data for a Mandarin language."
    _save_jp_term(mandarin, "猫", 99)
    _save_jp_term(mandarin, "爱", 99)

    resp = client.get(f"/stats/hsk2_data?lang_id={mandarin.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] > 0
    assert len(data["levels"]) == 6
    l1 = next(l for l in data["levels"] if l["level"] == "1")
    assert l1["mastered"] == 2  # 猫 and 爱 are both HSK 2.0 level 1

    resp = client.get(f"/stats/hsk3_data?lang_id={mandarin.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["levels"]) == 7
    l1 = next(l for l in data["levels"] if l["level"] == "1")
    assert l1["mastered"] == 1  # only 爱 is HSK 3.0 level 1 (猫 is level 2)

    resp = client.get("/stats/hsk2_data")
    assert resp.status_code == 400
    resp = client.get("/stats/hsk3_data?lang_id=abc")
    assert resp.status_code == 400


def test_get_hsk3_words_filters(mandarin, app_context):
    "HSK 3.0 drilldown respects the unmastered/mastered/notseen filters."
    _save_jp_term(mandarin, "爱", 99)
    _save_jp_term(mandarin, "运动", 3)

    mastered = get_hsk3_words(db.session, mandarin.id, "1", "mastered")
    unmastered_l2 = get_hsk3_words(db.session, mandarin.id, "2", "unmastered")
    notseen = get_hsk3_words(db.session, mandarin.id, "1", "notseen")

    # HSK 3.0 level 1 contains 爱 (mastered) and many unseen words; 猫 is level 2.
    assert {w["word"] for w in mastered} == {"爱"}
    assert mastered[0]["status_text"] == "Well Known"
    assert [w["word"] for w in unmastered_l2] == ["运动"]

    notseen_words = {w["word"] for w in notseen}
    assert "爱" not in notseen_words
    assert all(w["id"] is None for w in notseen)


def test_hsk_words_and_export_endpoints(mandarin, app_context, client):
    "The hsk2_* and hsk3_* endpoint groups behave like the JLPT ones."
    _save_jp_term(mandarin, "猫", 99)
    _save_jp_term(mandarin, "爱", 99)
    _save_jp_term(mandarin, "运动", 3)

    # HSK 2.0
    resp = client.get(
        f"/stats/hsk2_words?lang_id={mandarin.id}&level=2&filter=unmastered&page=1"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] >= 1

    resp = client.get(f"/stats/hsk2_words?lang_id={mandarin.id}&level=1&filter=bogus")
    assert resp.status_code == 400
    resp = client.get(f"/stats/hsk2_words?lang_id={mandarin.id}&level=all")
    assert resp.status_code == 400

    resp = client.get(
        f"/stats/hsk2_export?lang_id={mandarin.id}&level=1&filter=mastered"
    )
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    lines = resp.get_data(as_text=True).strip().splitlines()
    assert lines[0] == "Level,Word,Reading,Meaning,Status"
    assert any("猫" in ln for ln in lines[1:])

    resp = client.get(f"/stats/hsk2_export?lang_id={mandarin.id}&level=all&filter=all")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "1," in body
    assert "6," in body

    # HSK 3.0
    resp = client.get(
        f"/stats/hsk3_words?lang_id={mandarin.id}&level=1&filter=mastered&page=1"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] >= 1
    words = {w["word"] for w in data["words"]}
    assert "爱" in words

    resp = client.get(f"/stats/hsk3_words?lang_id={mandarin.id}&level=2&filter=bogus")
    assert resp.status_code == 400
    resp = client.get(f"/stats/hsk3_words?lang_id={mandarin.id}&level=9")
    assert resp.status_code == 400  # level must be 1-7

    resp = client.get(
        f"/stats/hsk3_export?lang_id={mandarin.id}&level=1&filter=mastered"
    )
    assert resp.status_code == 200
    lines = resp.get_data(as_text=True).strip().splitlines()
    assert lines[0] == "Level,Word,Reading,Meaning,Status"
    assert any("爱" in ln for ln in lines[1:])

    resp = client.get(f"/stats/hsk3_export?lang_id={mandarin.id}&level=all&filter=all")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "7," in body


def test_get_term_languages_includes_zero_word_languages(
    mandarin, classical_chinese, app_context
):
    "Every active language appears in the selector, even with no words yet."
    langs = get_term_languages(db.session)
    by_name = {l["name"].lower(): l for l in langs}

    # Mandarin Chinese appears with count 0 and is flagged for HSK.
    mand = by_name["mandarin chinese"]
    assert mand["count"] == 0
    assert mand["is_chinese"] is True

    # Classical Chinese must NOT be treated as the modern HSK language.
    cls = by_name["classical chinese"]
    assert cls["is_chinese"] is False
