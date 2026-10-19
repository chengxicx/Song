/**
 * Reader-mode EPUB: inject word spans into the chapter's own markup.
 *
 * A reader-mode EPUB chapter is stored as its original (sanitised)
 * HTML, so the reading screen cannot render server-side token spans the
 * way read/page_content.html does -- the chapter's own tags have to stay
 * intact.  What the server does instead is tokenise the whole chapter
 * and ship the token stream in render order (see read/epub_page.html).
 *
 * This file walks the chapter's text nodes in document order, builds a
 * character stream from them, and walks the token stream alongside it.
 * When the two agree character for character (whitespace excluded --
 * the server collapsed it, the browser did not), each token's character
 * range maps onto a slice of a text node, and the slice is replaced by a
 * span.textitem with exactly the attributes textitem.html would have
 * produced.  From there the whole reader works untouched: lookups,
 * status colours, multi-word selection, TTS, review cards.
 *
 * The match is all-or-nothing per chapter, like pdf-reader.js's geometry
 * refinement: one bad EPUB (exotic entities, a stylesheet-only block)
 * falls back to plain chapter text instead of mis-wrapping words.
 *
 * Whitespace is deliberately tested against an explicit ASCII set:
 * JavaScript's \s also matches U+3000 (ideographic space), which is real
 * Japanese text and a token of its own on the server.
 */
