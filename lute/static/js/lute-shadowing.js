/* lute-shadowing.js
   --------------------------------------------------------------
   影子跟读 (shadowing) practice area, entered from the player's
   "Shadow" toggle (tts_player.html / youtube_player.html, next to
   the voice/settings gear).

   While active, the right pane becomes the practice area:
     - the current sentence, following the player's playhead via the
       lute:cue-changed events dispatched by media-player-base.js and
       tts-player.js,
     - a big record button (MediaRecorder -> /read/shadowing/transcribe,
       which whisper-transcribes the clip and diffs it against the
       sentence's own word tokens),
     - the diff result (score, speech rate, what whisper heard),
     - a session history of attempts.

   Verdicts are painted onto the sentence's word spans in the reading
   text as underline-only marks (the words' status background colours
   stay untouched) and onto the tokens shown in the panel.

   Auto mode: with it on (and the engine's Auto-pause on), the cue-end
   event (lute:cue-ended) starts a recording by itself; stopping the
   take scores it and advances to the next sentence.

   Deliberately NOT wrapped in an IIFE: like tts.js / tts-player.js it
   shares the read page's single global scope and is loaded after them
   (see lute/templates/read/index.html).
   --------------------------------------------------------------
*/
"use strict";

/* ------------------------------------------------------------------
 * 1. State
 * ------------------------------------------------------------------ */

let shadowingActive = false;
let shadowingAuto = false;
let shadowingBusy = false;
let shadowingStartPending = false;
let shadowingRecorder = null;
let shadowingStream = null;
let shadowingChunks = [];
let shadowingRecording = false;
let shadowingRecStart = 0;
let shadowingRecTimer = null;
let shadowingRecCapTimer = null;
let shadowingUnit = null; // { lines, el, spans, texts, fullText, langId, src }
let shadowingLastCue = null; // last lute:cue-changed detail
let shadowingHistory = [];
let shadowingRecordingBlob = null;
let shadowingRecordingUrl = null;

const SHADOWING_MODEL_KEY = "shadowingModel";
const SHADOWING_AUTO_KEY = "shadowingAuto";
const SHADOWING_REC_CAP_MS = 30000;

const MIC_SVG =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v5a3 3 0 0 0 3 3z"></path><path d="M19 11a1 1 0 1 0-2 0 5 5 0 0 1-10 0 1 1 0 1 0-2 0 7 7 0 0 0 6 6.92V20H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-2v-2.08A7 7 0 0 0 19 11z"></path></svg>';
const STOP_SVG =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg>';
const PREV_SVG =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 6h2v12H7zM20 6l-8.5 6 8.5 6z"></path></svg>';
const NEXT_SVG =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6h2v12h-2zM4 6l8.5 6L4 18z"></path></svg>';
const SPEAKER_SVG =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 10v4a1 1 0 0 0 1 1h3l4 4a1 1 0 0 0 1.7-.7V5.7A1 1 0 0 0 11 5L7 9H4a1 1 0 0 0-1 1z"></path><path d="M16 8.5a5 5 0 0 1 0 7"></path><path d="M18.5 6a8.5 8.5 0 0 1 0 12"></path></svg>';

/* ------------------------------------------------------------------
 * 2. Practice-unit resolution
 *
 * A "unit" is one practiceable sentence: the word spans to diff
 * against (and to paint marks on), the plain text for the panel and
 * TTS, and the language id.  TTS cues carry a sentence index into
 * #thetext .textsentence spans; media cues are resolved to page lines
 * through window.LUTE_PAGE_CUE_MAP (same verification as
 * lute-playing-line.js -- one cue can own several lines).
 * ------------------------------------------------------------------ */

function shadowingSentences() {
  const div = document.getElementById("thetext");
  return div
    ? Array.prototype.slice.call(div.querySelectorAll(".textsentence"))
    : [];
}

function shadowingLineText(el) {
  const clone = el.cloneNode(true);
  clone
    .querySelectorAll(".lute-sentence-play-btn")
    .forEach(function (b) {
      b.remove();
    });
  return (clone.textContent || "").replace(/[\s\u200b]+/g, "");
}

