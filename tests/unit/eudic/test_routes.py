"""Route smoke tests for the Eudic page."""

import json
import re

import pytest

from lute.ai.crypto import decrypt
from lute.db import db
from lute.eudic import tasks
from lute.eudic.models import EudicConfigRepository
from lute.eudic.service import EudicService


@pytest.fixture(name="page")
def fixture_page(app, app_context, english):
    "The page needs at least one language with terms to offer."
    from lute.term.model import Repository as TermRepository

    repo = TermRepository(db.session)
    term = repo.find_or_new(english.id, "apple")
    term.status = 3
    repo.add(term)
    repo.commit()
    return app.test_client()


def test_page_renders(page):
    resp = page.get("/eudic/")
    assert resp.status_code == 200
    assert b"Eudic sync" in resp.data or b"\xe6\xac\xa7\xe8\xb7\xaf" in resp.data


def test_configured_page_renders_the_actions(page, app_context, english):
    "The action block only renders once a scope exists; cover that branch."
    EudicService(db.session).set_scope(english.id, "en", "134317089127793466", "日文生词")
    resp = page.get(f"/eudic/?language_id={english.id}")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    for marker in (
        'data-eudic-mode="preview"',
        'data-eudic-mode="pull"',
        'data-eudic-mode="push"',
        'data-eudic-mode="twoway"',
        "/eudic/export.csv",
        "日文生词",
    ):
        assert marker in html, f"missing from page: {marker}"


def _add_term(lg_id, text):
    from lute.term.model import Repository as TermRepository

    repo = TermRepository(db.session)
    term = repo.find_or_new(lg_id, text)
    term.status = 3
    repo.add(term)
    repo.commit()


def test_an_explicit_language_wins_over_a_configured_one(
    page, app_context, english, arabic
):
    """
    Regression: the dropdown reloads /eudic/?language_id=N, and N used to be
    replaced by the first *configured* language, so picking English landed
    on Arabic every time.
    """
    _add_term(arabic.id, "كتاب")
    EudicService(db.session).set_scope(arabic.id, "en", "111", "arabic list")

    html = page.get(f"/eudic/?language_id={english.id}").data.decode("utf-8")
    assert f'data-language-id="{english.id}"' in html
    assert "arabic list" not in html


def test_the_page_falls_back_to_a_configured_language(
    page, app_context, english, arabic
):
    "With no argument, land on a language that is actually mapped."
    _add_term(arabic.id, "كتاب")
    EudicService(db.session).set_scope(arabic.id, "en", "111", "arabic list")

    html = page.get("/eudic/").data.decode("utf-8")
    assert f'data-language-id="{arabic.id}"' in html
    assert "arabic list" in html


def test_the_form_carries_one_language_id_only(page):
    "A second (hidden) field shadowed the dropdown on submit."
    html = page.get("/eudic/").data.decode("utf-8")
    assert 'type="hidden" name="language_id"' not in html
    assert html.count('name="language_id"') == 1


def test_saving_the_key_encrypts_it(page, app_context):
    page.post("/eudic/config", data={"api_key": "MY-SECRET-KEY"})
    row = EudicConfigRepository(db.session).load()
    assert row.api_key.startswith("v1:")
    assert "MY-SECRET-KEY" not in row.api_key
    assert decrypt(row.api_key) == "MY-SECRET-KEY"


def test_blank_key_does_not_wipe_the_saved_one(page, app_context):
    page.post("/eudic/config", data={"api_key": "MY-SECRET-KEY"})
    page.post("/eudic/config", data={"api_key": ""})
    assert EudicService(db.session).api_key() == "MY-SECRET-KEY"


def test_disconnect_clears_the_key(page, app_context):
    page.post("/eudic/config", data={"api_key": "MY-SECRET-KEY"})
    page.post("/eudic/disconnect")
    assert EudicService(db.session).api_key() == ""


