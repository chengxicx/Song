"""
The parent backfill on a space-delimited language.

This is lute-lemma's own demo (github.com/LuteOrg/lute-lemma) used as the
acceptance case: the same ten Spanish words, and the same expected pairs
its README prints, except that they come out of the backfill that has
dry-run, a JSON plan, an audit log and an undo, instead of a CSV to be
hand-imported.

The engine is the spaCy Spanish pipeline the grammar panel already
installs, so the expected lemmas below are model-dependent; they are
frozen against es_core_news_md as shipped by the "spanish" extra.
"""

import importlib.util

import pytest
from sqlalchemy import select

from lute.cli.term_parent_backfill import run_backfill
from lute.db import db
from lute.models.term import Term, wordparents

# lute-lemma's demo/es_input.txt.
DEMO_TERMS = [
    "perros",
    "perro",
    "perras",
    "vives",
    "vivimos",
    "vivieron",
    "muchacho",
    "muchacha",
    "coches",
    "vez",
]

# lute-lemma's README expected output.  The four words whose lemma is the
# word itself (perro, muchacho, muchacha, vez) are absent from it too.
EXPECTED_LINKS = {
    ("coches", "coche"),
    ("perras", "perra"),
    ("perros", "perro"),
    ("vives", "vivir"),
    ("vivimos", "vivir"),
    ("vivieron", "vivir"),
}

# Parent terms that do not exist in the input list and have to be created.
CREATED_PARENTS = {"coche", "perra", "vivir"}


def _requires_spanish_model():
    "Skip when the spanish grammar-engine extra is not installed."
    for module in ("spacy", "es_core_news_md"):
        if importlib.util.find_spec(module) is None:
            pytest.skip("the spanish grammar-engine extra is not installed")


def _add_term(language, text):
    "Add an exact term; no parsing, so no zero-width spaces appear."
    t = Term.create_term_no_parsing(language, text)
    t.status = 0
    db.session.add(t)
    db.session.commit()
    return t


def _links():
    "Every (child text, parent text) pair in the database."
    words = Term.__table__
    text_by_id = dict(db.session.execute(select(words.c.WoID, words.c.WoText)).all())
    rows = db.session.execute(
        select(wordparents.c.WpWoID, wordparents.c.WpParentWoID)
    ).all()
    return {(text_by_id[child], text_by_id[parent]) for child, parent in rows}


def _all_texts():
    words = Term.__table__
    return {r[0] for r in db.session.execute(select(words.c.WoText)).all()}


def test_dry_run_plans_the_lute_lemma_demo_without_writing(spanish):
    _requires_spanish_model()
    for text in DEMO_TERMS:
        _add_term(spanish, text)

    res = run_backfill("Spanish")

    assert res.committed is False
    assert res.lemma_source.startswith("spaCy (es_core_news_md")
    assert {(lnk.child_text, lnk.lemma) for lnk, _pid, _new in res.links} == (
        EXPECTED_LINKS
    )
    assert res.parents_new == len(CREATED_PARENTS)
    assert _links() == set()
    assert _all_texts() == set(DEMO_TERMS)


def test_commit_creates_the_parent_terms_and_links(spanish):
    _requires_spanish_model()
    for text in DEMO_TERMS:
        _add_term(spanish, text)

    res = run_backfill("Spanish", commit=True)

    assert res.committed is True
    assert res.links_written == len(EXPECTED_LINKS)
    assert _links() == EXPECTED_LINKS
    assert CREATED_PARENTS <= _all_texts()

    # The link is what the reading page reads: perros now has exactly one
    # parent, so the popup can show "perros (perro)" and promote its
    # translation.
    perros = db.session.execute(
        select(Term).where(Term.__table__.c.WoText == "perros")
    ).scalar_one()
    assert [p.text for p in perros.parents] == ["perro"]

    # Statuses: the children are untouched, and the created parents carry
    # the child's status under the default "inherit" policy.
    assert perros.status == 0
    vivir = db.session.execute(
        select(Term).where(Term.__table__.c.WoText == "vivir")
    ).scalar_one()
    assert vivir.status == 0
    assert vivir.sync_status is False


def test_second_run_is_a_no_op(spanish):
    "Idempotency: every term already has its parent, so nothing is planned."
    _requires_spanish_model()
    for text in DEMO_TERMS:
        _add_term(spanish, text)
    run_backfill("Spanish", commit=True)

    again = run_backfill("Spanish")

    assert again.links == []
    assert again.parents_new == 0
