"""
Dictionary import and lookup tests.
"""

import os
import time

from lute.dict import service

from tests.unit.dict.mdx_fixture import build_mdx

ENTRIES = [
    ("たのしい", "<div>楽しい<br/>愉快yúkuài</div>"),
    ("ひさしぶり", "<div>久し振り<br/>好久不见</div>"),
    ("箱", "<div>はこ<br/>箱子</div>"),
]


def _wait(task_id, timeout=10.0):
    "Poll an import task until it reaches a terminal state."
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = service.task_status(task_id)
        if task["state"] in ("finished", "error"):
            return task
        time.sleep(0.01)
    raise AssertionError(f"import task {task_id} never finished")


def _import(app, scratch_dir, mdx_bytes, name="fixture.mdx", target=None):
    "Write the MDX, run the import, and return the finished task."
    path = os.path.join(scratch_dir, name)
    with open(path, "wb") as f:
        f.write(mdx_bytes)
    task_id = service.start_import(app, path, name, target)
    return _wait(task_id)


def test_import_publishes_the_dictionary(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    assert service.is_installed() is False

    task = _import(app, scratch, build_mdx(ENTRIES, title="Fixture"))

    assert task["state"] == "finished"
    assert task["result"]["entries"] == len(ENTRIES)
    assert task["result"]["terms"] == len(ENTRIES)
    assert task["result"]["title"] == "Fixture"
    assert task["result"]["source"] == "fixture.mdx"
    assert service.is_installed() is True


def test_imported_dictionary_is_looked_up(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES))

    assert service.lookup("たのしい") == "楽しい\n愉快yúkuài"
    assert service.lookup("箱") == "はこ\n箱子"


def test_lookup_falls_back_to_the_reading_and_strips_honorifics(
    app, app_context, dict_dirs
):
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES))

    # The key is the kana reading; the kanji spelling is only found
    # through the term's reading.
    assert service.lookup("楽しい", "たのしい") == "楽しい\n愉快yúkuài"
    # おひさしぶり is keyed as ひさしぶり.
    assert service.lookup("お久しぶり", "おひさしぶり") == "久し振り\n好久不见"


def test_lookup_strips_the_zero_width_spaces_between_tokens(
    app, app_context, dict_dirs
):
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES))

    assert service.lookup("たの\u200bしい") == "楽しい\n愉快yúkuài"


HOMOGRAPH_ENTRIES = [
    # The bare-kanji key holds the homograph disambiguation list, as
    # MOJi serves it; the reading-qualified keys hold the real entries.
    ("下", "<div>See also: 下【か】</div>"),
    ("下【か】", "<div>下级。（位が下のもの。）</div>"),
    ("下【もと②】", "<div>下面。（物の下部。）</div>"),
]


def test_homograph_lookup_defaults_to_the_bare_key(app, app_context, dict_dirs):
    "Without an explicit reading the disambiguation list is the answer."
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(HOMOGRAPH_ENTRIES))

    assert service.lookup("下", "か") == "See also: 下【か】"


def test_homograph_lookup_prefers_the_reading_qualified_key(
    app, app_context, dict_dirs
):
    "A clicked xref names a reading, so the bare key must not win."
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(HOMOGRAPH_ENTRIES))

    assert service.lookup("下", "か", prefer_reading=True) == "下级。（位が下のもの。）"
    # Accent mark: the composite key is tried as-is and accent-stripped.
    assert service.lookup("下", "もと②", prefer_reading=True) == "下面。（物の下部。）"
    # No reading-qualified key for this reading: falls back to the bare term.
    assert service.lookup("下", "しも", prefer_reading=True) == "See also: 下【か】"


def test_homograph_candidates_put_the_bare_term_last():
    assert service._homograph_candidates("下", "もと②") == [
        "下【もと②】",
        "下(もと②)",
        "もと②",
        "下【もと】",
        "下(もと)",
        "もと",
        "下",
    ]


def test_lookup_returns_none_without_a_dictionary(app_context, dict_dirs):
    assert service.lookup("たのしい") is None


def test_lookup_returns_none_for_an_unknown_word(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES))
    assert service.lookup("存在しない") is None


