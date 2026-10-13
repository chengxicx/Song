"""
Drive the furigana harness and assert the reading-pane annotation rules.

Checks, against the real lute-furigana.js:

  1. the initial load's three entry points (DOMContentLoaded,
     htmx:afterSwap, page_content's explicit call) fire one request per
     paragraph, not one per entry point -- the production regression let
     each pass re-read an already-wrapped span and wrap it again, so the
     reading was appended to the base text ("音" became "音おと") and the
     ruby sat above the wrong (longer) text;
  2. a kanji word gets exactly one ruby whose base is the word itself;
  3. a pure-katakana word (カサカサ) and a pure-kana word get no ruby --
     furigana is for kanji;
  4. a mixed word (聞こえ) keeps its reading;
  5. calling annotate again over annotated content is a no-op;
  6. a failed request releases its claim, so a later pass retries and
     succeeds.
"""

import pathlib

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
HARNESS = (HERE / "furigana_harness.html").resolve()
RESULTS = []


def check(label, cond, detail=""):
    print("  %s %s%s" % ("PASS" if cond else "FAIL", label, detail))
    RESULTS.append((label, cond))


def word_state(page, text):
    return page.evaluate(
        """(t) => {
          const spans = Array.from(document.querySelectorAll('#thetext span.word'));
          const sp = spans.find(s => s.getAttribute('data-text') === t);
          if (!sp) return null;
          const rubies = sp.querySelectorAll('ruby');
          const r = rubies[0];
          return {
            ruby_count: rubies.length,
            base: r ? r.firstChild.textContent : null,
            reading: r && r.querySelector('rt') ? r.querySelector('rt').textContent : null,
            span_text: sp.textContent,
            marked: sp.hasAttribute('data-furigana'),
          };
        }""",
        text,
    )


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 700})
        page.goto(HARNESS.as_uri())

        # --- 1. three back-to-back entry points, one request each ---
        # lute-furigana.js already annotated once on load (its readyState
        # branch), and that request is still in flight (the stub answers
        # after 150ms).  Fire the initial load's remaining entry points
        # NOW -- annotate (DOMContentLoaded), a real htmx:afterSwap
        # targeting #thetext, then annotate again (page_content's
        # explicit call) -- so every pass sees the same unmarked spans,
        # exactly like the real page.  If claiming did not happen
        # synchronously, each pass would add its own request per
        # paragraph and re-wrap the already-wrapped spans.
        page.evaluate(
            """() => {
              window.lute_furigana_annotate();
              document.getElementById('thetext').dispatchEvent(
                new CustomEvent('htmx:afterSwap', { bubbles: true })
              );
              window.lute_furigana_annotate();
            }"""
        )
        page.wait_for_timeout(400)
        calls = page.evaluate("window.__fetch_calls")
        check(
            "load + three entry points -> still one request per paragraph (2)",
            calls == 2,
            " fetch_calls=%r" % (calls,),
        )

        # --- 2. kanji words: exactly one ruby, clean base ---
        for text, reading in (("音", "おと"), ("夜", "よる")):
            st = word_state(page, text)
            check(
                "%s gets one ruby, base is the word itself" % text,
                st["ruby_count"] == 1 and st["base"] == text,
                " state=%r" % (st,),
            )
            check(
                "%s carries reading %s" % (text, reading),
                st["reading"] == reading,
                " state=%r" % (st,),
            )

        # --- 3. no duplication anywhere ---
        bad = page.evaluate(
            """() => Array.from(document.querySelectorAll('#thetext ruby'))
                 .filter(r => r.firstChild.textContent.includes(
                   r.querySelector('rt').textContent))
                 .map(r => r.firstChild.textContent)"""
        )
        check("no base text contains its own reading", bad == [], " bad=%r" % (bad,))

        # --- 4. pure-kana / pure-katakana words stay untouched ---
        for text in ("カサカサ", "わたし"):
            st = word_state(page, text)
            check(
                "%s gets no ruby (no kanji)" % text,
                st["ruby_count"] == 0 and st["span_text"] == text,
                " state=%r" % (st,),
            )

        # --- 5. a mixed word keeps its reading ---
        st = word_state(page, "聞こえ")
        check(
            "聞こえ keeps its reading (has kanji)",
            st["ruby_count"] == 1 and st["reading"] == "きこえ",
            " state=%r" % (st,),
        )

        # --- 6. re-annotating annotated content is a no-op ---
        page.evaluate("window.__fetch_calls = 0; window.lute_furigana_annotate();")
        page.wait_for_timeout(200)
        calls = page.evaluate("window.__fetch_calls")
        check("re-annotate over done content fires nothing", calls == 0,
              " fetch_calls=%r" % (calls,))

        # --- 7. a failed request releases its claim and retries ---
        page.evaluate(
            """() => {
              // Fresh spans, as a page swap would deliver.
              const t = document.getElementById('thetext');
              t.innerHTML =
                '<p><span class="textsentence" id="sent_1">' +
                '<span class="textitem click word" data-paragraph-id="1"' +
                ' data-sentence-id="1" data-text="夜">夜</span>' +
                '</span></p>';
              window.__fetch_fail = true;
              window.__fetch_calls = 0;
              window.lute_furigana_annotate();
            }"""
        )
        page.wait_for_timeout(300)
        st = word_state(page, "夜")
        check("failed request leaves the word unannotated",
              st["ruby_count"] == 0 and not st["marked"], " state=%r" % (st,))
        page.evaluate("window.__fetch_fail = false; window.lute_furigana_annotate();")
        page.wait_for_timeout(300)
        st = word_state(page, "夜")
        check(
            "a later pass retries and annotates",
            st["ruby_count"] == 1 and st["base"] == "夜" and st["reading"] == "よる",
            " state=%r" % (st,),
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
