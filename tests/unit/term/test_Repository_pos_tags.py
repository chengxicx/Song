"""
Tests for the POS tag applied automatically when a term is first saved.

The tag is derived from the POS the *page parse* recorded on the term
(see migration 20261016_add_term_pos.sql), never from re-parsing the
word on its own -- so these tests build terms the way the reading page
does, through Term.create_term_no_parsing().
"""

import pytest

from lute.db import db
from lute.models.term import Term as DBTerm, Status
from lute.parse.registry import is_supported
from lute.term.model import Repository
from tests.dbasserts import assert_sql_result

SUDACHI = "japanese_sudachi"

# 出そう in 「声を出そうとしましたが」, straight from the parser.
VERB_POS = ("動詞", "非自立可能", "*", "*", "五段-サ行", "意志推量形")
VERB_TAG = "動詞/非自立可能/五段-サ行/意志推量形"
PARTICLE_POS = ("助詞", "格助詞", "*", "*", "*", "*")

# One row per term, with its tag text ('' when it has no tag at all).
TAG_SQL = """
select w.WoText, ifnull(t.TgText, '') from words w
left join wordtags wt on wt.WtWoID = w.WoID
left join tags t on t.TgID = wt.WtTgID
order by w.WoText
"""


@pytest.fixture(name="jp")
def fixture_jp(japanese):
    "Japanese, but only when it is backed by Sudachi."
    if japanese.parser_type != SUDACHI or not is_supported(SUDACHI):
        pytest.skip("sudachipy and a sudachi dictionary are required")
    return japanese


def _open_book_term(language, text, pos):
    "A term exactly as the reading page makes one when a book opens."
    t = DBTerm.create_term_no_parsing(language, text, pos=pos)
    t.status = Status.UNKNOWN
    db.session.add(t)
    db.session.commit()
    return t


def _save_from_term_form(term_id, **changes):
    "Save a term the way the term form does."
    repo = Repository(db.session)
    biz = repo.load(term_id)
    for key, value in changes.items():
        setattr(biz, key, value)
    repo.add(biz)
    repo.commit()


def test_saving_a_term_attaches_its_pos_tag(app_context, jp):
    t = _open_book_term(jp, "出そう", VERB_POS)
    assert_sql_result(TAG_SQL, ["出そう; "], "no tag while still unknown")

    _save_from_term_form(t.id, status=1, translation="to let out")
    assert_sql_result(TAG_SQL, [f"出そう; {VERB_TAG}"], "tagged on save")


def test_the_tag_is_applied_only_once(app_context, jp):
    "One-shot on purpose: a user who deletes the tag keeps it deleted."
    t = _open_book_term(jp, "出そう", VERB_POS)
    _save_from_term_form(t.id, status=1)
    assert_sql_result(TAG_SQL, [f"出そう; {VERB_TAG}"], "tagged")

    _save_from_term_form(t.id, status=1, term_tags=[])
    assert_sql_result(TAG_SQL, ["出そう; "], "deletion sticks")


def test_particles_and_auxiliaries_are_never_tagged(app_context, jp):
    t = _open_book_term(jp, "を", PARTICLE_POS)
    _save_from_term_form(t.id, status=1)
    assert_sql_result(TAG_SQL, ["を; "], "particles stay untagged")


def test_term_without_a_stored_pos_is_never_tagged(app_context, jp):
    """
    Terms typed into a form by hand, and every term that predates the
    WoPos column, land here.
    """
    t = _open_book_term(jp, "出そう", None)
    _save_from_term_form(t.id, status=1)
    assert_sql_result(TAG_SQL, ["出そう; "], "nothing to derive a tag from")


def test_a_new_parent_does_not_inherit_the_pos_tag(app_context, jp):
    """
    The tag belongs to 出そう, not to its lemma 出す -- different words,
    with different POS.
    """
    t = _open_book_term(jp, "出そう", VERB_POS)
    _save_from_term_form(t.id, status=1, parents=["出す"])
    assert_sql_result(TAG_SQL, ["出す; ", f"出そう; {VERB_TAG}"], "parent is left alone")
