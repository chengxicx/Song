"""
The local dictionary's gloss in the term popup.

The point of the feature is the word you have never studied: it has no
translation, no tags and no romanization, so before this the popup for it
was either empty or not shown at all.  These tests pin both halves --
the gloss reaching the popup, and the popup being *shown* because of it.

Both the switch and the dictionaries are the term's *language's* now
(see Language.local_dict_files / Language.term_popup_show_dict_gloss),
so the tests also pin that a language which has none gets nothing.
"""

import os
import time

from lute.db import db
from lute.dict import service
from lute.models.language import Language, LanguageDictionary
from lute.models.term import Term
from lute.read.service import DICT_GLOSS_POPUP_MAX_LINES, Service

from tests.unit.dict.mdx_fixture import build_mdx

ENTRIES = [
    ("たのしい", "<div>楽しい<br/>愉快yúkuài</div>"),
    ("ひさしぶり", "<div>久し振り<br/>好久不见</div>"),
]


def _import(app, scratch, entries=ENTRIES, name="fixture.mdx", target=None):
    "Convert a fixture dictionary and wait for it to be published."
    path = os.path.join(scratch, name)
    with open(path, "wb") as f:
        f.write(build_mdx(entries))
    task_id = service.start_import(app, path, name, target)
    deadline = time.time() + 10
    while time.time() < deadline:
        task = service.task_status(task_id)
        if task["state"] == "finished":
            return
        assert task["state"] != "error", task["error"]
        time.sleep(0.01)
    raise AssertionError("import never finished")


def _use(language, filename=None, gloss=True):
    "Give the language a Local dictionary row, and set the popup switch."
    d = LanguageDictionary()
    d.usefor = "terms"
    d.dicttype = "localdict"
    # None = whatever _import() just published (the pool's only file in
    # these tests); a language row always stores a concrete name.
    d.dicturi = filename or service.default_name()
    d.is_active = True
    d.sort_order = 0
    language.dictionaries.append(d)
    language.term_popup_show_dict_gloss = gloss
    db.session.commit()
    return language


def _service():
    return Service(db.session)


def test_a_studied_term_gets_the_gloss(app, app_context, dict_dirs, japanese):
    _import(app, dict_dirs[1])
    _use(japanese)
    t = Term(japanese, "たのしい")
    t.translation = "fun"
    db.session.add(t)
    db.session.commit()

    d = _service().get_popup_data(t.id)

    assert d.translation == "fun"
    assert d.dict_gloss == "楽しい\n愉快yúkuài"


def test_an_unstudied_term_gets_a_popup_without_a_dictionary(
    app_context, dict_dirs, japanese
):
    "Without a dictionary there is nothing to show, so there is no popup."
    t = Term(japanese, "たのしい")
    db.session.add(t)
    db.session.commit()

    assert _service().get_popup_data(t.id) is None


def test_the_gloss_shows_for_a_term_with_no_translation(
    app, app_context, dict_dirs, japanese
):
    _import(app, dict_dirs[1])
    _use(japanese)
    t = Term(japanese, "たのしい")
    db.session.add(t)
    db.session.commit()

    d = _service().get_popup_data(t.id)

    assert d is not None, "the dictionary gloss alone should show the popup"
    assert d.show is False
    assert d.dict_gloss == "楽しい\n愉快yúkuài"


def test_the_reading_finds_a_kanji_keyed_term(app, app_context, dict_dirs, japanese):
    _import(app, dict_dirs[1])
    _use(japanese)
    t = Term(japanese, "楽しい")
    t.romanization = "たのしい"
    db.session.add(t)
    db.session.commit()

    d = _service().get_popup_data(t.id)

    # The entry's headword line is the term's own spelling, which the
    # popup already shows above the gloss.
    assert d.dict_gloss == "愉快yúkuài"


def test_no_gloss_when_no_dictionary_is_installed(app_context, dict_dirs, japanese):
    t = Term(japanese, "たのしい")
    t.translation = "fun"
    db.session.add(t)
    db.session.commit()

    d = _service().get_popup_data(t.id)

    assert d.dict_gloss is None


def test_no_gloss_when_the_language_has_not_chosen_a_dictionary(
    app, app_context, dict_dirs, japanese
):
    "An imported file is not enough -- the language has to pick it."
    _import(app, dict_dirs[1])
    t = Term(japanese, "たのしい")
    t.translation = "fun"
    db.session.add(t)
    db.session.commit()

    d = _service().get_popup_data(t.id)

    assert d.dict_gloss is None


def test_the_gloss_comes_from_the_languages_own_dictionary(
    app, app_context, dict_dirs, japanese
):
    _import(app, dict_dirs[1], target="ja.db")
    _import(
        app,
        dict_dirs[1],
        entries=[("たのしい", "<div>别的词典</div>")],
        name="other.mdx",
        target="other.db",
    )
    _use(japanese, "other.db")
    t = Term(japanese, "たのしい")
    t.translation = "fun"
    db.session.add(t)
    db.session.commit()

    assert _service().get_popup_data(t.id).dict_gloss == "别的词典"