def test_identical_definitions_are_stored_once(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    entries = [
        ("たのしい", "<div>楽しい</div>"),
        ("愉しい", "<div>楽しい</div>"),
    ]
    task = _import(app, scratch, build_mdx(entries))

    assert task["result"]["entries"] == 1
    assert task["result"]["terms"] == 2
    assert service.lookup("愉しい") == "楽しい"


def test_definitions_are_truncated(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    long_definition = "あ" * 2000
    _import(app, scratch, build_mdx([("ながい", f"<div>{long_definition}</div>")]))

    assert len(service.lookup("ながい")) == service.MAX_DEFINITION_CHARS


def test_status_reports_the_installed_dictionary(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    assert service.status()["installed"] is False

    _import(app, scratch, build_mdx(ENTRIES, title="Fixture"))
    info = service.status()

    assert info["installed"] is True
    assert info["filename"] == service.DB_FILENAME
    assert info["title"] == "Fixture"
    assert info["source"] == "fixture.mdx"
    assert info["entries"] == len(ENTRIES)
    # Rounds to 0.0 for a fixture-sized dictionary; what matters is that
    # the published file is there and its size was reported.
    assert info["size_mb"] is not None
    assert os.path.getsize(service.db_path()) > 0
    assert info["url"].endswith(f"/static/dict/{service.DB_FILENAME}")


def test_remove_deletes_the_dictionary(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES))

    assert service.remove() is True
    assert service.is_installed() is False
    assert service.lookup("たのしい") is None
    assert service.remove() is False


def test_a_failed_import_leaves_the_previous_dictionary_alone(
    app, app_context, dict_dirs
):
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES, title="Good"))
    assert service.lookup("たのしい") == "楽しい\n愉快yúkuài"

    # Every definition is empty, so there is nothing to publish.
    bad = build_mdx([("x", "<div></div>")])
    task = _import(app, scratch, bad, name="bad.mdx")

    assert task["state"] == "error"
    assert "no usable entries" in task["error"].lower()
    assert service.lookup("たのしい") == "楽しい\n愉快yúkuài"
    assert service.status()["title"] == "Good"


def test_a_corrupt_file_fails_the_import(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    task = _import(app, scratch, b"this is not an MDX file at all", name="junk.mdx")

    assert task["state"] == "error"
    assert service.is_installed() is False


def test_import_deletes_the_source_file(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    path = os.path.join(scratch, "fixture.mdx")
    with open(path, "wb") as f:
        f.write(build_mdx(ENTRIES))
    _wait(service.start_import(app, path, "fixture.mdx"))

    assert os.path.exists(path) is False


def test_lookup_candidates_order():
    assert service.lookup_candidates("楽しい", "たのしい") == [
        "楽しい",
        "たのしい",
    ]
    assert service.lookup_candidates("お久しぶり", "おひさしぶり") == [
        "お久しぶり",
        "おひさしぶり",
        "ひさしぶり",
    ]
    assert service.lookup_candidates("", None) == []
    assert service.lookup_candidates("  ", "  ") == []


def test_lookup_candidates_are_deduplicated():
    assert service.lookup_candidates("たのしい", "たのしい") == ["たのしい"]


# ---------------------------------------------------------------------------
# The pool: several dictionaries side by side
# ---------------------------------------------------------------------------


def test_a_second_dictionary_is_a_separate_file(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES), target=service.DB_FILENAME)
    other = [("안녕", "<div>你好</div>")]
    _import(app, scratch, build_mdx(other), name="ko.mdx", target="ko_zh_basic.db")

    # Each lookup only ever sees its own file.
    assert service.lookup("たのしい") == "楽しい\n愉快yúkuài"
    assert service.lookup("たのしい", None, "ko_zh_basic.db") is None
    assert service.lookup("안녕", None, "ko_zh_basic.db") == "你好"
    assert service.lookup("안녕") is None


def test_list_installed_puts_the_phones_file_first(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    assert service.list_installed() == []

    for target in ("zz_other.db", service.DB_FILENAME, "aa_other.db"):
        _import(app, scratch, build_mdx(ENTRIES), name="fixture.mdx", target=target)

    names = [d["filename"] for d in service.list_installed()]
    assert names == [service.DB_FILENAME, "aa_other.db", "zz_other.db"]
    assert service.list_installed()[0]["is_default"] is True


def test_list_installed_ignores_files_that_are_not_dictionaries(
    app, app_context, dict_dirs
):
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES))
    with open(
        os.path.join(service.dict_dir(), "notes.txt"), "w", encoding="utf-8"
    ) as f:
        f.write("not a dictionary")

    assert [d["filename"] for d in service.list_installed()] == [service.DB_FILENAME]


def test_remove_only_touches_the_named_dictionary(app, app_context, dict_dirs):
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES), target="a.db")
    _import(app, scratch, build_mdx(ENTRIES), name="b.mdx", target="b.db")

    assert service.remove("a.db") is True
    assert service.is_installed("a.db") is False
    assert service.is_installed("b.db") is True
    assert [d["filename"] for d in service.list_installed()] == ["b.db"]