function shadowingLinesForCue(index, text) {
  // lute-playing-line.js setCueIndex resolution, returning the matched
  // <p> elements instead of marking them.
  const div = document.getElementById("thetext");
  if (!div) return [];
  const ps = Array.prototype.slice.call(div.querySelectorAll(":scope > p"));
  if (!ps.length) return [];
  const want = (text || "").replace(/[\s\u200b]+/g, "");
  const map = window.LUTE_PAGE_CUE_MAP;

  if (Array.isArray(map) && map.length === ps.length) {
    const hits = ps.filter(function (p, k) {
      return map[k] === index;
    });
    if (hits.length) {
      let joined = "";
      hits.forEach(function (h) {
        joined += shadowingLineText(h);
      });
      if (joined === want) return hits;
    }
  }
  if (!want) return [];
  for (let j = 0; j < ps.length; j++) {
    if (shadowingLineText(ps[j]) === want) return [ps[j]];
  }
  return [];
}

function shadowingUnitFromEl(el, src) {
  if (!el || !el.isConnected) return null;
  const spans = Array.prototype.slice.call(el.querySelectorAll("span.word"));
  if (!spans.length) return null;
  const clone = el.cloneNode(true);
  clone.querySelectorAll(".lute-sentence-play-btn").forEach(function (b) {
    b.remove();
  });
  const langSpan = el.querySelector("span.word[data-lang-id]");
  return {
    el: el,
    lines: [el],
    spans: spans,
    texts: spans.map(function (sp) {
      return (sp.getAttribute("data-text") || sp.textContent || "").replace(
        /\u200B/g,
        ""
      );
    }),
    fullText: (clone.textContent || "")
      .replace(/\u200B/g, "")
      .replace(/🔊/g, "")
      .trim(),
    langId: langSpan ? langSpan.getAttribute("data-lang-id") : "",
    src: src || null,
  };
}

function shadowingUnitFromLines(lines, src) {
  if (!lines || !lines.length) return null;
  const spans = [];
  lines.forEach(function (l) {
    Array.prototype.push.apply(spans, l.querySelectorAll("span.word"));
  });
  if (!spans.length) return null;
  let fullText = "";
  lines.forEach(function (l) {
    const clone = l.cloneNode(true);
    clone.querySelectorAll(".lute-sentence-play-btn").forEach(function (b) {
      b.remove();
    });
    fullText += (fullText ? "\n" : "") + clone.textContent;
  });
  const langSpan = spans[0];
  return {
    el: lines[0],
    lines: lines,
    spans: spans,
    texts: spans.map(function (sp) {
      return (sp.getAttribute("data-text") || sp.textContent || "").replace(
        /\u200B/g,
        ""
      );
    }),
    fullText: fullText.replace(/\u200B/g, "").replace(/🔊/g, "").trim(),
    langId: langSpan ? langSpan.getAttribute("data-lang-id") : "",
    src: src || null,
  };
}

function shadowingSetUnit(unit) {
  if (shadowingUnit) shadowingClearMarks(shadowingUnit);
  shadowingUnit = unit;
  shadowingRenderCurrent();
  shadowingRenderResultIdle();
}

/* ------------------------------------------------------------------
 * 3. Cue-event following
 * ------------------------------------------------------------------ */

function shadowingApplyCue(d) {
  if (!d || d.index == null || d.index < 0) return false;
  if (d.source === "tts") {
    if (!(d.sentenceCount > 0)) return false;
    const sents = shadowingSentences();
    if (sents.length !== d.sentenceCount) return false; // stale after page swap
    const unit = shadowingUnitFromEl(
      sents[d.sentIdx] || null,
      { type: "tts", sentIdx: d.sentIdx, cueIndex: d.index }
    );
    if (unit) shadowingSetUnit(unit);
    return !!unit;
  }
  if (d.source === "media") {
    const lines = shadowingLinesForCue(d.index, d.text);
    const unit = shadowingUnitFromLines(lines, {
      type: "media",
      cueIndex: d.index,
      text: d.text,
    });
    if (unit) shadowingSetUnit(unit);
    return !!unit;
  }
  return false;
}

