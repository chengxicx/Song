"""
Drive the reader-mode EPUB harness and assert the span-injection rules.

Checks, against the real static/js/lute-epub-reader.js:

  1. the chapter's text is preserved character for character (nothing
     dropped, duplicated or reordered) while every non-whitespace
     character ends up inside a span.textitem;
  2. word tokens become .word spans carrying data-wid / status /
     sentence / paragraph, and every data-order is unique;
  3. a token cut by an inline tag ("Hel<b>lo</b>") renders as two
     spans that are marked split pieces and keep the whole word for
     TTS -- so a drag cannot splice the halves into a new term;
  4. sentences are wrapped in .textsentence, including several
     sentences inside one paragraph;
  5. the language parser's character substitutions are applied to the
     chapter before aligning -- a book's ‘the show’ and "..." must line
     up with the tokenizer's 'the show' and "…" -- while a
     no-break-space or ideographic-space indent does not cost the
     chapter its spans;
  6. a token stream that does not match the markup leaves the chapter
     untouched -- substitutions undone, no spans -- reporting where the
     streams diverged.

Run:  ./venv/bin/python tests/js/run_epub_reader_harness.py
"""

import pathlib
import sys

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
HARNESS = (HERE / "epub_reader_harness.html").resolve()
RESULTS = []


def check(label, cond, detail=""):
    print("  %s %s%s" % ("PASS" if cond else "FAIL", label, detail))
    RESULTS.append((label, cond))


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 700})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(HARNESS.as_uri())

        state = page.evaluate(
            """() => {
              const root = document.getElementById('epub-chapter');
              const words = Array.from(root.querySelectorAll('span.word'));
              const items = Array.from(root.querySelectorAll('span.textitem'));
              const sentences = Array.from(root.querySelectorAll('.textsentence'));
              const orders = items.map(i => i.getAttribute('data-order'));
              return {
                text: root.textContent,
                original: window.__originalText,
                mismatch: root.getAttribute('data-epub-mismatch'),
                mounted: root.getAttribute('data-epub-mounted'),
                wordCount: words.length,
                itemCount: items.length,
                sentenceCount: sentences.length,
                sentenceTexts: sentences.map(s => s.textContent.trim()),
                uniqueOrders: new Set(orders).size === orders.length,
                itemTexts: items.map(i => i.textContent),
                wordsMissingWid: words.filter(w => !w.getAttribute('data-wid')).length,
                wordsMissingStatus: words.filter(
                  w => !(w.getAttribute('data-status-class') || '').startsWith('status')
                ).length,
                splitPieces: Array.from(root.querySelectorAll('[data-split-piece]')).map(p => ({
                  text: p.textContent,
                  tts: p.getAttribute('data-tts-text'),
                  wid: p.getAttribute('data-wid'),
                })),
                helloP1: (() => {
                  const w = words.find(x => x.getAttribute('data-wid') === '102');
                  return w ? {text: w.textContent, cls: w.className} : null;
                })(),
                sentenceIds: sentences.map(s => s.getAttribute('data-sentence-id')),
                stylesheet: (() => {
                  const links = document.querySelectorAll(
                    'link#lute-epub-stylesheet');
                  return {
                    count: links.length,
                    href: links.length ? links[0].getAttribute('href') : null,
                    inHead: links.length ? links[0].parentElement.tagName : null,
                  };
                })(),
              };
            }"""
        )

        check("no page errors", not errors, " %s" % errors[:1])
        check("chapter mounted", state["mounted"] == "1", " %s" % state["mounted"])
        check("no mismatch reported", state["mismatch"] is None, " %s" % state["mismatch"])
        # A <link> inside the swapped fragment is dropped by innerHTML,
        # so the book's stylesheet is added to <head> by the mount.
        check(
            "the chapter's stylesheet is added to <head>",
            state["stylesheet"]["count"] == 1
            and state["stylesheet"]["inHead"] == "HEAD"
            and state["stylesheet"]["href"] == "/static/epub/fixture/lute.css",
            " %s" % (state["stylesheet"],),
        )
        check(
            "chapter text preserved exactly",
            state["text"] == state["original"],
            " %r != %r" % (state["text"][:60], state["original"][:60]),
        )
        check("12 word spans (one word cut in two)", state["wordCount"] == 12, " %d" % state["wordCount"])
        check("18 textitem spans", state["itemCount"] == 18, " %d" % state["itemCount"])
        check("every span has a unique data-order", state["uniqueOrders"])
        check("all word spans carry data-wid", state["wordsMissingWid"] == 0,
              " %d missing" % state["wordsMissingWid"])
        check("all word spans carry a status class", state["wordsMissingStatus"] == 0,
              " %d missing" % state["wordsMissingStatus"])
        check(
            "first Hello is a normal word span",
            state["helloP1"] is not None
            and state["helloP1"]["text"] == "Hello"
            and "word102" in state["helloP1"]["cls"],
            " %s" % state["helloP1"],
        )

        pieces = state["splitPieces"]
        check("the cut word renders as two split pieces", len(pieces) == 2, " %d" % len(pieces))
        check(
            "split pieces keep the whole word for TTS",
            [p["tts"] for p in pieces] == ["Hello", "Hello"],
            " %s" % pieces,
        )
        check(
            "split pieces share the term id",
            all(p["wid"] == "108" for p in pieces),
            " %s" % pieces,
        )
        check(
            "split pieces carry their own halves",
            [p["text"] for p in pieces] == ["Hel", "lo"],
            " %s" % (pieces,),
        )

        check("six sentence wrappers", state["sentenceCount"] == 6,
              " %d" % state["sentenceCount"])
        check(
            "sentences hold their own text",
            state["sentenceTexts"]
            == ["Chapter", "Hello world, again.", "第二段落です。",
                "See Hello there.", "One.", "Two."],
            " %s" % (state["sentenceTexts"],),
        )

        subs = page.evaluate("() => window.__runSubstitutions()")
        check(
            "curly quotes and a tight ellipsis still align",
            subs["mounted"] == "1" and subs["mismatch"] is None,
            " mounted=%s mismatch=%s" % (subs["mounted"], subs["mismatch"]),
        )
        check(
            "the chapter shows the parser's characters",
            "'the show' was over…" in subs["text"],
            " %r" % subs["text"][:80],
        )
        check(
            "an indented paragraph keeps its indent on screen",
            "\u00a0\u00a0Star" in subs["text"] and "\u3000Indented" in subs["text"],
            " %r" % subs["text"][:80],
        )
        check(
            "words on both sides of a curly quote become spans",
            subs["words"] == ["the", "show", "was", "over", "Star", "Indented"],
            " %s" % (subs["words"],),
        )
        check(
            "punctuation around the words gets its own spans",
            subs["spans"] == 9,  # six words, two quotes, the ellipsis
            " %d" % subs["spans"],
        )

        mismatch = page.evaluate("() => window.__runMismatch()")
        check("mismatch is detected", mismatch["offset"] is not None, " %s" % mismatch)
        check("mismatch leaves no spans", mismatch["spans"] == 0, " %s" % mismatch)
        check("mismatch leaves the text intact", mismatch["textIntact"] is True)
        check(
            "mismatch undoes the substitutions too",
            mismatch["subsIntact"] is True,
            " %s" % mismatch,
        )

        browser.close()

    failed = [label for label, ok in RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
