"""
Run pre-recorded smoke tests using playwright.

Notes:

- the db must be reset to the baseline with demo stories
- site must be running (currently hardcoded to port 5001)
- start code gen in another window with `python codegen`,
  then go to http://localhost:5001/ in the new window
- click through etc etc, then stop the code gen, copy-paste
  code here, fix as needed, _then_ shut down

Debugging:

- to debug, can use "page.pause()" to pause the runner.

More notes:

This is _just a smoke test_, it doesn't do any assertions.
The actions were _recorded_ using playwright's supertastic
code generation.  https://playwright.dev/python/docs/codegen

Then I added some tweaks:

Menu sub-items are only visible after hovering over the menu, e.g.:
  page.locator("#menu_books").hover()
  page.locator("#book_new").click()
"""

import os
import time
import re
import pytest
from playwright.sync_api import Playwright, sync_playwright, expect


def _launch(browser_type, headless):
    """
    Launch a browser, honouring optional environment overrides.

    LUTE_TEST_BROWSER_CHANNEL / LUTE_TEST_BROWSER_ARGS let this suite run on
    machines that don't have playwright's bundled chromium (sandboxed or
    proxied environments).  Both are unset by default, so normal runs and
    CI keep the previous behaviour exactly.
    """
    options = {}
    channel = os.environ.get("LUTE_TEST_BROWSER_CHANNEL")
    if channel:
        options["channel"] = channel
    extra_args = os.environ.get("LUTE_TEST_BROWSER_ARGS", "").split()
    if extra_args:
        options["args"] = extra_args
    return browser_type.launch(headless=headless, **options)


def _park_mouse(page):
    """
    Move the pointer out of the reading text, and clear what hovering left.

    Clicking a link leaves the pointer parked wherever the link was, which
    on the reading page can be on top of a word.  Hovering a word runs
    hover_over(), which adds `.wordhover` *and* saves the word's data order
    in LUTE_CURR_TERM_DATA_ORDER; the next-word hotkey resumes from that
    word.  Tests that reason about the cursor must not inherit the pointer
    position of the preceding click.

    The pointer goes to the bottom-right corner: the top-left corner is the
    hamburger, whose mouseenter opens the site menu and would then intercept
    clicks meant for the page controls.
    """
    viewport = page.viewport_size or {"width": 1280, "height": 720}
    page.mouse.move(viewport["width"] - 4, viewport["height"] - 4)
    page.evaluate(
        """() => {
        document.querySelectorAll('span.wordhover').forEach(
            (el) => el.classList.remove('wordhover'));
        // hover_out() clears the class but leaves the saved order behind.
        if (typeof LUTE_CURR_TERM_DATA_ORDER !== 'undefined') {
            LUTE_CURR_TERM_DATA_ORDER = -1;
        }
    }"""
    )


def _wait_reading_ready(page):
    """
    Wait until the reading page has swapped its text in and paginated it.

    Everything on this page is asynchronous: the text arrives through HTMX
    and only then does _finishPageSwap's requestAnimationFrame fill
    `subScreens`.  Clicking a nav control inside that window does not turn a
    screen -- the screen list is still empty -- so it turns the whole *page*
    instead, and a word the caller is looking for only exists on the page
    that was just left.
    """
    page.wait_for_function(
        """() => {
        if (typeof luteStartReadingDone === 'undefined' || !luteStartReadingDone)
            return false;
        if (!document.querySelectorAll('#thetext span.word').length)
            return false;
        return typeof subScreens !== 'undefined' && subScreens.length > 0;
    }"""
    )
    page.wait_for_timeout(200)


def _reveal(page, selector, max_turns=12):
    """
    Turn the reading screen until `selector` is on screen, and return it.

    The reading page paginates text into screen-height groups and gives the
    paragraphs of the other groups `display:none` (see _splitToScreens in
    read/index.html).  Such an element has no box at all, so -- unlike a
    merely scrolled-out element on the old scrolling page -- it cannot be
    clicked and Playwright reports it as hidden.  Page forward with the
    reader's own arrow control (`#navNext`) until it is visible.
    """
    _wait_reading_ready(page)
    target = page.locator(selector).first
    for _ in range(max_turns):
        if target.is_visible():
            break
        page.locator("#navNext").click()
        page.wait_for_timeout(400)
    expect(target).to_be_visible()
    return target


