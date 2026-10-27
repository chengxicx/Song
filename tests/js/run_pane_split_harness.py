"""
Drive the reading-pane split harness and assert the two panes stay split.

The reader's pane widths are owned by two places at once:

  * the stylesheet -- 50/50 on desktop, and on the narrow layout
    (@media max-width: 980px) the left pane fills the viewport while the
    right pane becomes a full-width bottom sheet;
  * `applyInitialPaneSizes` (lute/static/js/resize.js), which writes the
    reader's remembered split inline.

`getTextWidthPercentage()` measures the *computed* width, so on the narrow
layout it reads 100 -- and writing that back inline ("left 100%, right
0%") used to bake the narrow geometry into the document.  Inline styles
outrank the stylesheet, so the first time the viewport was wide again --
un-maximising the window, or the reader dropping a flash notice and with
it the document scrollbar, which shifts the 980px breakpoint -- the left
pane stayed full width and the dictionary pane collapsed to zero width at
the right edge.  The divider "jumps to the right" and the pane is gone.

Checks, against the real styles.css and resize.js:

  1. a narrow load writes no inline pane width;
  2. widening the window after that load restores the 50/50 split;
  3. crossing the breakpoint the other way (wide -> narrow) drops the
     inline widths again;
  4. the reader's stored split is honoured when the window is wide;
  5. the dictionary pane is never left at zero width on desktop.
"""

import pathlib

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
HARNESS = (HERE / "pane_split_harness.html").resolve()
RESULTS = []

PROBE = """() => {
  const l = document.getElementById('read_pane_left');
  const p = document.getElementById('read_pane_right');
  const c = document.getElementById('read_pane_container');
  const lr = l.getBoundingClientRect();
  const pr = p.getBoundingClientRect();
  return {
    narrow: matchMedia('(max-width: 980px)').matches,
    container: Math.round(c.getBoundingClientRect().width),
    left: Math.round(lr.width),
    right: Math.round(pr.width),
    divider: Math.round(pr.left),
    leftInline: l.style.width || '',
    rightInline: p.style.width || '',
  };
}"""


def check(label, cond, detail=""):
    print("  %s %s%s" % ("PASS" if cond else "FAIL", label, detail))
    RESULTS.append((label, cond))


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 700})
        page.goto(HARNESS.as_uri())
        page.wait_for_function("window.LUTE_HARNESS_READY === true", timeout=15000)

        narrow = page.evaluate(PROBE)
        check(
            "narrow load leaves the pane widths to the media query",
            narrow["narrow"] and not narrow["leftInline"] and not narrow["rightInline"],
            " %r" % (narrow,),
        )

        page.set_viewport_size({"width": 1400, "height": 900})
        page.wait_for_timeout(300)
        wide = page.evaluate(PROBE)
        half = wide["container"] / 2
        check(
            "widening after a narrow load restores the 50/50 split",
            not wide["narrow"]
            and abs(wide["left"] - half) <= 2
            and abs(wide["right"] - half) <= 2,
            " %r" % (wide,),
        )
        check(
            "the divider is back in the middle of the pane",
            abs(wide["divider"] - half) <= 2,
            " divider=%s half=%s" % (wide["divider"], half),
        )

        page.set_viewport_size({"width": 900, "height": 700})
        page.wait_for_timeout(300)
        back = page.evaluate(PROBE)
        check(
            "going narrow again drops the desktop split",
            back["narrow"] and not back["leftInline"] and not back["rightInline"],
            " %r" % (back,),
        )

        # The remembered split still wins on desktop.
        page.evaluate("() => localStorage.setItem('textWidth', 60)")
        page.set_viewport_size({"width": 1400, "height": 900})
        page.wait_for_timeout(300)
        stored = page.evaluate(PROBE)
        check(
            "the reader's stored 60/40 split is honoured",
            abs(stored["left"] - stored["container"] * 0.6) <= 2
            and abs(stored["right"] - stored["container"] * 0.4) <= 2,
            " %r" % (stored,),
        )
        check(
            "the dictionary pane never collapses to zero width",
            stored["right"] > 0 and wide["right"] > 0,
            " right=%s/%s" % (wide["right"], stored["right"]),
        )

        # A bogus remembered split must not be able to collapse a pane.
        page.evaluate("() => localStorage.setItem('textWidth', 100)")
        page.set_viewport_size({"width": 900, "height": 700})
        page.wait_for_timeout(200)
        page.set_viewport_size({"width": 1400, "height": 900})
        page.wait_for_timeout(300)
        clamped = page.evaluate(PROBE)
        check(
            "a bogus remembered 100% is clamped to a usable split",
            clamped["right"] > 0 and clamped["left"] < clamped["container"],
            " %r" % (clamped,),
        )

        page.evaluate("() => localStorage.removeItem('textWidth')")
        browser.close()

    failed = [label for label, ok in RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(RESULTS), len(failed)))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
