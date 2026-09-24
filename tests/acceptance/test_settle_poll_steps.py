"""
Guards on the settle-poll step helpers in conftest.

The same mistake has been made more than once in this suite: a step
reads the page or the db *once*, immediately after an action that may
not have landed yet, and then fails intermittently.  The symptom is a
different handful of scenarios red on every CI retry, which the
nick-fields/retry wrapper in ci.yml only masks.  (See then_read_content,
_assert_settles and check_book_table for the other instances.)

A race cannot be reproduced on demand, so this drives the helper
directly against content that is deliberately late.
"""

from tests.acceptance.conftest import check_book_table, then_page_contains


def test_page_contains_waits_for_late_content(luteclient):
    """
    'the page contains' must poll, not read once.

    It normally follows a click whose response is still in flight.  The
    marker here is injected 1.5s after the page has already loaded, so a
    read-once implementation fails this and a polling one passes.
    """
    luteclient.visit("/")
    luteclient.page.evaluate(
        """() => {
             setTimeout(() => {
               const p = document.createElement("p");
               p.textContent = "LATE_MARKER";
               document.body.appendChild(p);
             }, 1500);
           }"""
    )
    then_page_contains(luteclient, "LATE_MARKER")


def test_book_table_check_waits_for_late_content(luteclient, monkeypatch):
    """
    'the book table contains' must poll, not read once.

    The table is rendered by DataTables after an ajax fetch, so reading it
    once catches it half-built -- on a loaded CI runner that surfaced as
    test_disabled_data_is_hidden intermittently reporting another test's
    book.  The getter is deliberately late here, so a read-once
    implementation fails this and a polling one passes.
    """
    reads = {"count": 0}

    def late_getter():
        reads["count"] += 1
        return "LATE_TABLE" if reads["count"] >= 3 else ""

    monkeypatch.setattr(luteclient, "get_book_table_content", late_getter)
    check_book_table(luteclient, "LATE_TABLE")
    assert reads["count"] >= 3, "the step must have polled"


def test_book_table_read_is_a_single_snapshot(luteclient, monkeypatch):
    """
    get_book_table_content must read the whole table in one evaluate().

    The table is serverSide (book/tablelisting.html), so DataTables
    replaces the entire tbody when the search ajax lands.  A read
    assembled from one locator round-trip per cell can have the rows
    swapped out from under it, and Playwright then waits its full 4s for
    a detached node and *raises* instead of returning stale text -- which
    is how test_reenabled_data_is_still_available broke once
    check_book_table started polling.  Polling alone does not fix that:
    the read itself has to be atomic.
    """

    class StubPage:
        "Counts evaluate() calls, and rejects per-cell locator reads."

        def __init__(self):
            self.evaluates = 0

        def evaluate(self, script):
            self.evaluates += 1
            return [["a", "b", "c", "d"], ["e", "f", "g", "h"]]

        def locator(self, *args, **kwargs):
            raise AssertionError("the table must be read in one snapshot")

    page = StubPage()
    monkeypatch.setattr(luteclient, "page", page)

    assert luteclient.get_book_table_content() == "a; b\ne; f"
    assert page.evaluates == 1
