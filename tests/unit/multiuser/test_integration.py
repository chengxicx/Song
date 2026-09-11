"""
Multi-user integration tests over the HTTP test client: login flow,
auth gate, per-user data isolation, user management permissions.
"""

import os

from lute.db import db
from lute.models.book import Book as BookModel
from lute.book.model import Book as ServiceBook, Repository as ServiceRepository
from lute.models.repositories import UserSettingRepository
from lute.multiuser import context, store
from lute.language.service import Service as LanguageService


def _make_book(session, language, title):
    svcbook = ServiceBook()
    svcbook.language_id = language.id
    svcbook.title = title
    svcbook.text = f"{title} text.  Hello world."
    repo = ServiceRepository(session)
    repo.add(svcbook)
    repo.commit()
    return svcbook


def _make_language(session, name):
    lang = LanguageService(session).get_language_def(name).language
    session.add(lang)
    session.commit()
    return lang


def test_unauthenticated_requests_redirect_to_login(mu_client):
    resp = mu_client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]

    resp = mu_client.get("/login")
    assert resp.status_code == 200


def test_login_flow(mu_client):
    resp = mu_client.login("admin", "wrongpass")
    assert resp.status_code == 200, "failed login re-renders the form"
    assert b"Wrong username or password" in resp.data

    resp = mu_client.login("admin", "pass1234")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/"), "lands on home"

    resp = mu_client.get("/")
    assert resp.status_code == 200, "authenticated home renders"


def test_per_user_data_isolation(mu_client, mu_enabled_app):
    "Each user's db only shows their own books and languages."
    mu_client.login("admin", "pass1234")

    # Admin creates a user via the management page.
    resp = mu_client.post(
        "/users/new",
        data={"username": "mei", "password": "meipass1", "confirm": "meipass1"},
        follow_redirects=True,
    )
    assert b"created" in resp.data

    # Admin's own library: one language + one book, in admin's db.
    with mu_enabled_app.app_context(), context.user_scope("admin"):
        lang = _make_language(db.session, "English")
        _make_book(db.session, lang, "AdminBook")

    # mei's library: different book, in mei's db.
    with mu_enabled_app.app_context(), context.user_scope("mei"):
        lang2 = _make_language(db.session, "English")
        _make_book(db.session, lang2, "MeiBook")

    # mei's db only contains her own data...
    with mu_enabled_app.app_context(), context.user_scope("mei"):
        mei_books = sorted(b.title for b in db.session.query(BookModel).all())
    # ...and admin's db only contains admin's.
    with mu_enabled_app.app_context(), context.user_scope("admin"):
        admin_books = sorted(b.title for b in db.session.query(BookModel).all())

    assert mei_books == ["MeiBook"], "mei sees her own book only"
    assert "AdminBook" not in mei_books, "mei's db has no admin books"
    assert admin_books == ["AdminBook"], "admin's db has no mei books"

    # Over HTTP too: mei's datatables feed only lists her books.
    mu_client.post("/logout")
    assert mu_client.login("mei", "meipass1").status_code == 302
    resp = mu_client.get("/book/datatables", follow_redirects=True)
    if resp.status_code == 200:
        html = resp.get_data(as_text=True)
        assert "MeiBook" in html and "AdminBook" not in html


def test_per_user_settings_isolation(mu_client, mu_enabled_app):
    "Settings changed for one user don't affect another."
    mu_client.login("admin", "pass1234")
    mu_client.post(
        "/users/new",
        data={"username": "mei", "password": "meipass1", "confirm": "meipass1"},
    )

    # Use the key/value setter endpoint the frontend uses (JSON response).
    resp = mu_client.post("/settings/set/show_highlights/0")
    assert resp.status_code == 200
    with mu_enabled_app.app_context(), context.user_scope("admin"):
        admin_val = UserSettingRepository(db.session).get_value("show_highlights")

    mu_client.post("/logout")
    mu_client.login("mei", "meipass1")
    mu_client.post("/settings/set/show_highlights/1")
    with mu_enabled_app.app_context(), context.user_scope("mei"):
        mei_val = UserSettingRepository(db.session).get_value("show_highlights")

    assert admin_val != mei_val, "settings are per user"


def test_users_page_shows_own_account_to_non_admin(mu_client):
    "Non-admins see their own account page; management stays admin-only."
    mu_client.login("admin", "pass1234")
    mu_client.post(
        "/users/new",
        data={"username": "mei", "password": "meipass1", "confirm": "meipass1"},
    )

    mu_client.post("/logout")
    mu_client.login("mei", "meipass1")

    # Non-admins see only their own account (change-password lives
    # here now), with no management actions.
    resp = mu_client.get("/users/index")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "mei" in html, "own account shown"
    assert "New User" not in html, "no New User button"
    assert "/users/admin/password" not in html, "other users' rows hidden"

    # Management routes stay admin-only.
    resp = mu_client.post("/users/delete/mei", follow_redirects=True)
    assert b"Only an admin" in resp.data
    assert store.user_exists("mei"), "account not deleted"


def test_menu_placement(mu_client):
    "Log out lives in the About menu; Setting menu has Users only."
    mu_client.login("admin", "pass1234")
    resp = mu_client.get("/")
    html = resp.get_data(as_text=True)
    assert "Log out (admin)" in html, "logout in menu"
    assert 'href="/users/index"' in html, "Users menu item present"
    assert "/users/me/password" not in html, "no Change password menu item"


def test_delete_user_removes_data(mu_client, mu_enabled_app):
    "Deleting a user removes their account and their data directory."
    mu_client.login("admin", "pass1234")
    mu_client.post(
        "/users/new",
        data={"username": "mei", "password": "meipass1", "confirm": "meipass1"},
    )
    udir = os.path.join(mu_enabled_app.test_datapath, "users", "mei")
    assert os.path.exists(udir)

    resp = mu_client.post("/users/delete/mei", follow_redirects=True)
    assert b"were deleted" in resp.data
    assert not store.user_exists("mei")
    assert not os.path.exists(udir), "user data directory removed"


def test_change_own_password(mu_client):
    "A user can change their own password with the current one."
    mu_client.login("admin", "pass1234")

    resp = mu_client.post(
        "/users/me/password",
        data={
            "current": "wrongcur",
            "password": "newpass1",
            "confirm": "newpass1",
        },
        follow_redirects=True,
    )
    assert b"current password is wrong" in resp.data.lower()
    assert store.authenticate("admin", "pass1234")

    resp = mu_client.post(
        "/users/me/password",
        data={
            "current": "pass1234",
            "password": "newpass1",
            "confirm": "newpass1",
        },
        follow_redirects=True,
    )
    assert b"password was changed" in resp.data.lower()
    assert store.authenticate("admin", "newpass1")


def test_single_user_mode_has_no_gate(mu_app):
    "Without multi-user mode, pages render without login."
    client = mu_app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200, "no auth gate in single-user mode"
