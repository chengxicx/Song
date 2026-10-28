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

/* The reading page injects window.LUTE_BOOK_TYPE; the harness runs the
   module bare in Node.  A test sets it through the payload to check the
   fixed-page (manga / pdf) paths, and leaves it unset otherwise. */
if (input.book_type) globalThis.LUTE_BOOK_TYPE = input.book_type;

/* ---------- a very small DOM ---------- */


/* The reading page injects window.I18N via base.html; the harness runs
   the module bare in Node, so provide the identity fallback it uses. */
globalThis.LUTE_I18N = {};
globalThis.I18N = { t: (k) => k };

class FakeEl {
  constructor(tag) {
    this.tagName = (tag || "").toUpperCase();
    this.children = [];
    this.classes = new Set();
    this.style = {};
    this.attributes = {};
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
      toggle: (c, on) => (on ? s.add(c) : s.delete(c)),
    };
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  getAttribute(name) {
    return name in this.attributes ? this.attributes[name] : null;
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  remove() {}
  // The header's scope control walks the rendered buttons this way, so a
  // stub without it would silently skip the whole "which scope is active"
  // bookkeeping.  The buttons are not in this stub DOM tree -- the panel
  // sets its markup as an HTML string -- so the scope buttons are collected
  // by the harness and handed to the panel through this list.
  querySelectorAll() {
    return scopeButtons;
  }
}

// The scope buttons, rebuilt whenever the panel re-renders (the markup is an
// HTML string, and the panel reads the buttons back out of the DOM).
let scopeButtons = [];

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
const requestedUrls = [];
// The panel instance the module built, so a test can drive its scope
// buttons.  $("<div/>") is the only call that produces a panel object.
let livePanel = null;

function fakePanel() {
  const el = new FakeEl("div");
  const api = {
    0: el,
    html(h) {
      if (h === undefined) return lastHtml;
      lastHtml = String(h);
      // The panel re-renders by replacing its innerHTML, so the scope
      // buttons it later reads back through querySelectorAll have to be
      // rebuilt from the new markup -- one stub element per button, in
      // document order, each carrying its own data-scope.
      scopeButtons = [...lastHtml.matchAll(/data-scope="([^"]*)"/g)].map(
        (m) => {
          const b = new FakeEl("button");
          b.setAttribute("data-scope", m[1]);
          return b;
        }
      );
      return api;
    },
    // The scope buttons are bound through .find(...).on("click"), so the
    // handlers have to be reachable for a test to drive a scope switch.
    // The same lookup is also used to put a placeholder into the body while
    // a scope's request is in flight, so the returned stub needs html().
    handlers: {},
    find(selector) {
      const scope = /grammar-analysis-panel__scope/.test(String(selector));
      const sub = {
        text: () => api,
        remove: () => api,
        html: (h) => (h === undefined ? lastHtml : ((lastHtml = String(h)), api)),
        on: function (event, fn) {
          if (scope && event === "click") api.handlers[selector] = fn;
          return this;
        },
      };
      return sub;
    },
    append: () => api,
    remove: () => api,
  };
  livePanel = api;
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
  requestedUrls.push(url);
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

// Drive a scope switch: the panel binds one click handler per selector, and
// the handler reads the button's own data-scope through `this`.  A test
// supplies the scope, the harness supplies a matching `this`.
function clickScope(scope) {
  if (!livePanel || !livePanel.handlers) return;
  Object.keys(livePanel.handlers).forEach(function (k) {
    livePanel.handlers[k].call({
      getAttribute: function (name) {
        return name === "data-scope" ? scope : null;
      },
    });
  });
}

// Everything below is computed on demand rather than once, because a scope
// switch re-renders the panel and a test then asks about the new markup.
function snapshot() {
  const html = lastHtml;

function firstMatch(re) {
  const m = html.match(re);
  return m ? m[1] : null;
}

  const refexAt = html.indexOf("grammar-item__refex");
  const refex = refexAt < 0 ? "" : html.slice(refexAt);
  return {
    url: requestedUrls[0] || "",
    // Every request the panel made, in order: the page's own, then the
    // book's when the reader switches scope.
    urls: requestedUrls,
    html: html,
    has_refex: refexAt >= 0,
    refex: refex,
    reftr: firstMatch(/grammar-item__reftr">([\s\S]*?)<\/div>/),
    marks: [...refex.matchAll(/<mark class="grammar-item__match">([\s\S]*?)<\/mark>/g)].map(
      (m) => m[1]
    ),
    formation: firstMatch(/grammar-item__formation">([\s\S]*?)<\/div>/),
    notes: firstMatch(/grammar-item__notes">([\s\S]*?)<\/div>/),
    // The register chip, the curated long explanation, and the cross-links
    // into the grammar library -- all optional, so a row may show none.
    formality: firstMatch(/grammar-item__formality">([\s\S]*?)<\/span>/),
    detail: firstMatch(/grammar-item__detail">([\s\S]*?)<\/div>/),
    related: [
      ...html.matchAll(
        /grammar-item__related-link" href="([^"]*)">([\s\S]*?)<\/a>/g
      ),
    ].map((m) => ({ href: m[1], name: m[2] })),
    more_label: firstMatch(/<summary>([\s\S]*?)<\/summary>/),
    item_count: (html.match(/class="grammar-item /g) || []).length,
    // The term form's Grammar button marks the card it jumped to, and tests
    // address cards by key, so the anchor has to survive into the markup.
    keys: [...html.matchAll(/data-grammar-key="([^"]*)"/g)].map((m) => m[1]),
    // The header's scope group: which buttons exist, and which one the
    // panel marked active.  Read from the stub DOM's classes -- that is
    // where syncScopeControl records it -- rather than from the HTML, which
    // the panel never re-serialises after the class change.  "sentence" is
    // only rendered when the panel was opened from a sentence, and "book"
    // is dropped for fixed-page books (manga / pdf), so the set itself is
    // part of the contract.
    scopes: scopeButtons.map((b) => b.getAttribute("data-scope")),
    active_scope: (function () {
      const active = scopeButtons.find((b) =>
        b.classList.contains("grammar-analysis-panel__scope--active")
      );
      return active ? active.getAttribute("data-scope") : null;
    })(),
    // The folded reference block starts open: the panel owns a whole column
    // of the reader, so the translated example is worth showing without a
    // click.  Asserted on the attribute rather than on rendered height,
    // which a stub DOM cannot report.
    more_open: /<details class="grammar-item__more" open>/.test(html),
  };
}

const out = snapshot();
out.html = lastHtml;
// A test may ask for the state after a scope switch: the payload carries
// "click" with the scope to drive, and the snapshot is taken afterwards so
// the new markup and the new request are both visible.
if (input.click) {
  clickScope(input.click);
  const after = snapshot();
  after.html = lastHtml;
  after.urls = requestedUrls;
  process.stdout.write(JSON.stringify(after));
} else {
  process.stdout.write(JSON.stringify(out));
}
