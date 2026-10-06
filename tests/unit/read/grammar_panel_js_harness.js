/**
 * Harness for tests/unit/read/test_grammar_panel_js.py.
 *
 * Runs the real lute/static/js/lute-commands.js against a stub right-hand
 * pane and a stubbed $.getJSON, so the grammar panel's markup can be checked
 * without a browser.
 *
 * What needs pinning is not "does a row appear" but "does the folded
 * reference block render".  The backend names the block's source sentence
 * `sentence`, and the Japanese engine still spells it `japanese`; a renderer
 * that reads only one of the two drops the block silently for the other
 * language -- no error, no empty state, just a missing section, which is
 * exactly the kind of thing no Python test can see.
 *
 * One scenario per process; a JSON payload on stdin:
 *
 *   {"data": [ <grammar entry>, ... ]}
 *
 * stdout: the rendered panel markup, plus the parts a test asks about.
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
    this.style = {};
    // The panel's callbacks bail out when the panel has been detached while
    // the request was in flight (a real DOM element's isConnected).  Fake
    // elements are attached as far as these tests are concerned, so without
    // this the whole .done branch is skipped and every render assertion
    // reads the "Analyzing…" placeholder.
    this.isConnected = true;
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
}

// #thetext is deliberately absent: the panel then sends no ?text= snippet
// and skips the hover-ring layer, neither of which this harness is about.
const pane = new FakeEl("div");

global.document = {
  getElementById: (id) => (id === "read_pane_right" ? pane : null),
  querySelectorAll: () => [],
  createElement: (t) => new FakeEl(t),
  addEventListener: () => {},
  body: new FakeEl("body"),
};

global.window = {
  addEventListener: () => {},
  removeEventListener: () => {},
};
// window.matchMedia and window.convertPixelsToRem stay undefined: the code
// guards both, and stubbing them would only exercise the small-screen paths.

/* ---------- a very small jQuery ---------- */

let lastHtml = "";
let requestedUrl = "";

function fakePanel() {
  const el = new FakeEl("div");
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

function $(arg) {
  if (typeof arg === "string" && arg.charAt(0) === "<") return fakePanel();
  return {
    val: () => "1",
    remove: () => {},
    append: () => {},
    find: () => ({ text: () => {}, on: () => {} }),
    html: () => {},
  };
}

$.getJSON = function (url) {
  requestedUrl = url;
  const chain = {
    done: function (fn) {
      fn(input.data);
      return chain;
    },
    // The module also chains .fail(); a real request resolves here, so the
    // error branch is never the one under test.
    fail: function () {
      return chain;
    },
  };
  return chain;
};

global.$ = $;

/* ---------- the module under test ---------- */

eval(fs.readFileSync(modPath, "utf8")); // eslint-disable-line no-eval

open_grammar_analysis();

/* ---------- what a test can ask about ---------- */

const html = lastHtml;

// The folded block is the last part of an item, after the page examples, so
// slicing from its class name cannot pick up an example's <mark>.
const refexAt = html.indexOf("grammar-item__refex");
const refex = refexAt < 0 ? "" : html.slice(refexAt);

function firstMatch(re) {
  const m = html.match(re);
  return m ? m[1] : null;
}

process.stdout.write(
  JSON.stringify({
    url: requestedUrl,
    html: html,
    has_refex: refexAt >= 0,
    refex: refex,
    reftr: firstMatch(/grammar-item__reftr">([\s\S]*?)<\/div>/),
    marks: [...refex.matchAll(/<mark class="grammar-item__match">([\s\S]*?)<\/mark>/g)].map(
      (m) => m[1]
    ),
    formation: firstMatch(/grammar-item__formation">([\s\S]*?)<\/div>/),
    notes: firstMatch(/grammar-item__notes">([\s\S]*?)<\/div>/),
    more_label: firstMatch(/<summary>([\s\S]*?)<\/summary>/),
    item_count: (html.match(/class="grammar-item /g) || []).length,
    // The term form's Grammar button marks the card it jumped to, and tests
    // address cards by key, so the anchor has to survive into the markup.
    keys: [...html.matchAll(/data-grammar-key="([^"]*)"/g)].map((m) => m[1]),
  })
);
