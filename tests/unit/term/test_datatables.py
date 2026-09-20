"""
Book tests.
"""

import pytest
from lute.term.datatables import get_data_tables_list
from lute.db import db
from tests.utils import add_terms


@pytest.fixture(name="_dt_params")
def fixture_dt_params():
    "Sample query params."
    columns = [
        {"data": "0", "name": "WoID", "searchable": False, "orderable": False},
        {"data": "1", "name": "WoText", "searchable": True, "orderable": True},
    ]
    params = {
        "draw": "1",
        "columns": columns,
        "order": [{"column": "1", "dir": "asc"}],
        "start": "0",
        "length": "10",
        "search": {"value": "", "regex": False},
        # Filters - set "manually" in the route.
        # Cheating here ... had to look at the request payload
        # in devtools to see what was being sent.
        "filtLanguage": "null",  # Ha!
        "filtParentsOnly": "false",
        "filtAgeMin": "",
        "filtAgeMax": "",
        "filtStatusMin": "0",
        "filtStatusMax": "99",
        "filtIncludeIgnored": "false",
        "filtTermIDs": "",
    }
    return params


def test_smoke_term_datatables_query_runs(app_context, _dt_params):
    """
    Smoke test only, ensure query runs.
    """
    get_data_tables_list(_dt_params, db.session)
    # print(d['data'])
    a = 1
    assert a == 1, "dummy check"


def test_smoke_query_with_filter_params_runs(app_context, _dt_params):
    "Smoke test with filters set."
    _dt_params["filtLanguage"] = "44"
    _dt_params["filtParentsOnly"] = "true"
    _dt_params["filtAgeMin"] = "1"
    _dt_params["filtAgeMax"] = "10"
    _dt_params["filtStatusMin"] = "2"
    _dt_params["filtStatusMax"] = "4"
    _dt_params["filtIncludeIgnored"] = "true"
    _dt_params["filtTermIDs"] = "42,43"
    get_data_tables_list(_dt_params, db.session)


def test_parents_included_in_termids_query(app_context, _dt_params, spanish):
    "For term list viewing from page, it's useful to see parents as well."
    # pylint: disable=unbalanced-tuple-unpacking
    [t, p, g] = add_terms(spanish, ["T", "P", "G"])
    t.add_parent(p)
    p.add_parent(g)
    db.session.add(t)
    db.session.add(p)
    db.session.commit()

    _dt_params["filtTermIDs"] = f"{t.id}"
    d = get_data_tables_list(_dt_params, db.session)
    terms = [t["WoText"] for t in d["data"]]
    terms = sorted(terms)
    assert terms == ["P", "T"]


def test_parents_only_filter_returns_terms_that_have_no_parent(
    empty_db, _dt_params, spanish
):
    """
    The filter is labelled "Terms without a parent" and its SQL is
    `parents.parentlist IS NULL`, which keeps terms that have no parents.

    Pin the meaning here rather than in the template: the label used to read
    "Parent terms only", which promised the opposite of what the query did
    (roadmap 2.2b).  If you ever flip this query to `IS NOT NULL`, the label
    has to change with it -- and the render check below will fail until it does.
    """
    # pylint: disable=unbalanced-tuple-unpacking
    [t, p, g] = add_terms(spanish, ["T", "P", "G"])
    t.add_parent(p)
    p.add_parent(g)
    db.session.add(t)
    db.session.add(p)
    db.session.commit()

    _dt_params["filtParentsOnly"] = "true"
    d = get_data_tables_list(_dt_params, db.session)
    terms = sorted(x["WoText"] for x in d["data"])
    assert terms == ["G"], "only G has no parent, so only G should be listed"

    _dt_params["filtParentsOnly"] = "false"
    d = get_data_tables_list(_dt_params, db.session)
    terms = sorted(x["WoText"] for x in d["data"])
    assert terms == ["G", "P", "T"], "the filter is off by default"


def test_term_page_labels_the_filter_the_way_the_query_behaves(client):
    "The checkbox text has to match `filtParentsOnly`'s SQL, not contradict it."
    # The listing lives at /term/index -- there is no route for bare /term,
    # which is why nothing had ever asserted on this page's markup.
    html = client.get("/term/index").data.decode("utf-8")
    assert "Terms without a parent" in html
    assert "Parent terms only" not in html