function shadowingOnCueChanged(e) {
  shadowingLastCue = e.detail || null;
  if (!shadowingActive || shadowingRecording) return;
  shadowingApplyCue(shadowingLastCue);
}

// Auto mode: the engine paused (or looped) at the cue end -- start the
// take for the sentence that just played.
function shadowingOnCueEnded(e) {
  if (!shadowingActive || !shadowingAuto) return;
  if (shadowingRecording || shadowingBusy || shadowingStartPending) return;
  const d = e.detail || {};
  if (!shadowingUnit || !shadowingUnit.el || !shadowingUnit.el.isConnected) {
    if (!shadowingApplyCue(shadowingLastCue)) return;
  }
  shadowingStartRecording();
}

/* ------------------------------------------------------------------
 * 4. Mode (player "Shadow" toggle)
 * ------------------------------------------------------------------ */

function shadowingSetMode(on) {
  shadowingActive = on;
  const btn =
    document.getElementById("tts-shadowing-btn") ||
    document.getElementById("yt-shadowing-btn");
  if (btn) btn.classList.toggle("on", on);

  if (on) {
    shadowingOpenPanel();
    // Initial sentence: the last known cue if it still resolves,
    // otherwise the first readable sentence on the page.
    if (!shadowingUnit || !shadowingUnit.el || !shadowingUnit.el.isConnected) {
      if (!shadowingApplyCue(shadowingLastCue)) {
        const first = shadowingSentences().find(function (s) {
          return s.querySelector("span.word");
        });
        shadowingSetUnit(shadowingUnitFromEl(first, null));
      }
    }
  } else {
    shadowingCancelRecording();
    if (shadowingUnit) shadowingClearMarks(shadowingUnit);
    shadowingUnit = null;
    shadowingClosePanel();
  }
}

function shadowingToggleMode() {
  shadowingSetMode(!shadowingActive);
}

window.luteShadowingAfterSwap = function () {
  // Page turn / term-save: the old sentence is gone.  Clear marks (they
  // lived on the swapped-out spans), then re-resolve -- the players
  // rebuild their cues on the new text and refire lute:cue-changed,
  // which takes precedence; the first sentence is the fallback.
  if (!shadowingActive) return;
  if (shadowingUnit) {
    shadowingUnit.spans.forEach(function (sp) {
      sp.classList.remove("shadow-ok", "shadow-fuzzy", "shadow-miss");
    });
    shadowingUnit = null;
  }
  if (!shadowingApplyCue(shadowingLastCue)) {
    const first = shadowingSentences().find(function (s) {
      return s.querySelector("span.word");
    });
    shadowingSetUnit(shadowingUnitFromEl(first, null));
  }
};

/* ------------------------------------------------------------------
 * 5. Panel (fills #read_pane_right, mirroring the grammar panel)
 * ------------------------------------------------------------------ */

function shadowingEscapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c];
  });
}

function shadowingClosePanel() {
  const panel = document.getElementById("shadowing-panel");
  if (panel) panel.remove();
  const pane = document.getElementById("read_pane_right");
  if (!pane) return;
  pane.classList.remove("shadowing-mode");
  if (window.matchMedia && window.matchMedia("(max-width: 980px)").matches) {
    pane.classList.remove("open-dict");
    pane.style.removeProperty("transform");
    pane.style.removeProperty("opacity");
    const btm = document.querySelector(".btm-margin-container");
    if (btm) btm.classList.remove("open-dict");
  }
}
window.closeShadowingPanel = function () {
  // Exposed for the LuteTermFormOpened hook: looking a word up leaves
  // shadowing mode entirely, so the player toggle stays in sync.
  shadowingSetMode(false);
};

function shadowingGetModel() {
  let m = null;
  try {
    m = localStorage.getItem(SHADOWING_MODEL_KEY);
  } catch (_) {}
  return ["base", "small", "medium"].indexOf(m) !== -1 ? m : "small";
}

