/* lute-shadowing.js
   --------------------------------------------------------------
   影子跟读 (shadowing): a per-sentence mic button in the reading
   pane.  One click starts a MediaRecorder capture, a second click
   stops it; the clip goes to /read/shadowing/transcribe, which
   whisper-transcribes it and diffs the result against the
   sentence's own word tokens.  Verdicts are painted onto the
   sentence's word spans as underline-only marks (the words' status
   background colours stay untouched), and the full comparison --
   transcription, missed / misread lists, speech rate, playback of
   the user's own recording -- opens in the right pane, mirroring
   the grammar-analysis panel.

   Deliberately NOT wrapped in an IIFE: like tts.js / tts-ui.js it
   shares the read page's single global scope and is loaded after
   them (see lute/templates/read/index.html).
   --------------------------------------------------------------
*/
"use strict";

/* ------------------------------------------------------------------
 * 1. State
 * ------------------------------------------------------------------ */

let shadowingButtonsVisible = true;
let shadowingRecorder = null;
let shadowingStream = null;
let shadowingChunks = [];
let shadowingActiveBtn = null;
let shadowingActiveSentence = null;
let shadowingTimerInterval = null;
let shadowingRecordingStart = 0;
let shadowingBusy = false;
let shadowingStartPending = false;
let shadowingMarkedSentence = null;
let shadowingRecordingBlob = null;
let shadowingRecordingUrl = null;
let _shadowingObserver = null;

const SHADOWING_MODEL_KEY = "shadowingModel";

const MIC_SVG =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v5a3 3 0 0 0 3 3z"></path><path d="M19 11a1 1 0 1 0-2 0 5 5 0 0 1-10 0 1 1 0 1 0-2 0 7 7 0 0 0 6 6.92V20H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-2v-2.08A7 7 0 0 0 19 11z"></path></svg>';
const STOP_SVG =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg>';

/* ------------------------------------------------------------------
 * 2. Mic button injection (same lifecycle as the sentence 🔊
 *    buttons: initial pass + observer + the synchronous afterSwap
 *    hook the reading page calls before it measures paragraphs).
 * ------------------------------------------------------------------ */

function injectShadowingButtons() {
  if (!shadowingButtonsVisible) return;
  const textDiv = document.getElementById("thetext");
  if (!textDiv) return;

  textDiv.querySelectorAll(".textsentence").forEach(function (s) {
    if (s.querySelector(".lute-shadowing-btn")) return;
    // Same readability check as the 🔊 buttons: skip ghost /
    // punctuation-only sentences.
    const text = cleanSentenceText(s.textContent || "");
    if (text === "" || !/[\p{L}\p{N}]/u.test(text)) return;

    const btn = document.createElement("span");
    btn.className = "lute-shadowing-btn";
    btn.title = "Shadowing: record yourself reading this sentence";
    btn.innerHTML = MIC_SVG;
    // Right after the sentence's 🔊 button when present, else first.
    const playBtn = s.querySelector(".lute-sentence-play-btn");
    if (playBtn) {
      playBtn.insertAdjacentElement("afterend", btn);
    } else if (s.firstChild) {
      s.insertBefore(btn, s.firstChild);
    } else {
      s.appendChild(btn);
    }
  });
}
window.luteInjectShadowingButtons = injectShadowingButtons;

function startShadowingObserver() {
  const textDiv = document.getElementById("thetext");
  if (!textDiv || _shadowingObserver) return;

  _shadowingObserver = new MutationObserver(function (mutations) {
    let needsUpdate = false;
    for (const m of mutations) {
      if (m.addedNodes.length > 0) {
        needsUpdate = true;
        break;
      }
    }
    if (!needsUpdate) return;

    if (startShadowingObserver._t) clearTimeout(startShadowingObserver._t);
    startShadowingObserver._t = setTimeout(function () {
      startShadowingObserver._t = null;
      injectShadowingButtons();
    }, 100);
  });

  _shadowingObserver.observe(textDiv, { childList: true });
}