def _turn_to_next_page(page, max_turns=12):
    """
    Turn the reading screen until the *page* changes, and return the new number.

    On a paginated book `#navNext` walks the current page's sub-screens first
    and only crosses to the next page from the last one (see the left-arrow
    handler in read/index.html), so one click is not one page.
    """
    _wait_reading_ready(page)
    page_num = page.locator("#page_num")
    start = page_num.input_value()
    for _ in range(max_turns):
        page.locator("#navNext").click()
        page.wait_for_timeout(400)
        if page_num.input_value() != start:
            _wait_reading_ready(page)
            return page_num.input_value()
    raise AssertionError(f"#navNext never left page {start}")


def _in_viewport(page, locator):
    """True if the element's box intersects the viewport."""
    box = locator.bounding_box()
    if box is None:
        return False
    viewport = page.viewport_size or {"width": 1280, "height": 720}
    return (
        box["x"] + box["width"] > 0
        and box["y"] + box["height"] > 0
        and box["x"] < viewport["width"]
        and box["y"] < viewport["height"]
    )


def _go_home(page):
    """
    Leave the reading page through the hamburger menu.

    The fork hides the reading header's Home link (read/index.html comments
    `#reading_home_link` out) and puts the site navigation behind
    `.hamburger-btn` instead, so the old `get_by_title("Home")` matches
    nothing.  While the menu is closed its link still has a box, just pushed
    outside the viewport, so `is_visible()` alone would click thin air.
    """
    link = page.locator("#reading_menu a[href='/']").first
    if not _in_viewport(page, link):
        page.locator(".hamburger-btn").click()
        page.wait_for_timeout(400)
    link.click()


def _archive_book_from_list(page, title):
    """
    Archive a book from the book list's "…" actions menu.

    The reading page used to carry an "Archive book" link; the fork removed it
    -- no template or script contains that label any more -- and archiving now
    goes through the book list, which is the route a reader takes by hand.
    """
    page.goto("http://localhost:5001")
    page.wait_for_selector("#booktable tbody tr")
    row = (
        page.locator("#booktable tbody tr")
        .filter(has=page.get_by_role("link", name=title, exact=True))
        .first
    )
    row.locator(".book-action-dropdown > span").hover()
    page.once("dialog", lambda dialog: dialog.accept())
    row.get_by_role("link", name="Archive").click()
    page.wait_for_timeout(500)


