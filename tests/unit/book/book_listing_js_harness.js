/*
 * Load-order harness for static/js/book-listing-shared.js.
 *
 * The home page (book/tablelisting.html) loads that file with `defer`, so it
 * executes AFTER the page's own inline <script> -- the one that defines the
 * real setTagFilter / setNewWordFilter / setTypeFilter.  A classic script's
 * top-level `function setTagFilter(tag) {}` replaces the global binding, so
 * an unguarded declaration in the deferred file silently wiped the page's
 * implementation and every tag / new-word / type click became a no-op.
 *
 * This harness reproduces that order: it installs page-style implementations
 * first (when the payload says the page defines them), evaluates the real
 * module in the same global scope a <script> would use, then calls the three
 * setters and reports which implementation ran.
 *
 * Payload on stdin: {"page_defines": true|false}
 * argv[2]: path to book-listing-shared.js
 */

const fs = require("fs");

/* The reading page injects window.I18N via base.html; the harness runs
   the module bare in Node, so provide the identity fallback it uses. */
globalThis.LUTE_I18N = {};
globalThis.I18N = { t: (k) => k };

const vm = require("vm");

const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const modulePath = process.argv[2];

// A browser script's `window` is its global object; mirror that so a module
// that feature-detects `window.setTagFilter` sees the same thing here.
globalThis.window = globalThis;

// The module's top level only touches `$` (to register delegated handlers)
// and passes `document` to it.  Nothing else is read at load time.
globalThis.document = {};
globalThis.$ = function () {
  return { on: function () {} };
};

const calls = [];
if (payload.page_defines) {
  globalThis.setTagFilter = function (tag) {
    calls.push(["tag", tag]);
  };
  globalThis.setNewWordFilter = function (filter) {
    calls.push(["newword", filter]);
  };
  globalThis.setTypeFilter = function (btype) {
    calls.push(["type", btype]);
  };
}

// Exactly what the browser does for a classic script: top-level `function`
// declarations land on the global object, replacing what was there.
vm.runInThisContext(fs.readFileSync(modulePath, "utf8"), {
  filename: modulePath,
});

const defined = {
  setTagFilter: typeof globalThis.setTagFilter,
  setNewWordFilter: typeof globalThis.setNewWordFilter,
  setTypeFilter: typeof globalThis.setTypeFilter,
};

let error = null;
try {
  globalThis.setTagFilter("probe");
  globalThis.setNewWordFilter("HARD");
  globalThis.setTypeFilter("text");
} catch (e) {
  error = String(e);
}

console.log(JSON.stringify({ defined: defined, calls: calls, error: error }));
