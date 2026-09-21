/**
 * Harness for tests/unit/book/test_playing_line_js.py.
 *
 * Runs the real lute/static/js/lute-playing-line.js against a stub
 * #thetext, so the mark it puts on the line the player is reading can be
 * checked without a browser.  The interesting part is not "does it add a
 * class" but "does it add it to the RIGHT paragraph": the server hands
 * over a positional cue map, the page text can drift out of step with the
 * cues, and a wrong mark is worse than no mark.
 *
 * One scenario per process; a JSON payload on stdin:
 *
 *   {
 *     "paragraphs": [ ["Hola."], ["Adios", "amigo."] ],  // per <p>, its sentences
 *     "cue_map": [0, 1],
 *     "steps": [
 *       {"set_cue_index": [1, "Adios amigo."]},
 *       {"state": true}
 *     ]
 *   }
 *
 * Each sentence becomes <span class="textsentence"> with a 🔊
 * <span class="lute-sentence-play-btn"> inside it and a zero-width-space
 * <span class="textitem"> appended to the paragraph -- exactly what the
 * reading page renders (read/page_content.html plus the buttons tts.js
 * injects), since both are things the module has to see past.
 *
 * Steps:
 *   {"cue_map": [...]}                   replace window.LUTE_PAGE_CUE_MAP
 *   {"set_cue_index": [idx, text]}       the media players' call
 *   {"set_sentence": [p, s]}             the TTS player's call
 *   {"set_sentence": [p, s], "detached": true}   ...on a span a page turn dropped
 *   {"clear": true}
 *
 * and the state of the mark after every step on stdout.
 */

const fs = require("fs");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const modPath = process.argv[2];

/* ---------- a very small DOM ---------- */

class FakeEl {
  constructor(tag) {
    this.tagName = (tag || "").toUpperCase();
    this.children = [];
    this.classes = new Set();
    this.isConnected = true;
    this.parentNode = null;
    this._text = null;
  }
  get classList() {
    const s = this.classes;
    return {
      add: (c) => s.add(c),
      remove: (c) => s.delete(c),
      contains: (c) => s.has(c),
    };
  }
  // Like a real element: the text of a node is its descendants', so the
  // 🔊 button's own text is included until something removes it.
  get textContent() {
    if (this._text !== null) return this._text;
    return this.children.map((c) => c.textContent).join("");
  }
  set textContent(v) {
    this._text = String(v);
    this.children = [];
  }
  cloneNode() {
    const c = new FakeEl(this.tagName);
    c.classes = new Set(this.classes);
    c.isConnected = this.isConnected;
    c._text = this._text;
    this.children.forEach((child) => c.appendChild(child.cloneNode(true)));
    return c;
  }
  appendChild(c) {
    c.parentNode = this;
    this.children.push(c);
    return c;
  }
  removeChild(c) {
    const i = this.children.indexOf(c);
    if (i >= 0) this.children.splice(i, 1);
    return c;
  }
  querySelectorAll(sel) {
    const direct = sel.match(/^:scope\s*>\s*(.*)$/);
    const out = [];
    if (direct) {
      const tag = direct[1].toUpperCase();
      this.children.forEach((c) => {
        if (c.tagName === tag) out.push(c);
      });
      return out;
    }
    const cls = sel.replace(/^\./, "");
    const walk = (n) => {
      n.children.forEach((c) => {
        if (c.classes.has(cls)) out.push(c);
        walk(c);
      });
    };
    walk(this);
    return out;
  }
}

function span(cls, text) {
  const el = new FakeEl("span");
  if (cls) el.classes.add(cls);
  if (text !== undefined) el.textContent = text;
  return el;
}

// <p><span class="textsentence">🔊…</span>…<span class="textitem">\u200b</span></p>
function buildParagraph(sentences) {
  const p = new FakeEl("p");
  sentences.forEach((text) => {
    const sentence = span("textsentence");
    sentence.appendChild(span("lute-sentence-play-btn", "\uD83D\uDD0A"));
    sentence.appendChild(span(null, text));
    p.appendChild(sentence);
  });
  p.appendChild(span("textitem", "\u200b"));
  return p;
}

const thetext = new FakeEl("div");
thetext.id = "thetext";
(input.paragraphs || []).forEach((s) => thetext.appendChild(buildParagraph(s)));

global.window = { LUTE_PAGE_CUE_MAP: input.cue_map };
global.document = { getElementById: (id) => (id === "thetext" ? thetext : null) };

eval(fs.readFileSync(modPath, "utf8")); // eslint-disable-line no-eval

const helper = global.window.LutePlayingLine;
const paragraphs = () => thetext.querySelectorAll(":scope > p");

const CLASS = "lute-playing-line";

// What the reader sees, without the UI the page injects into the text:
// the 🔊 button is markup, not text (the module strips it too, and this
// is how a test can tell the two apart).
function visibleText(el) {
  const clone = el.cloneNode(true);
  const strip = (n) => {
    n.children.slice().forEach((c) => {
      if (c.classes.has("lute-sentence-play-btn")) n.removeChild(c);
      else strip(c);
    });
  };
  strip(clone);
  return clone.textContent.replace(/[\s\u200b]+/g, "");
}

// Every element carrying the mark, as {para, sentence, text}: the media
// players mark a <p> (sentence null), the TTS player marks a sentence
// span inside one.
function marked() {
  const out = [];
  paragraphs().forEach((p, pi) => {
    if (p.classes.has(CLASS)) {
      out.push({ para: pi, sentence: null, text: visibleText(p) });
    }
    p.querySelectorAll(".textsentence").forEach((s, si) => {
      if (s.classes.has(CLASS)) {
        out.push({ para: pi, sentence: si, text: visibleText(s) });
      }
    });
  });
  return out;
}

const results = [];
(input.steps || []).forEach((s, i) => {
  if (s.cue_map !== undefined) {
    global.window.LUTE_PAGE_CUE_MAP = s.cue_map;
  } else if (s.set_cue_index !== undefined) {
    helper.setCueIndex(s.set_cue_index[0], s.set_cue_index[1]);
  } else if (s.set_sentence !== undefined) {
    const p = paragraphs()[s.set_sentence[0]];
    const el = p.querySelectorAll(".textsentence")[s.set_sentence[1]];
    if (s.detached) el.isConnected = false;
    helper.setElement(el);
  } else if (s.clear) {
    helper.clear();
  } else {
    throw new Error(`unknown step ${JSON.stringify(s)}`);
  }
  results.push({ step: i, marked: marked() });
});

process.stdout.write(JSON.stringify({ results: results }));
