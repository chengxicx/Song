"""
Browser-level checks for reader-mode EPUB: the book's stylesheet must
stay inside the chapter, and the existing reading-pane controls must
keep working on a chapter that brings its own typography.

The harness is generated rather than checked in, because two of its
inputs are computed: the scoped stylesheet comes from the real
lute/book/epub_css.py, and the chapter markup from the same sanitiser
the import uses.  Everything else is the real thing -- lute/static/css/
styles.css, lute/static/js/lute-epub-reader.js and
lute/static/js/text-options.js are loaded from disk, not stubbed.

Checks:

  1. the book's CSS applies to the chapter (text-indent from a
     `body.novel p` rule) and NOT to a paragraph outside it;
  2. `position: fixed` became `absolute`, so a decoration stays inside
     the chapter instead of escaping to the viewport;
  3. oversized images are clamped to the reading measure;
  4. a book's `color: #000` on a block that paints no background is
     dropped at import (the default theme is dark, so keeping it would
     print the chapter black on near-black), while a block that paints
     its own background keeps both;
  5. A+/A- scale the whole chapter container (so the whitespace gaps
     between word spans grow too, which stamping each span cannot do)
     and keep the book's relative sizes;
  6. two-column is refused for a continuously scrolling chapter;
  7. word spans live in the parent document, so the reading page's
     delegated click / drag handlers see them with no cross-document
     bridge;
  8. sentence wrappers stay inline -- a block-level wrapper would
     break every paragraph's line flow;
  9. every word is hit-testable where it is painted, and words are in
     reading order in the DOM -- a drag-select walks the spans the
     pointer lands on (the word spans opt out of native selection), so
     a decoration painted over the text would silently make a range
     unselectable;
  10. a theme's absolute paragraph size does not freeze the chapter's
     prose.  Default / Default-Light carry `div#thetext p { font-size:
     18px }` for the text page, and a pinned size there breaks the
     inheritance A+/A- rely on: the container grew, the words did not.
     The harness carries that rule verbatim, so the scope check below
     (text-indent 24px = 1.5em against the inherited 16px) and the
     A+ check both go red without the fix in styles.css.

Run:  ./venv/bin/python tests/js/run_epub_layout_harness.py
"""

import pathlib
import sys
import tempfile

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent.parent
STATIC = ROOT / "lute" / "static"

sys.path.insert(0, str(ROOT))

from lute.book.epub_css import scope_css  # noqa: E402

RESULTS = []

CHAPTER = """<div class="epub-body novel">
<h2>Chapter one</h2>
<p>Hello world, again.</p>
<p>Second paragraph with <em>emphasis</em> in it.</p>
<p><img src="pic.png" width="2000" height="2000" alt=""/></p>
<div class="fixed-deco"></div>
</div>"""

BOOK_CSS = """
html body { margin: 0; font-family: Georgia, serif; }
body.novel p { text-indent: 1.5em; }
p { background: #eeeeee; color: #000; }
.fixed-deco { position: fixed; bottom: 0; right: 0; width: 20px; height: 20px; }
h2 { font-size: 32px; color: #000; }
"""

def chapter_text():
    "The chapter's rendered text -- what the token stream has to match."
    return BeautifulSoup(CHAPTER, "html.parser").get_text("")


def build_tokens():
    """Word / non-word runs over the chapter's own text.

    The front end aligns the token stream against the DOM character by
    character, so the tokens have to be *exactly* the rendered text,
    split into runs -- which words the runs contain does not matter to
    the layout checks, so anything alphanumeric counts as a word.
    """
    text = chapter_text()
    tokens = []
    sentence = 0
    for ch in text:
        is_word = ch.isalnum() or ch == "_"
        if is_word and tokens and tokens[-1][1] and tokens[-1][0][-1].isalnum():
            tokens[-1][0] += ch
            continue
        if is_word:
            tokens.append([ch, len(tokens) + 100, 0, sentence, 0])
            continue
        tokens.append([ch, 0, -1, sentence, 0])
        if ch in ".!?":
            sentence += 1
    return tokens