function shadowingOpenPanel() {
  shadowingClosePanel();
  const pane = document.getElementById("read_pane_right");
  const panel = document.createElement("div");
  panel.id = "shadowing-panel";
  panel.className = "shadowing-panel";
  panel.innerHTML =
    '<div class="shadowing-panel__header">' +
    '<span class="shadowing-panel__title">Shadowing</span>' +
    '<button type="button" id="shadowing-auto-btn" class="shadowing-auto-btn"' +
    ' title="Auto: record at every sentence end, score, then advance">Auto</button>' +
    '<select id="shadowing-model" class="shadowing-panel__model" title="Whisper model">' +
    '<option value="base">base</option>' +
    '<option value="small">small</option>' +
    '<option value="medium">medium</option>' +
    "</select>" +
    '<button type="button" class="shadowing-panel__close" aria-label="Close">&times;</button>' +
    "</div>" +
    '<div class="shadowing-panel__body">' +
    '<div id="shadowing-current" class="shadowing-current"></div>' +
    '<div class="shadowing-controls">' +
    '<button type="button" class="shadowing-nav-btn" data-shadowing-nav="-1" title="Previous sentence">' +
    PREV_SVG +
    "</button>" +
    '<button type="button" id="shadowing-listen-btn" class="shadowing-nav-btn" title="Listen to this sentence">' +
    SPEAKER_SVG +
    "</button>" +
    '<button type="button" class="shadowing-nav-btn" data-shadowing-nav="1" title="Next sentence">' +
    NEXT_SVG +
    "</button>" +
    '<button type="button" id="shadowing-rec-btn" class="shadowing-rec-btn" title="Record / stop">' +
    MIC_SVG +
    '<span id="shadowing-rec-time" class="shadowing-rec-time"></span>' +
    "</button>" +
    "</div>" +
    '<div id="shadowing-result" class="shadowing-result"></div>' +
    '<div id="shadowing-history" class="shadowing-history"></div>' +
    "</div>";

  if (pane) {
    pane.classList.add("shadowing-mode");
    // The pane's width is set inline by resize.js on page load (the
    // stylesheet's calc fallback resolves to 0).  If that init raced
    // the first paint, the pane computes to 0 wide and the panel would
    // be invisible -- give it the default width.
    if (
      !window.matchMedia ||
      !window.matchMedia("(max-width: 980px)").matches
    ) {
      if (pane.getBoundingClientRect().width < 50) {
        pane.style.width = "50%";
      }
    }
    pane.appendChild(panel);
    // Small screens keep the pane translated off-screen until a term
    // opens; bring it up so the panel is actually visible.
    if (window.matchMedia && window.matchMedia("(max-width: 980px)").matches) {
      pane.classList.add("open-dict");
      pane.style.transform = "translateY(0)";
      pane.style.opacity = "1";
      const btm = document.querySelector(".btm-margin-container");
      if (btm) btm.classList.add("open-dict");
    }
  } else {
    document.body.appendChild(panel);
  }

  const sel = panel.querySelector("#shadowing-model");
  sel.value = shadowingGetModel();
  sel.addEventListener("change", function () {
    try {
      localStorage.setItem(SHADOWING_MODEL_KEY, sel.value);
    } catch (_) {}
  });
  panel
    .querySelector(".shadowing-panel__close")
    .addEventListener("click", function () {
      shadowingSetMode(false);
    });

  const autoBtn = panel.querySelector("#shadowing-auto-btn");
  autoBtn.classList.toggle("on", shadowingAuto);
  autoBtn.addEventListener("click", function () {
    shadowingSetAuto(!shadowingAuto);
  });

  panel.querySelectorAll("[data-shadowing-nav]").forEach(function (b) {
    b.addEventListener("click", function () {
      shadowingNav(parseInt(b.getAttribute("data-shadowing-nav"), 10));
    });
  });
  panel
    .querySelector("#shadowing-listen-btn")
    .addEventListener("click", shadowingListen);
  panel
    .querySelector("#shadowing-rec-btn")
    .addEventListener("click", shadowingToggleRecording);

  shadowingRenderHistory();
  shadowingRenderCurrent();
  shadowingRenderResultIdle();
}

