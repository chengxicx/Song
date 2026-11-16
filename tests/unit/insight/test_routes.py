"""Route smoke tests for the 3D insight page."""

import pytest

from lute.db import db
from lute.term.model import Repository as TermRepository


@pytest.fixture(name="page")
def fixture_page(app, app_context, english):
    "A language with one word, so the page has something to select."
    repo = TermRepository(db.session)
    term = repo.find_or_new(english.id, "apple")
    term.status = 2
    repo.add(term)
    repo.commit()
    return app.test_client()


def test_page_renders_the_canvas_and_tabs(page):
    resp = page.get("/insight/")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    for marker in (
        'id="insight3d_canvas"',
        'data-insight-chart="retention"',
        'data-insight-chart="galaxy"',
        'data-insight-chart="terrain"',
        'data-insight-chart="levels"',
        "insight3d.js",
        "three.module.min.js",
    ):
        assert marker in html, f"missing from page: {marker}"


def test_page_declares_an_import_map_for_three(page):
    "The bare 'three' specifier has to resolve to the vendored module."
    html = page.get("/insight/").data.decode("utf-8")
    assert 'type="importmap"' in html
    assert '"/static/vendor/three/three.module.min.js?v=' in html or (
        "/static/vendor/three/three.module.min.js?v=" in html
    )


@pytest.mark.parametrize("endpoint", ["retention", "galaxy", "terrain", "levels"])
def test_data_endpoints_return_json(page, endpoint):
    resp = page.get(f"/insight/data/{endpoint}")
    assert resp.status_code == 200
    assert resp.is_json
    assert isinstance(resp.get_json(), dict)


def test_retention_payload_shape(page):
    body = page.get("/insight/data/retention").get_json()
    assert "x_labels" in body and "y_labels" in body
    assert isinstance(body["cells"], list)
    assert isinstance(body["theory"], list)


def test_galaxy_payload_shape(page):
    body = page.get("/insight/data/galaxy").get_json()
    assert body["total"] == 1
    assert body["days"] == [0]


def test_levels_payload_shape(page):
    body = page.get("/insight/data/levels").get_json()
    assert "supported" in body
    assert isinstance(body["levels"], list)


def test_unknown_language_id_does_not_crash(page):
    resp = page.get("/insight/data/galaxy?language_id=99999")
    assert resp.status_code == 200
    assert resp.get_json()["empty"] is True


def test_stats_page_is_untouched(page):
    "The 3D page must not have added anything to /stats/ itself."
    resp = page.get("/stats/")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    # The nav link is shared chrome and expected; the chart machinery is not.
    assert 'id="insight3d_canvas"' not in html
    assert "three.module.min.js" not in html
    assert "insight3d.js" not in html
