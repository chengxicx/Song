"""
What the lemma/parent backfill actually writes (roadmap 2.3).

The rules themselves are tested in tests/unit/term/test_lemma_parents.py;
these tests are about the database: the parent terms created, the statuses
that must not move, idempotency, and the undo path.
"""

import json

import pytest

pytest.importorskip("sudachipy")
pytest.importorskip("sudachidict_core")

from sqlalchemy import select

from lute.cli.term_parent_backfill import (
    TermParentBackfillError,
    run_backfill,
    undo_backfill,
)
from lute.db import db
from lute.models.term import Term, wordparents
from lute.parse.sudachi_parser import JapaneseSudachiParser
from lute.term.lemma_parents import (
    POLICY_SINGLE_CONTENT_WORD,
    REASON_HAS_PARENT,
    REASON_KANA,
    REASON_MULTI_TOKEN,
    REASON_NO_LEMMA,
)

ZWS = "\u200B"


@pytest.fixture(name="_parser_ok", autouse=True)
def fixture_parser_ok():
    "Skip this module when sudachi can't run."
    if not JapaneseSudachiParser.is_supported():
        pytest.skip("sudachipy and a sudachi dictionary are required")


def _add_term(language, text, status):
    """
    Add an exact term.

    create_term_no_parsing() is used on purpose: Term(language, text)
    re-tokenizes and would insert zero-width spaces, and these tests need
    control over which shape goes in.
    """
    t = Term.create_term_no_parsing(language, text)
    t.status = status
    db.session.add(t)
    db.session.commit()
    return t


def _all_links():
    "Every (child, parent) pair."
    return set(
        db.session.execute(
            select(wordparents.c.WpWoID, wordparents.c.WpParentWoID)
        ).all()
    )


def _term(text):
    "The term with this exact stored text, or None."
    words = Term.__table__
    return (
        db.session.execute(select(Term).where(words.c.WoText == text)).scalars().first()
    )


def test_dry_run_plans_but_writes_nothing(japanese):
    "食べ (a book token) has a dictionary form; 東京 and たべる don't."
    _add_term(japanese, "食べ", 0)
    _add_term(japanese, "東京", 0)
    _add_term(japanese, "たべる", 0)

    res = run_backfill("Japanese")

    assert len(res.links) == 1
    assert res.links[0][0].lemma == "食べる"
    assert res.parents_new == 1
    assert res.skipped[REASON_KANA] == 1
    assert res.skipped[REASON_NO_LEMMA] == 1
    assert _all_links() == set()
    assert _term("食べる") is None


def test_commit_creates_the_parent_with_the_child_status(japanese):
    "The parent is created with the child's status (inherit is the default)."
    child = _add_term(japanese, "食べ", 99)

    res = run_backfill("Japanese", commit=True)

    assert res.committed is True
    assert res.links_written == 1
    child = db.session.get(Term, child.id)
    parent = _term("食べる")
    assert parent is not None
    assert parent.status == 99
    assert parent.sync_status is False
    assert _all_links() == {(child.id, parent.id)}
    # The parent is a single token and its text is exactly the lemma.
    assert ZWS not in parent.text
    assert parent.text_lc == "食べる".lower()


def test_new_parent_status_zero_option(japanese):
    "The conservative alternative: parents are created unknown."
    _add_term(japanese, "食べ", 99)
    run_backfill("Japanese", commit=True, new_parent_status="zero")
    assert _term("食べる").status == 0


def test_an_existing_parent_keeps_its_status(japanese):
    """
    The reason this tool does not use TermRepository._build_db_term().

    _find_or_create_parent() assigns the child's status to an existing
    parent that is status 0, which would change an existing WoStatus.
    """
    parent = _add_term(japanese, "食べる", 0)
    child = _add_term(japanese, "食べ", 1)

    run_backfill("Japanese", commit=True)

    assert db.session.get(Term, parent.id).status == 0
    assert db.session.get(Term, child.id).status == 1
    assert _all_links() == {(child.id, parent.id)}


def test_nothing_gets_a_sync_status(japanese):
    "sync stays 0, so the schema trigger can never move a status."
    _add_term(japanese, "食べ", 1)
    run_backfill("Japanese", commit=True)
    statuses = {
        r[0]
        for r in db.session.execute(
            select(Term.__table__.c.WoID).where(Term.__table__.c.WoSyncStatus == 1)
        )
    }
    assert statuses == set()


