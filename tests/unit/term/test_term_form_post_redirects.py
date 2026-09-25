"""
Standalone term form posts must redirect on success.

``lute.term.routes._handle_form`` passes a plain ``redirect(...)`` response
as ``return_on_success``, and ``handle_term_form`` is documented to accept
either a response or a zero-arg callable returning one.

Werkzeug responses are themselves callable -- a ``Response`` is a WSGI app,
so it defines ``__call__(environ, start_response)`` -- which means a naive
``callable(return_on_success)`` test classifies the response as a factory
and calls it with no arguments, raising::

    TypeError: __call__() missing 2 required positional arguments:
               'environ' and 'start_response'

on every successful save from /term/edit, /term/new and the edit-by-text
route.  These tests post a valid form and require the redirect.
"""

import pytest

from lute.db import db
from lute.term.model import Repository, Term


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


def _form_data(language, text, original_text):
    "Minimum valid TermForm post."
    return {
        "language_id": language.id,
        "original_text": original_text,
        "text": text,
        "translation": "",
        "romanization": "",
        "status": "1",
        "parentslist": "[]",
        "termtagslist": "[]",
    }


def test_edit_post_redirects(app_context, client, english, repo):
    "Saving an edit from the standalone term page redirects."
    term = _save_term(repo, english, "postredirect")
    resp = client.post(
        f"/term/edit/{term.id}", data=_form_data(english, term.text, term.text)
    )
    assert resp.status_code == 302


def test_new_post_redirects(app_context, client, english):
    "Creating a term from the standalone page redirects."
    resp = client.post("/term/new", data=_form_data(english, "brandnewpostterm", ""))
    assert resp.status_code == 302


def test_edit_by_text_post_redirects(app_context, client, english, repo):
    "The edit-by-language-and-text route redirects too."
    term = _save_term(repo, english, "postredirect2")
    resp = client.post(
        f"/term/editbytext/{english.id}/{term.text}",
        data=_form_data(english, term.text, term.text),
    )
    assert resp.status_code == 302
