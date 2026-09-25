"""
Guards on the page fragments that get swapped into the reading pane.

read/page_content.html (and its manga/PDF siblings) is swapped into #thetext
by htmx on every status change, and htmx runs a fragment's <script>s a tick
AFTER inserting its nodes.  Those scripts restore the cursor marker -- and
must not also drop the reader's selection, or a shift-click made while the
fragment was landing is silently lost and the next status hotkey finds
nothing to act on.

That is not hypothetical: it is what made
test_pressing_a_hotkey_updates_a_terms_status and
test_page_start_date_is_set_correctly_during_reading flake on CI, and only on
CI, where the timing shifts.  The fragment's call is driven directly here, at
the moment it actually runs, so the guard does not depend on winning a race.
"""

from tests.acceptance.conftest import then_read_content


def test_swapped_fragment_keeps_the_readers_selection(luteclient):
    """
    restore_cursor_marker() must leave span.kwordmarked alone.

    This is what a swapped-in fragment calls when its <script> finally runs.
    """
    luteclient.visit("/")
    luteclient.make_book("Hola", "Tengo otro amigo.", "Spanish")
    luteclient.wait_reading_ready()

    luteclient.shift_click_words(["Tengo", "otro"])
    marked = luteclient.page.locator("span.kwordmarked")
    assert marked.count() == 2, "sanity: the shift-click should mark both words"

    luteclient.page.evaluate("() => parent.restore_cursor_marker()")
    assert (
        marked.count() == 2
    ), "the swapped fragment must not clear the reader's selection"

    # And so the hotkey still acts on that selection.
    luteclient.press_hotkey("1")
    then_read_content(luteclient, "Tengo (1)/ /otro (1)/ /amigo/.")


def test_reset_cursor_marker_still_clears_a_selection(luteclient):
    """
    The other half of the contract: a same-DOM reset (ESC, page navigation)
    *should* drop the selection, so reset_cursor_marker() keeps clearing it.
    """
    luteclient.visit("/")
    luteclient.make_book("Hola", "Tengo otro amigo.", "Spanish")
    luteclient.wait_reading_ready()

    luteclient.shift_click_words(["Tengo", "otro"])
    marked = luteclient.page.locator("span.kwordmarked")
    assert marked.count() == 2, "sanity: the shift-click should mark both words"

    luteclient.page.evaluate("() => parent.reset_cursor_marker()")
    assert marked.count() == 0, "reset_cursor_marker should clear the marks"