def test_an_unusable_name_never_touches_a_file(app_context, dict_dirs):
    """
    The name is stored per language and comes back through a form, so a
    bad one must resolve to "no dictionary" -- not to a path outside the
    pool, and not to the default file.
    """
    for bad in ("../secret.db", "/etc/passwd", ".hidden.db", "a/b.db", "a.txt"):
        assert service.is_pool_name(bad) is False, bad
        assert service.db_path(bad) is None, bad
        assert service.is_installed(bad) is False, bad
        assert service.lookup("x", None, bad) is None, bad
        assert service.remove(bad) is False, bad
        assert service.status(bad)["installed"] is False, bad


def test_an_empty_name_does_not_fall_back_to_the_default(app, app_context, dict_dirs):
    """
    "" is what a language row holds when it has no dictionary.  It must
    not resolve to the phone's file -- showing the Japanese dictionary
    under a Korean book is the bug this setting exists to fix.
    """
    _static, scratch = dict_dirs
    _import(app, scratch, build_mdx(ENTRIES))

    assert service.is_installed(None) is True
    assert service.is_pool_name("") is False
    assert service.db_path("") is None
    assert service.is_installed("") is False
    assert service.lookup("たのしい", None, "") is None
    assert service.remove("") is False


def test_start_import_refuses_a_name_outside_the_pool(app_context, dict_dirs):
    try:
        service.start_import(None, "/tmp/nope.mdx", "nope.mdx", "../evil.db")
    except ValueError as e:
        assert "Not a dictionary file name" in str(e)
    else:  # pragma: no cover
        raise AssertionError("start_import accepted a name outside the pool")


def test_suggested_filename_is_always_usable():
    assert service.suggested_filename("shogakukan_v2.mdx") == "shogakukan_v2.db"
    assert service.suggested_filename("/tmp/a/b.mdx") == "b.db"
    # Spaces and non-ASCII collapse rather than failing validation.
    assert service.suggested_filename("小学館 日本語.mdx") == "dictionary.db"
    assert service.suggested_filename("My Dict (v2).mdx") == "My-Dict-v2.db"
    assert service.suggested_filename("") == "dictionary.db"
    for name in ("shogakukan_v2.db", "b.db", "dictionary.db", "My-Dict-v2.db"):
        assert service.is_pool_name(name) is True, name


# --- the readers' copy of a definition --------------------------------------
#
# A converted definition leads with the dictionary's own headword
# ("目覚まし時計【めざましどけい⑤】").  Every reader that shows a gloss
# shows the looked-up word above it, so that line is dropped there --
# but only when it really is the same word.


def test_strip_duplicate_headword_drops_the_headword_line():
    gloss = "目覚まし時計【めざましどけい⑤】\n名\n闹钟。"
    assert service.strip_duplicate_headword(gloss, "目覚まし時計") == "名\n闹钟。"


def test_strip_duplicate_headword_drops_a_bare_headword_line():
    gloss = "たのしい\n愉快yúkuài"
    assert service.strip_duplicate_headword(gloss, "たのしい") == "愉快yúkuài"


def test_strip_duplicate_headword_keeps_a_different_headword():
    """
    The kana spelling was looked up and the entry answers under its
    kanji headword: that headword is part of the answer, not a repeat.
    """
    gloss = "終わり【おわり◎】\n名\n终，终了。"
    assert service.strip_duplicate_headword(gloss, "おわり") == gloss


def test_strip_duplicate_headword_keeps_a_first_line_with_content():
    "Not every definition opens with a bare headword."
    assert service.strip_duplicate_headword("箱は\n名\n箱子", "箱") == "箱は\n名\n箱子"
    assert service.strip_duplicate_headword("終わりです。\n名", "終わり") == "終わりです。\n名"


def test_strip_duplicate_headword_keeps_a_headword_only_entry():
    gloss = "たのしい"
    assert service.strip_duplicate_headword(gloss, "たのしい") == gloss


def test_strip_duplicate_headword_ignores_zero_width_spaces():
    "Lute joins multi-token terms with one; a dictionary key never has it."
    assert service.strip_duplicate_headword("たのしい\n愉快", "たの\u200bしい") == "愉快"


def test_strip_duplicate_headword_passes_empty_input_through():
    assert service.strip_duplicate_headword(None, "たのしい") is None
    assert service.strip_duplicate_headword("", "たのしい") == ""
    assert service.strip_duplicate_headword(" gloss\nmore", "") == " gloss\nmore"
    assert service.strip_duplicate_headword(" gloss\nmore", "\u200b") == " gloss\nmore"
