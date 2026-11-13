"""
Drive the furigana harness and assert the reading-pane annotation rules.

Checks, against the real lute-furigana.js:

  1. the initial load's three entry points (DOMContentLoaded,
     htmx:afterSwap, page_content's explicit call) fire one batched
     request, not one per entry point or per paragraph -- the production
     regression let each pass re-read an already-wrapped span and wrap it
     again, so the reading was appended to the base text ("音" became
     "音おと") and the ruby sat above the wrong (longer) text;
     the request's full_text is the paragraph's real text, punctuation
     included (joining the word tokens turned 李：今日は into 李今日は
     and made Sudachi read 今日 as こんにち);
  2. a kanji word gets exactly one ruby whose base is the word itself;
  3. a pure-katakana word (カサカサ) and a pure-kana word get no ruby --
     furigana is for kanji;
  4. a mixed word (聞こえ) keeps its reading;
  5. calling annotate again over annotated content is a no-op;
  6. a failed request releases its claim, so a later pass retries and
     succeeds;
  7. re-annotating the same tokens after a re-render (a term save) is
     answered from the readings cache with no fetch, and the ruby lands
     in a microtask of the calling task -- before any paint, so it does
     not blink off;
  8. a changed token set is a cache miss and fetches again;
  9. invalidating the saved term drops only the batches holding it, so
     the edited ruby refetches and updates while other paragraphs keep
     their cached ruby; an unknown term text clears everything.
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

        # --- 1. three back-to-back entry points, one batched request ---
        # lute-furigana.js already annotated once on load (its readyState
        # branch), and that request is still in flight (the stub answers
        # after 150ms).  Fire the initial load's remaining entry points
        # NOW -- annotate (DOMContentLoaded), a real htmx:afterSwap
        # targeting #thetext, then annotate again (page_content's
        # explicit call) -- so every pass sees the same unmarked spans,
        # exactly like the real page.  If claiming did not happen
        # synchronously, each pass would add its own requests and re-wrap
        # the already-wrapped spans.
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
            "load + three entry points -> one batched request",
            calls == 1,
            " fetch_calls=%r" % (calls,),
        )

        # --- 1b. the request carries the paragraph's real text ---
        # The spans alone are not the text the parser saw: punctuation
        # between them has to survive, or 李：今日は is sent as 李今日は
        # and 今日 is read こんにち instead of きょう.
        requests = page.evaluate("window.__requests")
        para1 = requests[0]["paragraphs"][0]
        check(
            "the readings request keeps the paragraph punctuation",
            para1["full_text"] == "音、カサカサが聞こえ"
            and para1["tokens"] == ["音", "カサカサ", "聞こえ"],
            " paragraph=%r" % (para1,),
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
        check(
            "re-annotate over done content fires nothing",
            calls == 0,
            " fetch_calls=%r" % (calls,),
        )

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
        check(
            "failed request leaves the word unannotated",
            st["ruby_count"] == 0 and not st["marked"],
            " state=%r" % (st,),
        )
        page.evaluate("window.__fetch_fail = false; window.lute_furigana_annotate();")
        page.wait_for_timeout(300)
        st = word_state(page, "夜")
        check(
            "a later pass retries and annotates",
            st["ruby_count"] == 1 and st["base"] == "夜" and st["reading"] == "よる",
            " state=%r" % (st,),
        )

        # --- 8. term-save re-render: cache hit, ruby lands in-task ---
        # A term save re-renders the same page and re-annotates the same
        # tokens (a status change does not re-tokenize).  The readings
        # must come from the cache -- no fetch -- and land synchronously
        # with the swap: a resolved promise's then runs in a microtask,
        # before the browser can paint, so the ruby never blinks off.
        # The re-rendered paragraph replicates the initial load's
        # paragraph-1 payload exactly (same tokens, same order), which
        # is what makes this a cache hit.
        result = page.evaluate(
            """() => {
              const t = document.getElementById('thetext');
              t.innerHTML =
                '<p><span class="textsentence" id="sent_1">' +
                '<span class="textitem click word" data-paragraph-id="1" data-sentence-id="1" data-text="音">音</span>' +
                '<span class="textitem">が</span>' +
                '<span class="textitem click word" data-paragraph-id="1" data-sentence-id="1" data-text="カサカサ">カサカサ</span>' +
                '<span class="textitem">ました</span>' +
                '<span class="textitem click word" data-paragraph-id="1" data-sentence-id="1" data-text="聞こえ">聞こえ</span>' +
                '</span></p>';
              window.__fetch_calls = 0;
              window.lute_furigana_annotate();
              return Promise.resolve().then(() => {
                const sp = Array.from(t.querySelectorAll('span.word'))
                  .find(s => s.getAttribute('data-text') === '音');
                const ruby = sp && sp.querySelector('ruby');
                const rt = ruby && ruby.querySelector('rt');
                return {
                  fetch_calls: window.__fetch_calls,
                  ruby_in_microtask: !!(rt && rt.textContent === 'おと'
                                        && ruby.firstChild.textContent === '音'),
                  marked: !!(sp && sp.hasAttribute('data-furigana')),
                };
              });
            }"""
        )
        check(
            "a term-save re-render annotates from the cache without a fetch",
            result["fetch_calls"] == 0
            and result["ruby_in_microtask"]
            and result["marked"],
            " result=%r" % (result,),
        )

        # --- 9. a changed token set is a cache miss and does fetch ---
        # Merging a multi-word term changes the tokens; the cache must
        # not serve the old paragraph's payload for the new split.
        result = page.evaluate(
            """() => {
              const t = document.getElementById('thetext');
              t.innerHTML =
                '<p><span class="textsentence" id="sent_1">' +
                '<span class="textitem click word" data-paragraph-id="1" data-sentence-id="1" data-text="音">音</span>' +
                '</span></p>';
              window.__fetch_calls = 0;
              window.lute_furigana_annotate();
              return Promise.resolve().then(() => window.__fetch_calls);
            }"""
        )
        page.wait_for_timeout(300)
        check(
            "a changed token set is a cache miss (fetches again)",
            result == 1,
            " fetch_calls=%r" % (result,),
        )

        # --- 10. a term save invalidates only the batches holding it ---
        # The saved word's pronunciation changed: its paragraph must
        # refetch, while the other paragraph keeps its cached ruby (no
        # fetch, and ruby present in the same microtask).
        page.evaluate("window.__READINGS['音'] = 'おとの';")
        result = page.evaluate(
            """() => {
              const t = document.getElementById('thetext');
              t.innerHTML =
                '<p><span class="textsentence" id="sent_1">' +
                '<span class="textitem click word" data-paragraph-id="1" data-sentence-id="1" data-text="音">音</span>' +
                '<span class="textitem">が</span>' +
                '<span class="textitem click word" data-paragraph-id="1" data-sentence-id="1" data-text="カサカサ">カサカサ</span>' +
                '<span class="textitem">ました</span>' +
                '<span class="textitem click word" data-paragraph-id="1" data-sentence-id="1" data-text="聞こえ">聞こえ</span>' +
                '</span></p>' +
                '<p><span class="textsentence" id="sent_2">' +
                '<span class="textitem click word" data-paragraph-id="2" data-sentence-id="2" data-text="夜">夜</span>' +
                '<span class="textitem click word" data-paragraph-id="2" data-sentence-id="2" data-text="わたし">わたし</span>' +
                '</span></p>';
              window.lute_furigana_invalidate_term('音');
              window.__fetch_calls = 0;
              window.lute_furigana_annotate();
              return Promise.resolve().then(() => {
                const night = Array.from(t.querySelectorAll('span.word'))
                  .find(s => s.getAttribute('data-text') === '夜');
                const rb = night && night.querySelector('ruby');
                return {
                  fetch_calls: window.__fetch_calls,
                  night_ruby_kept: !!(rb && rb.querySelector('rt').textContent === 'よる'),
                };
              });
            }"""
        )
        page.wait_for_timeout(300)
        after = page.evaluate(
            """() => {
              const song = Array.from(
                document.querySelectorAll('#thetext span.word')
              ).find(s => s.getAttribute('data-text') === '音');
              const ruby = song && song.querySelector('ruby');
              return {
                fetch_calls: window.__fetch_calls,
                reading: ruby && ruby.querySelector('rt')
                  ? ruby.querySelector('rt').textContent : null,
              };
            }"""
        )
        check(
            "a saved term refetches its paragraph only",
            result["fetch_calls"] == 1
            and result["night_ruby_kept"]
            and after["fetch_calls"] == 1
            and after["reading"] == "おとの",
            " result=%r after=%r" % (result, after),
        )

        # --- 11. without the term text, the invalidation clears all ---
        result = page.evaluate(
            """() => {
              const t = document.getElementById('thetext');
              t.innerHTML =
                '<p><span class="textsentence" id="sent_1">' +
                '<span class="textitem click word" data-paragraph-id="1" data-sentence-id="1" data-text="音">音</span>' +
                '</span></p>' +
                '<p><span class="textsentence" id="sent_2">' +
                '<span class="textitem click word" data-paragraph-id="2" data-sentence-id="2" data-text="夜">夜</span>' +
                '</span></p>';
              window.lute_furigana_invalidate_term(null);
              window.__fetch_calls = 0;
              window.lute_furigana_annotate();
              return Promise.resolve().then(() => window.__fetch_calls);
            }"""
        )
        page.wait_for_timeout(300)
        after = page.evaluate("window.__fetch_calls")
        check(
            "an unknown term text clears every cached batch",
            result == 1 and after == 1,
            " in_task=%r after=%r" % (result, after),
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