// Called from the reading page's htmx:afterSwap handler.  A page turn
// or term-save replaces #thetext, so any marks/panel tied to the old
// sentence must go, and the new text needs its mic buttons.
window.luteShadowingAfterSwap = function () {
  if (shadowingMarkedSentence && !document.contains(shadowingMarkedSentence)) {
    shadowingMarkedSentence = null;
    shadowingClosePanel();
  }
  if (shadowingButtonsVisible) injectShadowingButtons();
};

/* ------------------------------------------------------------------
 * 3. Reading-menu toggle (mirrors the Sentence 🔊 toggle)
 * ------------------------------------------------------------------ */

function setShadowingButtonsVisible(visible) {
  shadowingButtonsVisible = visible;
  if (visible) injectShadowingButtons();
  document.querySelectorAll(".lute-shadowing-btn").forEach(function (btn) {
    btn.style.display = visible ? "" : "none";
  });
  const toggle = document.getElementById("shadowing-buttons-toggle");
  if (toggle) toggle.checked = visible;
  if (document.body) {
    if (visible) document.body.classList.add("shadowing-buttons-active");
    else document.body.classList.remove("shadowing-buttons-active");
  }

  try {
    localStorage.setItem("shadowingButtonsVisible", visible ? "1" : "0");
  } catch (_) {}
  try {
    fetch("/settings/set/shadowing_show_buttons/" + (visible ? "1" : "0"), {
      method: "POST",
    });
  } catch (_) {}
}

function setupShadowingToggle() {
  const toggle = document.getElementById("shadowing-buttons-toggle");
  if (!toggle) return;

  let saved = null;
  try {
    saved = localStorage.getItem("shadowingButtonsVisible");
  } catch (_) {}
  shadowingButtonsVisible = saved !== null ? saved !== "0" : true;

  toggle.checked = shadowingButtonsVisible;
  if (shadowingButtonsVisible) {
    document.body.classList.add("shadowing-buttons-active");
  }

  toggle.addEventListener("change", function () {
    setShadowingButtonsVisible(toggle.checked);
  });
}

