/* Furigana above reading-page words.
 *
 * Enabled per language (Language > "Show furigana above words"), and only
 * for Japanese-capable parsers; the flag reaches us via LUTE_FURIGANA in
 * read/index.html.  Readings come from the same endpoint the shadowing
 * panel uses (/read/shadowing/readings), which pairs each token with the
 * contextual kana reading of the sentence it sits in (一つ reads ひとつ,
 * not いち + つ).  Every kanji-bearing word span gets its display text
 * wrapped in <ruby>text<rt>reading</rt></ruby>; kana words are untouched
 * (the endpoint answers reading=None for them, same as the panel).
 */
(function () {
  "use strict";

  const CONFIG =
    typeof LUTE_FURIGANA !== "undefined"
      ? LUTE_FURIGANA
      : { enabled: false, langId: 0 };

  function mark_annotated(sp) {
    sp.dataset.furigana = "1";
  }

  function wrap_ruby(sp, reading) {
    const text = sp.textContent;
    if (!reading || reading === text) return;
    const ruby = document.createElement("ruby");
    ruby.appendChild(document.createTextNode(text));
    const rt = document.createElement("rt");
    rt.appendChild(document.createTextNode(reading));
    ruby.appendChild(rt);
    sp.textContent = "";
    sp.appendChild(ruby);
  }

  function annotate_paragraph(spans) {
    // data-text is the parser's canonical token text; textContent can
    // drift from it after a term edit re-renders display text.
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
          wrap_ruby(sp, entry.reading);
          mark_annotated(sp);
        });
      })
      .catch(() => {});
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
    );
    if (spans.length === 0) return;
    // One request per paragraph: readings follow sentence context, and a
    // paragraph is the natural batch a page breaks into.
    const paras = new Map();
    spans.forEach((sp) => {
      const pid = sp.dataset.paragraphId || "0";
      if (!paras.has(pid)) paras.set(pid, []);
      paras.get(pid).push(sp);
    });
    paras.forEach(annotate_paragraph);
  }

  // Called by the page_content fragment after every swap (read/
  // page_content.html), and by the listener below as a safety net for
  // the reader's own initial load.  Idempotent: already-annotated spans
  // carry data-furigana and are skipped.
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
