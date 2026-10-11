/**
 * Harness for tests/unit/read/test_tts_player_cues.py.
 *
 * Runs the real lute/static/js/tts-player.js -- and, for the clause
 * shadowing scenario, lute/static/js/lute-shadowing.js -- against a
 * stub DOM, a manual timer queue and a fake SpeechSynthesis engine, so
 * the way the TTS player turns a long sentence into clause cues and
 * paces them can be checked without a browser.
 *
 * What is interesting here is not "does it speak" but WHAT it speaks
 * and WHEN: a long sentence must become several clause cues (short
 * sentences stay whole), each clause followed by a held rest before
 * the next clause of the same sentence but not before the next
 * sentence, a stop during that rest must kill the pending cue, and
 * auto-pause must park on the clause rather than the sentence.  The
 * clause cues must also hand shadowing the clause's own live spans.
 *
 * One scenario per process; a JSON payload on stdin:
 *
 *   {
 *     "sentences": [
 *       {"cjk": "时候既然是深冬；渐近故乡时，天气又阴晦了。"},
 *       {"latin": "The first clause goes on for a good while, and so on."}
 *     ],
 *     "steps": [
 *       {"autopause": true},
 *       {"play": true},
 *       {"flush_all": true},
 *       {"state": true}
 *     ]
 *   }
 *
 * A CJK sentence becomes one span per character (the reading page
 * tokenizes it that way); a Latin sentence becomes word spans glued to
 * their trailing punctuation, separated by plain-space text nodes.
 * Both carry the injected 🔊 button as their first child, since the
 * splitter has to see past it.
 *
 * Steps:
 *   {"build": true}          (re)build the cue list
 *   {"play": true} / {"stop": true}
 *   {"autopause": b} / {"loop": b}
 *   {"seek_cue": i}          jump to a cue, no autoplay
 *   {"jump": -1|1}           prev/next cue buttons
 *   {"flush_one": true}      run exactly the next due timer
 *   {"advance_ms": n}        run every timer due within n ms
 *   {"flush_all": true}      run the timer queue until it is empty
 *   {"shadow_apply_whole": sentIdx}   cue-changed without spanEls
 *   {"state": true}          push a snapshot of everything observed
 *
 * The engine speaks every utterance for UTTER_MS of fake time, so the
 * recorded speak timestamps pin the pacing exactly: a clause boundary
 * lands UTTER_MS + TTS_PART_PAUSE_MS after the previous clause, a
 * sentence boundary only UTTER_MS + the usual 20 ms start delay.
 *
 * The harness runs under node (CI, invoked by the pytest wrapper) and
 * also under macOS JavaScriptCore via osascript -- when the globals
 * __INPUT_JSON__ / __EMIT__ / __MODULE_FS__ are provided by the
 * caller -- so the scenario can be exercised during development on a
 * machine without node.
 */

const fsShimLike = typeof require === "function" ? require("fs") : null;

/* A deterministic clock: the player's playback chain is entirely
   setTimeout-driven, so the harness owns time and runs it by hand.
   These shadow the runtime's own timers inside this file's scope, and
   the eval'd player code resolves them through the scope chain. */
let nowMs = 0;
const timers = [];
let timerSeq = 0;
function setTimeout(fn, ms) {
  timerSeq += 1;
  timers.push({ seq: timerSeq, at: nowMs + (ms || 0), fn: fn });
  return timerSeq;
}
function clearTimeout() {} // the player cancels via gen checks, not clearTimeout

const UTTER_MS = 400; // fake speech duration for every utterance
const START_DELAY_MS = 20; // the player's anti-Chrome-bug start delay

const performance = { now: () => nowMs };

/* ---------- a very small DOM ---------- */

class FakeTextNode {
  constructor(text) {
    this.nodeType = 3;
    this._text = String(text);
    this.parentNode = null;
  }
  get textContent() {
    return this._text;
  }
  set textContent(v) {
    this._text = String(v);
  }
  cloneNode() {
    return new FakeTextNode(this._text);
  }
  get isConnected() {
    let n = this;
    while (n.parentNode) n = n.parentNode;
    return Boolean(n._root);
  }
}

