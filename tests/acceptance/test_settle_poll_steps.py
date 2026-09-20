"""
Guards on the settle-poll step helpers in conftest.

The same mistake has been made more than once in this suite: a step
reads the page or the db *once*, immediately after an action that may
not have landed yet, and then fails intermittently.  The symptom is a
different handful of scenarios red on every CI retry, which the
nick-fields/retry wrapper in ci.yml only masks.  (See then_read_content
and _assert_settles for the other two instances.)

A race cannot be reproduced on demand, so this drives the helper
directly against content that is deliberately late.
"""

from tests.acceptance.conftest import then_page_contains


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