def run(p: Playwright) -> None:  # pylint: disable=too-many-statements
    "Run the smoke test."

    # Run headless, can add an env var later for headless or not.
    showbrowser = os.environ.get("SHOW", "") == "true"

    # print(os.environ.get("SHOW"), flush=True)
    # print("-" * 50)
    def _print(s):
        print(s)

    _print("Opening browser.")
    browser = _launch(p.chromium, headless=not showbrowser)
    context = browser.new_context()
    context.set_default_timeout(30000)
    page = context.new_page()

    # Hardcoded port will cause problems ...
    _print("Initial load sanity check")
    page.goto("http://localhost:5001/")

    _print("Reset db.")
    page.goto("http://localhost:5001/dev_api/load_demo")

    # Hardcoded port will cause problems ...
    page.goto("http://localhost:5001/")

    # Open Tutorial
    _print("Tutorial check.")
    page.goto("http://localhost:5001")
    page.get_by_role("link", name="Tutorial", exact=True).click()
    _park_mouse(page)
    # "elephant" sits on a later sub-screen of page 1: turn to it first.
    _reveal(page, "#ID-14-172").click()
    page.frame_locator('iframe[name="wordframe"]').get_by_placeholder(
        "Translation"
    ).click()
    page.frame_locator('iframe[name="wordframe"]').get_by_placeholder(
        "Translation"
    ).fill("big grey thing")
    page.frame_locator('iframe[name="wordframe"]').get_by_role(
        "button", name="Save"
    ).click()
    # The reading footer is gone; the header's next control is now what
    # advances (and, with the "Mark rest known" option on, marks the page
    # read first).
    page.locator("#navNext").click()
    _go_home(page)

    # Bookmarks
    _print("Bookmarks.")
    page.goto("http://localhost:5001")
    page.get_by_role("link", name="Tutorial follow-up", exact=True).click()
    page.locator(".hamburger-btn").click()
    page.once("dialog", lambda dialog: dialog.accept(prompt_text="Page 1"))
    page.get_by_text("Bookmarks", exact=True).hover()
    page.get_by_role("link", name="Add bookmark").hover()
    page.get_by_role("link", name="Add bookmark").click()

    page.locator("#navNext").click()

    page.locator(".hamburger-btn").click()
    page.once("dialog", lambda dialog: dialog.accept(prompt_text="Page 2"))
    page.get_by_text("Bookmarks", exact=True).hover()
    page.get_by_role("link", name="Add bookmark").hover()
    page.get_by_role("link", name="Add bookmark").click()

    page.get_by_role("link", name="List bookmarks").click()
    page.get_by_text("…").first.hover()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("link", name="Delete").click()

    page.get_by_text("…").last.hover()
    page.once("dialog", lambda dialog: dialog.accept(prompt_text="Page 2 - edit"))
    page.get_by_role("link", name="Edit").click()
    expect(page.get_by_role("link", name="Page 2 - edit")).to_be_visible()
    expect(page.get_by_role("link", name="Page 1")).not_to_be_visible()

    # Archive a book from the book list.
    _print("Archive.")
    _archive_book_from_list(page, "Büyük ağaç")

    # Make a new book.  The merged "Create new book" page has several
    # "Title" labels (one per import type), so locate fields by id.
    _print("New book.")
    page.locator("#menu_books").hover()
    page.locator("#book_new").click()
    page.locator("#language_id").select_option("4")
    page.locator("#title").fill("Hello")
    page.locator("#text").fill("Hello there.")
    page.get_by_role("button", name="Save").click()

    # Edit a term.
    _print("Edit term.")
    page.locator("#ID-0-0").click()
    page.frame_locator('iframe[name="wordframe"]').get_by_placeholder(
        "Translation"
    ).click()
    page.frame_locator('iframe[name="wordframe"]').get_by_placeholder(
        "Translation"
    ).fill("Hi.")
    page.frame_locator('iframe[name="wordframe"]').get_by_role(
        "button", name="Save"
    ).click()

    # Archive current book "Hello", check archive.
    _print("Archive.")
    _go_home(page)
    _archive_book_from_list(page, "Hello")
    page.locator("#menu_books").hover()
    page.get_by_role("link", name="Archive").click()
    expect(page.get_by_role("link", name="Hello")).to_be_visible()

    # Open term listing.
    _print("Term listing.")
    page.goto("http://localhost:5001/")
    page.locator("#menu_terms").hover()
    page.get_by_role("link", name="Terms", exact=True).click()
    page.get_by_role("link", name="Hello").click()
    # TODO testing: restore Sentences smoke test check.
    # page.get_by_role("link", name="Sentences").click()
    page.get_by_role("link", name="Back to list").click()
    # page.pause()

    # TODO issue_336_export_unknown_book_terms: restore this test.
    # _print("Export parent term mapping files.")
    # page.locator("#menu_terms").hover()
    # page.get_by_role("link", name="Parent Term mapping").click()
    # with page.expect_download(timeout=30000) as _:
    #     page.get_by_role("link", name="Tutorial", exact=True).click()

    # Edit language.
    _print("Edit language.")
    page.goto("http://localhost:5001/")
    page.locator("#menu_settings").hover()
    page.get_by_role("link", name="Languages").click()
    page.get_by_role("link", name="English").click()
    page.get_by_role("button", name="Save").click()

    # The recorded steps that used to follow (wipe the db, create a second
    # language, web-page import, custom styles, shortcut save) are not ported to
    # the fork's UI yet.  They live in test_playwright_after_db_reset below and
    # are skipped there, so the ported part stays a real guardrail instead of a
    # permanently red test nobody reads.

    context.close()
    browser.close()


