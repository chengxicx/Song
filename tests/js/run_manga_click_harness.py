"""
Drive the manga click harness and assert the interaction rules.

Checks, against the real lute-cursor.js / lute-tooltip.js:

  1. clicking a split piece opens that word's edit form (the production
     regression: the first cut of the fix swallowed the click entirely);
  2. the form it opens is the WHOLE word's (data-wid 2694, not a scrap);
  3. a drag across a split piece and a normal word offers a multiword
     term that does not include the piece;
  4. clicking a normal word is unchanged;
  5. hovering a split piece shows a (non-empty-rendering) popup card;
  6. hovering a split piece SPEAKS the whole word (data-tts-text),
     while a normal word is still spoken as itself.
"""

import json
import os
import pathlib

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
HARNESS = (HERE / "manga_click_harness.html").resolve()
RESULTS = []


def check(label, cond, detail=""):
    print("  %s %s%s" % ("PASS" if cond else "FAIL", label, detail))
    RESULTS.append((label, cond))


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 700})
        page.goto(HARNESS.as_uri())
        page.wait_for_function("window.LUTE_HARNESS_READY === true", timeout=15000)

        def reveal(block_n=0):
            # Manga lines are display:none until the block is hovered.
            bb = page.eval_on_selector_all(
                ".manga-text-block",
                "els => els[%d].getBoundingClientRect().toJSON()" % block_n,
            )
            page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
            page.wait_for_timeout(150)

        def center(sel):
            return page.eval_on_selector(
                sel, "e => { const r = e.getBoundingClientRect();"
                " return {x: r.x + r.width/2, y: r.y + r.height/2}; }"
            )

        # --- 1. click a split piece -> edit form for the whole word ---
        reveal()
        c = center("#ID-1-0")
        page.mouse.click(c["x"], c["y"])
        page.wait_for_timeout(200)
        opened = page.evaluate("window.__opened_forms")
        check(
            "click on split piece opens the form",
            len(opened) == 1,
            " opened=%r" % (opened,),
        )
        check(
            "the form is the whole word's (wid 2694)",
            bool(opened) and opened[0]["wid"] == "2694",
            " opened=%r" % (opened,),
        )
        check(
            "the clicked piece is marked data-split-piece",
            page.eval_on_selector(
                "#ID-1-0", "e => e.hasAttribute('data-split-piece')"
            ),
        )

        # --- 2. click a normal word is unchanged ---
        page.evaluate("window.__opened_forms = []")
        reveal()
        c = center("#ID-1-2")
        page.mouse.click(c["x"], c["y"])
        page.wait_for_timeout(200)
        opened = page.evaluate("window.__opened_forms")
        check(
            "click on a normal word still opens its form",
            len(opened) == 1 and opened[0]["wid"] == "28",
            " opened=%r" % (opened,),
        )

        # --- 3. drag from the piece to a normal word: piece excluded ---
        page.evaluate("window.__multiword_calls = []")
        reveal()
        a = center("#ID-1-0")
        b = center("#ID-1-2")
        page.mouse.move(a["x"], a["y"])
        page.mouse.down()
        page.mouse.move(b["x"], b["y"], steps=6)
        page.mouse.up()
        page.wait_for_timeout(200)
        calls = page.evaluate("window.__multiword_calls")
        check(
            "drag over a piece offers a multiword term without it",
            len(calls) == 1 and "プレゼン" not in calls[0] and "ト" not in calls[0],
            " calls=%r" % (calls,),
        )

        # --- 4. hover a split piece shows a popup card ---
        reveal()
        c = center("#ID-1-0")
        page.mouse.move(c["x"], c["y"])
        try:
            page.wait_for_selector(".ui-tooltip", state="visible", timeout=6000)
            visible = True
        except Exception:
            visible = False
        check("hovering a split piece shows a popup card", visible)

        # --- 5. hover pronunciation speaks the WHOLE word, not the piece ---
        # The hover from check 4 already started the (5ms-delayed) utterance.
        page.wait_for_timeout(500)
        spoken = page.evaluate("window.__spoken")
        check(
            "hovering a split piece speaks the whole word",
            len(spoken) >= 1 and spoken[0] == "プレゼント",
            " spoken=%r" % (spoken,),
        )

        # --- 6. a normal word is still spoken as itself ---
        page.evaluate("window.__spoken = []")
        reveal()
        c = center("#ID-1-2")
        page.mouse.move(c["x"], c["y"])
        page.wait_for_timeout(400)
        spoken = page.evaluate("window.__spoken")
        check(
            "hovering a normal word speaks its own text",
            len(spoken) >= 1 and spoken[0] == "です",
            " spoken=%r" % (spoken,),
        )
        browser.close()

    print()
    failed = [l for l, ok in RESULTS if not ok]
    if failed:
        print("FAILURES (%d): %s" % (len(failed), failed))
        raise SystemExit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()