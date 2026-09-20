/**
 * Harness for tests/unit/review/test_criteria_builder_js.py.
 *
 * Two modes, both driven by a JSON payload on stdin:
 *
 *   { "meta": {...}, "cases": [...] }        -- parse/serialize only
 *   { "meta": {...}, "init": {...}, "act": [ {"id","value"} ... ] }
 *                                            -- build a stub DOM, run the
 *                                               page's init(), report the
 *                                               resulting control values
 *
 * The module is the real lute-review-criteria.js; only the DOM is faked.
 * That keeps the page's wiring (which element ids it needs, which
 * listeners it attaches, what it writes into the textarea) under test.
 */
const fs = require("fs");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const modPath = process.argv[2];

/* ---------- a very small DOM ---------- */

class FakeEl {
  constructor(tag) {
    this.tagName = (tag || "").toUpperCase();
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.attrs = {};
    this.listeners = {};
    this.classes = new Set();
    this.value = "";
    this.textContent = "";
    this.html = "";
    this.hidden = false;
    this.open = false;
    this.disabled = false;
    this.id = "";
  }
  get classList() {
    const s = this.classes;
    return {
      toggle: (c, on) => (on ? s.add(c) : s.delete(c)),
      add: (c) => s.add(c),
      contains: (c) => s.has(c),
    };
  }
  set className(v) {
    this.classes = new Set(String(v).split(/\s+/).filter(Boolean));
  }
  get className() {
    return [...this.classes].join(" ");
  }
  // HTMLSelectElement.options: every option, including those nested in
  // an <optgroup>.  The module matches presets against this.
  get options() {
    const out = [];
    this.children.forEach((c) => {
      if (c.tagName === "OPTION") out.push(c);
      else (c.children || []).forEach((o) => out.push(o));
    });
    return out;
  }
  set innerHTML(v) {
    this.html = String(v);
    if (this.html === "") this.children = [];
  }
  get innerHTML() {
    return this.html;
  }
  appendChild(c) {
    this.children.push(c);
    return c;
  }
  setAttribute(k, v) {
    this.attrs[k] = v;
  }
  getAttribute(k) {
    return this.attrs[k];
  }
  addEventListener(ev, fn) {
    (this.listeners[ev] = this.listeners[ev] || []).push(fn);
  }
  fire(ev) {
    (this.listeners[ev] || []).forEach((fn) => fn({ preventDefault() {} }));
  }
  querySelectorAll() {
    return [];
  }
}

function collect_text(node) {
  let out = node.textContent || "";
  node.children.forEach((c) => {
    out += collect_text(c);
  });
  return out;
}

const byId = {};
const ids = [
  "criteria_builder",
  "criteria_rows",
  "criteria_joiner",
  "criteria_preset",
  "criteria_text",
  "criteria_preview",
  "criteria_warning",
  "criteria_advanced",
  "criteria_add",
  "review_spec_form",
];
ids.forEach((id) => {
  const e = new FakeEl("div");
  e.id = id;
  // Mirrors the `hidden` attribute in review/_form.html.
  if (id === "criteria_warning") e.hidden = true;
  byId[id] = e;
});

let dom_ready = null;
const metaText = JSON.stringify(input.meta);
const initialText =
  input.init === undefined ? null : JSON.stringify(input.init);

global.window = {};
global.Option = function (text, value) {
  const o = new FakeEl("option");
  o.textContent = text;
  o.value = value;
  return o;
};
global.document = {
  addEventListener: (ev, fn) => {
    if (ev === "DOMContentLoaded") dom_ready = fn;
  },
  getElementById: (id) => {
    if (id === "criteria_meta") return { textContent: metaText };
    if (id === "criteria_initial") return { textContent: initialText };
    return byId[id] || null;
  },
  createElement: (tag) => new FakeEl(tag),
  body: { appendChild() {} },
};

eval(fs.readFileSync(modPath, "utf8")); // eslint-disable-line no-eval
const api = global.window.LuteReviewCriteria;

/* ---------- mode 1: parse / serialize ---------- */

if (input.cases) {
  const out = {};
  for (const c of input.cases) {
    const parsed = api._parse(c);
    out[c] =
      parsed === null
        ? null
        : {
            parsed: parsed,
            rebuilt: api._serialize_rows(parsed.joiner, parsed.rows),
          };
  }
  process.stdout.write(JSON.stringify(out));
  return;
}

/* ---------- mode 2: run the page's init() ---------- */

if (dom_ready === null) {
  process.stderr.write("the module never registered a DOMContentLoaded hook\n");
  process.exit(1);
}
dom_ready();

const groups = byId.criteria_preset.children.map((g) => ({
  label: g.label,
  count: (g.children || []).length,
}));

// Drive the controls the way a user would.
(input.act || []).forEach((step) => {
  const e = byId[step.id];
  if (!e) throw new Error(`no element ${step.id}`);
  e.value = step.value;
  e.fire("change");
});

// Only the condition rows; an empty criteria renders a <p> note instead.
const rows = (byId.criteria_rows.children || [])
  .filter((r) => r.className === "criteria-row")
  .map((r) => ({
    field: r.children[0].value,
    op: r.children[1].value,
    value: r.children[2].value,
    op_labels: r.children[1].children.map((o) => o.textContent),
  }));

process.stdout.write(
  JSON.stringify({
    row_count: rows.length,
    rows: rows,
    preset_groups: groups,
    preset_count: byId.criteria_preset.children.reduce(
      (n, g) => n + (g.children || []).length,
      0
    ),
    joiner: byId.criteria_joiner.value,
    preset_selected: byId.criteria_preset.value,
    textarea: byId.criteria_text.value,
    preview: byId.criteria_preview.textContent,
    warning_hidden: byId.criteria_warning.hidden,
    empty_note: collect_text(byId.criteria_rows),
  })
);
