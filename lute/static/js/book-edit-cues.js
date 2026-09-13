/*
 * Lyrics & timing side panel for the book edit page (/book/edit/<id>)
 * and the page edit form (/read/editpage/<bookid>/<pagenum>).
 *
 * Modes (window.LUTE_CUE_EDIT.mode):
 * - "book": the subtitle textarea (#text) is the single source of
 *   truth.  The panel parses its SRT content into per-line editors
 *   (start / end / text) and rewrites the whole textarea on every panel
 *   edit, mirroring the backend serializer cues_to_srt_text
 *   (lute/book/service.py), so the form save flow is unchanged.  If the
 *   textarea cannot be parsed, the panel disables itself instead of
 *   writing back, so hand-edited content is never clobbered.
 * - "page": the panel is built from the page's cues handed over by the
 *   server (window.LUTE_CUE_EDIT.cues, absolute cue indices).  Row text
 *   edits rewrite the matching textarea line (so the saved page text
 *   stays consistent), timings live only in the panel state, and on
 *   submit the panel fills the #cueDataInput hidden field so the
 *   backend can apply them to the cues (read/routes.py
 *   _apply_media_page_cue_data).
 *
 * Visibility is driven by body classes: the book edit page calls
 * LuteCueEdit.setType() when the book type changes; the page edit form
 * has nothing to switch, so it activates itself.
 */