function shadowingSetAuto(on) {
  shadowingAuto = on;
  const btn = document.getElementById("shadowing-auto-btn");
  if (btn) btn.classList.toggle("on", on);
  try {
    localStorage.setItem(SHADOWING_AUTO_KEY, on ? "1" : "0");
  } catch (_) {}
  if (on) shadowingPrepareEngine();
}

function shadowingPrepareEngine() {
  // The auto loop leans on auto-pause (record while paused at the cue
  // end); loop would replay over the take, so turn it off.
  const ap =
    document.getElementById("tts-autopause-btn") ||
    document.getElementById("yt-autopause-btn");
  if (ap && !ap.classList.contains("on")) ap.click();
  const loop =
    document.getElementById("tts-loop-btn") ||
    document.getElementById("yt-loop-btn");
  if (loop && loop.classList.contains("on")) loop.click();
}

/* ------------------------------------------------------------------
 * 6. Panel rendering
 * ------------------------------------------------------------------ */

function shadowingRenderCurrent() {
  const box = document.getElementById("shadowing-current");
  if (!box) return;
  if (!shadowingUnit) {
    box.innerHTML =
      '<div class="shadowing-panel__state">Play the player, or pick a sentence with the arrows below.</div>';
    return;
  }
  box.innerHTML = shadowingUnit.texts
    .map(function (t, i) {
      return (
        '<span class="shadow-tok" data-idx="' + i + '">' +
        shadowingEscapeHtml(t) +
        "</span>"
      );
    })
    .join(" ");
}

function shadowingRenderResultIdle() {
  const box = document.getElementById("shadowing-result");
  if (!box) return;
  box.innerHTML =
    '<div class="shadowing-result__hint">Record yourself reading the sentence to score it.</div>';
}

function shadowingRenderPanelMessage(msg, isError) {
  const box = document.getElementById("shadowing-result");
  if (!box) return;
  box.innerHTML =
    '<div class="shadowing-panel__state' +
    (isError ? " shadowing-panel__error" : "") +
    '">' +
    shadowingEscapeHtml(msg) +
    "</div>";
}

function shadowingClearMarks(unit) {
  if (!unit) return;
  unit.spans.forEach(function (sp) {
    sp.classList.remove("shadow-ok", "shadow-fuzzy", "shadow-miss");
  });
}

function shadowingPaintVerdicts(unit, data) {
  if (!unit || !unit.el.isConnected) return;
  const statuses = data.statuses || [];
  const fuzzySpoken = data.spoken_for_fuzzy || {};

  unit.spans.forEach(function (sp, i) {
    const st = i < statuses.length ? statuses[i] : 0;
    sp.classList.add(
      st === 2 ? "shadow-ok" : st === 1 ? "shadow-fuzzy" : "shadow-miss"
    );
  });

  const box = document.getElementById("shadowing-current");
  if (box) {
    box.querySelectorAll(".shadow-tok").forEach(function (tok) {
      const i = parseInt(tok.getAttribute("data-idx"), 10);
      const st = i < statuses.length ? statuses[i] : 0;
      tok.classList.add(
        st === 2 ? "shadow-ok" : st === 1 ? "shadow-fuzzy" : "shadow-miss"
      );
      if (st === 1 && fuzzySpoken[i] != null) {
        tok.insertAdjacentHTML(
          "beforeend",
          '<span class="shadow-tok__heard">→ ' +
            shadowingEscapeHtml(fuzzySpoken[i]) +
            "</span>"
        );
      }
    });
  }
}