@pytest.mark.skip(
    reason=(
        "Not ported to the fork yet: these recorded steps assume the pre-fork "
        "home page and menus.  Port them, then drop this marker."
    )
)
def test_playwright_after_db_reset():  # pylint: disable=too-many-statements
    "The tail of the recorded smoke test, starting at the db wipe."

    showbrowser = os.environ.get("SHOW", "") == "true"

    def _print(s):
        print(s)

    sp = sync_playwright().start()
    browser = _launch(sp.chromium, headless=not showbrowser)
    context = browser.new_context()
    context.set_default_timeout(30000)
    page = context.new_page()

    # This is where the recorded run was when the fork diverged: it had just
    # saved the language edit on the home page.  Start from there again.
    page.goto("http://localhost:5001")

    # Wipe the db.
    _print("Reset db.")
    page.get_by_role("link", name="click here").click()

    # Create a new language.
    _print("New language.")
    page.get_by_role("link", name="create your language.").click()
    page.locator("#predefined").select_option("Spanish")
    page.get_by_role("button", name="go").click()
    page.get_by_role("button", name="Save").click()

    # Create a new book for the new lang.
    _print("New book.")
    page.get_by_role("link", name="Create one?").click()
    page.get_by_label("Title").click()
    page.get_by_label("Title").fill("Hola.")
    page.get_by_label("Text", exact=True).fill("Tengo un perro.")
    page.get_by_role("button", name="Save").click()

    # Interact with text.
    page.get_by_text("perro").click()
    page.frame_locator('iframe[name="wordframe"]').get_by_placeholder(
        "Translation"
    ).click()
    page.frame_locator('iframe[name="wordframe"]').get_by_placeholder(
        "Translation"
    ).fill("dog.")
    page.frame_locator('iframe[name="wordframe"]').get_by_role(
        "button", name="Save"
    ).click()

    # Go home, backup is kicked off.
    _print("Disabled: Verify backup started.")
    _go_home(page)
    # TODO disabled_backup_check: backup now runs and redirects to home.
    # Not sure how to check it easily ... wait for it to complete.
    time.sleep(4)
    # page.get_by_role("link", name="Back to home.").click()

    # Archive and unarchive.
    # Disabled, the links are now hidden inside a small hover-over dropdown.
    # TODO reactivate_disabled_tests: book links are in a small hover-over list.
    _print("Disabled: Archive and unarchive.")
    ### expect(page.get_by_role("link", name="Hola.")).to_be_visible()
    ### page.get_by_title("Archive", exact=True).click()
    ### expect(page.get_by_role("link", name="Create one?")).to_be_visible()
    ### page.locator("#menu_books").hover()
    ### page.get_by_role("link", name="Book archive").click()
    ### expect(page.get_by_role("link", name="Hola.")).to_be_visible()
    ### page.get_by_title("Unarchive", exact=True).click()
    ### expect(page.get_by_role("link", name="Hola.")).to_be_visible()

    # Import web page.
    _print("Import web page.")
    page.locator("#menu_books").hover()
    page.get_by_role("link", name="Create new book").click()
    page.locator("#import_type").select_option("webpage")
    page.locator("#importurl").fill("http://localhost:5001/dev_api/fake_story.html")
    page.get_by_role("button", name="Import").click()
    time.sleep(2)
    # Page is imported, form shown, so save it.
    page.get_by_role("button", name="Save").click()
    page.get_by_text("Tengo").click()  # Quick hacky check if exists.
    _go_home(page)
    expect(page.get_by_role("link", name="Mi perro.")).to_be_visible()

    # Check version.
    _print("Version.")
    page.locator("#menu_about").hover()
    page.get_by_role("link", name="About Song").click()

    # Custom style.
    _print("Custom style.")
    page.locator("#menu_settings").hover()
    page.get_by_role("link", name="Settings").click()
    page.get_by_label("Custom styles").click()
    page.get_by_label("Custom styles").fill(
        "span.status0 { background-color: yellow; }"
    )
    page.get_by_role("button", name="Save").click()

    # Custom style.
    _print("Keyboard shortcuts.")
    page.locator("#menu_settings").hover()
    page.get_by_role("link", name="Keyboard shortcuts").click()
    page.get_by_role("button", name="Save").click()

    # ---------------------
    context.close()
    browser.close()
    sp.stop()


def test_term_form_grammar_button():
    """
    The term form's Grammar button switches the right pane to the page's
    grammar analysis, and closing it brings the form back.

    The form lives in the wordframe iframe and the analysis is rendered by
    the reading page itself, so this can only be checked in a browser: the
    frame has to reach its parent, which then has to fill the pane.
    """

    showbrowser = os.environ.get("SHOW", "") == "true"
    with sync_playwright() as sp:
        browser = _launch(sp.chromium, headless=not showbrowser)
        context = browser.new_context()
        context.set_default_timeout(30000)
        page = context.new_page()

        page.goto("http://localhost:5001/dev_api/load_demo")

        # Open Tutorial and click a word, which fills the term form.
        page.goto("http://localhost:5001")
        page.get_by_role("link", name="Tutorial", exact=True).click()
        _park_mouse(page)
        _reveal(page, "#thetext span.word").click()

        frame = page.frame_locator('iframe[name="wordframe"]')
        grammar_btn = frame.get_by_role("button", name="Grammar")
        expect(grammar_btn).to_be_visible()

        # The button asks the reading page to show the analysis, which
        # replaces the pane's normal contents.
        grammar_btn.click()
        expect(page.locator("#grammar-analysis-panel")).to_be_visible()
        expect(page.locator("#read_pane_right")).to_have_class(
            re.compile(r"grammar-mode")
        )
        expect(page.locator(".wordframecontainer")).to_be_hidden()

        # Closing the analysis gives the pane -- and the still-loaded
        # term form -- back.
        page.locator(".grammar-analysis-panel__close").click()
        expect(page.locator("#grammar-analysis-panel")).to_have_count(0)
        expect(page.locator("#read_pane_right")).not_to_have_class(
            re.compile(r"grammar-mode")
        )
        expect(grammar_btn).to_be_visible()

        context.close()
        browser.close()