/* ------------------------------------------------------------------
 * 4. Result panel (mirrors the grammar-analysis panel: fills
 *    #read_pane_right while the default wordframe/dict are hidden)
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
window.closeShadowingPanel = shadowingClosePanel;

function shadowingGetModel() {
  let m = null;
  try {
    m = localStorage.getItem(SHADOWING_MODEL_KEY);
  } catch (_) {}
  return ["base", "small", "medium"].indexOf(m) !== -1 ? m : "small";
}

function shadowingOpenPanel(stateHtml) {
  shadowingClosePanel();
  const pane = document.getElementById("read_pane_right");
  const panel = document.createElement("div");
  panel.id = "shadowing-panel";
  panel.className = "shadowing-panel";
  panel.innerHTML =
    '<div class="shadowing-panel__header">' +
    '<span class="shadowing-panel__title">Shadowing</span>' +
    '<select id="shadowing-model" class="shadowing-panel__model" title="Whisper model">' +
    '<option value="base">base</option>' +
    '<option value="small">small</option>' +
    '<option value="medium">medium</option>' +
    "</select>" +
    '<button type="button" class="shadowing-panel__close" aria-label="Close">&times;</button>' +
    "</div>" +
    '<div class="shadowing-panel__body">' +
    (stateHtml || '<div class="shadowing-panel__state">…</div>') +
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
    .addEventListener("click", shadowingClosePanel);
  return panel;
}

function shadowingPanelBody() {
  const panel = document.getElementById("shadowing-panel");
  return panel ? panel.querySelector(".shadowing-panel__body") : null;
}

function shadowingRenderPanelError(msg) {
  const html =
    '<div class="shadowing-panel__state shadowing-panel__error">' +
    shadowingEscapeHtml(msg) +
    "</div>";
  const body = shadowingPanelBody();
  if (!body) {
    shadowingOpenPanel(html);
  } else {
    body.innerHTML = html;
  }
}

function shadowingRenderResult(sentenceEl, wordTexts, data) {
  const body = shadowingPanelBody();
  if (!body) return;

  const statuses = data.statuses || [];
  const fuzzySpoken = data.spoken_for_fuzzy || {};
  const extras = data.extras || [];
  const isMorpheme = data.token_kind === "morpheme";
  const kindLabel = isMorpheme ? "morphemes" : "words";
  const extraJoin = isMorpheme ? "" : " ";

  let html = '<div class="shadowing-score">';
  html +=
    '<span class="shadowing-score__value">' + Number(data.score || 0) + "%</span>";
  if (data.tokens_per_minute != null) {
    html +=
      '<span class="shadowing-score__rate">' +
      data.tokens_per_minute +
      " " +
      kindLabel +
      "/min · " +
      data.duration +
      "s</span>";
  }
  html += "</div>";

  html +=
    '<div class="shadowing-label">Heard</div>' +
    '<div class="shadowing-heard">' +
    shadowingEscapeHtml(data.transcription || "—") +
    "</div>";

  html += '<div class="shadowing-label">Sentence</div><div class="shadowing-tokens">';
  for (let i = 0; i < wordTexts.length; i++) {
    const st = i < statuses.length ? statuses[i] : 0;
    const cls = st === 2 ? "shadow-ok" : st === 1 ? "shadow-fuzzy" : "shadow-miss";
    let chip = shadowingEscapeHtml(wordTexts[i]);
    if (st === 1 && fuzzySpoken[i] != null) {
      chip +=
        '<span class="shadowing-token__heard">→ ' +
        shadowingEscapeHtml(fuzzySpoken[i]) +
        "</span>";
    }
    html += '<span class="shadowing-token ' + cls + '">' + chip + "</span>";
  }
  html += "</div>";

  if (extras.length) {
    html +=
      '<div class="shadowing-extras"><span class="shadowing-label">Also heard</span> ' +
      shadowingEscapeHtml(extras.join(extraJoin)) +
      "</div>";
  }

  html +=
    '<div class="shadowing-actions">' +
    '<button type="button" class="shadowing-btn" data-shadowing-action="mine">▶ ' +
    (shadowingRecordingBlob ? "Your recording" : "Recording") +
    "</button>" +
    '<button type="button" class="shadowing-btn" data-shadowing-action="sentence">▶ Sentence</button>' +
    '<button type="button" class="shadowing-btn" data-shadowing-action="again">Record again</button>' +
    "</div>";

  body.innerHTML = html;

  body
    .querySelector('[data-shadowing-action="mine"]')
    .addEventListener("click", shadowingPlayRecording);
  body
    .querySelector('[data-shadowing-action="sentence"]')
    .addEventListener("click", function () {
      shadowingPlayOriginal(sentenceEl);
    });
  body
    .querySelector('[data-shadowing-action="again"]')
    .addEventListener("click", function () {
      const btn = sentenceEl.querySelector(".lute-shadowing-btn");
      shadowingStart(sentenceEl, btn);
    });
}

/* ------------------------------------------------------------------
 * 5. Recording (MediaRecorder), one capture at a time
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

async function shadowingStart(sentenceEl, btn) {
  // A second click during the getUserMedia await would otherwise start
  // two captures; collapse it.
  if (!btn || shadowingBusy || shadowingStartPending) return;
  shadowingStartPending = true;
  try {
    if (!shadowingCanRecord()) {
      shadowingRenderPanelError(
        "Microphone recording needs a secure context (open Lute on localhost or HTTPS) and a browser with MediaRecorder support."
      );
      return;
    }

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      shadowingRenderPanelError(shadowingPermissionMessage(err));
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
      shadowingRenderPanelError(shadowingPermissionMessage(err));
      return;
    }

    shadowingRecorder = recorder;
    shadowingActiveBtn = btn;
    shadowingActiveSentence = sentenceEl;
    shadowingRecordingStart = Date.now();
    recorder.ondataavailable = function (e) {
      if (e.data && e.data.size > 0) shadowingChunks.push(e.data);
    };
    recorder.onstop = shadowingOnStop;

    btn.classList.add("shadowing-recording");
    btn.classList.remove("shadowing-busy");
    btn.innerHTML = STOP_SVG;
    shadowingTimerInterval = setInterval(function () {
      const secs = Math.floor((Date.now() - shadowingRecordingStart) / 1000);
      btn.textContent = secs + "s";
    }, 250);
    shadowingOpenPanel(
      '<div class="shadowing-panel__state">Recording — tap the mic again to stop.</div>'
    );
    recorder.start();
  } finally {
    shadowingStartPending = false;
  }
}

function shadowingStop() {
  if (shadowingTimerInterval) {
    clearInterval(shadowingTimerInterval);
    shadowingTimerInterval = null;
  }
  const rec = shadowingRecorder;
  const btn = shadowingActiveBtn;
  if (btn) {
    btn.classList.remove("shadowing-recording");
    btn.innerHTML = MIC_SVG;
    if (rec && rec.state === "recording") btn.classList.add("shadowing-busy");
  }
  if (rec && rec.state === "recording") {
    try {
      rec.stop();
    } catch (_) {
      if (btn) btn.classList.remove("shadowing-busy");
      shadowingTeardownStream();
      shadowingRecorder = null;
      shadowingActiveBtn = null;
      shadowingActiveSentence = null;
    }
  } else {
    // Nothing was actually recording; just reset the bookkeeping.
    shadowingTeardownStream();
    shadowingRecorder = null;
    shadowingActiveBtn = null;
    shadowingActiveSentence = null;
  }
}

function shadowingOnStop() {
  const btn = shadowingActiveBtn;
  const sentence = shadowingActiveSentence;
  const chunks = shadowingChunks;
  const mime = shadowingRecorder ? shadowingRecorder.mimeType : "";

  shadowingRecorder = null;
  shadowingActiveBtn = null;
  shadowingActiveSentence = null;
  shadowingChunks = [];
  shadowingTeardownStream();
  if (btn) btn.classList.remove("shadowing-busy");

  // The page was swapped while recording; the clip has no sentence
  // to score against anymore.
  if (!sentence || !document.contains(sentence)) return;

  const blob = new Blob(chunks, { type: mime || "audio/webm" });
  if (!blob.size) {
    shadowingRenderPanelError("The recording was empty — try again.");
    return;
  }
  shadowingRecordingBlob = blob;
  shadowingSubmit(blob, sentence, btn);
}

function shadowingToggle(sentenceEl, btn) {
  if (shadowingRecorder && shadowingRecorder.state === "recording") {
    if (sentenceEl === shadowingActiveSentence) {
      shadowingStop();
    } else {
      shadowingRenderPanelError(
        "Already recording — tap the pulsing mic to stop first."
      );
    }
    return;
  }
  shadowingStart(sentenceEl, btn);
}

/* ------------------------------------------------------------------
 * 6. Submit + paint results
 * ------------------------------------------------------------------ */