function shadowingRenderResult(unit, data) {
  const box = document.getElementById("shadowing-result");
  if (!box) return;
  const extras = data.extras || [];
  const isMorpheme = data.token_kind === "morpheme";
  const kindLabel = isMorpheme ? "morphemes/min" : "words/min";

  let html = '<div class="shadowing-score">';
  html +=
    '<span class="shadowing-score__value">' + Number(data.score || 0) + "%</span>";
  if (data.tokens_per_minute != null) {
    html +=
      '<span class="shadowing-score__rate">' +
      data.tokens_per_minute +
      " " +
      kindLabel +
      " · " +
      data.duration +
      "s</span>";
  }
  html += "</div>";
  html +=
    '<div class="shadowing-heard">' +
    shadowingEscapeHtml(data.transcription || "—") +
    "</div>";
  if (extras.length) {
    html +=
      '<div class="shadowing-extras"><span class="shadowing-extras__label">Also heard</span> ' +
      shadowingEscapeHtml(
        extras.join(isMorpheme ? "" : " ")
      ) +
      "</div>";
  }
  box.innerHTML = html;
}

function shadowingRenderHistory() {
  const box = document.getElementById("shadowing-history");
  if (!box) return;
  if (!shadowingHistory.length) {
    box.innerHTML =
      '<div class="shadowing-history__empty">No attempts yet.</div>';
    return;
  }
  box.innerHTML =
    '<div class="shadowing-history__head">This session</div>' +
    shadowingHistory
      .slice(0, 30)
      .map(function (h) {
        return (
          '<div class="shadowing-history__row">' +
          '<span class="shadowing-history__score">' + h.score + "%</span>" +
          '<span class="shadowing-history__text">' +
          shadowingEscapeHtml(h.text) +
          "</span>" +
          (h.rate != null
            ? '<span class="shadowing-history__rate">' + h.rate + "/min</span>"
            : "") +
          "</div>"
        );
      })
      .join("");
}

/* ------------------------------------------------------------------
 * 7. Recording (MediaRecorder), one capture at a time
 * ------------------------------------------------------------------ */

function shadowingCanRecord() {
  return !!(
    window.isSecureContext &&
    navigator.mediaDevices &&
    navigator.mediaDevices.getUserMedia &&
    typeof MediaRecorder !== "undefined"
  );
}

function shadowingPickMimeType() {
  if (typeof MediaRecorder === "undefined") return "";
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  for (let i = 0; i < candidates.length; i++) {
    try {
      if (MediaRecorder.isTypeSupported(candidates[i])) return candidates[i];
    } catch (_) {}
  }
  return "";
}

function shadowingExtForMime(mime) {
  mime = mime || "";
  if (mime.indexOf("mp4") !== -1 || mime.indexOf("m4a") !== -1) return "mp4";
  if (mime.indexOf("ogg") !== -1) return "ogg";
  return "webm";
}

function shadowingPermissionMessage(err) {
  if (err && (err.name === "NotAllowedError" || err.name === "SecurityError")) {
    return "Microphone permission denied.  Allow microphone access for this site to use shadowing.";
  }
  if (err && err.name === "NotFoundError") {
    return "No microphone found.";
  }
  return (
    "Could not start recording" +
    (err && err.message ? ": " + err.message : ".")
  );
}

function shadowingTeardownStream() {
  if (shadowingStream) {
    shadowingStream.getTracks().forEach(function (t) {
      try {
        t.stop();
      } catch (_) {}
    });
    shadowingStream = null;
  }
}

function shadowingRecBtn() {
  return document.getElementById("shadowing-rec-btn");
}

