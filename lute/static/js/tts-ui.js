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
        // are stale after #thetext is replaced.  Unless this page turn
        // was the player's own: ttsAdvance turns the page when it runs
        // out of cues so the reading can carry on there, and leaves
        // ttsContinueOnSwap set to be resumed on the new page's text.
        // The reader's own page turns never set it, so they still stop
        // playback as they always did.
        const resume = ttsContinueOnSwap && ttsPlaying;
        ttsContinueOnSwap = false;
        ttsCancelSpeech();
        ttsPlaying = false;
        ttsPaused = false;
        ttsCueIndex = -1;
        ttsVirtualTime = 0;
        ttsUpdatePlayBtn();
        ttsBuildCues();
        // Re-apply status colours to the new subtitle word spans.
        ttsApplySubtitleStatusColors();
        // A page with nothing to read (an empty page, a divider) ends
        // the reading rather than leaving a silent player "playing".
        if (resume && ttsCues.length > 0) {
          ttsPlaying = true;
          ttsPaused = false;
          ttsUpdatePlayBtn();
          ttsPlayCue(0);
        }
      }, 150);
    });
    _textObserver.observe(textDiv, { childList: true });
  }

  /* ================================================================
     8. Player toggles (sidebar quick controls)
     ================================================================ */

  // The sidebar "Player" switch is the master visibility control: off
  // hides *every* player bar on the page (audio track, video transport
  // and TTS read-aloud alike), on brings back whichever bar the reader
  // was last using.
  let playerVisible = true;
  // The bar currently on screen when the player is visible (one of the
  // PLAYER_MODE_* values); remembered so toggling the switch back on
  // restores the same bar instead of guessing.
  let currentPlayerMode = null;
  let ttsSentenceButtonsVisible = true;

  /* ------------------------------------------------------------------
   * 8a. MP3 <-> TTS player mode
   *
   * Mirrors the mobile reader (features/reader/providers/
   * player_mode_provider.dart): a book that has audio -- an mp3 / netease
   * book, or a text book with an audio file attached -- can play either
   * its track or TTS read-aloud of the text, but only one player bar is
   * on screen at a time.  The bars are flex siblings of #thetext, so a
   * second visible bar would stack and steal the text's height.  Each
   * bar carries a button that hands over to the other
   * (youtube_player.html / tts_player.html).
   *
   * The button switch is session-only and never persisted, so a reload
   * starts from the book's own default (the audio player, when the book
   * has audio).  The sidebar "Player" checkbox is the master on/off for
   * both bars: it persists the reader's preference -- it writes the
   * language setting -- and hiding it silences the book entirely.
   * ------------------------------------------------------------------ */

  const PLAYER_MODE_MEDIA = "media";
  const PLAYER_MODE_TTS = "tts";
  // Page with no player bar at all (manga): nothing to show.
  const PLAYER_MODE_OFF = "off";

  function luteMediaPlayerContainer() {
    return document.getElementById("yt-player-container");
  }

  function luteTtsPlayerContainer() {
    if (!ttsContainer) {
      ttsContainer = document.getElementById("tts-player-container");
    }
    return ttsContainer;
  }

  // Show exactly one player bar (none when the sidebar switch is off).
  // `mode` is one of the PLAYER_MODE_* values; a mode the page cannot
  // offer degrades to the other one.
  function applyPlayerMode(mode, silent) {
    const media = luteMediaPlayerContainer();
    const tts = luteTtsPlayerContainer();

    if (mode === PLAYER_MODE_MEDIA && !media) mode = PLAYER_MODE_TTS;
    if (mode === PLAYER_MODE_TTS && !tts) {
      mode = media ? PLAYER_MODE_MEDIA : PLAYER_MODE_OFF;
    }

    currentPlayerMode = mode;

    const showMedia = playerVisible && mode === PLAYER_MODE_MEDIA;
    const showTts = playerVisible && mode === PLAYER_MODE_TTS;
    if (media) media.style.display = showMedia ? "" : "none";
    if (tts) tts.style.display = showTts ? "" : "none";

    // The TTS strip reserves its height from its own width, so it can
    // only measure once it is on screen (tts-player.js).
    if (showTts && typeof ttsSyncStripHeight === "function") {
      ttsSyncStripHeight();
    }

    document.body.classList.toggle("player-active", playerVisible);

    // The sidebar checkbox is the same switch, so keep it in sync --
    // without persisting: this is the session's state, not a preference.
    const toggle = document.getElementById("player-toggle");
    if (toggle) toggle.checked = playerVisible;

    // The reading pane re-flows around whichever bar is on screen
    // (index.html re-splits the page into screens on this event).
    if (!silent) {
      try { window.dispatchEvent(new Event("lute:tts-ui-changed")); } catch (_) {}
    }
    return mode;
  }

  // Pause the audio / video track, if this page has one loaded.
  function pauseMediaPlayer() {
    const api = window.LUTE_MEDIA_API;
    if (!api) return;
    try {
      const p = api.player();
      if (p && typeof p.pauseVideo === "function") p.pauseVideo();
    } catch (_) {}
  }

  // Hand the book over to the other player: stop whatever is playing on
  // the bar being left, like the mobile mode switch does (MP3 -> pause
  // the track; TTS -> stop the read-aloud).  Neither side auto-plays.
  function switchPlayerMode(mode) {
    if (mode === PLAYER_MODE_TTS) {
      pauseMediaPlayer();
    } else if (typeof ttsStop === "function") {
      try { ttsStop(); } catch (_) {}
    }
    applyPlayerMode(mode);
  }

  function setupPlayerModeSwitch() {
    const toTts = document.getElementById("yt-tts-mode-btn");
    if (toTts) {
      toTts.addEventListener("click", function () {
        switchPlayerMode(PLAYER_MODE_TTS);
      });
    }
    const toMedia = document.getElementById("tts-mp3-mode-btn");
    if (toMedia) {
      toMedia.addEventListener("click", function () {
        switchPlayerMode(PLAYER_MODE_MEDIA);
      });
    }
  }

  // Sidebar switch: show / hide the player bar(s) for the whole book.
  function setPlayerVisible(visible) {
    playerVisible = visible;

    // Persisted, unlike the in-player mode buttons: the switch is the
    // reader's preference (localStorage cache + language setting, which
    // the settings page shows as "Show player").
    try {
      localStorage.setItem("playerVisible", visible ? "1" : "0");
    } catch (_) {}

    var val = visible ? "1" : "0";
    try {
      fetch("/settings/set/tts_show_control_panel/" + val, { method: "POST" });
    } catch (_) {}

    // Hiding silences the book: leaving a track or read-aloud running
    // with no visible transport is never what the reader wants.
    if (!visible) {
      pauseMediaPlayer();
      if (typeof ttsStop === "function") {
        try { ttsStop(); } catch (_) {}
      }
    }

    // Re-show the bar that was on screen before (the book's own default
    // on first load); applyPlayerMode degrades if it is gone.
    applyPlayerMode(currentPlayerMode || PLAYER_MODE_MEDIA);
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

  function setupPlayerToggle() {
    const toggle = document.getElementById("player-toggle");
    if (!toggle) return;

    const media = luteMediaPlayerContainer();
    const tts = luteTtsPlayerContainer();

    // Nothing to show (manga): drop the row rather than leave a switch
    // that can't do anything.
    if (!media && !tts) {
      const row = document.getElementById("player-toggle-container");
      if (row) row.style.display = "none";
      return;
    }

    // A book renders the media player (MP3 / video / youtube) when it has
    // an audio file; the TTS bar is always rendered for text books, so
    // its presence alone says nothing about what the reader should see.
    const hasMediaPlayer = !!media;

    // PDF books are fixed-page readers: the player UI would cover the
    // page, so it starts hidden on every PDF load (the reading_menu
    // toggle can still show it for the session).
    var isPdfBook = function () {
      if (window.LUTE_BOOK_TYPE === "pdf") return true;
      return !!document.querySelector("#thetext.pdf-text-container");
    };

    // Saved preference: `savedVisible` is true / false / null (none yet),
    // `savedMode` is only set when migrating the old TTS-only key.
    let savedVisible = null;
    let savedMode = null;
    try {
      const val = localStorage.getItem("playerVisible");
      if (val !== null) {
        savedVisible = val !== "0";
      } else {
        // Pre-rename key: it meant "TTS bar shown", so on an audio book
        // a "0" still meant a *visible* player (the media bar).  Only a
        // text-only book's "0" was a real hide, and "1" means the reader
        // was on TTS.
        const legacy = localStorage.getItem("ttsPlayerVisible");
        if (legacy !== null) {
          if (legacy === "0" && !hasMediaPlayer) {
            savedVisible = false;
          } else {
            savedVisible = true;
            savedMode = legacy === "1" ? PLAYER_MODE_TTS : PLAYER_MODE_MEDIA;
          }
        }
      }
    } catch (_) {}

    // Default: the book's own player (MP3 when it has audio, TTS
    // otherwise) -- same first-load behaviour as the mobile reader.
    let initialVisible;
    if (isPdfBook()) {
      initialVisible = false;
    } else if (savedVisible !== null) {
      initialVisible = savedVisible;
    } else if (hasMediaPlayer) {
      initialVisible = true;
    } else {
      initialVisible = SETTINGS.showControlPanel;
    }

    playerVisible = initialVisible;
    toggle.checked = initialVisible;
    toggle.addEventListener("change", function () {
      setPlayerVisible(toggle.checked);
    });

    // Silent: nothing has been laid out yet (the first page arrives via
    // HTMX), so there is no re-flow to trigger.
    applyPlayerMode(
      savedMode || (hasMediaPlayer ? PLAYER_MODE_MEDIA : PLAYER_MODE_TTS),
      true
    );
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
    setupPlayerToggle();
    setupTtsSentenceButtonsToggle();
    setupPlayerModeSwitch();

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