def test_second_run_is_a_no_op(japanese):
    _add_term(japanese, "食べ", 1)
    first = run_backfill("Japanese", commit=True)
    assert first.links_written == 1

    second = run_backfill("Japanese", commit=True)

    assert second.links == []
    assert second.links_written == 0
    assert second.parents_new == 0
    assert second.skipped[REASON_HAS_PARENT] == 1
    assert len(_all_links()) == 1


def test_a_term_that_already_has_a_parent_is_left_alone(japanese):
    "The existing parent is not replaced, and no second parent is added."
    existing_parent = _add_term(japanese, "食う", 1)
    child = _add_term(japanese, "食べ", 1)
    db.session.execute(
        wordparents.insert().values(WpWoID=child.id, WpParentWoID=existing_parent.id)
    )
    db.session.commit()

    res = run_backfill("Japanese", commit=True)

    assert res.links_written == 0
    assert res.skipped[REASON_HAS_PARENT] == 1
    assert _all_links() == {(child.id, existing_parent.id)}
    assert _term("食べる") is None


def test_multi_token_terms_need_the_relaxed_policy(japanese):
    "入りました is the shape most hand-made links have."
    text = "入り" + ZWS + "ました"
    _add_term(japanese, text, 1)

    strict = run_backfill("Japanese")
    assert strict.links == []
    assert strict.skipped[REASON_MULTI_TOKEN] == 1
    assert strict.relaxed_link_count == 1
    assert strict.relaxed_parent_count == 1

    relaxed = run_backfill(
        "Japanese", commit=True, multi_token_policy=POLICY_SINGLE_CONTENT_WORD
    )
    assert relaxed.links_written == 1
    parent = _term("入る")
    assert parent is not None
    assert parent.status == 1


def test_no_cycles_and_one_parent_per_child(japanese):
    "The invariants the acceptance criteria name."
    _add_term(japanese, "食べ", 1)
    _add_term(japanese, "食べた", 1)
    _add_term(japanese, "食べる", 1)
    run_backfill("Japanese", commit=True, multi_token_policy=POLICY_SINGLE_CONTENT_WORD)

    links = _all_links()
    assert links, "the fixture should have produced links"
    children = [c for c, _p in links]
    assert len(children) == len(set(children)), "one parent per child"

    parents = dict(links)
    for child_id in children:
        seen = {child_id}
        node = parents.get(child_id)
        while node is not None:
            assert node not in seen, "cycle"
            seen.add(node)
            node = parents.get(node)


def test_undo_removes_the_links_and_the_created_terms(japanese, tmp_path):
    _add_term(japanese, "食べ", 1)
    audit = str(tmp_path / "audit.jsonl")
    run_backfill("Japanese", commit=True, audit_path=audit)
    assert len(_all_links()) == 1

    preview = undo_backfill(audit, commit=False)
    assert preview[0] == 1
    assert len(_all_links()) == 1, "a dry run must not change anything"

    removed, terms_removed, terms_kept = undo_backfill(
        audit, commit=True, delete_created_terms=True
    )

    assert (removed, terms_removed, terms_kept) == (1, 1, 0)
    assert _all_links() == set()
    assert _term("食べる") is None


def test_undo_keeps_a_created_term_that_gained_data(japanese, tmp_path):
    "Once a term has a translation it is vocabulary data, not residue."
    _add_term(japanese, "食べ", 1)
    audit = str(tmp_path / "audit.jsonl")
    run_backfill("Japanese", commit=True, audit_path=audit)
    parent = _term("食べる")
    parent.translation = "to eat"
    db.session.commit()

    _removed, terms_removed, terms_kept = undo_backfill(
        audit, commit=True, delete_created_terms=True
    )

    assert (terms_removed, terms_kept) == (0, 1)
    assert _term("食べる") is not None


def test_unknown_language_is_an_error(japanese):  # pylint: disable=unused-argument
    with pytest.raises(TermParentBackfillError):
        run_backfill("Klingon")


def test_a_language_without_lemmas_is_an_error(spanish):
    "Refuse rather than write thousands of nothing-links."
    with pytest.raises(TermParentBackfillError):
        run_backfill("Spanish")


def test_audit_log_records_what_was_written(japanese, tmp_path):
    _add_term(japanese, "食べ", 1)
    audit = str(tmp_path / "audit.jsonl")
    run_backfill("Japanese", commit=True, audit_path=audit)

    with open(audit, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    assert len(records) == 1
    assert records[0]["child_text"] == "食べ"
    assert records[0]["parent_text"] == "食べる"
    assert records[0]["parent_created"] is True
    assert records[0]["child_status"] == 1
