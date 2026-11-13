/* Furigana above reading-page words.
 *
 * Enabled per language (Language > "Show furigana above words"), and only
 * for parsers that can supply readings -- Japanese (kana via Sudachi/
 * MeCab), Mandarin (pinyin), Cantonese (jyutping) and Thai (RTGS
 * romanization) via their plugins; the flag reaches us via LUTE_FURIGANA
 * in read/index.html.  Readings come from the same endpoint the shadowing
 * panel uses (/read/shadowing/readings), which pairs each token with the
 * contextual reading of the sentence it sits in (Japanese 一つ reads
 * ひとつ, not いち + つ).  Every Han-character-bearing word span gets
 * its display text wrapped in <ruby>text<rt>reading</rt></ruby>; words
 * without Han characters (kana, hangul, Latin text) are left alone --
 * they already show their own pronunciation.  Thai is not a Han script
 * but it hides its pronunciation just as completely, so with
 * LUTE_FURIGANA.script === "thai" every Thai word gets a reading
 * instead.
 */
(function () {
  "use strict";

  const CONFIG =
    typeof LUTE_FURIGANA !== "undefined"
      ? LUTE_FURIGANA
      : { enabled: false, langId: 0, script: "han" };

  // Spans claimed by an in-flight request.  Claiming is synchronous, at
  // collection time: annotate() has several entry points that can fire
  // back-to-back over the same fresh content (DOMContentLoaded, the
  // htmx:afterSwap listener, and page_content's explicit call), and
  // marking only after the fetch resolved let a second pass re-read an
  // already-wrapped span.  ruby flattens under textContent, so the
  // reading was appended to the base text ("夜" became "夜よる") and the
  // word was wrapped again.
  const in_flight = new Set();

  // Furigana exists to make an opaque script readable; a word without
  // kanji (聞こえ's kana is already visible, トントン is all katakana)
  // needs no reading above it.  Thai has no kanji but hides its
  // pronunciation just as thoroughly, so there every word counts.
  function has_kanji(text) {
    return /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/.test(text);
  }

  function has_thai(text) {
    return /[\u0e00-\u0e7f]/.test(text);
  }

  function needs_reading(text) {
    if (CONFIG.script === "thai") return has_thai(text);
    return has_kanji(text);
  }

  function wrap_ruby(sp, text, reading) {
    if (!reading || reading === text) return;
    if (!needs_reading(text)) return;
    if (sp.querySelector("ruby")) return;
    const ruby = document.createElement("ruby");
    ruby.appendChild(document.createTextNode(text));
    const rt = document.createElement("rt");
    rt.appendChild(document.createTextNode(reading));
    ruby.appendChild(rt);
    sp.textContent = "";
    sp.appendChild(ruby);
  }

  // Readings of already-annotated token batches, keyed by the exact
  // request payload.  A term save re-renders the same page and calls
  // annotate() over the same tokens; answering from the cache lets the
  // ruby land in the same task as the swap (a resolved promise's then
  // runs in a microtask, before the next paint) instead of the readings
  // blinking off for the re-fetch round-trip.  In-memory only: a reload
  // re-fetches, so a parser or settings change can never serve stale
  // readings across sessions.
  const reading_cache = new Map();
  const READING_CACHE_MAX = 128;

  // A term save can change the term's stored pronunciation, so batches
  // fetched before the save must not be replayed.  Only the batches
  // containing the saved term are dropped: an edit in one paragraph
  // doesn't make every paragraph on the page re-fetch (and blink).
  // Unknown term text clears everything, which is the safe fallback
  // for the save paths that don't post it.
  window.lute_furigana_invalidate_term = function (termText) {
    const wanted = (termText || "").replace(/\u200B/g, "").trim();
    if (!wanted) {
      reading_cache.clear();
      return;
    }
    Array.from(reading_cache.keys()).forEach((key) => {
      const body = key.slice(key.indexOf("\u0000") + 1);
      try {
        if (JSON.parse(body).includes(wanted)) reading_cache.delete(key);
      } catch (e) {
        reading_cache.delete(key);
      }
    });
  };

  // The paragraph's own text, punctuation included.  The word spans
  // alone are not the text the parser read: joining their tokens drops
  // the paragraph's commas, colons and full stops, so 李：今日は became
  // 李今日は and Sudachi read 今日 as こんにち even though the real
  // sentence reads きょう.  The range covers exactly this group's spans,
  // so the punctuation between them survives; it also works in EPUB
  // chapters, where the spans have no <p> to start from.
  function group_text(spans) {
    if (!spans.length) return "";
    const range = document.createRange();
    range.setStartBefore(spans[0]);
    range.setEndAfter(spans[spans.length - 1]);
    const fragment = range.cloneContents();
    return (window.lutePlainText(fragment) || "")
      .replace(/\u200B/g, "")
      .replace(/🔊/g, "")
      .trim();
  }

  // One paragraph's batch: its spans and the exact token list sent for
  // them, captured before any ruby is applied.  A flat token list is both
  // the request unit and the cache key (a term save invalidates only the
  // paragraphs that contain it, see lute_furigana_invalidate_term).
  function paragraph_group(spans) {
    // Capture the display text up front: once a span is wrapped, its
    // textContent includes the reading, and dataset.text (the parser's
    // canonical token) can drift from the display text after a term edit
    // re-renders it.
    const captured = spans.map((sp) => sp.textContent);
    const tokens = spans.map((sp) => sp.dataset.text || sp.textContent.trim());
    const full_text = group_text(spans);
    const key = `${CONFIG.langId}\u0000${JSON.stringify(tokens)}`;
    return { spans, captured, tokens, full_text, key };
  }

  function draw_group(group, data) {
    if (!data || !Array.isArray(data.tokens)) return;
    group.spans.forEach((sp, i) => {
      const entry = data.tokens[i];
      if (!entry || entry.reading == null) return;
      wrap_ruby(sp, group.captured[i], entry.reading);
      sp.dataset.furigana = "1";
    });
  }

  function release_group(group) {
    group.spans.forEach((sp) => in_flight.delete(sp));
  }

  function cache_group(group, pending) {
    // Cache successful responses only, so a failed batch stays
    // retriable; FIFO eviction keeps a long reading session bounded.
    pending.then((data) => {
      if (!data || !Array.isArray(data.tokens)) return;
      if (reading_cache.size >= READING_CACHE_MAX) {
        reading_cache.delete(reading_cache.keys().next().value);
      }
      reading_cache.set(group.key, pending);
    });
  }

  function annotate() {
    const textDiv = document.getElementById("thetext");
    if (!textDiv) return;
    // Manga/PDF panes overlay positioned word spans on a page image;
    // ruby there would break the layout and add nothing visible.
    if (
      textDiv.classList.contains("manga-text-container") ||
      textDiv.classList.contains("pdf-text-container")
    ) {
      return;
    }
    // The class flips the roomier line-height even before the readings
    // arrive, so the text doesn't jump when the ruby lands.
    textDiv.classList.add("furigana-on");
    const spans = Array.from(
      textDiv.querySelectorAll("span.word:not([data-furigana])")
    ).filter((sp) => !in_flight.has(sp));
    if (spans.length === 0) return;
    // Readings follow sentence context, so a paragraph is the unit we
    // request and cache.  Every paragraph still on the page goes out in
    // ONE request (each was its own POST before, and a page has several
    // paragraphs); the ruby for each paragraph is then drawn from its
    // own slice of the answer, so all of them land together instead of
    // popping in paragraph by paragraph.
    const byPid = new Map();
    spans.forEach((sp) => {
      in_flight.add(sp);
      const pid = sp.dataset.paragraphId || "0";
      if (!byPid.has(pid)) byPid.set(pid, []);
      byPid.get(pid).push(sp);
    });

    const misses = [];
    byPid.forEach((groupSpans) => {
      const group = paragraph_group(groupSpans);
      const cached = reading_cache.get(group.key);
      if (cached) {
        cached
          .then((data) => draw_group(group, data))
          .catch(() => {})
          .then(() => release_group(group));
        return;
      }
      misses.push(group);
    });
    if (misses.length === 0) return;

    const batch = fetch("/read/shadowing/readings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        language_id: CONFIG.langId,
        paragraphs: misses.map((g) => ({
          tokens: g.tokens,
          full_text: g.full_text,
        })),
      }),
    })
      .then((r) => (r.ok ? r.json() : null))
      // Failure releases every group unannotated and unmarked, so a later
      // pass retries them.
      .catch(() => null);

    misses.forEach((group, i) => {
      const one = batch.then((data) => {
        if (!data || !Array.isArray(data.paragraphs)) return null;
        return data.paragraphs[i] || null;
      });
      cache_group(group, one);
      one
        .then((data) => draw_group(group, data))
        .catch(() => {})
        .then(() => release_group(group));
    });
  }

  // Called by the page_content fragment after every swap (read/
  // page_content.html), and by the listener below as a safety net for
  // the reader's own initial load.  Idempotent: annotated spans carry
  // data-furigana and in-flight ones are claimed.
  window.lute_furigana_annotate = annotate;

  if (window.htmx) {
    document.body.addEventListener("htmx:afterSwap", function (e) {
      if (!e.target || e.target.id !== "thetext") return;
      annotate();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", annotate);
  } else {
    annotate();
  }
})();
