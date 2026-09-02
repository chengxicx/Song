"""
Tests for the reading screen term form's JSON data endpoints and
AJAX save flow.

The wordframe's term form is updated in place: lute.js fetches the
form's data from ?format=json and posts saves with the
X-Requested-With header.  The server must return JSON in those cases,
and keep its old behavior (rendered form page / updated.html) for
regular requests.
"""

import pytest

from lute.db import db
from lute.term.model import Repository, Term


@pytest.fixture(name="repo")
def fixture_repo():
    return Repository(db.session)


def _save_term(repo, language, text, translation=None, status=1):
    "Create and commit a term business object, return it reloaded."
    t = Term()
    t.language_id = language.id
    t.text = text
    t.translation = translation
    t.status = status
    repo.add(t)
    repo.commit()
    return repo.find(language.id, text)


def test_edit_term_json_returns_form_data(app_context, client, english, repo):
    "?format=json GET of an existing term returns its form data."
    term = _save_term(repo, english, "HELLO", translation="greeting", status=2)

    resp = client.get(f"/read/edit_term/{term.id}?format=json")
    assert resp.status_code == 200, resp.status_code
    data = resp.get_json()

    assert data["term_id"] == term.id
    assert data["is_new"] is False
    assert data["text"] == "HELLO"
    assert data["original_text"] == "HELLO"
    assert data["translation"] == "greeting"
    assert data["status"] == "2"
    assert data["form_action"] == f"/read/edit_term/{term.id}"
    assert data["parents"] == []
    assert data["tags"] == []
    assert "term_tags_whitelist" in data
    assert "hide_pronunciation" in data


def test_edit_term_json_includes_tags_and_parents(app_context, client, english, repo):
    "Tags and parents come back as plain string lists."
    parent = _save_term(repo, english, "parentterm")
    term = Term()
    term.language_id = english.id
    term.text = "childterm"
    term.parents = ["parentterm"]
    term.term_tags = ["tag-a"]
    repo.add(term)
    repo.commit()
    term = repo.find(english.id, "childterm")

    resp = client.get(f"/read/edit_term/{term.id}?format=json")
    data = resp.get_json()

    assert data["parents"] == [parent.text]
    assert data["tags"] == ["tag-a"]
    assert "tag-a" in data["term_tags_whitelist"]


def test_termform_new_word_json(app_context, client, english):
    "?format=json GET of an unsaved word returns new-term form data."
    resp = client.get(f"/read/termform/{english.id}/newword?format=json")
    assert resp.status_code == 200, resp.status_code
    data = resp.get_json()

    assert data["term_id"] is None
    assert data["is_new"] is True
    assert data["original_text"] == "newword"
    assert data["status"] == "1"
    assert data["form_action"] == f"/read/termform/{english.id}/newword"


def test_termform_multiword_json_action_uses_luteslash(app_context, client, english):
    "Multiword terms with slashes get the LUTESLASH encoding in form_action."
    # lute.js replaces / with LUTESLASH before building the URL.
    resp = client.get(f"/read/termform/{english.id}/someLUTESLASHword?format=json")
    assert resp.status_code == 200, resp.status_code
    data = resp.get_json()

    # The text is tokenized, so it carries ZWS token boundaries.
    zws = "\u200B"
    assert data["original_text"] == zws.join(["some", "/", "word"])
    assert data["form_action"] == (
        f"/read/termform/{english.id}/{zws.join(['some', 'LUTESLASH', 'word'])}"
    )


def test_ajax_post_save_returns_ok_json(app_context, client, english, repo):
    "AJAX form post returns ok JSON and saves the term."
    url = f"/read/termform/{english.id}/ajaxword"
    post_data = {
        "language_id": str(english.id),
        "original_text": "ajaxword",
        "text": "ajaxword",
        "translation": "saved via ajax",
        "romanization": "",
        "status": "3",
        "parentslist": "[]",
        "termtagslist": "[]",
        "current_image": "",
    }
    resp = client.post(
        url, data=post_data, headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert resp.status_code == 200, resp.status_code
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["term_text"] == "ajaxword"
    assert data["term_id"] is not None

    saved = repo.find(english.id, "ajaxword")
    assert saved is not None
    assert saved.translation == "saved via ajax"
    assert saved.status == 3


def test_ajax_post_invalid_returns_invalid_json(app_context, client, english):
    "AJAX post with validation errors returns invalid + the form HTML."
    url = f"/read/termform/{english.id}/badword"
    post_data = {
        "language_id": str(english.id),
        "original_text": "badword",
        "text": "",  # required field, so validation fails
        "translation": "",
        "romanization": "",
        "status": "1",
        "parentslist": "[]",
        "termtagslist": "[]",
        "current_image": "",
    }
    resp = client.post(
        url, data=post_data, headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert resp.status_code == 200, resp.status_code
    data = resp.get_json()
    assert data["status"] == "invalid"
    assert "<html" in data["html"].lower()


def test_regular_post_still_returns_updated_html(app_context, client, english, repo):
    "Non-AJAX posts keep the old behavior: updated.html replaces the form."
    url = f"/read/termform/{english.id}/regularword"
    post_data = {
        "language_id": str(english.id),
        "original_text": "regularword",
        "text": "regularword",
        "translation": "",
        "romanization": "",
        "status": "1",
        "parentslist": "[]",
        "termtagslist": "[]",
        "current_image": "",
    }
    resp = client.post(url, data=post_data)
    assert resp.status_code == 200, resp.status_code
    body = resp.get_data(as_text=True)
    assert "<html" in body.lower()
    assert "updated" in body.lower()

    assert repo.find(english.id, "regularword") is not None


def test_regular_get_still_renders_form_html(app_context, client, english, repo):
    "GET without ?format=json renders the full form page as before."
    term = _save_term(repo, english, "plainget")
    resp = client.get(f"/read/edit_term/{term.id}")
    assert resp.status_code == 200, resp.status_code
    body = resp.get_data(as_text=True)
    assert 'id="term-form-container"' in body
