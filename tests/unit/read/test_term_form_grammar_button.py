"""
Guardrails for the term form's "Grammar" shortcut.

While reading, the term form fills the right pane (#read_pane_right) and the
grammar analysis is rendered by replacing that pane's contents -- the form
document itself has no grammar view.  The button therefore only exists in the
reading frame, where it asks the parent page to open the analysis, and must
not show up on the standalone /term/edit pages (nothing to switch to there),
so these tests check both directions plus the wiring in the reading page.
"""

import os

import pytest

from lute.db import db
from lute.term.model import Repository, Term

_READ_INDEX = os.path.join(
    os.path.dirname(__import__("lute").__file__), "templates", "read", "index.html"
)

_FORM_PATH = os.path.join(
    os.path.dirname(__import__("lute").__file__), "templates", "term", "_form.html"
)


@pytest.fixture(name="repo")
def fixture_repo():
    return Repository(db.session)


def _save_term(repo, language, text):
    "Create and commit a term business object, return it reloaded."
    t = Term()
    t.language_id = language.id
    t.text = text
    t.status = 1
    repo.add(t)
    repo.commit()
    return repo.find(language.id, text)


def test_reading_frame_edit_form_offers_the_shortcut(
    app_context, client, english, repo
):
    "The reading frame's edit form carries Save / Delete / Grammar."
    term = _save_term(repo, english, "grammarbtn")
    body = client.get(f"/read/edit_term/{term.id}").get_data(as_text=True)

    assert 'id="btn-grammar"' in body
    assert 'onclick="openGrammarFromTermForm()"' in body
    # The handler must be defined in the same document, else the click is dead.
    assert "function openGrammarFromTermForm()" in body
    assert "LuteTermFormGrammarRequested" in body


def test_reading_frame_new_term_form_also_offers_it(app_context, client, english):
    "A brand-new term has no Delete button, but still gets the Grammar one."
    body = client.get(f"/read/termform/{english.id}/brandnew").get_data(as_text=True)

    assert 'id="btn-grammar"' in body
    assert 'id="delete"' not in body


def test_standalone_edit_page_has_no_grammar_button(app_context, client, english, repo):
    """
    Outside the reading screen there is no grammar pane, so no button.  The
    page still carries the handler (it lives in the shared form template and
    is runtime-gated on EMBEDDED_IN_READING_FRAME), but nothing can call it.
    """
    term = _save_term(repo, english, "grammarbtn2")
    body = client.get(f"/term/edit/{term.id}").get_data(as_text=True)

    assert 'id="btnsubmit"' in body  # the form did render
    assert 'id="btn-grammar"' not in body


def test_standalone_new_page_has_no_grammar_button(app_context, client):
    "Same for the standalone 'create new term' page."
    body = client.get("/term/new").get_data(as_text=True)

    assert 'id="btn-grammar"' not in body


def test_reading_page_opens_the_analysis_for_that_event():
    "The reading page must turn the frame's request into the grammar view."
    with open(_READ_INDEX, encoding="utf-8") as f:
        src = f.read()

    assert "LuteTermFormGrammarRequested" in src
    event_block = src.split('"LuteTermFormGrammarRequested"')[1][:200]
    assert "open_grammar_analysis" in event_block


def test_edit_form_button_order_save_delete_grammar(app_context, client, english, repo):
    "Server render keeps Save before Delete before Grammar."
    term = _save_term(repo, english, "grammarbtn3")
    body = client.get(f"/read/edit_term/{term.id}").get_data(as_text=True)

    assert (
        body.index('id="btnsubmit"')
        < body.index('id="delete"')
        < body.index('id="btn-grammar"')
    )


def test_dynamic_delete_button_lands_before_grammar():
    """
    Saving a brand-new term in the frame adds the Delete button at runtime
    (ensure_delete_button), on a form whose container is Save / Grammar.
    The new button must be inserted before Grammar -- a plain appendChild
    yields Save / Grammar / Delete.
    """
    with open(_FORM_PATH, encoding="utf-8") as f:
        src = f.read()

    fn = src.split("function ensure_delete_button()", 1)[1].split(
        "function update_flash_message_notice", 1
    )[0]
    assert 'getElementById("btn-grammar")' in fn
    assert "insertBefore(del, grammar_btn)" in fn