function sentenceWordTexts(sentenceEl) {
  return Array.from(sentenceEl.querySelectorAll("span.word")).map(function (sp) {
    return (sp.getAttribute("data-text") || sp.textContent || "").replace(
      /\u200B/g,
      ""
    );
  });
}

function sentenceLangId(sentenceEl) {
  const sp = sentenceEl.querySelector("span.word[data-lang-id]");
  return sp ? sp.getAttribute("data-lang-id") : "";
}

function shadowingClearMarks(sentenceEl) {
  if (!sentenceEl) return;
  sentenceEl.querySelectorAll("span.word").forEach(function (sp) {
    sp.classList.remove("shadow-ok", "shadow-fuzzy", "shadow-miss");
  });
}

async function shadowingSubmit(blob, sentenceEl, btn) {
  shadowingBusy = true;
  shadowingClearMarks(sentenceEl);
  shadowingOpenPanel(
    '<div class="shadowing-panel__state">Transcribing…</div>'
  );

  const words = sentenceWordTexts(sentenceEl);
  const fd = new FormData();
  fd.append("audio", blob, "shadowing." + shadowingExtForMime(blob.type));
  fd.append("language_id", sentenceLangId(sentenceEl) || "");
  fd.append("tokens", JSON.stringify(words));
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
      shadowingRenderPanelError(
        data.error || "Request failed (" + resp.status + ")"
      );
      return;
    }
    shadowingApplyResults(sentenceEl, words, data);
  } catch (err) {
    shadowingRenderPanelError("Network error: " + err);
  } finally {
    shadowingBusy = false;
  }
}

