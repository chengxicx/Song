/* tts-ui.js
   --------------------------------------------------------------
   Observers (text / UI / form), player + sentence-button
   visibility toggles, and boot.
   --------------------------------------------------------------
   Split out of tts.js.  These scripts are deliberately NOT
   wrapped in an IIFE: they share one global scope and are loaded
   in order (see lute/templates/read/index.html), so splitting the
   file changes nothing at runtime.
*/
"use strict";
  /* ------------------------------------------------------------------
   * Re-build cues when the page text changes (ajax navigation).
   * ------------------------------------------------------------------ */

  let _textObserver = null;
  function ttsStartTextObserver() {
    const textDiv = document.getElementById("thetext");
    if (!textDiv || _textObserver) return;
    _textObserver = new MutationObserver(function (mutations) {
      let needsRebuild = false;
      for (const m of mutations) {
        if (m.addedNodes.length > 0) { needsRebuild = true; break; }
      }
      if (!needsRebuild) return;
      // Debounce -- goto_relative_page does multiple DOM writes.
      if (ttsStartTextObserver._t) clearTimeout(ttsStartTextObserver._t);
      ttsStartTextObserver._t = setTimeout(function () {
        ttsStartTextObserver._t = null;

        const textDiv = document.getElementById("thetext");
        // Use the same splitter that built ttsCues: a single sentence
        // may now expand to multiple sub-cues, so a naive 1-per-sentence
        // map will mismatch on every term-status update.
        const newCueTexts = textDiv
          ? Array.prototype.reduce.call(
              textDiv.querySelectorAll(".textsentence"),
              function (acc, s) {
                return acc.concat(
                  ttsSplitSentenceIntoCues(s).map(function (sub) {
                    return sub.text;
                  })
                );
              },
              []
            ).filter(Boolean)
          : [];

        // A term status update swaps #thetext with the *same* sentences
        // (only word status classes change), so playback should keep
        // going. Only a real page navigation -- different sentence
        // content -- should stop the player and reset its position.
        const sameText =
          newCueTexts.length > 0 &&
          newCueTexts.length === ttsCues.length &&
          newCueTexts.every(function (t, i) {
            return t === ttsCues[i].text;
          });

        if (sameText) {
          // Preserve the playhead across the rebuild so the current
          // utterance keeps looping / playing from where it was.
          const keepIndex = ttsCueIndex;
          const keepTime = ttsVirtualTime;
          const oldActual = ttsCues.map(function (c) {
            return c.actualDuration;
          });
          ttsBuildCues();
          // Keep measured durations so the timeline doesn't jump.
          ttsCues.forEach(function (c, i) {
            if (oldActual[i] != null) c.actualDuration = oldActual[i];
          });
          if (keepIndex >= 0 && keepIndex < ttsCues.length) {
            ttsCueIndex = keepIndex;
            const cue = ttsCues[keepIndex];
            ttsVirtualTime = Math.min(keepTime, cue ? cue.end : keepTime);
            ttsActivateCue(keepIndex);
          }
          // Re-apply status colours to the new subtitle word spans.
          ttsApplySubtitleStatusColors();
          return;
        }

        // Stop any playback before rebuilding -- the old cue indices
        // are stale after #thetext is replaced.
        ttsCancelSpeech();
        ttsPlaying = false;
        ttsPaused = false;
        ttsCueIndex = -1;
        ttsVirtualTime = 0;
        ttsUpdatePlayBtn();
        ttsBuildCues();
        // Re-apply status colours to the new subtitle word spans.
        ttsApplySubtitleStatusColors();
      }, 150);
    });
    _textObserver.observe(textDiv, { childList: true });
  }

  /* ================================================================
     8. TTS toggles (sidebar quick controls)
     ================================================================ */

  let ttsPlayerVisible = true;
  let ttsSentenceButtonsVisible = true;

  function setTtsPlayerVisible(visible) {
    ttsPlayerVisible = visible;
    const toggle = document.getElementById("tts-player-toggle");
    const container = document.body;

    if (ttsContainer) {
      ttsContainer.style.display = visible ? "" : "none";
    }
    if (toggle) {
      toggle.checked = visible;
    }
    if (container) {
      if (visible) {
        container.classList.add("tts-player-active");
      } else {
        container.classList.remove("tts-player-active");
      }
    }

    try {
      localStorage.setItem("ttsPlayerVisible", visible ? "1" : "0");
    } catch (_) {}

    var val = visible ? "1" : "0";
    try {
      fetch("/settings/set/tts_show_control_panel/" + val, { method: "POST" });
    } catch (_) {}

    try {
      window.dispatchEvent(new Event("lute:tts-ui-changed"));
    } catch (_) {}

    // Stop playback when the player is hidden mid-stream.
    if (!visible && (ttsPlaying || ttsPaused)) {
      ttsStop();
    }
  }

  function setTtsSentenceButtonsVisible(visible) {
    ttsSentenceButtonsVisible = visible;
    SETTINGS.showSentenceButtons = visible;
    if (visible && document.getElementById("thetext")) {
      injectSentencePlayButtons();
    }
    const btns = document.querySelectorAll(".lute-sentence-play-btn");
    const toggle = document.getElementById("tts-sentence-buttons-toggle");
    const container = document.body;

    btns.forEach(function (btn) {
      btn.style.display = visible ? "" : "none";
    });
    if (toggle) {
      toggle.checked = visible;
    }
    if (container) {
      if (visible) {
        container.classList.add("tts-sentence-buttons-active");
      } else {
        container.classList.remove("tts-sentence-buttons-active");
      }
    }

    try {
      localStorage.setItem("ttsSentenceButtonsVisible", visible ? "1" : "0");
    } catch (_) {}

    var val = visible ? "1" : "0";
    try {
      fetch("/settings/set/tts_show_sentence_buttons/" + val, { method: "POST" });
    } catch (_) {}

    try {
      window.dispatchEvent(new Event("lute:tts-ui-changed"));
    } catch (_) {}
  }

  function setupTtsPlayerToggle() {
    const toggle = document.getElementById("tts-player-toggle");
    if (!toggle) return;

    // Detect YouTube / MP3 books: they render a dedicated
    // .youtube-player-container player and the #book_audio_file hidden
    // input is explicitly left empty (the Jinja template sets it to
    // the empty string for youtube/mp3 types).
    var isYouTubeOrMp3 = function () {
      // The YouTube player is included for youtube/mp3 books; the TTS
      // player is included for everything else.  When the YouTube
      // player is present we hide the TTS player by default to avoid
      // having two competing players on screen.
      if (document.getElementById("yt-player-container")) return true;
      var audioInput = document.getElementById("book_audio_file");
      if (audioInput && (audioInput.value || "").trim() === "") {
        if (document.getElementById("ytContainer")) return true;
        if (typeof window.CUES !== "undefined") return true;
      }
      return false;
    };

    // PDF books are fixed-page readers: the player UI would cover the
    // page, so it starts hidden on every PDF load (the reading_menu
    // toggle can still show it for the session).
    var isPdfBook = function () {
      if (window.LUTE_BOOK_TYPE === "pdf") return true;
      return !!document.querySelector("#thetext.pdf-text-container");
    };

    let saved = null;
    try {
      saved = localStorage.getItem("ttsPlayerVisible");
    } catch (_) {}

    var initialVisible;
    if (isPdfBook()) {
      initialVisible = false;
    } else if (saved !== null) {
      initialVisible = saved !== "0";
    } else if (isYouTubeOrMp3()) {
      initialVisible = false;
    } else {
      initialVisible = SETTINGS.showControlPanel;
    }
    ttsPlayerVisible = initialVisible;

    toggle.checked = initialVisible;
    if (initialVisible) {
      document.body.classList.add("tts-player-active");
    } else {
      document.body.classList.remove("tts-player-active");
    }
    // ttsContainer is cached by ttsCacheElements() (called from
    // ttsInitPlayer()), which runs after this in boot().  Look it up
    // here so the container is hidden on first paint even when the
    // media player is present.
    if (!ttsContainer) ttsContainer = document.getElementById("tts-player-container");
    if (ttsContainer) {
      ttsContainer.style.display = initialVisible ? "" : "none";
    }

    toggle.addEventListener("change", function () {
      setTtsPlayerVisible(toggle.checked);
    });
  }

  function setupTtsSentenceButtonsToggle() {
    const toggle = document.getElementById("tts-sentence-buttons-toggle");
    if (!toggle) return;

    let saved = null;
    try {
      saved = localStorage.getItem("ttsSentenceButtonsVisible");
    } catch (_) {}

    var initialVisible;
    if (saved !== null) {
      initialVisible = saved !== "0";
    } else {
      initialVisible = SETTINGS.showSentenceButtons;
    }
    ttsSentenceButtonsVisible = initialVisible;

    toggle.checked = initialVisible;
    if (initialVisible) {
      document.body.classList.add("tts-sentence-buttons-active");
    } else {
      document.body.classList.remove("tts-sentence-buttons-active");
    }

    toggle.addEventListener("change", function () {
      setTtsSentenceButtonsVisible(toggle.checked);
    });
  }

  /* ------------------------------------------------------------------
   * 9. Lightweight UI observer (sentence buttons + event delegation)
   * ------------------------------------------------------------------ */

  let _uiObserver = null;
  let _uiDebounceTimer = null;
  function startUIObserver() {
    if (_uiObserver) return;
    const textDiv = document.getElementById("thetext");
    if (!textDiv) return;

    _uiObserver = new MutationObserver(function (mutations) {
      let needsUpdate = false;
      for (const m of mutations) {
        if (m.addedNodes.length > 0) { needsUpdate = true; break; }
      }
      if (!needsUpdate) return;

      if (_uiDebounceTimer) clearTimeout(_uiDebounceTimer);
      _uiDebounceTimer = setTimeout(function () {
        _uiDebounceTimer = null;
        if (SETTINGS.showSentenceButtons) injectSentencePlayButtons();
        setupEventDelegation();
      }, 100);
    });

    _uiObserver.observe(textDiv, { childList: true });
  }

  // Form observer: lightweight — only fires on body childList changes
  // (e.g., when Lute opens a term form iframe).
  let _formObserver = null;
  function startFormObserver() {
    if (_formObserver) return;
    _formObserver = new MutationObserver(function () {
      debouncedFormCheck();
    });
    _formObserver.observe(document.body, { childList: true, subtree: false });
  }

  // Override inject functions to respect toggle states.
  const _origInjectSentenceButtons = injectSentencePlayButtons;
  injectSentencePlayButtons = function () {
    if (!SETTINGS.showSentenceButtons) return;
    _origInjectSentenceButtons();
    if (!ttsSentenceButtonsVisible) {
      document.querySelectorAll(".lute-sentence-play-btn").forEach(function (btn) {
        btn.style.display = "none";
      });
    }
  };

  // Synchronous re-inject hook for the reading page (read/index.html).  When
  // a term-save or page-turn swap replaces #thetext, the screen splitter
  // (_splitToScreens) measures paragraph heights before the debounced
  // MutationObserver below re-adds the sentence 🔊 buttons.  Because each
  // button adds inline width, the late re-injection reflows sentences / can
  // push wrapping, so paragraphs measured without the buttons end up too tall
  // and the reading area visibly jumps.  The page calls this hook right after
  // the swap -- before the split measures -- so the buttons are present for
  // that single measurement and no reflow happens afterwards.
  window.luteInjectSentenceButtons = injectSentencePlayButtons;

  /* ------------------------------------------------------------------
   * 10. Boot
   * ------------------------------------------------------------------ */

  function boot() {
    setupTtsPlayerToggle();
    setupTtsSentenceButtonsToggle();

    // Initialise the full TTS player if its container is on the page.
    // (For YouTube / MP3 books the container isn't rendered and we
    // fall back to the legacy small panel behaviour.)
    if (document.getElementById("tts-player-container")) {
      ttsInitPlayer();
      ttsStartTextObserver();
    }

    if (document.getElementById("thetext")) {
      if (SETTINGS.showSentenceButtons) injectSentencePlayButtons();
      setupEventDelegation();
      startUIObserver();
    }

    startFormObserver();
    processTranslationFlow();

    // After a term status update, lute.js reloads #thetext.  The cue
    // list rebuild is handled by the #thetext MutationObserver, but
    // we also re-apply status colours to the subtitle.  Skip events
    // that another book's player triggered (bookId mismatch).
    window.addEventListener("lute:status-updated", function (e) {
      var detail = e.detail || {};
      var pageBookId = Number($("#book_id").val()) || null;
      if (detail.bookId && pageBookId &&
          Number(detail.bookId) !== pageBookId) {
        return;
      }
      ttsApplySubtitleStatusColors();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
