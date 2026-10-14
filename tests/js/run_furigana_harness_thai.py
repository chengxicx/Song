"""
Drive the Thai-mode furigana harness.

Thai is not a Han script, so the Han-only rule in the default harness
would leave every Thai word bare -- yet Thai hides its pronunciation
just as completely as kanji do.  With LUTE_FURIGANA.script === "thai"
every Thai word must get a reading.

Checks, against the real lute-furigana.js:

  1. every Thai word span gets exactly one ruby whose base is the word;
  2. a non-Thai span (the "!") stays untouched;
  3. no duplication: no base text contains its own reading;
  4. the three back-to-back entry points still fire one request per
     paragraph (the in-flight claim works in thai mode too);
  5. a failed request releases its claim, so a later pass retries.
"""

import pathlib

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
HARNESS = (HERE / "furigana_harness_thai.html").resolve()
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
        # The load-time annotate already claimed the paragraphs; firing
        # the other two entry points while that request is in flight must
        # not double up.
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

        # --- 2. every Thai word gets exactly one ruby ---
        for text, reading in (
            ("สวัสดี", "sàwàtdi"),
            ("ครับ", "khráp"),
            ("บ้าน", "bân"),
            ("แมว", "maeo"),
        ):
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

        # --- 4. a non-Thai span stays untouched ---
        st = page.evaluate(
            """() => {
              const spans = Array.from(
                document.querySelectorAll('#thetext span.textitem'));
              const sp = spans.find(s => s.textContent === '!');
              return { rubies: sp.querySelectorAll('ruby').length,
                       text: sp.textContent };
            }"""
        )
        check(
            "'!' gets no ruby (no Thai)",
            st["rubies"] == 0 and st["text"] == "!",
            " state=%r" % (st,),
        )

        # --- 5. a failed request releases its claim and retries ---
        page.evaluate(
            """() => {
              const t = document.getElementById('thetext');
              t.innerHTML =
                '<p><span class="textsentence" id="sent_1">' +
                '<span class="textitem click word" data-paragraph-id="1"' +
                ' data-sentence-id="1" data-text="บ้าน">บ้าน</span>' +
                '</span></p>';
              window.__fetch_fail = true;
              window.__fetch_calls = 0;
              window.lute_furigana_annotate();
            }"""
        )
        page.wait_for_timeout(300)
        st = word_state(page, "บ้าน")
        check(
            "failed request leaves the word unannotated",
            st["ruby_count"] == 0 and not st["marked"],
            " state=%r" % (st,),
        )
        page.evaluate("window.__fetch_fail = false; window.lute_furigana_annotate();")
        page.wait_for_timeout(300)
        st = word_state(page, "บ้าน")
        check(
            "a later pass retries and annotates",
            st["ruby_count"] == 1 and st["base"] == "บ้าน" and st["reading"] == "bân",
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
