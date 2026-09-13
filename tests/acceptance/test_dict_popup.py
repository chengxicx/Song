"""
Test dictionary popup window reuse.
"""


def test_dictionary_popup_reuses_window(luteclient):
    """
    Verify that subsequent lookups reuse the same popup dictionary window.
    """
    # Update Spanish dictionary to be popuphtml
    luteclient.visit(
        "dev_api/execsql/UPDATE%20languagedicts%20SET%20LdType%20%3D%20%27popuphtml%27"
    )

    luteclient.make_book("Hola", "Hola. Adios.", "Spanish")
    luteclient.click_word("Hola")
    context = luteclient.page.context
    initial_page_count = len(context.pages)
    dict_btn = luteclient.page.locator(".dict-btn-external").first
    with luteclient.page.expect_popup():
        dict_btn.click()

    assert len(context.pages) == initial_page_count + 1, "new page opened"

    luteclient.click_word("Adios")
    dict_btn_2 = luteclient.page.locator(".dict-btn-external").first
    dict_btn_2.click()

    luteclient.sleep(1)  # wait for window
    assert len(context.pages) == initial_page_count + 1, "same window reused"


def test_dictionary_popup_closed_on_unload(luteclient):
    """
    Verify that popup dictionary windows are closed when navigating away from the book.
    """
    # Update Spanish dictionary to be popuphtml
    luteclient.visit(
        "dev_api/execsql/UPDATE%20languagedicts%20SET%20LdType%20%3D%20%27popuphtml%27"
    )

    luteclient.make_book("Hola", "Hola. Adios.", "Spanish")
    luteclient.click_word("Hola")
    dict_btn = luteclient.page.locator(".dict-btn-external").first

    context = luteclient.page.context
    initial_page_count = len(context.pages)
    with luteclient.page.expect_popup():
        dict_btn.click()

    assert len(context.pages) == initial_page_count + 1, "New page opened"

    # The fork moved the reading page's navigation behind the hamburger menu
    # (`#reading_home_link` is commented out in read/index.html), so the old
    # `[title="Home"]` locator matches nothing.  The menu's logo link is what
    # leaves the book now -- any full navigation fires the unload handler
    # under test.
    luteclient.page.click(".hamburger-btn")
    luteclient.sleep(0.5)  # let the menu finish sliding in
    luteclient.page.click("#reading_menu a[href='/']")
    luteclient.sleep(1)  # wait for page transition and unload event

    assert len(context.pages) == initial_page_count, "popup closed automatically"