async function shadowingStartRecording() {
  const btn = shadowingRecBtn();
  if (!btn || shadowingBusy || shadowingStartPending || shadowingRecording) {
    return;
  }
  if (!shadowingUnit || !shadowingUnit.el || !shadowingUnit.el.isConnected) {
    return;
  }
  if (!shadowingCanRecord()) {
    shadowingRenderPanelMessage(
      "Microphone recording needs a secure context (open Lute on localhost or HTTPS) and a browser with MediaRecorder support.",
      true
    );
    return;
  }

  // A second click during the getUserMedia await would otherwise start
  // two captures; collapse it.
  shadowingStartPending = true;
  try {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      shadowingRenderPanelMessage(shadowingPermissionMessage(err), true);
      return;
    }
    shadowingStream = stream;
    shadowingChunks = [];

    const mime = shadowingPickMimeType();
    let recorder;
    try {
      recorder = mime
        ? new MediaRecorder(stream, { mimeType: mime })
        : new MediaRecorder(stream);
    } catch (err) {
      shadowingTeardownStream();
      shadowingRenderPanelMessage(shadowingPermissionMessage(err), true);
      return;
    }

    shadowingRecorder = recorder;
    shadowingRecording = true;
    shadowingRecStart = Date.now();
    recorder.ondataavailable = function (e) {
      if (e.data && e.data.size > 0) shadowingChunks.push(e.data);
    };
    recorder.onstop = shadowingOnStop;

    btn.classList.add("recording");
    btn.innerHTML = STOP_SVG + '<span id="shadowing-rec-time" class="shadowing-rec-time"></span>';
    shadowingRecTimer = setInterval(function () {
      const secs = Math.floor((Date.now() - shadowingRecStart) / 1000);
      const el = document.getElementById("shadowing-rec-time");
      if (el) el.textContent = secs + "s";
    }, 250);
    // Auto mode keeps hands free: cap the take so a forgotten stop
    // cannot block the loop forever.
    if (shadowingAuto) {
      shadowingRecCapTimer = setTimeout(function () {
        if (shadowingRecording) shadowingStopRecording();
      }, SHADOWING_REC_CAP_MS);
    }
    recorder.start();
  } finally {
    shadowingStartPending = false;
  }
}

function shadowingStopRecording() {
  if (!shadowingRecording) return;
  shadowingRecording = false;
  if (shadowingRecTimer) {
    clearInterval(shadowingRecTimer);
    shadowingRecTimer = null;
  }
  if (shadowingRecCapTimer) {
    clearTimeout(shadowingRecCapTimer);
    shadowingRecCapTimer = null;
  }
  const btn = shadowingRecBtn();
  if (btn) {
    btn.classList.remove("recording");
    btn.innerHTML = MIC_SVG + '<span id="shadowing-rec-time" class="shadowing-rec-time"></span>';
  }
  const rec = shadowingRecorder;
  if (rec && rec.state === "recording") {
    try {
      rec.stop();
    } catch (_) {
      shadowingRecorder = null;
      shadowingTeardownStream();
    }
  } else {
    shadowingRecorder = null;
    shadowingTeardownStream();
  }
}

function shadowingCancelRecording() {
  // Leaving the mode mid-take: drop the capture without scoring it.
  if (!shadowingRecording) {
    shadowingTeardownStream();
    return;
  }
  shadowingRecording = false;
  if (shadowingRecTimer) {
    clearInterval(shadowingRecTimer);
    shadowingRecTimer = null;
  }
  if (shadowingRecCapTimer) {
    clearTimeout(shadowingRecCapTimer);
    shadowingRecCapTimer = null;
  }
  const rec = shadowingRecorder;
  shadowingRecorder = null;
  if (rec) {
    rec.onstop = null;
    try {
      if (rec.state !== "inactive") rec.stop();
    } catch (_) {}
  }
  shadowingChunks = [];
  shadowingTeardownStream();
}

function shadowingToggleRecording() {
  if (shadowingRecording) {
    shadowingStopRecording();
  } else {
    shadowingStartRecording();
  }
}

function shadowingOnStop() {
  const rec = shadowingRecorder;
  const chunks = shadowingChunks;
  const mime = rec ? rec.mimeType : "";
  const unit = shadowingUnit; // captured: the cue may move while scoring

  shadowingRecorder = null;
  shadowingChunks = [];
  shadowingTeardownStream();

  const blob = new Blob(chunks, { type: mime || "audio/webm" });
  if (!blob.size) {
    shadowingRenderPanelMessage("The recording was empty — try again.", true);
    return;
  }
  shadowingRecordingBlob = blob;
  shadowingSubmit(blob, unit);
}

/* ------------------------------------------------------------------
 * 8. Submit + results
 * ------------------------------------------------------------------ */

