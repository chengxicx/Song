/**
 * Harness for tests/unit/read/test_grammar_rings_js.py.
 *
 * Drives the real lute/static/js/lute-commands.js grammar panel against a
 * stubbed reading pane in which ONE .textsentence node holds two sentences:
 *
 *   cell layout (10px per character, all on one rendered line):
 *     cell1 "AAA。"  x = 0..40
 *     cell2 "BBB。"  x = 100..140   (a merged multi-word-sized cell)
 *
 * The panel payload's example is the second sentence "BBB。" with a matched
 * span [0, 2) ("BB").  After hovering that example the rings must be:
 *
 *   blue  : x 97..143  (cell2 only -- never the neighbouring sentence's cell1)
 *   amber : x 99..121  ("BB" inside cell2 -- narrower than the whole cell)
 *
 * A whole-node blue ring would start near x=0; a whole-cell amber box would
 * be 40px wide instead of 20px.
 *
 * One scenario per process; JSON payload on stdin, stdout: JSON describing
 * every ring appended to the body layer.
 */

const fs = require("fs");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const modPath = process.argv[2];

/* ---------- fake layout ---------- */

const CHAR_W = 10;
const LINE_TOP = 50;
const LINE_H = 20;

class FakeEl {
  constructor(tag) {
    this.tagName = (tag || "").toUpperCase();
    this.children = [];
    this.classes = new Set();
    this.style = {};
    this.isConnected = true;
    this.listeners = {};
    this.textContent = "";
    // Selector tables the grammar binding code happens to need.
    this.qaAll = {};
    this.qaOne = {};
    this.baseX = 0;
    this.textNodes = [];
  }
  get classList() {
    const s = this.classes;
    return {
      add: (c) => s.add(c),
      remove: (c) => s.delete(c),
      contains: (c) => s.has(c),
    };
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  remove() {}
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  dispatch(type) {
    (this.listeners[type] || []).forEach((fn) => fn());
  }
  querySelectorAll(sel) {
    return this.qaAll[sel] || [];
  }
  querySelector(sel) {
    return this.qaOne[sel] || null;
  }
  getClientRects() {
    // Whole-element box: spans every text node's characters.
    const len = this.textNodes.reduce((n, tn) => n + tn.data.length, 0);
    if (!len) return [];
    const left = this.baseX;
    return [
      {
        left: left,
        right: left + len * CHAR_W,
        top: LINE_TOP,
        bottom: LINE_TOP + LINE_H,
        width: len * CHAR_W,
        height: LINE_H,
      },
    ];
  }
}

// The reading text: one .textsentence node, two sentences inside it.
const cell1 = new FakeEl("span");
cell1.classes.add("textitem");
cell1.baseX = 0;
cell1.textContent = "AAA。";
cell1.textNodes = [{ data: "AAA。", cell: cell1 }];

const cell2 = new FakeEl("span");
cell2.classes.add("textitem");
cell2.baseX = 100;
cell2.textContent = "BBB。";
cell2.textNodes = [{ data: "BBB。", cell: cell2 }];

const sentenceNode = new FakeEl("span");
sentenceNode.classes.add("textsentence");
sentenceNode.textContent = "AAA。BBB。";
sentenceNode.qaAll[".textitem"] = [cell1, cell2];

const textRoot = new FakeEl("div");
textRoot.qaAll[".textsentence"] = [sentenceNode];
textRoot.qaAll[":scope > p"] = [];

const pane = new FakeEl("div");

/* ---------- panel elements the binding walks ---------- */

const exampleEl = new FakeEl("div");
exampleEl.classes.add("grammar-item__example");

const headEl = new FakeEl("div");
headEl.classes.add("grammar-item__head");

const itemEl = new FakeEl("div");
itemEl.classes.add("grammar-item");
itemEl.qaAll[".grammar-item__example"] = [exampleEl];
itemEl.qaOne[".grammar-item__head"] = headEl;

function rectsForRange(startNode, startOff, endNode, endOff) {
  // One line, fixed pitch, range endpoints always inside one cell here.
  const left = startNode.cell.baseX + startOff * CHAR_W;
  const width = Math.max(0, (endOff - startOff) * CHAR_W);
  return [
    {
      left: left,
      right: left + width,
      top: LINE_TOP,
      bottom: LINE_TOP + LINE_H,
      width: width,
      height: LINE_H,
    },
  ];
}

class FakeRange {
  setStart(node, off) {
    this.sn = node;
    this.so = off;
  }
  setEnd(node, off) {
    this.en = node;
    this.eo = off;
  }
  getClientRects() {
    return rectsForRange(this.sn, this.so, this.en, this.eo);
  }
}

class FakeTreeWalker {
  constructor(root) {
    this.root = root;
    this.i = 0;
  }
  nextNode() {
    const nodes = this.root.textNodes || [];
    return this.i < nodes.length ? nodes[this.i++] : null;
  }
}

global.NodeFilter = { SHOW_TEXT: 4 };

const docListeners = {};

global.document = {
  getElementById: (id) => {
    if (id === "read_pane_right") return pane;
    if (id === "thetext") return textRoot;
    return null;
  },
  querySelectorAll: () => [],
  querySelector: () => null,
  createElement: (t) => new FakeEl(t),
  addEventListener: (type, fn) => {
    (docListeners[type] = docListeners[type] || []).push(fn);
  },
  createTreeWalker: (root) => new FakeTreeWalker(root),
  createRange: () => new FakeRange(),
  body: new FakeEl("body"),
};

global.window = {
  addEventListener: () => {},
  removeEventListener: () => {},
};

/* ---------- a very small jQuery ---------- */

let lastHtml = "";

function fakePanel() {
  const el = new FakeEl("div");
  el.qaAll[".grammar-item"] = [itemEl];
  const api = {
    0: el,
    html(h) {
      if (h === undefined) return lastHtml;
      lastHtml = String(h);
      return api;
    },
    find: () => ({ text: () => api, on: () => api, remove: () => api }),
    append: () => api,
    remove: () => api,
  };
  return api;
}

function $() {
  if (typeof arguments[0] === "string" && arguments[0].charAt(0) === "<") return fakePanel();
  return {
    val: () => "1",
    remove: () => {},
    append: () => {},
    find: () => ({ text: () => {}, on: () => {} }),
    html: () => {},
  };
}

$.getJSON = function () {
  const chain = {
    done: function (fn) {
      fn(input.data);
      return chain;
    },
    fail: function () {
      return chain;
    },
  };
  return chain;
};

global.$ = $;

/* ---------- run the module and hover the example ---------- */

eval(fs.readFileSync(modPath, "utf8")); // eslint-disable-line no-eval

open_grammar_analysis();
// Real hovers only fire the ring handler after a pointer move (the panel
// gates on it to ignore synthetic mouseenter from the Grammar-jump scroll).
(docListeners["mousemove"] || []).forEach((fn) => fn());
exampleEl.dispatch("mouseenter");

/* ---------- collect every ring in the body layer ---------- */

function walk(el, out) {
  el.children.forEach((c) => {
    // The module sets ring.className as a plain string property.
    if (c.className === "grammar-ring" || c.className === "grammar-word-ring") {
      out.push({
        amber: c.className === "grammar-word-ring",
        left: parseFloat(c.style.left),
        top: parseFloat(c.style.top),
        width: parseFloat(c.style.width),
        height: parseFloat(c.style.height),
      });
    }
    walk(c, out);
  });
  return out;
}

const rings = walk(global.document.body, []);
process.stdout.write(JSON.stringify({ rings: rings }));
