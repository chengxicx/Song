/**
 * Harness for tests/unit/book/test_cue_panel_js.py.
 *
 * Runs the real lute/static/js/book-edit-cues.js against a stub DOM and
 * a stub <audio>, so the panel's auto-pause state machine can be driven
 * one timeupdate at a time -- a real browser only offers ~4 timeupdates
 * a second, which is far too coarse to pin the cue-boundary handling
 * (see the "a seek lands a hair before the time asked for" case below).
 *
 * One scenario per process; a JSON payload on stdin:
 *
 *   {
 *     "cues": [{"i":0,"start":0,"end":1,"text":"a"}, ...],
 *     "quantize": 0.000002,   // how early a seek lands, in seconds
 *     "auto_pause": true,
 *     "steps": [ {"click_row": 3}, {"tick": 4.999998}, {"tick": 5.3} ]
 *   }
 *
 * and the audio element's state after every step on stdout.  `tick` sets
 * the playhead and fires one timeupdate; `click_row` fires a click on the
 * row's line-number span, which is how the panel is played from.
 *
 * Only the DOM and the audio element are faked.  bind_line_click() is
 * stubbed too: the real one (lute-cursor.js) defers the action by one
 * double-click window to let a double-click select a word instead, and
 * that arbitration is not what is under test here.
 */

const fs = require("fs");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const modPath = process.argv[2];
const QUANTIZE = input.quantize || 0;

/* ---------- a very small DOM ---------- */

class FakeEl {
  constructor(tag) {
    this.tagName = (tag || "").toUpperCase();
    this.children = [];
    this.style = {};
    this.listeners = {};
    this.classes = new Set();
    this.value = "";
    this.textContent = "";
    this.html = "";
    this.scrollTop = 0;
    this.scrollHeight = 20;
    this.isConnected = true;
    this.offsetParent = {};
  }
  get classList() {
    const s = this.classes;
    return {
      toggle: (c, on) => (on ? s.add(c) : s.delete(c)),
      add: (c) => s.add(c),
      remove: (c) => s.delete(c),
      contains: (c) => s.has(c),
    };
  }
  set className(v) {
    this.classes = new Set(String(v).split(/\s+/).filter(Boolean));
  }
  get className() {
    return [...this.classes].join(" ");
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
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
  querySelectorAll(sel) {
    // Only the ".cue-txt" / ".cue-time-*" lookups matter here.
    const cls = sel.replace(/^\./, "");
    const out = [];
    const walk = (n) => {
      for (const c of n.children) {
        if (c.classes.has(cls)) out.push(c);
        walk(c);
      }
    };
    walk(this);
    return out;
  }
  scrollIntoView() {}
  addEventListener(ev, fn) {
    (this.listeners[ev] = this.listeners[ev] || []).push(fn);
  }
  fire(ev, event) {
    (this.listeners[ev] || []).forEach((fn) => fn(event || { preventDefault() {} }));
  }
}

class FakeAudio extends FakeEl {
  constructor() {
    super("audio");
    this.position = 0;
    this.paused = true;
    this.ended = false;
    this.duration = 300;
    this.seekable = { length: 1, start: () => 0, end: () => 300 };
    this.play_calls = 0;
    this.pause_calls = 0;
    this.seeks = [];
  }
  get currentTime() {
    return this.position;
  }
  set currentTime(v) {
    // A real seek lands on the audio's own frame grid, which can be a
    // hair BEFORE the time that was asked for; that is the whole point
    // of this harness.
    this.seeks.push(v);
    this.position = Math.max(0, v - QUANTIZE);
    this.fire("seeking");
    this.fire("seeked");
  }
  play() {
    this.play_calls += 1;
    this.paused = false;
    this.ended = false;
    this.fire("play");
    this.fire("playing");
  }
  pause() {
    this.pause_calls += 1;
    this.paused = true;
    this.fire("pause");
  }
  tick(t) {
    this.position = t;
    this.fire("timeupdate");
  }
}

const byId = {};
[
  "cueEditorPanel",
  "cuePanelTab",
  "cuePanelRows",
  "cuePanelWarn",
  "cuePanelCount",
  "cuePanelCollapse",
  "cuePanelPlayer",
  "cuePlayBtn",
  "cuePrevBtn",
  "cueNextBtn",
  "cueAutoPauseBtn",
  "cueCurrentTime",
  "cueDuration",
  "cueProgress",
  "cueProgressFill",
  "cueProgressKnob",
  "text",
  "editPageForm",
  "cueDataInput",
].forEach((id) => {
  byId[id] = new FakeEl("div");
});
byId.cueAudio = new FakeAudio();
byId.text.value = input.cues.map((c) => c.text).join("\n");

global.window = {
  LUTE_CUE_EDIT: {
    mode: "page",
    hasCues: true,
    audioUrl: "/useraudio/stream/1",
    cues: input.cues,
  },
  setTimeout: (fn) => setTimeout(fn, 0),
};
global.document = {
  getElementById: (id) => byId[id] || null,
  createElement: (tag) => new FakeEl(tag),
  body: { classList: new FakeEl("body").classList },
  addEventListener() {},
};
global.localStorage = { getItem: () => null, setItem() {} };
global.bind_line_click = (el, action) => el.addEventListener("click", action);

eval(fs.readFileSync(modPath, "utf8")); // eslint-disable-line no-eval

const audio = byId.cueAudio;
if (input.auto_pause) byId.cueAutoPauseBtn.fire("click");

const state = (step) => ({
  step: step,
  paused: audio.paused,
  t: Number(audio.currentTime.toFixed(6)),
  play_calls: audio.play_calls,
  pause_calls: audio.pause_calls,
  seeks: audio.seeks.map((s) => Number(s.toFixed(6))),
  active_row: byId.cuePanelRows.children.findIndex((r) =>
    r.classes.has("active")
  ) + 1,
});

const results = [];
(input.steps || []).forEach((s, i) => {
  if (s.click_row !== undefined) {
    byId.cuePanelRows.children[s.click_row - 1].fire("click");
  } else if (s.click !== undefined) {
    // A player control, e.g. {"click": "cueNextBtn"}.
    if (!byId[s.click]) throw new Error(`no element ${s.click}`);
    byId[s.click].fire("click");
  } else if (s.tick !== undefined) {
    audio.tick(s.tick);
  } else {
    throw new Error(`unknown step ${JSON.stringify(s)}`);
  }
  results.push(state(i));
});

process.stdout.write(JSON.stringify({ results: results }));
