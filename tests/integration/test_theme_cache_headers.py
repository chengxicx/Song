"""
Cache headers for the theme CSS routes.

The theme URL is content-addressed (?v=<sha1 of the css>), so every
response must stay browser-cacheable: an uncacheable /theme/current
costs a full origin round trip on every page load.

Regression: a bare 304 inherited Flask's default text/html mimetype and
was rewritten to no-store by the app-wide dynamic-page hook; per RFC 9111
a 304's headers update the stored entry, so that poisoned the immutable
cache and forced a full re-download on every load.
"""


def _current_theme_response(client):
    resp = client.get("/theme/current")
    assert resp.status_code == 200
    cc = resp.headers["Cache-Control"]
    assert "immutable" in cc, "content-addressed URL must be immutable"
    assert "no-store" not in cc, "200 must not be stamped no-store"
    assert resp.headers["Content-Type"].startswith("text/css")
    return resp


def test_theme_200_is_immutable(client):
    resp = _current_theme_response(client)
    assert resp.headers["ETag"]


def test_theme_200_does_not_rotate_session_cookie(client):
    "Per-response cookie re-issuing defeats Vary: Cookie reuse (see app config)."
    resp = _current_theme_response(client)
    assert (
        "Set-Cookie" not in resp.headers
    ), "theme responses must not rotate the session cookie"


def test_theme_304_keeps_immutable_cache_control(client):
    first = _current_theme_response(client)
    resp = client.get(
        "/theme/current", headers={"If-None-Match": first.headers["ETag"]}
    )
    assert resp.status_code == 304
    cc = resp.headers["Cache-Control"]
    assert "no-store" not in cc, "no-store on a 304 poisons the stored entry"
    assert "immutable" in cc, "304 must keep the immutable policy"
    # Werkzeug strips Content-Type from emitted 304s (no body); the
    # mimetype used by the no-store hook is still text/css internally.


def test_session_refresh_each_request_disabled(app):
    "Flask's default re-signs the permanent cookie on every response."
    assert app.config["SESSION_REFRESH_EACH_REQUEST"] is False