def test_the_gloss_comes_from_the_first_dictionary_with_an_entry(
    app, app_context, dict_dirs, japanese
):
    "Two local dictionaries: tab order decides, the first hit wins."
    _import(
        app,
        dict_dirs[1],
        entries=[("ひさしぶり", "<div>好久不见</div>")],
        name="narrow.mdx",
        target="narrow.db",
    )
    _import(app, dict_dirs[1], target="wide.db")
    _use(japanese, "narrow.db")
    wide = LanguageDictionary()
    wide.usefor = "terms"
    wide.dicttype = "localdict"
    wide.dicturi = "wide.db"
    wide.is_active = True
    wide.sort_order = 1
    japanese.dictionaries.append(wide)
    db.session.commit()
    t = Term(japanese, "たのしい")
    t.translation = "fun"
    db.session.add(t)
    db.session.commit()

    d = _service().get_popup_data(t.id)

    assert d.dict_gloss == "楽しい\n愉快yúkuài", "narrow.db has no たのしい entry"


def test_the_languages_setting_turns_the_gloss_off(
    app, app_context, dict_dirs, japanese
):
    _import(app, dict_dirs[1])
    _use(japanese, gloss=False)
    t = Term(japanese, "たのしい")
    t.translation = "fun"
    db.session.add(t)
    db.session.commit()

    d = _service().get_popup_data(t.id)

    assert d is not None, "the translation still shows the popup"
    assert d.dict_gloss is None


def test_the_setting_is_on_by_default_for_a_new_language(app_context, empty_db):
    lang = Language()
    lang.name = "Popup Default"
    db.session.add(lang)
    db.session.commit()
    langid = lang.id
    db.session.expunge(lang)

    assert db.session.get(Language, langid).term_popup_show_dict_gloss is True


def test_the_gloss_is_plain_text(app, app_context, dict_dirs, japanese):
    _import(app, dict_dirs[1])
    _use(japanese)
    t = Term(japanese, "たのしい")
    t.translation = "fun"
    db.session.add(t)
    db.session.commit()

    gloss = _service().get_popup_data(t.id).dict_gloss

    # The popup template escapes it rather than piping it through |safe,
    # so it must not carry markup.
    assert "<" not in gloss
    assert "&" not in gloss


# --- a 小学館-shaped entry -------------------------------------------------
#
# The real dictionary's definitions open with the entry's own headword
# ("終わり【おわり◎】"), then the part of speech, the senses, and a long
# tail of example sentences.  The popup shows the term and its reading
# above the gloss, and it is a glance -- the dictionary strip carries
# the full entry.

LONG_ENTRY = (
    "おわり",
    "<div>終わり【おわり◎】<br/>名<br/>"
    "終，終了，末尾，末了，結束，結局，终点，尽头。(物事が終わること。)<br/>"
    "始めから終わりまで。<br/>从到头，自始至终。<br/>"
    "終わりまで聞く。<br/>听到最后，听完。<br/>"
    "これで終わりです。<br/>到此完（结束）。<br/>"
    "あいつも一巻の終わりだ。<br/>他也一刀全结束了。<br/>"
    "この世の終わり。<br/>今生的晚年，人世的末路。</div>",
)


def _owari_term(japanese):
    "終わり, found through its reading, like a kanji spelling in the text."
    t = Term(japanese, "終わり")
    t.romanization = "おわり"
    db.session.add(t)
    db.session.commit()
    return t


def test_the_popup_caps_the_dictionary_gloss(app, app_context, dict_dirs, japanese):
    _import(app, dict_dirs[1], entries=[LONG_ENTRY], name="long.mdx")
    _use(japanese)
    t = _owari_term(japanese)

    gloss = _service().get_popup_data(t.id).dict_gloss

    lines = gloss.split("\n")
    assert len(lines) == DICT_GLOSS_POPUP_MAX_LINES + 1, "five lines and a '…'"
    assert lines[-1] == "…", "the cut is visible"
    assert lines[0] == "名"
    assert "終，終了" in gloss, "the senses stay"
    assert "今生的晚年" not in gloss, "the example tail is cut"


def test_the_popup_leaves_a_short_gloss_alone(app, app_context, dict_dirs, japanese):
    _import(app, dict_dirs[1])
    _use(japanese)
    t = Term(japanese, "たのしい")
    t.translation = "fun"
    db.session.add(t)
    db.session.commit()

    assert _service().get_popup_data(t.id).dict_gloss == "楽しい\n愉快yúkuài"


def test_the_popup_drops_the_duplicated_headword_line(
    app, app_context, dict_dirs, japanese
):
    "The popup shows the term and its reading; the entry need not repeat them."
    _import(app, dict_dirs[1], entries=[LONG_ENTRY], name="long.mdx")
    _use(japanese)
    t = _owari_term(japanese)

    gloss = _service().get_popup_data(t.id).dict_gloss

    assert "終わり【おわり◎】" not in gloss
    assert gloss.startswith("名\n")
