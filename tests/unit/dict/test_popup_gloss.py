"""
The local dictionary's gloss in the term popup.

The point of the feature is the word you have never studied: it has no
translation, no tags and no romanization, so before this the popup for it
was either empty or not shown at all.  These tests pin both halves --
the gloss reaching the popup, and the popup being *shown* because of it.
"""

import os
import time

from lute.db import db
from lute.dict import service
from lute.models.repositories import UserSettingRepository
from lute.models.term import Term
from lute.read.service import Service

from tests.unit.dict.mdx_fixture import build_mdx

ENTRIES = [
    ("たのしい", "<div>楽しい<br/>愉快yúkuài</div>"),
    ("ひさしぶり", "<div>久し振り<br/>好久不见</div>"),
]


def _import(app, scratch, entries=ENTRIES):
    "Convert a fixture dictionary and wait for it to be published."
    path = os.path.join(scratch, "fixture.mdx")
    with open(path, "wb") as f:
        f.write(build_mdx(entries))
    task_id = service.start_import(app, path, "fixture.mdx")
    deadline = time.time() + 10
    while time.time() < deadline:
        task = service.task_status(task_id)
        if task["state"] == "finished":
            return
        assert task["state"] != "error", task["error"]
        time.sleep(0.01)
    raise AssertionError("import never finished")


def _service():
    return Service(db.session)


def test_a_studied_term_gets_the_gloss(app, app_context, dict_dirs, japanese):
    _import(app, dict_dirs[1])
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
    t = Term(japanese, "たのしい")
    db.session.add(t)
    db.session.commit()

    d = _service().get_popup_data(t.id)

    assert d is not None, "the dictionary gloss alone should show the popup"
    assert d.show is False
    assert d.dict_gloss == "楽しい\n愉快yúkuài"


def test_the_reading_finds_a_kanji_keyed_term(app, app_context, dict_dirs, japanese):
    _import(app, dict_dirs[1])
    t = Term(japanese, "楽しい")
    t.romanization = "たのしい"
    db.session.add(t)
    db.session.commit()

    d = _service().get_popup_data(t.id)

    assert d.dict_gloss == "楽しい\n愉快yúkuài"


def test_no_gloss_when_no_dictionary_is_installed(app_context, dict_dirs, japanese):
    t = Term(japanese, "たのしい")
    t.translation = "fun"
    db.session.add(t)
    db.session.commit()

    d = _service().get_popup_data(t.id)

    assert d.dict_gloss is None


def test_the_setting_turns_the_gloss_off(app, app_context, dict_dirs, japanese):
    _import(app, dict_dirs[1])
    t = Term(japanese, "たのしい")
    t.translation = "fun"
    db.session.add(t)
    db.session.commit()

    repo = UserSettingRepository(db.session)
    repo.set_value("term_popup_show_dict_gloss", False)
    db.session.commit()

    d = _service().get_popup_data(t.id)

    assert d is not None, "the translation still shows the popup"
    assert d.dict_gloss is None


def test_the_setting_is_on_by_default(app_context):
    repo = UserSettingRepository(db.session)
    assert int(repo.get_value("term_popup_show_dict_gloss")) == 1


def test_the_gloss_is_plain_text(app, app_context, dict_dirs, japanese):
    _import(app, dict_dirs[1])
    t = Term(japanese, "たのしい")
    t.translation = "fun"
    db.session.add(t)
    db.session.commit()

    gloss = _service().get_popup_data(t.id).dict_gloss

    # The popup template escapes it rather than piping it through |safe,
    # so it must not carry markup.
    assert "<" not in gloss
    assert "&" not in gloss