(function () {
  "use strict";

  var CFG = window.LUTE_CUE_EDIT || null;
  var PREF_KEY = "lute-bookedit-cuepanel-collapsed";

  var panel = document.getElementById("cueEditorPanel");
  var tab = document.getElementById("cuePanelTab");
  var rowsBox = document.getElementById("cuePanelRows");
  var warnBox = document.getElementById("cuePanelWarn");
  var countEl = document.getElementById("cuePanelCount");
  var textarea = document.getElementById("text");
  if (!CFG || !panel || !tab || !rowsBox || !warnBox || !countEl || !textarea) {
    return;
  }

  var MODE = CFG.mode === "page" ? "page" : "book";
  if (MODE === "book" && !CFG.hasCues) return;
  if (MODE === "page" && (!CFG.cues || !CFG.cues.length)) return;

  var audio = document.getElementById("cueAudio");
  var playBtn = document.getElementById("cuePlayBtn");
  var curTimeEl = document.getElementById("cueCurrentTime");
  var durEl = document.getElementById("cueDuration");
  var bar = document.getElementById("cueProgress");
  var fill = document.getElementById("cueProgressFill");
  var knob = document.getElementById("cueProgressKnob");

  var cues = [];          // working copies: {i, start, end, text}
  var lastActive = -1;
  var taTimer = null;

  /* ---------- time formatting / parsing ---------- */

  function fmtSRT(secs) {
    var ms = Math.max(0, Math.round((parseFloat(secs) || 0) * 1000));
    var h = String(Math.floor(ms / 3600000)).padStart(2, "0");
    var m = String(Math.floor((ms % 3600000) / 60000)).padStart(2, "0");
    var s = String(Math.floor((ms % 60000) / 1000)).padStart(2, "0");
    return h + ":" + m + ":" + s + "," + String(ms % 1000).padStart(3, "0");
  }

  // Compact mm:ss.mmm (h:mm:ss.mmm past the hour) for the panel inputs.
  function fmtShort(secs) {
    var ms = Math.max(0, Math.round((parseFloat(secs) || 0) * 1000));
    var h = Math.floor(ms / 3600000);
    var m = Math.floor((ms % 3600000) / 60000);
    var s = Math.floor((ms % 60000) / 1000);
    var head = h ? h + ":" + String(m).padStart(2, "0") : String(m).padStart(2, "0");
    return head + ":" + String(s).padStart(2, "0") + "." + String(ms % 1000).padStart(3, "0");
  }

  // Accepts ss.mmm, mm:ss(.mmm), h:mm:ss(.mmm); comma or dot decimals.
  function parseTime(text) {
    var v = (text || "").trim().replace(",", ".");
    if (!v) return null;
    var parts = v.split(":");
    if (parts.length > 3) return null;
    var t = 0;
    for (var i = 0; i < parts.length; i++) {
      var p = parseFloat(parts[i]);
      if (isNaN(p) || p < 0) return null;
      t = t * 60 + p;
    }
    return t;
  }

  var TS_RE = /(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})/;

  function tsToSecs(h, mi, s, ms) {
    return +h * 3600 + +mi * 60 + +s + +(ms.padEnd(3, "0")) / 1000;
  }

  // SRT text -> [{start, end, text}], or null when unparseable.  (Book
  // mode only: the page-edit textarea holds plain lines, no SRT.)
  function parseSRT(text) {
    var lines = text.split(/\r?\n/);
    var out = [];
    var i = 0;
    while (i < lines.length) {
      if (!lines[i].trim()) { i++; continue; }
      if (/^\d+$/.test(lines[i].trim())) i++;   // block number line
      if (i >= lines.length) return null;
      var m = lines[i].match(TS_RE);
      if (!m) return null;
      var start = tsToSecs(m[1], m[2], m[3], m[4]);
      var end = tsToSecs(m[5], m[6], m[7], m[8]);
      i++;
      var tl = [];
      while (
        i < lines.length &&
        lines[i].trim() !== "" &&
        !/^\d+$/.test(lines[i].trim()) &&
        !TS_RE.test(lines[i])
      ) {
        tl.push(lines[i]);
        i++;
      }
      out.push({ start: start, end: end, text: tl.join("\n") });
    }
    return out.length ? out : null;
  }

  // Mirrors cues_to_srt_text (lute/book/service.py).  (Book mode only.)
  function serializeSRT(cs) {
    var lines = [];
    for (var i = 0; i < cs.length; i++) {
      lines.push(String(i + 1));
      lines.push(fmtSRT(cs[i].start) + " --> " + fmtSRT(cs[i].end));
      lines.push(cs[i].text || "");
      lines.push("");
    }
    return lines.join("\n").replace(/\n+$/, "");
  }

  /* ---------- two-way sync ---------- */

  function autosize(t) {
    // Skip nodes inside the still-hidden panel: scrollHeight measures 0
    // there, which would clamp the editor to a 0px sliver.  The panel's
    // setType()/activation re-runs autosize once the panel is rendered.
    if (!t.isConnected || t.offsetParent === null) return;
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, 110) + "px";
  }

  function autosizeAll() {
    var txts = rowsBox.querySelectorAll(".cue-txt");
    for (var i = 0; i < txts.length; i++) autosize(txts[i]);
  }

  function syncToTextarea() {
    textarea.value = serializeSRT(cues);
  }

  // Page mode: write one textarea line, keeping the page text (what the
  // backend saves into the Text record) in lockstep with the row.
  function writeTextareaLine(k, val) {
    var lines = textarea.value.replace(/\r/g, "").split("\n");
    if (k >= lines.length) return;
    lines[k] = val;
    textarea.value = lines.join("\n");
  }

  // Book mode: re-parse the textarea after hand edits.  Panel writes set
  // .value directly, which fires no input event, so there is no loop.
  function refreshFromTextarea() {
    var parsed = parseSRT(textarea.value);
    warnBox.style.display = parsed ? "none" : "block";
    rowsBox.classList.toggle("disabled", !parsed);
    if (!parsed) return;
    cues = parsed.map(function (c, i) {
      return { i: i, start: c.start, end: c.end, text: c.text };
    });
    renderRows();
  }

  // Page mode: textarea hand edits update the matching row texts while
  // the page still has exactly one line per cue; otherwise warn (the
  // backend will skip the cues and flash the same reason).
  function pageSyncFromTextarea() {
    var lines = textarea.value.replace(/\r/g, "").split("\n");
    if (lines.length !== cues.length) {
      warnBox.textContent =
        "Line count no longer matches the subtitle cues (" + cues.length +
        ") \u2014 the timing edits will not be saved. Keep one line per subtitle line.";
      warnBox.style.display = "block";
      return;
    }
    warnBox.style.display = "none";
    var changed = false;
    for (var k = 0; k < lines.length; k++) {
      if (cues[k].text !== lines[k]) {
        cues[k].text = lines[k];
        changed = true;
      }
    }
    if (changed) renderRows();   // focus is in the textarea, safe to rebuild
  }

  /* ---------- rows ---------- */

  function renderRows() {
    var st = rowsBox.scrollTop;
    rowsBox.innerHTML = "";
    for (var i = 0; i < cues.length; i++) rowsBox.appendChild(buildRow(cues[i], i));
    autosizeAll();
    countEl.textContent = " (" + cues.length + " lines)";
    rowsBox.scrollTop = st;
    lastActive = -1;
    setActiveRow();
  }

  function timeEdit(input, k, key) {
    var t = parseTime(input.value);
    if (t === null) {
      input.classList.add("invalid");
      return;
    }
    input.classList.remove("invalid");
    var old = cues[k][key];
    cues[k][key] = t;
    chainBoundary(k, key, old);
    if (MODE === "book") syncToTextarea();
    setActiveRow();
  }

  var EPS = 0.001;

  // Retiming keeps shared boundaries contiguous: when a cue's start
  // moves, the previous cue's end follows when it was sharing (or
  // overlapping) that boundary -- LRC-derived cues are contiguous by
  // construction, so retiming one line shifts the neighbour's edge
  // instead of leaving a gap or overlap.  A true gap (neighbour ends
  // before the old start, e.g. an instrumental break) is left alone.
  // Symmetrically, moving a cue's end pulls the next cue's start.
  function chainBoundary(k, key, oldVal) {
    if (key === "start" && k > 0) {
      var prevEnd = cues[k - 1].end;
      if (
        prevEnd >= oldVal - EPS &&
        Math.abs(prevEnd - cues[k].start) > EPS
      ) {
        cues[k - 1].end = cues[k].start;
        updateTimeInput(k - 1, "end");
        flashRow(k - 1);
      }
    } else if (key === "end" && k < cues.length - 1) {
      var nextStart = cues[k + 1].start;
      if (
        nextStart <= oldVal + EPS &&
        Math.abs(nextStart - cues[k].end) > EPS
      ) {
        cues[k + 1].start = cues[k].end;
        updateTimeInput(k + 1, "start");
        flashRow(k + 1);
      }
    }
  }

  function updateTimeInput(k, key) {
    var row = rowsBox.children[k];
    if (!row) return;
    var inp = row.querySelector(".cue-time-" + key);
    if (inp) {
      inp.value = fmtShort(cues[k][key]);
      inp.classList.remove("invalid");
    }
  }

  function flashRow(k) {
    var row = rowsBox.children[k];
    if (!row) return;
    row.classList.add("cue-flash");
    window.setTimeout(function () { row.classList.remove("cue-flash"); }, 400);
  }

  function buildRow(c, k) {
    var row = document.createElement("div");
    row.className = "cue-row";

    var top = document.createElement("div");
    top.className = "cue-row-top";
    var idx = document.createElement("span");
    idx.className = "cue-idx";
    idx.textContent = MODE === "page" ? c.i + 1 : k + 1;
    var set = document.createElement("button");
    set.type = "button";
    set.className = "cue-set-btn";
    set.title = "Set start to current playback time";
    set.innerHTML =
      '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2.2" stroke-linecap="round" aria-hidden="true">' +
      '<circle cx="12" cy="13" r="8"></circle><path d="M12 9v4l2.5 2.5"></path>' +
      '<path d="M9 2h6"></path></svg>';
    set.addEventListener("click", function () {
      if (!audio) return;
      var old = cues[k].start;
      cues[k].start = audio.currentTime;
      var inp = row.querySelector(".cue-time-start");
      inp.value = fmtShort(cues[k].start);
      inp.classList.remove("invalid");
      chainBoundary(k, "start", old);
      if (MODE === "book") syncToTextarea();
      setActiveRow();
      row.classList.add("cue-flash");
      window.setTimeout(function () { row.classList.remove("cue-flash"); }, 400);
    });
    top.appendChild(idx);
    top.appendChild(set);

    var times = document.createElement("div");
    times.className = "cue-times";
    ["start", "end"].forEach(function (key) {
      var lab = document.createElement("label");
      lab.textContent = key === "start" ? "Start" : "End";
      var inp = document.createElement("input");
      inp.type = "text";
      inp.className = "cue-time-in cue-time-" + key;
      inp.spellcheck = false;
      inp.value = fmtShort(c[key]);
      inp.addEventListener("input", function () { timeEdit(inp, k, key); });
      // On leave, normalize the display or revert an invalid value.
      inp.addEventListener("change", function () {
        var t = parseTime(inp.value);
        inp.value = t === null ? fmtShort(cues[k][key]) : fmtShort(t);
        inp.classList.remove("invalid");
      });
      times.appendChild(lab);
      times.appendChild(inp);
    });

    var txt = document.createElement("textarea");
    txt.className = "cue-txt";
    txt.rows = 1;
    txt.spellcheck = false;
    txt.value = c.text || "";
    autosize(txt);
    txt.addEventListener("input", function () {
      autosize(txt);
      cues[k].text = txt.value;
      if (MODE === "book") {
        syncToTextarea();
      } else {
        writeTextareaLine(k, txt.value);
      }
    });

    row.appendChild(top);
    row.appendChild(times);
    row.appendChild(txt);
    return row;
  }

  /* ---------- playback highlight ---------- */

  function setActiveRow() {
    var playing = audio && !audio.paused && !audio.ended;
    var idx = -1;
    if (playing) {
      var t = audio.currentTime;
      for (var k = 0; k < cues.length; k++) {
        if (t >= cues[k].start) idx = k;
      }
    }
    var kids = rowsBox.children;
    for (var j = 0; j < kids.length; j++) {
      kids[j].classList.toggle("active", j === idx);
    }
    if (idx >= 0 && idx !== lastActive && kids[idx]) {
      kids[idx].scrollIntoView({ block: "nearest" });
    }
    lastActive = idx;
  }

  /* ---------- mini player ---------- */

  function initPlayer() {
    var playerBoxEl = document.getElementById("cuePanelPlayer");
    if (!CFG.audioUrl || !audio || !playBtn) {
      panel.classList.add("no-audio");
      if (playerBoxEl) playerBoxEl.style.display = "none";
      return;
    }
    playerBoxEl.style.display = "";
    playBtn.addEventListener("click", function () {
      if (audio.paused) {
        audio.play();
      } else {
        audio.pause();
      }
    });
    var updBtn = function () {
      playBtn.innerHTML = audio.paused ? "&#9654;" : "&#10074;&#10074;";
      setActiveRow();
    };
    audio.addEventListener("play", updBtn);
    audio.addEventListener("pause", updBtn);
    audio.addEventListener("ended", updBtn);
    audio.addEventListener("timeupdate", function () {
      curTimeEl.textContent = fmtShort(audio.currentTime);
      if (audio.duration && isFinite(audio.duration)) {
        var pct = (audio.currentTime / audio.duration) * 100;
        fill.style.width = pct + "%";
        knob.style.left = pct + "%";
      }
      setActiveRow();
    });
    audio.addEventListener("loadedmetadata", function () {
      if (audio.duration && isFinite(audio.duration)) {
        durEl.textContent = "/ " + fmtShort(audio.duration);
      }
    });
    bar.addEventListener("click", function (ev) {
      if (!audio.duration || !isFinite(audio.duration)) return;
      var r = bar.getBoundingClientRect();
      audio.currentTime =
        Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)) * audio.duration;
      setActiveRow();
    });
    updBtn();
  }

  /* ---------- panel visibility (body-class driven) ---------- */

  function applyCollapsePref() {
    document.body.classList.toggle(
      "cue-panel-closed",
      localStorage.getItem(PREF_KEY) === "1"
    );
  }

  var collapseBtn = document.getElementById("cuePanelCollapse");
  collapseBtn.addEventListener("click", function () {
    localStorage.setItem(PREF_KEY, "1");
    document.body.classList.add("cue-panel-closed");
  });
  tab.addEventListener("click", function () {
    localStorage.setItem(PREF_KEY, "0");
    document.body.classList.remove("cue-panel-closed");
  });

  /* ---------- init ---------- */

  if (MODE === "page") {
    cues = CFG.cues.map(function (c) {
      return { i: c.i, start: +c.start || 0, end: +c.end || 0, text: c.text || "" };
    });
    renderRows();
    // Nothing to switch on this page: activate the panel right away.
    document.body.classList.add("cue-panel-open");
    applyCollapsePref();
    autosizeAll();
    textarea.addEventListener("input", function () {
      clearTimeout(taTimer);
      taTimer = setTimeout(pageSyncFromTextarea, 300);
    });
    var form = document.getElementById("editPageForm");
    var cueDataInput = document.getElementById("cueDataInput");
    if (form && cueDataInput) {
      form.addEventListener("submit", function () {
        cueDataInput.value = JSON.stringify(
          cues.map(function (c) {
            return { i: c.i, start: c.start, end: c.end, text: c.text };
          })
        );
      });
    }
  } else {
    refreshFromTextarea();
    textarea.addEventListener("input", function () {
      clearTimeout(taTimer);
      taTimer = setTimeout(refreshFromTextarea, 300);
    });
  }

  initPlayer();

  window.LuteCueEdit = {
    setType: function (active) {
      document.body.classList.toggle("cue-panel-open", !!active);
      if (active) {
        applyCollapsePref();
        // Rows were built while the panel was still hidden; re-measure
        // the text editors now that it shows.
        autosizeAll();
      }
    }
  };
})();