async function shadowingSubmit(blob, unit) {
  if (!unit || !unit.texts.length) return;
  shadowingBusy = true;
  shadowingClearMarks(unit);
  shadowingRenderPanelMessage("Transcribing…", false);

  const fd = new FormData();
  fd.append("audio", blob, "shadowing." + shadowingExtForMime(blob.type));
  fd.append("language_id", unit.langId || "");
  fd.append("tokens", JSON.stringify(unit.texts));
  fd.append("model", shadowingGetModel());

  try {
    const resp = await fetch("/read/shadowing/transcribe", {
      method: "POST",
      body: fd,
    });
    let data = {};
    try {
      data = await resp.json();
    } catch (_) {}
    if (!resp.ok) {
      shadowingRenderPanelMessage(
        data.error || "Request failed (" + resp.status + ")",
        true
      );
      return;
    }

    shadowingPaintVerdicts(unit, data);
    shadowingRenderResult(unit, data);

    const rate =
      data.tokens_per_minute != null ? data.tokens_per_minute : null;
    shadowingHistory.unshift({
      text: unit.fullText.slice(0, 40),
      score: Number(data.score || 0),
      rate: rate,
      duration: data.duration,
    });
    shadowingRenderHistory();

    if (shadowingActive && shadowingAuto) shadowingAutoAdvance();
  } catch (err) {
    shadowingRenderPanelMessage("Network error: " + err, true);
  } finally {
    shadowingBusy = false;
  }
}

function shadowingAutoAdvance() {
  // Advance to the next cue and play it; the next cue-end starts the
  // next take.  Auto-pause semantics autoplay the next cue in both
  // engines (media: the next-cue button; tts: seekToCue with autoplay).
  if (typeof ttsSeekToCue === "function" &&
      document.getElementById("tts-player-container") &&
      typeof ttsCues !== "undefined" && ttsCues.length) {
    const next = shadowingLastCue && shadowingLastCue.source === "tts"
      ? shadowingLastCue.index + 1
      : -1;
    if (next >= 0 && next < ttsCues.length) {
      ttsSeekToCue(next, true);
      return;
    }
    shadowingRenderPanelMessage("End of the text — auto stopped.", false);
    return;
  }
  const nextBtn = document.getElementById("yt-next-cue-btn");
  if (nextBtn) {
    nextBtn.click();
    return;
  }
  shadowingRenderPanelMessage("No player cues to advance to.", true);
}

/* ------------------------------------------------------------------
 * 9. Panel actions
 * ------------------------------------------------------------------ */

function shadowingNav(delta) {
  const sents = shadowingSentences().filter(function (s) {
    return s.querySelector("span.word");
  });
  if (!sents.length) return;
  const anchor =
    shadowingUnit && shadowingUnit.el
      ? shadowingUnit.spans[0].closest(".textsentence") || shadowingUnit.el
      : null;
  let idx = anchor ? sents.indexOf(anchor) : -1;
  idx = Math.max(0, Math.min(sents.length - 1, idx + delta));
  shadowingSetUnit(shadowingUnitFromEl(sents[idx], null));
}

function shadowingListen() {
  if (!shadowingUnit || typeof speakText !== "function") return;
  if (
    typeof ttsPlaying !== "undefined" &&
    ttsPlaying &&
    typeof ttsStop === "function"
  ) {
    ttsStop();
  }
  speakText(shadowingUnit.fullText);
}

/* ------------------------------------------------------------------
 * 10. Boot
 * ------------------------------------------------------------------ */

function shadowingBoot() {
  let savedAuto = null;
  try {
    savedAuto = localStorage.getItem(SHADOWING_AUTO_KEY);
  } catch (_) {}
  shadowingAuto = savedAuto === "1";

  window.addEventListener("lute:cue-changed", shadowingOnCueChanged);
  window.addEventListener("lute:cue-ended", shadowingOnCueEnded);

  const btn =
    document.getElementById("tts-shadowing-btn") ||
    document.getElementById("yt-shadowing-btn");
  if (btn) {
    btn.addEventListener("click", shadowingToggleMode);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", shadowingBoot);
} else {
  shadowingBoot();
}