class FakeEl {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = (tag || "").toUpperCase();
    this.children = [];
    this.classes = new Set();
    this.attrs = new Map();
    this.parentNode = null;
    this._text = null;
    this._root = false;
  }
  get classList() {
    const s = this.classes;
    return {
      add: (c) => s.add(c),
      remove: (c) => s.delete(c),
      contains: (c) => s.has(c),
    };
  }
  // The player's splitter walks childNodes; the harness keeps one
  // children array and aliases it (live, like a real NodeList).
  get childNodes() {
    return this.children;
  }
  getAttribute(name) {
    const v = this.attrs.get(name);
    return v === undefined ? null : v;
  }
  setAttribute(name, v) {
    this.attrs.set(name, String(v));
  }
  get isConnected() {
    let n = this;
    while (n.parentNode) n = n.parentNode;
    return Boolean(n._root);
  }
  // Like a real element: a node's text is its descendants' unless set
  // directly.
  get textContent() {
    if (this._text !== null) return this._text;
    return this.children.map((c) => c.textContent).join("");
  }
  set textContent(v) {
    this._text = String(v);
    this.children = [];
  }
  get innerHTML() {
    const ser = (n) => {
      if (n.nodeType === 3) return n.textContent;
      const cls = Array.from(n.classes).join(" ");
      const attrs = Array.from(n.attrs)
        .map(([k, v]) => ` ${k}="${v}"`)
        .join("");
      const open = `<${n.tagName.toLowerCase()}${cls ? ` class="${cls}"` : ""}${attrs}>`;
      return open + n.children.map(ser).join("") + `</${n.tagName.toLowerCase()}>`;
    };
    return this.children.map(ser).join("");
  }
  set innerHTML(v) {
    this._text = String(v);
    this.children = [];
  }
  cloneNode(deep) {
    const c = new FakeEl(this.tagName);
    c.classes = new Set(this.classes);
    c.attrs = new Map(this.attrs);
    c._text = this._text;
    if (deep) {
      this.children.forEach((child) => {
        c.appendChild(child.cloneNode(true));
      });
    }
    return c;
  }
  appendChild(c) {
    c.parentNode = this;
    this.children.push(c);
    return c;
  }
  removeChild(c) {
    const i = this.children.indexOf(c);
    if (i >= 0) {
      this.children.splice(i, 1);
      c.parentNode = null;
    }
    return c;
  }
  insertBefore(c, ref) {
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) {
      this.appendChild(c);
    } else {
      c.parentNode = this;
      this.children.splice(i, 0, c);
    }
    return c;
  }
  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }
  closest(sel) {
    const cls = sel.replace(/^\./, "");
    let n = this;
    while (n) {
      if (n.classes && n.classes.has(cls)) return n;
      n = n.parentNode;
    }
    return null;
  }
  querySelectorAll(sel) {
    // Supports the shapes lute-shadowing.js uses: ".class",
    // "span.textitem", "span.word[data-lang-id]".
    const m = sel.match(/^(?:([\w-]+))?(?:\.([\w-]+))?(?:\[([\w-]+)(?:="?([^"]*)"?)?\])?$/);
    const tag = m && m[1] ? m[1].toUpperCase() : null;
    const cls = m && m[2] ? m[2] : null;
    const attr = m && m[3] ? m[3] : null;
    const attrVal = m && m[4] !== undefined ? m[4] : null;
    const out = [];
    const walk = (n) => {
      (n.children || []).forEach((c) => {
        if (
          c.nodeType === 1 &&
          (!tag || c.tagName === tag) &&
          (!cls || (c.classes && c.classes.has(cls))) &&
          (!attr ||
            (c.attrs && c.attrs.has(attr) &&
              (attrVal === null || c.attrs.get(attr) === attrVal)))
        ) {
          out.push(c);
        }
        walk(c);
      });
    };
    walk(this);
    return out;
  }
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
}

function textNode(t) {
  return new FakeTextNode(t);
}

/* One .textsentence exactly as the reading page renders it: the 🔊
   button first, then the tokens.  CJK: one span per character, word
   spans carrying data-lang-id like the page does.  Latin: word spans
   glued to their trailing punctuation, plain-space text nodes between. */
function buildSentence(spec) {
  const sent = new FakeEl("span");
  sent.classes.add("textsentence");
  const btn = new FakeEl("span");
  btn.classes.add("lute-sentence-play-btn");
  btn.textContent = "\uD83D\uDD0A";
  sent.appendChild(btn);

  const addWord = (t, punct) => {
    const sp = new FakeEl("span");
    sp.classes.add("textitem");
    if (!punct) {
      sp.classes.add("word");
      sp.setAttribute("data-lang-id", "1");
    }
    sp.textContent = t;
    sent.appendChild(sp);
    return sp;
  };

  if (spec.cjk !== undefined) {
    for (const ch of spec.cjk) {
      const punct = /[\s\u3000-\u303F\uFF00-\uFFEF]/.test(ch);
      addWord(ch, punct);
    }
  } else if (spec.latin !== undefined) {
    spec.latin.split(/(\s+)/).forEach((tok) => {
      if (!tok) return;
      if (/^\s+$/.test(tok)) sent.appendChild(textNode(tok));
      else addWord(tok, false);
    });
  }
  return sent;
}

const thetext = new FakeEl("div");
thetext.id = "thetext";
thetext._root = true;

/* ---------- the globals tts.js would have provided ---------- */

// Same shape as tts.js's cleanSentenceText (this file runs without it).
function cleanSentenceText(rawText) {
  return rawText
    .replace(/[#＃]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
function getSelectedVoice() {
  return null;
}
function selectBestVoiceForLang() {
  return null;
}
function getCurrentLangCode() {
  return "zh-CN";
}

/* ---------- the fake speech engine + event plumbing ---------- */

const spoken = []; // { text, at, rate }
const events = []; // { type, detail } dispatched on window

class SpeechSynthesisUtterance {
  constructor(text) {
    this.text = text || "";
    this.lang = "";
    this.rate = 1;
    this.voice = null;
    this.onend = null;
    this.onerror = null;
    this.onboundary = null;
  }
}

class CustomEvent {
  constructor(type, init) {
    this.type = type;
    this.detail = init ? init.detail : null;
  }
}

const speechSynthesis = {
  getVoices() {
    return [];
  },
  cancel() {},
  speak(u) {
    spoken.push({ text: u.text, at: nowMs, rate: u.rate });
    setTimeout(function () {
      if (typeof u.onend === "function") u.onend();
    }, UTTER_MS);
  },
};

const listeners = {};
const window = {
  speechSynthesis: speechSynthesis,
  // ttsMarkPlayingSentence guards on this and dispatches the
  // lute:cue-changed event behind it, so shadowing needs it present.
  LutePlayingLine: {
    setElement() {},
    clear() {},
  },
  addEventListener(type, fn) {
    (listeners[type] = listeners[type] || []).push(fn);
  },
  removeEventListener() {},
  dispatchEvent(ev) {
    events.push({ type: ev.type, detail: ev.detail });
    (listeners[ev.type] || []).slice().forEach((fn) => {
      try {
        fn(ev);
      } catch (e) {
        /* a listener error must not take the player down */
      }
    });
  },
};

const document = {
  readyState: "complete",
  getElementById(id) {
    if (id === "thetext") return thetext;
    return null;
  },
  createElement(tag) {
    return new FakeEl(tag);
  },
  addEventListener() {},
  querySelectorAll() {
    return [];
  },
};

const localStorage = {
  getItem() {
    return null;
  },
  setItem() {},
  removeItem() {},
};

// The shadowing panel fetches kana readings for its unit; give it a
// chainable never-resolving response so the fetch attempt is harmless.
function fetch() {
  const p = { then(fn) { return p; }, catch(fn) { return p; } };
  return p;
}

/* ---------- module loading ---------- */

let lastExports = null;
function __EXPORT__(obj) {
  lastExports = obj;
}

function loadModule(path, exportSnippet) {
  const src =
    typeof __MODULE_FS__ === "function"
      ? __MODULE_FS__(path)
      : fsShimLike.readFileSync(path, "utf8");
  const wrapped = src + "\n;__EXPORT__(" + exportSnippet + ");";
  eval(wrapped); // eslint-disable-line no-eval
  return lastExports;
}

/* ---------- the scenario runner ---------- */

function runScenario(scenario, ttsPath, shadowingPath) {
  (scenario.sentences || []).forEach(function (spec) {
    const p = new FakeEl("p");
    p.appendChild(buildSentence(spec));
    thetext.appendChild(p);
  });

  spoken.length = 0;
  events.length = 0;
  timers.length = 0;
  nowMs = 0;

  const exportsBox = {};
  exportsBox.tts = loadModule(
    ttsPath,
    `{
      buildCues: ttsBuildCues,
      togglePlay: ttsTogglePlay,
      stop: ttsStop,
      seekToCue: ttsSeekToCue,
      jumpCue: ttsJumpCue,
      setAutoPause: function (v) { ttsAutoPause = v; },
      setLoop: function (v) { ttsLoop = v; },
      cues: function () { return ttsCues; },
      sentenceCount: function () { return ttsCueSentenceCount; },
      state: function () {
        return {
          playing: ttsPlaying,
          paused: ttsPaused,
          cueIndex: ttsCueIndex,
          virtualTime: ttsVirtualTime,
          total: ttsTotalDuration,
        };
      },
    }`
  );

  let shadowExports = null;
  if (shadowingPath) {
    shadowExports = loadModule(
      shadowingPath,
      `{
        applyCue: shadowingApplyCue,
        unit: function () {
          return shadowingUnit
            ? {
                texts: shadowingUnit.texts,
                fullText: shadowingUnit.fullText,
                langId: shadowingUnit.langId,
                elConnected: !!(shadowingUnit.el && shadowingUnit.el.isConnected),
                spanCount: shadowingUnit.spans.length,
              }
            : null;
        },
      }`
    );
  }

  function flushOne() {
    if (!timers.length) return false;
    let best = 0;
    for (let i = 1; i < timers.length; i++) {
      if (
        timers[i].at < timers[best].at ||
        (timers[i].at === timers[best].at && timers[i].seq < timers[best].seq)
      ) {
        best = i;
      }
    }
    const t = timers.splice(best, 1)[0];
    nowMs = Math.max(nowMs, t.at);
    t.fn();
    return true;
  }

  function flushAll() {
    let guard = 0;
    while (timers.length && guard++ < 10000) flushOne();
  }

  function advanceMs(n) {
    const target = nowMs + n;
    let guard = 0;
    while (timers.length && guard++ < 10000) {
      let best = 0;
      for (let i = 1; i < timers.length; i++) {
        if (timers[i].at < timers[best].at) best = i;
      }
      if (timers[best].at > target) break;
      flushOne();
    }
    nowMs = Math.max(nowMs, target);
  }

  function cueSnapshot() {
    return exportsBox.tts.cues().map(function (c) {
      return {
        text: c.text,
        sentIdx: c.sentIdx,
        pauseAfter: c.pauseAfter,
        start: c.start,
        end: c.end,
        duration: c.duration,
        hasSpanEls: Array.isArray(c.spanEls),
        spanCount: c.spanEls ? c.spanEls.length : 0,
      };
    });
  }

  function snapshot() {
    return {
      now: nowMs,
      spoken: spoken.map(function (s) {
        return { text: s.text, at: s.at };
      }),
      events: events.map(function (e) {
        const d = e.detail || {};
        return {
          type: e.type,
          index: d.index,
          sentIdx: d.sentIdx,
          sentenceCount: d.sentenceCount,
          hasSpanEls: Array.isArray(d.spanEls),
        };
      }),
      cues: cueSnapshot(),
      tts: exportsBox.tts.state(),
      shadowing: shadowExports ? shadowExports.unit() : null,
    };
  }

  const results = [];
  (scenario.steps || []).forEach(function (step, i) {
    if (step.build) {
      exportsBox.tts.buildCues();
    } else if (step.play) {
      exportsBox.tts.togglePlay();
    } else if (step.stop) {
      exportsBox.tts.stop();
    } else if (step.autopause !== undefined) {
      exportsBox.tts.setAutoPause(step.autopause);
    } else if (step.loop !== undefined) {
      exportsBox.tts.setLoop(step.loop);
    } else if (step.seek_cue !== undefined) {
      exportsBox.tts.seekToCue(step.seek_cue, false);
    } else if (step.jump !== undefined) {
      exportsBox.tts.jumpCue(step.jump);
    } else if (step.flush_one) {
      flushOne();
    } else if (step.advance_ms !== undefined) {
      advanceMs(step.advance_ms);
    } else if (step.flush_all) {
      flushAll();
    } else if (step.shadow_apply_whole !== undefined) {
      // A cue-changed without spanEls: the whole-sentence shape, which
      // must still fall back to the sentence unit.
      shadowExports.applyCue({
        source: "tts",
        index: 0,
        sentIdx: step.shadow_apply_whole,
        sentenceCount: exportsBox.tts.sentenceCount(),
        spanEls: null,
      });
    } else if (step.shadow_apply_cue !== undefined) {
      // The real thing: the detail ttsMarkPlayingSentence dispatches
      // for this cue, spanEls included.
      const cue = exportsBox.tts.cues()[step.shadow_apply_cue];
      shadowExports.applyCue({
        source: "tts",
        index: step.shadow_apply_cue,
        sentIdx: cue.sentIdx,
        sentenceCount: exportsBox.tts.sentenceCount(),
        spanEls: cue.spanEls || null,
      });
    } else if (step.state) {
      results.push({ step: i, state: snapshot() });
    } else {
      throw new Error("unknown step " + JSON.stringify(step));
    }
  });

  return { results: results };
}

/* ---------- entry ---------- */

if (typeof process !== "undefined" && process.argv && process.argv[1]) {
  const ttsPath = process.argv[2];
  const shadowingPath = process.argv[3] || null;
  const inputJson =
    typeof __INPUT_JSON__ === "string"
      ? __INPUT_JSON__
      : fsShimLike.readFileSync(0, "utf8");
  const out = JSON.stringify(
    runScenario(JSON.parse(inputJson), ttsPath, shadowingPath)
  );
  if (typeof __EMIT__ === "function") __EMIT__(out);
  else process.stdout.write(out);
}