def build_html(theme_css_uri, orig_css_uri):
    """The generated harness page."""
    css = scope_css(BOOK_CSS, "", "/static/epub/x")
    tokens = build_tokens()
    # The token entries are [text, wo_id, status, sentence, paragraph]
    # as plain arrays; json keeps them compact for the embed.
    import json

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="{(STATIC / 'css' / 'styles.css').as_uri()}">
<style>body {{ margin: 0; background: #111; color: #e6e6e6; }}
/* The reading page's theme, verbatim in shape: Default.css:420-428 (and
   Default-Light.css:398-406) pin an absolute size on the paragraphs.
   Only that declaration is reproduced -- loading a whole theme would
   repaint the page and invalidate the colour checks. */
div#thetext p {{ font-size: 18px; }}
/* the scoped book stylesheet, generated by lute/book/epub_css.py */
{css}
/* a stray paragraph outside the chapter, to prove the scope holds */
#outside p {{ text-indent: 0; }}
</style>
</head>
<body>
<div style="height: 150px">a header, so the chapter does not start at the viewport top</div>
<div id="read_pane_left">
  <div id="thetext" class="epub-text-container">
    <div id="epub-chapter"
         data-epub-css="{theme_css_uri}"
         data-epub-orig-css="{orig_css_uri}">{CHAPTER}</div>
    <script type="application/json" id="lute-epub-tokens">{json.dumps(tokens)}</script>
  </div>
  <div id="outside"><p>outside paragraph</p></div>
</div>

<button class="font-plus"></button>
<button class="font-minus"></button>
<button class="lh-plus"></button>
<button class="lh-minus"></button>
<button class="width-plus"></button>
<button class="width-minus"></button>
<button class="column-one"></button>
<button class="column-two"></button>
<button class="manga-zoom-in"></button>
<button class="manga-zoom-out"></button>
<button class="manga-zoom-fit"></button>
<button class="epub-look-original"></button>
<button class="epub-look-night"></button>
<button class="epub-look-theme"></button>

<script>
// Minimal stand-ins for the globals resize.js provides to
// text-options.js.  Everything else in that file is the real code.
const theText = document.getElementById("thetext");
const readPaneLeft = document.getElementById("read_pane_left");
const readPaneRight = {{
  style: {{}},
  getBoundingClientRect: () => ({{width: 100}}),
}};
const widthDefault = 70;
function getFromLocalStorage(key, dflt) {{
  const v = localStorage.getItem(key);
  return v === null ? dflt : parseFloat(v);
}}
function clamp(v, lo, hi) {{ return Math.min(Math.max(v, lo), hi); }}
function getReadPaneWidthRatio() {{ return 0.35; }}
function setMangaZoom() {{}}
function resetMangaZoom() {{}}
window._splitToScreens = function () {{ window.__splitCalls = (window.__splitCalls || 0) + 1; }};
// The look tests start from the theme look so the pre-existing colour
// checks keep describing the theme-following variant.
localStorage.setItem("epubLookMode", "theme");
</script>
<script src="{(STATIC / 'js' / 'lute-epub-reader.js').as_uri()}"></script>
<script>
  window.EpubReader.mount(document.getElementById("epub-chapter"));
</script>
<script src="{(STATIC / 'js' / 'text-options.js').as_uri()}"></script>
<script>
// A delegated handler on the reading pane, exactly like the reader
// page's own: if the spans were in another document this would never
// fire.
window.__clicks = [];
theText.addEventListener("click", (e) => {{
  const w = e.target.closest && e.target.closest("span.word");
  window.__clicks.push(w ? w.textContent : null);
}});
</script>
</body></html>
"""


def check(label, cond, detail=""):
    print("  %s %s%s" % ("PASS" if cond else "FAIL", label, detail))
    RESULTS.append((label, cond))


def main():
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="epub-layout-"))
    # Both scoped variants as real files, like the import writes them:
    # lute.css follows the theme (unpainted colours dropped), the
    # original-look variant keeps every colour.  The look tests switch
    # between the two by swapping the <link>'s href, exactly like
    # lute-epub-reader.js does on the reading page.
    theme_css = tmpdir / "lute.css"
    theme_css.write_text(scope_css(BOOK_CSS, "", "/static/epub/x"), encoding="utf-8")
    orig_css = tmpdir / "lute-orig.css"
    orig_css.write_text(
        scope_css(BOOK_CSS, "", "/static/epub/x", keep_colors=True), encoding="utf-8"
    )
    harness = tmpdir / "harness.html"
    harness.write_text(
        build_html(theme_css.as_uri(), orig_css.as_uri()), encoding="utf-8"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 700})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(harness.as_uri())
        page.wait_for_function(
            "() => document.querySelectorAll('span.word').length > 0", timeout=5000
        )

        check("no page errors", not errors, " %s" % errors[:1])

        state = page.evaluate(
            """() => {
              const body = document.querySelector('.epub-body');
              const inP = body.querySelector('p');
              const outP = document.querySelector('#outside p');
              const img = body.querySelector('img');
              const h2 = body.querySelector('h2');
              const deco = body.querySelector('.fixed-deco');
              const cs = (el) => window.getComputedStyle(el);
              const words = Array.from(body.querySelectorAll('span.word'));
              const sent = body.querySelector('span.textsentence');
              return {
                inIndent: cs(inP).textIndent,
                outIndent: cs(outP).textIndent,
                inBackground: cs(inP).backgroundColor,
                imgWidth: img.getBoundingClientRect().width,
                bodyWidth: body.getBoundingClientRect().width,
                decoBottom: deco.getBoundingClientRect().bottom,
                bodyBottom: body.getBoundingClientRect().bottom,
                viewportHeight: window.innerHeight,
                h2Size: parseFloat(cs(h2).fontSize),
                bodySize: parseFloat(cs(body).fontSize),
                pSize: parseFloat(cs(inP).fontSize),
                wordCount: words.length,
                sentDisplay: sent ? cs(sent).display : 'none',
                sameDocument: words.every(w => w.ownerDocument === document),
                wordIds: words.map(w => w.getAttribute('data-wid')),
                h2WordSize: (() => {
                  const w = h2.querySelector('span.word');
                  return w ? parseFloat(cs(w).fontSize) : -1;
                })(),
                h2Color: cs(h2).color,
                pageColor: cs(document.body).color,
                spaceWidth: (() => {
                  const walker = document.createTreeWalker(
                    body.querySelector('p'), NodeFilter.SHOW_TEXT);
                  while (walker.nextNode()) {
                    const n = walker.currentNode;
                    if (/^ +$/.test(n.data)) {
                      const r = document.createRange();
                      r.selectNodeContents(n);
                      return r.getBoundingClientRect().width;
                    }
                  }
                  return -1;
                })(),
              };
            }"""
        )

        # 1. scope
        check(
            "book CSS reaches the chapter",
            state["inIndent"] == "24px",
            " text-indent %s" % state["inIndent"],
        )
        check(
            "book CSS does not leak outside the chapter",
            state["outIndent"] == "0px",
            " text-indent %s" % state["outIndent"],
        )
        check(
            "bare element rules are scoped too",
            state["inBackground"] == "rgb(238, 238, 238)",
            " bg %s" % state["inBackground"],
        )

        # 2. fixed -> absolute
        # Were it still `fixed`, the decoration would sit at the
        # viewport's bottom edge instead of the chapter's.
        check(
            "fixed positioning is pinned to the chapter",
            abs(state["decoBottom"] - state["bodyBottom"]) < 2
            and abs(state["decoBottom"] - state["viewportHeight"]) > 2,
            " deco %.0f / body %.0f / viewport %.0f"
            % (state["decoBottom"], state["bodyBottom"], state["viewportHeight"]),
        )

        # 3. images
        check(
            "oversized images are clamped",
            state["imgWidth"] <= state["bodyWidth"] + 1,
            " img %.0f / body %.0f" % (state["imgWidth"], state["bodyWidth"]),
        )

        # 6. spans are in the parent document
        check("word spans were injected", state["wordCount"] > 0,
              " %d" % state["wordCount"])
        check("word spans live in the reading page", state["sameDocument"])

        # The book's own type sizes must survive the injected spans.
        check(
            "a heading word keeps the heading size",
            abs(state["h2WordSize"] - state["h2Size"]) < 0.5,
            " word %.0f / heading %.0f" % (state["h2WordSize"], state["h2Size"]),
        )
        check(
            "the spaces between words still take room",
            state["spaceWidth"] > 2,
            " space %.1fpx" % state["spaceWidth"],
        )

        # The default theme is dark; a book's `color: #000` on a block
        # that paints no background would be black on near-black.
        check(
            "a book colour on an unpainted block is dropped",
            state["h2Color"] == state["pageColor"] == "rgb(230, 230, 230)",
            " h2 %s / page %s" % (state["h2Color"], state["pageColor"]),
        )
        check(
            "a block that paints its background keeps its colour",
            state["inBackground"] == "rgb(238, 238, 238)"
            and page.evaluate(
                "() => getComputedStyle(document.querySelector('.epub-body p')).color"
            )
            == "rgb(0, 0, 0)",
            " bg %s" % state["inBackground"],
        )

        # 7. sentence wrappers stay inline
        check("sentence wrappers stay inline", state["sentDisplay"] == "inline",
              " %s" % state["sentDisplay"])

        # 4. font size
        base = state["bodySize"]
        page.click(".font-plus")
        page.click(".font-plus")
        grown = page.evaluate(
            """() => {
              const body = document.querySelector('.epub-body');
              return {
                body: parseFloat(getComputedStyle(body).fontSize),
                h2: parseFloat(getComputedStyle(body.querySelector('h2')).fontSize),
                p: parseFloat(getComputedStyle(body.querySelector('p')).fontSize),
                span: parseFloat(getComputedStyle(
                  body.querySelector('p span.word')).fontSize),
              };
            }"""
        )
        check(
            "A+ scales the chapter container",
            grown["body"] > base,
            " %.0f -> %.0f" % (base, grown["body"]),
        )
        check(
            "word spans inherit the new size",
            abs(grown["span"] - grown["body"]) < 0.5,
            " span %.1f / body %.1f" % (grown["span"], grown["body"]),
        )
        # The theme's `div#thetext p { font-size: 18px }` must not win
        # here: the paragraph is the book's, and the control resizes it
        # by scaling .epub-body.  Compare the two *deltas*, not the two
        # sizes: after two steps the inherited paragraph lands on 18px,
        # which is exactly the theme's pinned value -- a size comparison
        # would pass with the bug still in place.
        check(
            "the theme's pinned paragraph size yields to A+",
            abs((grown["p"] - state["pSize"]) - (grown["body"] - base)) < 0.5,
            " p %.1f -> %.1f / body %.1f -> %.1f"
            % (state["pSize"], grown["p"], base, grown["body"]),
        )
        check(
            "the book's relative sizes survive",
            grown["h2"] > grown["body"] * 1.5,
            " h2 %.0f / body %.0f" % (grown["h2"], grown["body"]),
        )

        # 5. column count
        page.click(".column-two")
        cols = page.evaluate(
            "() => getComputedStyle(document.getElementById('thetext')).columnCount"
        )
        check("two-column is refused for a scrolling chapter", cols == "auto",
              " column-count %s" % cols)

        # 6b. delegated click reaches the word span
        page.click("span.word >> nth=0")
        clicked = page.evaluate("() => window.__clicks")
        check(
            "a click on a word bubbles to the reading pane",
            clicked and clicked[0] == "Chapter",
            " %s" % (clicked,),
        )

        # 8. multiword selection.  Lute does not use window.getSelection
        # here (the word spans opt out of native selection); a drag is
        # mousedown/mousemove over the spans the pointer lands on, so
        # what matters is that every word is hit-testable and that the
        # spans between two endpoints are contiguous in data-order.
        hits = page.evaluate(
            """() => {
              const words = Array.from(
                document.querySelectorAll('.epub-body span.word'));
              const missed = [];
              const blocked = [];
              for (const w of words) {
                const r = w.getBoundingClientRect();
                const hit = document.elementFromPoint(
                  r.left + Math.min(r.width / 2, 3), r.top + r.height / 2);
                if (!hit) { blocked.push(w.textContent); continue; }
                const span = hit.closest && hit.closest('span.word');
                if (span !== w) missed.push(w.textContent + '<-' + hit.className);
              }
              const orders = words.map(w => Number(w.getAttribute('data-order')));
              let contiguous = true;
              for (let i = 1; i < orders.length; i++) {
                if (orders[i] <= orders[i - 1]) contiguous = false;
              }
              return {missed, blocked, contiguous, count: words.length};
            }"""
        )
        check(
            "every word is hit-testable where it is painted",
            not hits["missed"] and not hits["blocked"],
            " %s" % (hits["missed"] or hits["blocked"]),
        )
        check(
            "words are in reading order in the DOM",
            hits["contiguous"],
            " %d words" % hits["count"],
        )

        # 10. look modes.  The reading menu switches between the
        # theme-following stylesheet (colours dropped) and the
        # keep-colours one on a paper surface; night additionally
        # inverts the chapter and un-inverts its images.
        def look_state():
            return page.evaluate(
                """() => {
                  const thetext = document.getElementById('thetext');
                  const link = document.getElementById('lute-epub-stylesheet');
                  const chapter = document.getElementById('epub-chapter');
                  const body = chapter.querySelector('.epub-body');
                  const h2 = body.querySelector('h2');
                  const img = body.querySelector('img');
                  const cs = (el) => window.getComputedStyle(el);
                  return {
                    original: thetext.classList.contains('epub-look-original'),
                    night: thetext.classList.contains('epub-look-night'),
                    href: link ? link.getAttribute('href') : null,
                    h2Color: cs(h2).color,
                    paperBg: cs(chapter).backgroundColor,
                    bodyFilter: cs(body).filter,
                    imgFilter: cs(img).filter,
                    stored: localStorage.getItem('epubLookMode'),
                  };
                }"""
            )

        page.evaluate("() => window.EpubReader.setLook('original')")
        s = look_state()
        check(
            "original look: paper surface + keep-colours stylesheet",
            s["original"] and not s["night"]
            and (s["href"] or "").endswith("lute-orig.css")
            and s["paperBg"] == "rgb(255, 255, 255)"
            and s["h2Color"] == "rgb(0, 0, 0)",
            " href=%s bg=%s h2=%s" % (s["href"], s["paperBg"], s["h2Color"]),
        )

        page.evaluate("() => window.EpubReader.setLook('night')")
        s = look_state()
        check(
            "night look: dark paper, chapter inverted, images restored",
            s["night"] and not s["original"]
            and s["paperBg"] == "rgb(22, 22, 29)"
            and "invert" in s["bodyFilter"]
            and s["imgFilter"] != "none"
            and "invert" in s["imgFilter"],
            " bg=%s body=%s img=%s" % (s["paperBg"], s["bodyFilter"], s["imgFilter"]),
        )

        page.evaluate("() => window.EpubReader.setLook('theme')")
        s = look_state()
        check(
            "theme look: no classes, dropped colours, theme stylesheet",
            not s["original"] and not s["night"]
            and (s["href"] or "").endswith("lute.css")
            and s["h2Color"] == "rgb(230, 230, 230)"
            and s["bodyFilter"] == "none"
            and s["stored"] == "theme",
            " href=%s h2=%s" % (s["href"], s["h2Color"]),
        )

        # 11. zoom.  The reading page applies the stored zoom with the
        # CSS `zoom` property on the chapter container (index.html
        # _applyEpubZoom, not loadable here).  Semantics this pins
        # down: type scales exactly, and zoom-out shrinks images
        # proportionally -- while zoom-in keeps images clamped to the
        # reading column (max-width: 100% resolves against the local,
        # zoom-divided width), which is the reflowable-book behaviour:
        # the column widens to the pane, the images fill the column.
        # A fixed-layout page box has no such clamp and scales whole.
        zoom_effect = page.evaluate(
            """() => {
              const chapter = document.getElementById('epub-chapter');
              const body = chapter.querySelector('.epub-body');
              const img = body.querySelector('img');
              const word = body.querySelector('p span.word');
              const rect = (el) => el.getBoundingClientRect().width;
              const before = { img: rect(img), word: rect(word) };
              chapter.style.zoom = "0.8";
              const shrunk = { img: rect(img), word: rect(word) };
              chapter.style.zoom = "1.5";
              const grown = { img: rect(img), word: rect(word),
                              column: body.getBoundingClientRect().width };
              chapter.style.zoom = "";
              return {
                imgOut: shrunk.img / before.img,
                wordOut: shrunk.word / before.word,
                wordIn: grown.word / before.word,
                imgInWithinColumn: grown.img <= grown.column + 1,
              };
            }"""
        )
        check(
            "zoom out scales text and images together",
            abs(zoom_effect["imgOut"] - 0.8) < 0.05
            and abs(zoom_effect["wordOut"] - 0.8) < 0.05,
            " img x%.2f word x%.2f" % (zoom_effect["imgOut"], zoom_effect["wordOut"]),
        )
        check(
            "zoom in scales the type, images stay fitted to the column",
            abs(zoom_effect["wordIn"] - 1.5) < 0.05
            and zoom_effect["imgInWithinColumn"],
            " word x%.2f imgInColumn=%s"
            % (zoom_effect["wordIn"], zoom_effect["imgInWithinColumn"]),
        )

        browser.close()

    failed = [label for label, ok in RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