def test_saving_a_scope_stores_bucket_and_category(page, app_context, english):
    page.post(
        "/eudic/config",
        data={
            "api_key": "K",
            "language_id": str(english.id),
            "bucket": "en",
            "category_id": "134317089127793466",
            "category_name": "日文生词",
        },
    )
    scopes = EudicService(db.session).scopes()
    assert scopes[english.id]["bucket"] == "en"
    assert scopes[english.id]["category_id"] == "134317089127793466"
    assert scopes[english.id]["category_name"] == "日文生词"


def test_unsupported_bucket_is_rejected_by_the_categories_endpoint(page):
    resp = page.get("/eudic/categories?bucket=ja")
    assert resp.status_code == 400


def _post_scope(client, lg_id, bucket, category_id, category_name, follow=False):
    return client.post(
        "/eudic/config",
        data={
            "language_id": str(lg_id),
            "bucket": bucket,
            "category_id": category_id,
            "category_name": category_name,
        },
        follow_redirects=follow,
    )


def test_a_word_list_cannot_be_shared_by_two_languages(
    page, app_context, english, arabic
):
    """
    A pull imports everything on the remote side that the local language
    lacks, so pointing two languages at one list would make each adopt the
    other's words.
    """
    EudicService(db.session).set_scope(english.id, "en", "111", "english list")

    resp = _post_scope(page, arabic.id, "en", "111", "arabic list", follow=True)

    scopes = EudicService(db.session).scopes()
    assert arabic.id not in scopes
    assert scopes[english.id]["category_name"] == "english list"
    # The flash names the language that owns the list.  Its wording is
    # translated per the user's ui_language, so match the notice box and
    # the name, not the sentence.
    notice = re.search(
        r'<div class="flash-notice">(.*?)</div>', resp.data.decode("utf-8"), re.S
    )
    assert notice is not None, "no notice was shown for the refused mapping"
    assert "English" in notice.group(1)


def test_the_same_language_can_resave_its_own_mapping(page, app_context, english):
    EudicService(db.session).set_scope(english.id, "en", "111", "first")
    _post_scope(page, english.id, "en", "111", "renamed")
    assert EudicService(db.session).scopes()[english.id]["category_name"] == "renamed"


def test_a_different_list_is_still_allowed(page, app_context, english, arabic):
    "Only the studylist is exclusive; the library is shared by design."
    EudicService(db.session).set_scope(english.id, "en", "111", "english list")
    _post_scope(page, arabic.id, "en", "222", "arabic list")
    assert EudicService(db.session).scopes()[arabic.id]["category_id"] == "222"


def test_sync_without_a_scope_is_refused(page):
    resp = page.post("/eudic/push", data={"language_id": "999"})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_sync_without_a_key_is_refused(page, app_context, english):
    EudicService(db.session).set_scope(english.id, "en", "0", "test")
    resp = page.post("/eudic/push", data={"language_id": str(english.id)})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_unknown_mode_is_rejected(page):
    resp = page.post("/eudic/nonsense", data={"language_id": "1"})
    assert resp.status_code == 400


def test_sync_starts_a_task(page, app_context, english, monkeypatch):
    "The route hands off to the thread pool; it must not sync inline."
    started = {}

    def fake_start(app, mode, scope, username=None):
        started["mode"] = mode
        started["lg_id"] = scope.lg_id
        return "task123", False

    monkeypatch.setattr(tasks, "start", fake_start)
    service = EudicService(db.session)
    service.set_api_key("K")
    service.set_scope(english.id, "en", "0", "test")

    resp = page.post("/eudic/twoway", data={"language_id": str(english.id)})
    body = resp.get_json()
    assert body["ok"] is True
    assert body["task_id"] == "task123"
    assert started["mode"] == "twoway"
    assert started["lg_id"] == english.id


def test_task_status_rejects_unknown_ids(page):
    resp = page.get("/eudic/task/doesnotexist")
    body = resp.get_json()
    assert body["state"] == "unknown"
    assert body["ok"] is False


def test_export_csv_has_the_eudic_header(page, app_context, english):
    EudicService(db.session).set_scope(english.id, "en", "0", "test")
    resp = page.get(f"/eudic/export.csv?language_id={english.id}")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    text = resp.data.decode("utf-8-sig")
    header = text.splitlines()[0]
    assert header == "word,phon,exp,add_time,star,context_line"
    assert "apple" in text
