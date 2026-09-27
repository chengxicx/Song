"""
Guardrails for the term form's "Sentence" shortcut.

Mirrors the LuteForMobile word card's Sentence button: while reading, the
term form lives in the right pane's wordframe, and clicking Sentence must
look up the sentence containing the term being edited in the language's
sentence dictionaries.  The frame document owns neither the parsed text
nor the dictionary list, so it posts an event and the parent reading page
runs the same lookup as its "translate sentence" hotkey.  Standalone
/term pages have no parent reader, so the button is absent there.
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
    "The reading frame's edit form carries the Sentence button."
    term = _save_term(repo, english, "sentencebtn")
    body = client.get(f"/read/edit_term/{term.id}").get_data(as_text=True)

    assert 'id="btn-sentence"' in body
    assert 'onclick="translateSentenceFromTermForm()"' in body
    # The handler must be defined in the same document, else the click is dead.
    assert "function translateSentenceFromTermForm()" in body
    assert "LuteTermFormSentenceRequested" in body


def test_reading_frame_new_term_form_also_offers_it(app_context, client, english):
    "A brand-new term still gets the Sentence shortcut."
    body = client.get(f"/read/termform/{english.id}/brandnew").get_data(as_text=True)

    assert 'id="btn-sentence"' in body


def test_standalone_edit_page_has_no_sentence_button(app_context, client, english, repo):
    "Outside the reading screen there is no reader to do the lookup."
    term = _save_term(repo, english, "sentencebtn2")
    body = client.get(f"/term/edit/{term.id}").get_data(as_text=True)

    assert 'id="btnsubmit"' in body  # the form did render
    assert 'id="btn-sentence"' not in body


def test_standalone_new_page_has_no_sentence_button(app_context, client):
    "Same for the standalone 'create new term' page."
    body = client.get("/term/new").get_data(as_text=True)

    assert 'id="btn-sentence"' not in body


def test_reading_page_translates_the_sentence_for_that_event():
    "The reading page must turn the frame's request into a sentence lookup."
    with open(_READ_INDEX, encoding="utf-8") as f:
        src = f.read()

    assert "LuteTermFormSentenceRequested" in src
    event_block = src.split('"LuteTermFormSentenceRequested"')[1][:200]
    assert 'handle_translate("sentence-id")' in event_block


def test_button_order_save_delete_grammar_sentence(app_context, client, english, repo):
    "Server render keeps Save before Delete before Grammar before Sentence."
    term = _save_term(repo, english, "sentencebtn3")
    body = client.get(f"/read/edit_term/{term.id}").get_data(as_text=True)

    assert (
        body.index('id="btnsubmit"')
        < body.index('id="delete"')
        < body.index('id="btn-grammar"')
        < body.index('id="btn-sentence"')
    )


def test_sentence_button_uses_the_tool_chip_style():
    "The shortcut must not share the Cancel/btn-secondary look."
    with open(_FORM_PATH, encoding="utf-8") as f:
        src = f.read()

    sentence_tag = src.split('id="btn-sentence"', 1)[1].split(">", 1)[0]
    assert "btn-tool" in sentence_tag
    assert "btn-secondary" not in sentence_tag