(function () {
  "use strict";

  const ASCII_WS = /[\t\n\v\f\r ]/;
  // Never descend into these: ruby readings (already stripped
  // server-side, but a stray one must not desync the stream), and the
  // sentence play buttons tts.js injects.
  const SKIP_SELECTOR = "rt, rp, script, style, .lute-sentence-play-btn";

  // Block containers whose inline content is split into
  // .textsentence spans (see wrapSentences).
  const BLOCK_TAGS = new Set([
    "p", "div", "section", "article", "aside", "header", "footer", "main",
    "blockquote", "figure", "figcaption", "li", "ul", "ol", "dl", "dt", "dd",
    "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr", "td", "th", "pre",
    "body",
  ]);

  function isAsciiWs(ch) {
    return ASCII_WS.test(ch);
  }

  function mount(root) {
    if (!root || root.dataset.epubMounted) return;
    const dataNode = document.getElementById("lute-epub-tokens");
    if (!dataNode) return;

    let tokens;
    try {
      tokens = JSON.parse(dataNode.textContent || "[]");
    } catch (err) {
      console.warn("epub-reader: unreadable token data", err);
      return;
    }
    if (!tokens.length) return;

    const nodes = collectTextNodes(root);
    if (!nodes.length) return;

    const chars = collectChars(nodes);
    const ranges = tokenCharRanges(tokens);
    const mismatch = compareStreams(chars, ranges);
    if (mismatch !== null) {
      // Keep the chapter readable; just without word interactions.
      console.warn(
        "epub-reader: token stream does not match the chapter markup at " +
          mismatch +
          " -- rendering the chapter without word spans"
      );
      root.dataset.epubMismatch = String(mismatch);
      return;
    }

    wrapTokens(chars, ranges, tokens);
    root.dataset.epubMounted = "1";
    wrapSentences(root);
    blockExternalLinks(root);
  }

  /* ------------------------------------------------------------------
   * Reading the DOM
   * ------------------------------------------------------------------ */

  function collectTextNodes(root) {
    const out = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (!node.nodeValue) return NodeFilter.FILTER_REJECT;
        const parent = node.parentElement;
        if (parent && parent.closest(SKIP_SELECTOR)) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    while (walker.nextNode()) out.push(walker.currentNode);
    return out;
  }

  /**
   * Non-whitespace characters of the chapter, in document order, each
   * with the text node it came from and its code-unit offset inside it
   * (splitText works in code units; the token stream counts code points,
   * which is what `for...of` gives us).
   */
  function collectChars(nodes) {
    const chars = [];
    for (const node of nodes) {
      const text = node.nodeValue;
      let cu = 0;
      for (const ch of text) {
        if (!isAsciiWs(ch)) chars.push({ node, cu, ch });
        cu += ch.length;
      }
    }
    return chars;
  }

  function tokenCharRanges(tokens) {
    const stream = [];
    const ranges = [];
    for (let i = 0; i < tokens.length; i++) {
      const start = stream.length;
      const text = tokens[i][0] || "";
      for (const ch of text) {
        if (!isAsciiWs(ch)) stream.push(ch);
      }
      ranges.push([start, stream.length]);
    }
    return { stream, ranges };
  }

  /** Index of the first difference, or null when the streams agree. */
  function compareStreams(chars, tokenStream) {
    const stream = tokenStream.stream;
    if (chars.length !== stream.length) return Math.min(chars.length, stream.length);
    for (let i = 0; i < stream.length; i++) {
      if (chars[i].ch !== stream[i]) return i;
    }
    return null;
  }

  /* ------------------------------------------------------------------
   * Wrapping
   * ------------------------------------------------------------------ */

  function wrapTokens(chars, tokenData, tokens) {
    const langId = (document.getElementById("lang_id") || {}).value || "";
    // node -> [{start, end, token, piece}], positions in code units.
    const perNode = new Map();
    // token -> number of spans it had to be cut into (one per text node
    // it crosses).  A word cut by an inline tag must be marked as split
    // in *every* piece, so the count is per token, not per node.
    const pieceCounts = new Map();

    for (let t = 0; t < tokens.length; t++) {
      const [start, end] = tokenData.ranges[t];
      if (end <= start) continue;
      let i = start;
      let piece = 0;
      while (i < end) {
        const node = chars[i].node;
        const segStart = chars[i].cu;
        let segEnd = chars[i].cu + chars[i].ch.length;
        let j = i + 1;
        while (j < end && chars[j].node === node) {
          segEnd = chars[j].cu + chars[j].ch.length;
          j++;
        }
        if (!perNode.has(node)) perNode.set(node, []);
        perNode.get(node).push({ start: segStart, end: segEnd, token: t, piece });
        piece += 1;
        i = j;
      }
      if (piece) pieceCounts.set(t, piece);
    }

    for (const [node, segments] of perNode) {
      // Right to left: splitting a text node only shortens its tail, so
      // every earlier offset stays valid.
      for (let k = segments.length - 1; k >= 0; k--) {
        const seg = segments[k];
        const pieceCount = pieceCounts.get(seg.token) || 1;
        node.splitText(seg.end);
        const middle = node.splitText(seg.start);
        const span = makeSpan(tokens[seg.token], seg.token, seg.piece, pieceCount, langId);
        span.appendChild(middle);
        node.parentNode.insertBefore(span, node.nextSibling);
      }
    }
  }

  function makeSpan(token, order, piece, pieceCount, langId) {
    const [text, wid, status] = token;
    const sentence = token[3];
    const paragraph = token.length > 4 ? token[4] : 0;
    const isWord = !!wid;
    const span = document.createElement("span");

    span.className = isWord ? `textitem click word word${wid}` : "textitem";
    // data-order stays unique per span (a token split across inline tags
    // renders one span per piece) while remaining monotonic with the
    // reading order, which is all the range filters rely on.
    span.dataset.order = String(order * 1000 + piece);
    span.id = `ID-${sentence}-${order}${pieceCount > 1 ? "-" + piece : ""}`;
    span.dataset.langId = langId;
    span.dataset.paragraphId = paragraph;
    span.dataset.sentenceId = sentence;
    span.dataset.text = text;
    span.dataset.statusClass = isWord && status >= 0 ? `status${status}` : "";
    if (isWord) span.dataset.wid = wid;
    if (pieceCount > 1) {
      // Halves of one word cut by an inline tag: like a manga word split
      // across OCR columns, a piece must not join a multi-word selection
      // (the drag would splice unrelated fragments), but clicking any
      // piece must still open the whole word -- and TTS must speak it
      // whole rather than half.
      span.dataset.splitPiece = "1";
      span.dataset.ttsText = text;
    }
    return span;
  }

  /* ------------------------------------------------------------------
   * Sentence wrappers
   * ------------------------------------------------------------------ */

  /**
   * Wrap each sentence's spans in a .textsentence element.
   *
   * Text pages get these from the server (one per sentence), and both
   * the TTS player and the sentence-lookup hotkeys read them.  A chapter
   * paragraph can hold several sentences and arbitrary inline markup, so
   * the block is first split at sentence boundaries -- cloning the inline
   * ancestors, the usual surroundContents problem -- and the resulting
   * top-level runs are then wrapped.
   */
  function wrapSentences(root) {
    const blocks = Array.from(root.querySelectorAll("*")).filter((el) => {
      if (!BLOCK_TAGS.has(el.tagName.toLowerCase())) return false;
      if (!el.querySelector("span.word")) return false;
      // Only the innermost block: a <div> holding <p>s must not swallow
      // its children's sentences.
      return !Array.from(el.children).some(
        (c) =>
          BLOCK_TAGS.has(c.tagName.toLowerCase()) && c.querySelector("span.word")
      );
    });
    blocks.forEach(wrapBlockSentences);
  }

  function wrapBlockSentences(block) {
    const spans = Array.from(block.querySelectorAll("span.word"));
    if (!spans.length) return;

    // One boundary per sentence change; the block's head belongs to the
    // first sentence it contains.
    const starts = [];
    let previous = spans[0].dataset.sentenceId;
    for (const span of spans) {
      const id = span.dataset.sentenceId;
      if (id !== previous) {
        starts.push(span);
        previous = id;
      }
    }

    const boundaries = new Set();
    for (const span of starts) {
      const node = splitBefore(block, span);
      if (node) boundaries.add(node);
    }

    let current = document.createElement("span");
    current.className = "textsentence";
    current.dataset.sentenceId = spans[0].dataset.sentenceId;

    // Snapshot the children *before* inserting the wrapper -- otherwise
    // the wrapper is one of them and would try to adopt itself.
    const children = Array.from(block.childNodes);
    block.insertBefore(current, block.firstChild);

    for (const child of children) {
      if (boundaries.has(child)) {
        current = document.createElement("span");
        current.className = "textsentence";
        current.dataset.sentenceId = firstWordOf(child, spans[0]).dataset.sentenceId;
        block.insertBefore(current, child);
      }
      current.appendChild(child);
    }
  }

  function firstWordOf(node, fallback) {
    const el = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    return (el && el.querySelector("span.word")) || fallback;
  }

  /**
   * Split the DOM immediately before `node`, so `node` starts a new
   * top-level child of `block`.  Returns that child (the boundary), or
   * null when the node already starts one.
   */
  function splitBefore(block, node) {
    let cursor = node;
    while (cursor && cursor.parentNode !== block) {
      const parent = cursor.parentNode;
      if (!parent) return null;
      if (cursor.nextSibling) {
        const clone = parent.cloneNode(false);
        // A cloned ancestor must not duplicate ids (footnote anchors are
        // the usual ones); everything else about the clone is cosmetic.
        clone.removeAttribute("id");
        while (cursor.nextSibling) clone.appendChild(cursor.nextSibling);
        parent.parentNode.insertBefore(clone, parent.nextSibling);
      }
      cursor = parent;
    }
    if (!cursor || cursor === block.firstChild) return null;
    return cursor;
  }

  /* ------------------------------------------------------------------
   * Links
   * ------------------------------------------------------------------ */

  /**
   * Chapter links point at the extracted copy of another chapter, which
   * would navigate away from the reader into a bare HTML file.  Same
   * document anchors (footnotes) are left alone.
   */
  function blockExternalLinks(root) {
    root.addEventListener("click", (event) => {
      const link = event.target.closest && event.target.closest("a[href]");
      if (!link) return;
      const href = link.getAttribute("href") || "";
      if (href.charAt(0) === "#" || href === "") return;
      event.preventDefault();
      console.info("epub-reader: cross-chapter link not followed:", href);
    });
  }

  window.EpubReader = { mount };
})();
