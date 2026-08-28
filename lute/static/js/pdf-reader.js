/**
 * PDF reader page controller.
 *
 * Draws the current PDF page onto the .pdf-page-canvas with the
 * vendored pdf.js build, and refines the server-estimated word boxes
 * against pdf.js's exact text geometry so the clickable highlight
 * boxes line up with the printed words.
 *
 * Everything is failure-soft: if pdf.js is missing or the text
 * geometry cannot be matched, the page canvas stays empty and the
 * server estimates keep the words usable.
 */

(function () {
  "use strict";

  // url -> Promise<pdf.js document>, shared across page swaps.
  const _docs = {};

  // WeakMap<doc, { pageNum: { page, vp1, items } }> render data cache.
  const _pageData = new WeakMap();

  // Increasing sequence number; only the newest render may touch the
  // canvas (page flips and zoom changes supersede in-flight work).
  let _renderSeq = 0;
  let _renderTask = null;
  let _resizeObserver = null;
  let _renderTimer = null;

  const _ZWS = "\u200B"; // zero-width space, inserted by the tokenizer

  function _el() {
    return document.querySelector("#thetext .pdf-page");
  }

  function _lib() {
    return window.pdfjsLib || null;
  }

  function _getDoc(url) {
    if (!_docs[url]) {
      // cmaps/standard_fonts are vendored next to pdf.module.js; CJK
      // CID-keyed fonts need the packed cmaps to render and extract text.
      _docs[url] = _lib().getDocument({
        url,
        cMapUrl: "/static/js/vendor/pdfjs/cmaps/",
        cMapPacked: true,
        standardFontDataUrl: "/static/js/vendor/pdfjs/standard_fonts/",
      }).promise;
    }
    return _docs[url];
  }

  async function _getPageData(doc, pageNum) {
    let perDoc = _pageData.get(doc);
    if (!perDoc) {
      perDoc = {};
      _pageData.set(doc, perDoc);
    }
    if (!perDoc[pageNum]) {
      const page = await doc.getPage(pageNum);
      perDoc[pageNum] = {
        page,
        vp1: page.getViewport({ scale: 1 }),
        items: (await page.getTextContent()).items,
      };
    }
    return perDoc[pageNum];
  }

  // Entry point, called by the pdf_page.html fragment script after
  // every page swap.
  function load() {
    const el = _el();
    if (!el) {
      return;
    }
    if (!_lib()) {
      // The inline module script publishing window.pdfjsLib may not
      // have run yet; retry once it signals readiness.
      window.addEventListener("pdfjs-ready", load, { once: true });
      return;
    }
    if (_resizeObserver) {
      _resizeObserver.disconnect();
    }
    // Re-fit and re-render whenever the page box changes size (window
    // resize, pane toggle, zoom).  The observer also fires once right
    // after observe() with the initial size, kicking off the first
    // render.
    _resizeObserver = new ResizeObserver(() => {
      if (typeof window._fitMangaPage === "function") {
        window._fitMangaPage();
      }
      _scheduleRender();
    });
    _resizeObserver.observe(el);
  }

  function _scheduleRender() {
    if (_renderTimer) {
      clearTimeout(_renderTimer);
    }
    // Debounce: zooming fires several resize events in a row.
    _renderTimer = setTimeout(_render, 60);
  }

  async function _render() {
    const el = _el();
    if (!el || !_lib()) {
      return;
    }
    const seq = ++_renderSeq;
    const url = el.dataset.pdfUrl;
    const pageNum = parseInt(el.dataset.pageNum, 10);
    const cssW = el.clientWidth;
    const cssH = el.clientHeight;
    if (!url || !pageNum || !cssW || !cssH) {
      return;
    }

    // pdf.js cannot run two render tasks on the same canvas: cancel
    // and await any in-flight task first.
    if (_renderTask) {
      const task = _renderTask;
      _renderTask = null;
      task.cancel();
      try {
        await task.promise;
      } catch (err) {
        // Cancelled renders always reject; expected.
      }
    }
    if (seq !== _renderSeq) {
      return; // superseded by a newer render
    }

    let data;
    try {
      const doc = await _getDoc(url);
      data = await _getPageData(doc, pageNum);
    } catch (err) {
      console.error("pdf-reader: failed to load page", err);
      return;
    }
    if (seq !== _renderSeq) {
      return;
    }

    const canvas = el.querySelector(".pdf-page-canvas");
    if (!canvas) {
      return;
    }
    // Render at device resolution: canvas backing store = displayed
    // size x devicePixelRatio, so the page stays crisp when zoomed.
    const dpr = window.devicePixelRatio || 1;
    const targetW = Math.round(cssW * dpr);
    const targetH = Math.round(cssH * dpr);
    if (canvas.width !== targetW) {
      canvas.width = targetW;
    }
    if (canvas.height !== targetH) {
      canvas.height = targetH;
    }
    const viewport = data.page.getViewport({
      scale: targetW / data.vp1.width,
    });
    const task = data.page.render({
      canvasContext: canvas.getContext("2d"),
      viewport,
    });
    _renderTask = task;
    try {
      await task.promise;
    } catch (err) {
      return; // cancelled or failed; a newer render takes over
    } finally {
      if (_renderTask === task) {
        _renderTask = null;
      }
    }
    if (seq !== _renderSeq) {
      return;
    }
    _refineBoxes(el, data.items, data.vp1);
  }

  // Concatenated text of one word overlay (the tokenized textitem
  // spans it contains).
  function _wordText(wordEl) {
    let text = "";
    wordEl.querySelectorAll("span.textitem").forEach((s) => {
      text += s.dataset.text || "";
    });
    return text;
  }

  function _nonWsCount(text) {
    let count = 0;
    for (const ch of text) {
      if (!/\s/.test(ch) && ch !== _ZWS) {
        count++;
      }
    }
    return count;
  }

  /**
   * Refine the word overlays' left/width (in % of the page) from
   * pdf.js's text geometry.
   *
   * Both the server (pypdf) and pdf.js walk the page content stream
   * in order, so when the two non-whitespace character streams are
   * identical, each server word maps onto a run of glyphs with exact
   * x positions, and the boxes can be corrected from the glyph
   * advances (spread evenly over each pdf.js text item).  Any
   * mismatch - different extractor behaviour, ligatures, exotic
   * encodings - leaves the server estimates untouched.
   */
  function _refineBoxes(el, items, vp1) {
    if (el.dataset.refined) {
      // Boxes are in %, so they survive zooming; one pass is enough.
      return;
    }
    if (((vp1.rotation || 0) % 360) !== 0) {
      return; // x coordinates are not comparable on rotated pages
    }
    const words = Array.from(el.querySelectorAll(".pdf-word"));
    if (!words.length || !items.length) {
      return;
    }

    // Glyph stream: non-whitespace chars with interpolated x ranges.
    const glyphs = [];
    for (const item of items) {
      if (!item.transform) {
        continue;
      }
      const chars = Array.from(item.str || "");
      if (!chars.length) {
        continue;
      }
      const x0 = item.transform[4];
      const w = item.width || 0;
      const n = chars.length;
      for (let i = 0; i < n; i++) {
        const ch = chars[i];
        if (/\s/.test(ch) || ch === _ZWS) {
          continue;
        }
        glyphs.push({
          ch,
          x0: x0 + (w * i) / n,
          x1: x0 + (w * (i + 1)) / n,
        });
      }
    }

    // Server word stream, in the same order, remembering each word's
    // position in the stream.
    const stream = [];
    const spans = [];
    for (const w of words) {
      const text = _wordText(w);
      const start = stream.length;
      for (const ch of text) {
        if (!/\s/.test(ch) && ch !== _ZWS) {
          stream.push(ch);
        }
      }
      spans.push({ el: w, start, count: stream.length - start });
    }

    if (stream.length !== glyphs.length) {
      return;
    }
    for (let i = 0; i < stream.length; i++) {
      if (stream[i] !== glyphs[i].ch) {
        return;
      }
    }

    const pw = vp1.width;
    for (const span of spans) {
      if (!span.count) {
        continue;
      }
      const first = glyphs[span.start];
      const last = glyphs[span.start + span.count - 1];
      const left = (first.x0 / pw) * 100;
      const width = (Math.max(last.x1 - first.x0, 0.5) / pw) * 100;
      span.el.style.left = `${left.toFixed(3)}%`;
      span.el.style.width = `${width.toFixed(3)}%`;
    }
    el.dataset.refined = "1";
  }

  window.PdfReaderPage = { load };
})();