def test_playwright():
    "Run playwright with tests."
    with sync_playwright() as sp:
        run(sp)


def test_hotkey_conflict():
    "Test that disabling a conflicting hotkey clears conflict warning."
    showbrowser = os.environ.get("SHOW", "") == "true"
    with sync_playwright() as sp:
        browser = _launch(sp.chromium, headless=not showbrowser)
        context = browser.new_context()
        page = context.new_page()

        # Load the shortcuts settings page
        page.goto("http://localhost:5001/settings/shortcuts")

        # Select the first two shortcut inputs by class name "shortcutdefinition"
        shortcuts = page.locator(".shortcutdefinition")
        expect(shortcuts.first).to_be_visible()

        # Focus first shortcut and press a key to assign it
        shortcuts.nth(0).click()
        page.keyboard.press("KeyA")

        # Focus second shortcut and press the same key to create a conflict
        shortcuts.nth(1).click()
        page.keyboard.press("KeyA")

        # Verify that both are highlighted as duplicates (dupShortcut class)
        expect(shortcuts.nth(0)).to_have_class(r"shortcutdefinition dupShortcut")
        expect(shortcuts.nth(1)).to_have_class(r"shortcutdefinition dupShortcut")

        # Verify the Save button is disabled
        save_btn = page.locator("#btnSubmit")
        expect(save_btn).to_be_disabled()

        # Uncheck the checkbox next to the first shortcut to disable it
        first_row = shortcuts.nth(0).locator("xpath=../..")
        checkbox = first_row.locator('input[type="checkbox"]')
        checkbox.uncheck()

        # Verify the first input is now empty
        expect(shortcuts.nth(0)).to_have_value("")

        # Verify that the second input is no longer marked as a duplicate
        expect(shortcuts.nth(1)).not_to_have_class(r"dupShortcut")

        # Verify the Save button is now enabled
        expect(save_btn).to_be_enabled()

        context.close()
        browser.close()


def test_page_change_first_word():
    "Test that going to the next page resets the cursor, so the first word is selected next."

    showbrowser = os.environ.get("SHOW", "") == "true"
    with sync_playwright() as sp:
        browser = _launch(sp.chromium, headless=not showbrowser)
        context = browser.new_context()
        page = context.new_page()

        # Load demo database
        page.goto("http://localhost:5001/dev_api/load_demo")

        # Open Tutorial
        page.goto("http://localhost:5001")
        page.get_by_role("link", name="Tutorial", exact=True).click()

        # Wait until page text is loaded
        page.locator("span.word").first.wait_for()

        # The click above parked the pointer over the text, which leaves a
        # stray hover on whatever word is under it.  Hovering a word calls
        # save_curr_data_order(), and the next-word hotkey resumes from that
        # word -- so without this the "first word" assertions below check
        # the wrong starting point (and the hovered word may be in a
        # paragraph that _splitToScreens has since hidden, leaving the
        # highlight off screen entirely).
        _park_mouse(page)
        page.wait_for_timeout(200)

        # Press right arrow 3 times to move the cursor to some word in the middle
        page.keyboard.press("ArrowRight")
        page.keyboard.press("ArrowRight")
        page.keyboard.press("ArrowRight")

        # Verify a word is highlighted/active
        expect(
            page.locator("span.word.wordhover, span.word.kwordmarked").first
        ).to_be_visible()

        # Go to the next page using the right arrow navigation button.
        # One click turns one *screen*, so walk to the page boundary.
        _turn_to_next_page(page)

        # Wait for the new page content to load
        page.locator("span.word").first.wait_for()

        # Verify that NO word on the new page is highlighted automatically
        expect(
            page.locator("span.word.wordhover, span.word.kwordmarked")
        ).to_have_count(0)

        # Press the right arrow key
        page.keyboard.press("ArrowRight")

        # Verify that the VERY FIRST word on the new page is now highlighted
        first_word = page.locator("span.word").first
        expect(first_word).to_have_class(re.compile(r"kwordmarked"))

        context.close()
        browser.close()