function shadowingApplyResults(sentenceEl, wordTexts, data) {
  if (shadowingMarkedSentence && shadowingMarkedSentence !== sentenceEl) {
    shadowingClearMarks(shadowingMarkedSentence);
  }
  shadowingMarkedSentence = sentenceEl;

  const spans = sentenceEl.querySelectorAll("span.word");
  const statuses = data.statuses || [];
  for (let i = 0; i < spans.length; i++) {
    const st = i < statuses.length ? statuses[i] : 0;
    spans[i].classList.add(
      st === 2 ? "shadow-ok" : st === 1 ? "shadow-fuzzy" : "shadow-miss"
    );
  }
  shadowingRenderResult(sentenceEl, wordTexts, data);
}

function shadowingPlayRecording() {
  if (!shadowingRecordingBlob) return;
  if (shadowingRecordingUrl) {
    try {
      URL.revokeObjectURL(shadowingRecordingUrl);
    } catch (_) {}
  }
  shadowingRecordingUrl = URL.createObjectURL(shadowingRecordingBlob);
  const audio = new Audio(shadowingRecordingUrl);
  audio.play().catch(function () {});
}

function shadowingPlayOriginal(sentenceEl) {
  if (!sentenceEl) return;
  // textContent (not innerText): the clone is detached, and the
  // paragraph's space tokens live in their own spans, so spacing
  // survives for alphabetic languages too.
  const clone = sentenceEl.cloneNode(true);
  clone
    .querySelectorAll(".lute-sentence-play-btn, .lute-shadowing-btn")
    .forEach(function (b) {
      b.remove();
    });
  const text = (clone.textContent || "")
    .replace(/🔊/g, "")
    .replace(/\u200B/g, "")
    .trim();
  if (!text || typeof speakText !== "function") return;
  if (typeof ttsPlaying !== "undefined" && ttsPlaying && typeof ttsStop === "function") {
    ttsStop();
  }
  speakText(text);
}

/* ------------------------------------------------------------------
 * 7. Event delegation + boot
 * ------------------------------------------------------------------ */

function setupShadowingDelegation() {
  const textDiv = document.getElementById("thetext");
  // #thetext survives htmx innerHTML swaps, so guard against re-binding.
  if (!textDiv || textDiv.shadowingDelegation) return;
  textDiv.shadowingDelegation = true;

  textDiv.addEventListener("click", function (e) {
    const btn = e.target.closest(".lute-shadowing-btn");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    const sentence = btn.closest(".textsentence") || btn.parentElement;
    if (sentence) shadowingToggle(sentence, btn);
  });
}

function shadowingBoot() {
  setupShadowingToggle();
  if (document.getElementById("thetext")) {
    if (shadowingButtonsVisible) injectShadowingButtons();
    startShadowingObserver();
    setupShadowingDelegation();
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", shadowingBoot);
} else {
  shadowingBoot();
}
