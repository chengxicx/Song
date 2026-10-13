/* Furigana above reading-page words.
 *
 * Enabled per language (Language > "Show furigana above words"), and only
 * for parsers that can supply readings -- Japanese (kana via Sudachi/
 * MeCab), Mandarin (pinyin) and Cantonese (jyutping) via their plugins;
 * the flag reaches us via LUTE_FURIGANA in read/index.html.  Readings
 * come from the same endpoint the shadowing panel uses
 * (/read/shadowing/readings), which pairs each token with the
 * contextual reading of the sentence it sits in (Japanese 一つ reads
 * ひとつ, not いち + つ).  Every Han-character-bearing word span gets
 * its display text wrapped in <ruby>text<rt>reading</rt></ruby>;
 * words without Han characters (kana, hangul, Latin text) are left
 * alone -- they already show their own pronunciation.
 */
(function () {
  "use strict";

  const CONFIG =
    typeof LUTE_FURIGANA !== "undefined"
      ? LUTE_FURIGANA
      : { enabled: false, langId: 0 };

  // Spans claimed by an in-flight request.  Claiming is synchronous, at
  // collection time: annotate() has several entry points that can fire
  // back-to-back over the same fresh content (DOMContentLoaded, the
  // htmx:afterSwap listener, and page_content's explicit call), and
  // marking only after the fetch resolved let a second pass re-read an
  // already-wrapped span.  ruby flattens under textContent, so the
  // reading was appended to the base text ("夜" became "夜よる") and the
  // word was wrapped again.
  const in_flight = new Set();

  // Furigana exists to make kanji readable; a word without kanji
  // (聞こえ's kana is already visible, トントン is all katakana) needs no
  // reading above it.
  function has_kanji(text) {
    return /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/.test(text);
  }

  function wrap_ruby(sp, text, reading) {
    if (!reading || reading === text) return;
    if (!has_kanji(text)) return;
    if (sp.querySelector("ruby")) return;
    const ruby = document.createElement("ruby");
    ruby.appendChild(document.createTextNode(text));
    const rt = document.createElement("rt");
    rt.appendChild(document.createTextNode(reading));
    ruby.appendChild(rt);
    sp.textContent = "";
    sp.appendChild(ruby);
  }

  function annotate_paragraph(spans) {
    // Capture the display text up front: once a span is wrapped, its
    // textContent includes the reading, and dataset.text (the parser's
    // canonical token) can drift from the display text after a term edit
    // re-renders it.
    const captured = spans.map((sp) => sp.textContent);
    const tokens = spans.map((sp) => sp.dataset.text || sp.textContent.trim());
    const full_text = tokens.join("");
    return fetch("/read/shadowing/readings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        language_id: CONFIG.langId,
        tokens: tokens,
        full_text: full_text,
      }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data || !Array.isArray(data.tokens)) return;
        spans.forEach((sp, i) => {
          const entry = data.tokens[i];
          if (!entry || entry.reading == null) return;
          wrap_ruby(sp, captured[i], entry.reading);
          sp.dataset.furigana = "1";
        });
      })
      .catch(() => {})
      .then(() => {
        // Release the claim either way: on failure the span stays
        // unannotated and unmarked, so a later pass retries it.
        spans.forEach((sp) => in_flight.delete(sp));
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
    // One request per paragraph: readings follow sentence context, and a
    // paragraph is the natural batch a page breaks into.
    const paras = new Map();
    spans.forEach((sp) => {
      in_flight.add(sp);
      const pid = sp.dataset.paragraphId || "0";
      if (!paras.has(pid)) paras.set(pid, []);
      paras.get(pid).push(sp);
    });
    paras.forEach(annotate_paragraph);
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
