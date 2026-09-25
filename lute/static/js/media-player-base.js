/* Shared engine for the reading page subtitle players.

   youtube-player.js (YouTube iframe / HTML5 audio+video backends) and
   bilibili-player.js (dash.js backend + embed fallback) drive the exact
   same UI -- the yt-* controls, the single-line scrolling subtitle whose
   words reuse the reading-page tokenization, and the transcript panel.
   This file holds everything the two share:

   - play/pause, seek timeline, playback rate controls
   - single-sentence loop and auto-pause-at-end-of-sentence
   - media -> transcript/subtitle syncing (highlight + smooth-center)
   - transcript -> media seeking on line click
   - the word-span subtitle with click-to-lookup behaviour

   Data (cues, words, bookId, ...) is injected by the player templates
   via window.LUTE_YT_DATA.

   A player file wires this up with:

     var player = LuteMediaPlayer({
       ...behaviour options for this backend...,
       handleError: fn,          // backend-specific error UI
       notReadyMessage: fn,      // "player never became ready" text (or null)
       beforeActivateCue: fn,    // e.g. lazy word fetching
       onPaused: fn,             // e.g. deferred subtitle re-sync
       onStatusUpdated: fn,      // term-status changed while reading
     });
     player.setPlayer(<YT.Player-shaped backend object>);

   The backend must look like a YT.Player: load, playVideo, pauseVideo,
   getCurrentTime, getDuration, seekTo, getPlaybackRate,
   setPlaybackRate, getPlayerState, and optionally getLoadedFraction /
   getVideoLoadedFraction for the timeline's buffered band.

   State names follow the YouTube IFrame API (the values are identical
   whether or not window.YT is loaded, so the fixed set below is used
   for every backend).
*/

(function () {
  "use strict";

  var PS = { UNSTARTED: -1, ENDED: 0, PLAYING: 1, PAUSED: 2, BUFFERING: 3, CUED: 5 };

  // The backend wrappers in the player files compare against the same
  // state set (they are written against the YT.Player interface too).
  window.LuteMediaPlayerStates = PS;

  window.LuteMediaPlayer = function (options) {
    var YT_DATA = window.LUTE_YT_DATA || {};
    var CUES = Array.isArray(YT_DATA.cues) ? YT_DATA.cues : [];
    var WORDS = Array.isArray(YT_DATA.words) ? YT_DATA.words : [];
    var BOOK_ID = YT_DATA.bookId;
    var START_POS = parseFloat(YT_DATA.startPos) || 0;

    var ytPlayer = null;
    var ytPlayerReady = false;
    var ytPlaying = false;
    var ytDuration = 0;
    var ytCueIndex = -1;
    var ytLoop = false;
    var ytAutoPause = false;
    var ytRate = 1.0;
    var ytDragging = false;
    var ytLastSavedT = -10;
    var ytMarqueeOverflow = 0;
    var ytIsRtl = false;
    // Set by the bilibili adapter when playback fell back to the
    // official embed player: the transport is Bilibili's own, so
    // syncing, buffering feedback and the not-ready notice all stop.
    var ytEmbedMode = false;

    var els = {
      container: document.getElementById("yt-player-container"),
      videoWrap: document.querySelector(".yt-player-video-wrap"),
      playBtn: document.getElementById("yt-play-btn"),
      prevCueBtn: document.getElementById("yt-prev-cue-btn"),
      nextCueBtn: document.getElementById("yt-next-cue-btn"),
      timeline: document.getElementById("yt-timeline"),
      curTimeEl: document.getElementById("yt-current-time"),
      durationEl: document.getElementById("yt-duration"),
      rateInd: document.getElementById("yt-rate-indicator"),
      loopBtn: document.getElementById("yt-loop-btn"),
      autoPauseBtn: document.getElementById("yt-autopause-btn"),
      fullscreenBtn: document.getElementById("yt-fullscreen-btn"),
      transcriptBtn: document.getElementById("yt-transcript-btn"),
      transcript: document.getElementById("yt-transcript"),
      transcriptList: document.getElementById("yt-transcript-list"),
      subtitle: document.getElementById("yt-scrolling-subtitle-inner"),
      loading: document.getElementById("yt-player-loading"),
      settingsBtn: document.getElementById("yt-settings-btn"),
      settingsDropdown: document.getElementById("yt-settings-dropdown"),
      audioModeCb: document.getElementById("yt-audio-mode-cb"),
      embedNotice: document.getElementById("bili-embed-notice"),
      qualityRow: document.getElementById("yt-quality-row"),
      qualitySelect: document.getElementById("yt-quality-select"),
    };
    var AUDIO_MODE_STORAGE_KEY = "ytAudioMode";

    function ytFmtTime(secs) {
      if (!isFinite(secs) || secs < 0) secs = 0;
      secs = Math.floor(secs);
      var h = Math.floor(secs / 3600);
      var m = Math.floor((secs % 3600) / 60);
      var s = secs % 60;
      var mm = m < 10 ? "0" + m : "" + m;
      var ss = s < 10 ? "0" + s : "" + s;
      return h > 0 ? h + ":" + mm + ":" + ss : m + ":" + ss;
    }

    // Fraction of the media buffered (0..1), for the timeline's
    // buffered band.  Backends may expose either YT's
    // getVideoLoadedFraction or an HTML5-wrapper getLoadedFraction.
    function ytLoadedFraction() {
      if (!ytPlayer) return 0;
      try {
        if (typeof ytPlayer.getLoadedFraction === "function")
          return ytPlayer.getLoadedFraction() || 0;
        if (typeof ytPlayer.getVideoLoadedFraction === "function")
          return ytPlayer.getVideoLoadedFraction() || 0;
      } catch (e) { /* ignore */ }
      return 0;
    }

    /* ------------------------------------------------------------------ */
    /* Backend lifecycle                                                   */
    /* ------------------------------------------------------------------ */

    function ytOnReady() {
      ytPlayerReady = true;
      if (els.loading && !ytEmbedMode) els.loading.style.display = "none";
      if (START_POS > 0) {
        try { ytPlayer.seekTo(START_POS, true); } catch (e) { /* ignore */ }
      }
      ytDuration = ytPlayer.getDuration() || 0;
      els.durationEl.textContent = ytFmtTime(ytDuration);
      els.timeline.max = ytDuration || 1000;
      ytUpdatePlayBtn();
      window.setInterval(ytPoll, 250);
    }

    // Buffering feedback: while the media is buffering (weak network,
    // seek past the buffered range), show the loading overlay so the
    // frozen timeline isn't mistaken for a crash.  Only after onReady —
    // before that, the overlay already shows "Loading player...".
    var ytBufferingVisible = false;
    function ytShowBuffering(show) {
      if (!els.loading || ytEmbedMode) return;
      if (show) {
        if (!ytBufferingVisible && ytPlayerReady) {
          ytBufferingVisible = true;
          els.loading.textContent = "Buffering...";
          els.loading.style.display = "block";
        }
      } else if (ytBufferingVisible) {
        ytBufferingVisible = false;
        els.loading.style.display = "none";
      }
    }

    function ytOnStateChange(event) {
      ytPlaying = event.data === PS.PLAYING;
      ytUpdatePlayBtn();
      ytShowBuffering(event.data === PS.BUFFERING);
      if (event.data === PS.PLAYING) {
        // The backend may reset the rate on (re)load; restore ours.
        try {
          if (Math.abs(ytPlayer.getPlaybackRate() - ytRate) > 0.01)
            ytPlayer.setPlaybackRate(ytRate);
        } catch (e) { /* ignore */ }
      }
      if (event.data === PS.PAUSED) {
        ytSavePosition();
        if (options.onPaused) options.onPaused();
      }
    }

    function ytOnError() {
      if (options.handleError) options.handleError();
    }

    /* ------------------------------------------------------------------ */
    /* Poll loop: sync timeline, subtitle, transcript, loop / auto-pause  */
    /* ------------------------------------------------------------------ */

    function ytPoll() {
      if (ytEmbedMode) return;
      if (!ytPlayerReady || !ytPlayer) return;
      var t = ytPlayer.getCurrentTime() || 0;
      var dur = ytPlayer.getDuration() || 0;
      if (dur > 0) {
        ytDuration = dur;
        els.timeline.max = dur;
        els.durationEl.textContent = ytFmtTime(dur);
      }

      if (!ytDragging) {
        els.timeline.value = t;
        var bufferedPct = Math.round(ytLoadedFraction() * 100);
        els.timeline.style.backgroundSize =
          (dur > 0 ? (t / dur) * 100 : 0) + "% 100%, " + bufferedPct + "% 100%";
      }
      els.curTimeEl.textContent = ytFmtTime(t);

      // Media -> transcript/subtitle: find the active cue.
      var idx = -1;
      for (var i = 0; i < CUES.length; i++) {
        if (t >= CUES[i].start && t < CUES[i].end) {
          idx = i;
          break;
        }
      }

      // Single-sentence loop / auto-pause.
      //
      // This MUST be checked BEFORE updating ytCueIndex: once t crosses
      // cue.end, the loop above already picks up the next cue (or none),
      // so checking the *new* cue.end would never trigger.  We compare
      // against the cue the user is currently watching (ytCueIndex).
      //
      // Loop takes precedence over auto-pause: when both are on, the
      // sentence keeps looping instead of pausing.  When auto-pause
      // fires, we seek back to the cue start and pause, so pressing play
      // replays the same sentence; turning on loop at that point resumes
      // the loop.
      if (ytPlaying && ytCueIndex >= 0) {
        var curCue = CUES[ytCueIndex];
        if (curCue && t >= curCue.end) {
          if (ytLoop) {
            ytPlayer.seekTo(curCue.start, true);
            ytUpdateMarquee(curCue.start);
            _ytMaybeSavePosition(curCue.start);
            return;
          } else if (ytAutoPause) {
            ytPlayer.pauseVideo();
            ytPlayer.seekTo(curCue.start, true);
            ytUpdateMarquee(curCue.start);
            _ytMaybeSavePosition(curCue.start);
            return;
          }
        }
      }

      if (idx !== ytCueIndex) {
        ytCueIndex = idx;
        if (idx >= 0) ytActivateCue(idx);
        else ytDeactivateCue();
      }

      ytUpdateMarquee(t);

      _ytMaybeSavePosition(t);
    }

    // Save position at most every options.saveIntervalSec seconds.
    // Called from the main poll loop and from the early-return loop /
    // auto-pause branches so the position is still persisted when
    // playback is paused at a cue end.  The pause handler and (where
    // enabled) the pagehide/visibilitychange beacon cover the rest.
    function _ytMaybeSavePosition(t) {
      var interval = options.saveIntervalSec || 15;
      if (t - ytLastSavedT >= interval) {
        ytLastSavedT = t;
        ytSavePosition(t);
      }
    }

    function ytActivateCue(idx) {
      if (options.beforeActivateCue) options.beforeActivateCue(idx);
      // Underline this cue's line in the reading text, so the reader can
      // follow along in the page and not only in the subtitle above it.
      ytMarkPlayingLine(idx);
      // Single-line scrolling subtitle, reusing the reading-page word
      // spans.  If the word HTML hasn't been loaded yet (WORDS is
      // empty), fall back to the plain cue text so the user sees
      // something immediately.
      if (els.subtitle) {
        // Close any in-progress drag-selection: the old word spans are
        // about to be replaced, so selection_start_el would point to a
        // detached element.
        if (typeof clear_newmultiterm_elements === "function")
          clear_newmultiterm_elements();
        // Same for an open term popup anchored to one of those spans:
        // the innerHTML write below detaches the word, and a card left
        // over from it would stay on screen (one more block per cue).
        if (typeof _hide_element_message_tooltips === "function")
          _hide_element_message_tooltips();
        var html = WORDS[idx];
        if (!html) {
          var cue = CUES[idx];
          html = cue ? ytEscapeHtml(cue.text || "") : "";
        }
        els.subtitle.innerHTML = html;
        els.subtitle.scrollLeft = 0;
        ytIsRtl = els.subtitle.getAttribute("dir") === "rtl";
        // Defer measurement until the next frame so the browser has laid
        // out the freshly-injected word spans.  Measuring synchronously
        // right after innerHTML = ... often reports zero overflow (or
        // stale numbers from the previous cue) because style/layout is
        // still pending, which causes the marquee to start at the wrong
        // size and visually "jump" once the layout finally settles.
        window.requestAnimationFrame(function () {
          var overflow = els.subtitle.scrollWidth - els.subtitle.clientWidth;
          ytMarqueeOverflow = ytIsRtl ? 0 : Math.max(0, overflow);
        });
        // Inherit the reading-page word status colors.  The subtitle
        // word spans are injected after add_status_classes() has already
        // run for the page, so they'd otherwise render without their
        // status background.  Mirror lute.js: when show_highlights is on,
        // paint every word; otherwise leave it to the hover handlers
        // (bound in bindSubtitleInteractions) to reveal the color.
        ytApplySubtitleStatusColors();
      }

      // Transcript highlight + smooth scroll to the center.
      var rows = els.transcriptList
        ? els.transcriptList.querySelectorAll(".yt-transcript-row")
        : [];
      for (var r = 0; r < rows.length; r++) {
        rows[r].classList.toggle("active", r === idx);
      }
      var row = rows[idx];
      if (row && els.transcript && els.transcript.style.display !== "none") {
        // Use getBoundingClientRect (not offsetTop) because the list
        // container has position:static so offsetTop is relative to BODY.
        var rowRect = row.getBoundingClientRect();
        var listRect = els.transcriptList.getBoundingClientRect();
        var rowTopInList = rowRect.top - listRect.top + els.transcriptList.scrollTop;
        var containerH = els.transcriptList.clientHeight;
        var target = rowTopInList - containerH / 2 + rowRect.height / 2;
        target = Math.max(0, Math.min(target, els.transcriptList.scrollHeight - containerH));
        els.transcriptList.scrollTo({
          top: target,
          behavior: "smooth",
        });
      }
    }

    // Underline the cue's line in the page text (#thetext).  The page's
    // lines are the cue lines, one <p> each, and the cue index of every
    // line was handed over by the server (window.LUTE_PAGE_CUE_MAP); the
    // helper resolves it against the current page and falls back to
    // matching the cue text.  A cue that is not on the page being read
    // marks nothing, which is the honest answer while the reader is
    // somewhere else in the book.
    function ytMarkPlayingLine(idx) {
      if (!window.LutePlayingLine) return;
      var cue = CUES[idx];
      window.LutePlayingLine.setCueIndex(idx, cue ? cue.text : "");
    }

    function ytDeactivateCue() {
      if (window.LutePlayingLine) window.LutePlayingLine.clear();
      var rows = els.transcriptList
        ? els.transcriptList.querySelectorAll(".yt-transcript-row")
        : [];
      for (var r = 0; r < rows.length; r++) {
        rows[r].classList.remove("active");
      }
      if (els.subtitle) {
        if (typeof clear_newmultiterm_elements === "function")
          clear_newmultiterm_elements();
        // The word spans are about to go: close the popup of whichever of
        // them has one open, or it would float on over the empty subtitle.
        if (typeof _hide_element_message_tooltips === "function")
          _hide_element_message_tooltips();
        els.subtitle.innerHTML = "";
        ytMarqueeOverflow = 0;
      }
    }

    function ytUpdateMarquee(t) {
      if (ytCueIndex < 0 || ytMarqueeOverflow <= 0 || !els.subtitle) return;
      var cue = CUES[ytCueIndex];
      if (!cue) return;
      var dur = Math.max(0.5, (cue.end || 0) - (cue.start || 0));
      var progress = Math.min(1, Math.max(0, (t - cue.start) / dur));
      els.subtitle.scrollLeft = ytMarqueeOverflow * progress;
    }

    /* ------------------------------------------------------------------ */
    /* Transcript panel                                                    */
    /* ------------------------------------------------------------------ */

    function buildTranscript() {
      if (!els.transcriptList) return;
      els.transcriptList.innerHTML = "";
      CUES.forEach(function (cue, i) {
        var row = document.createElement("div");
        row.className = "yt-transcript-row";
        row.id = "yt-transcript-row-" + i;

        var ts = document.createElement("span");
        ts.className = "yt-transcript-ts";
        ts.textContent = ytFmtTime(cue.start);
        ts.title = "Jump to " + ytFmtTime(cue.start);

        var txt = document.createElement("span");
        txt.className = "yt-transcript-text";
        txt.textContent = cue.text || "";

        row.appendChild(ts);
        row.appendChild(txt);
        // Clicking a line jumps to it and plays -- but not when the click
        // was the end of a selection, nor the first click of a
        // double-click, which selects a word to copy.  bind_line_click()
        // handles both (see lute-cursor.js).
        bind_line_click(row, function () {
          ytSeekToCue(i, true);
        });
        els.transcriptList.appendChild(row);
      });
    }

    // Transcript -> media: jump the playhead to the cue start.
    function ytSeekToCue(i, autoplay) {
      if (!ytPlayerReady || !ytPlayer || !CUES[i]) return;
      var cue = CUES[i];
      ytPlayer.seekTo(cue.start, true);
      ytCueIndex = i;
      ytActivateCue(i);
      // When jumping from the transcript list, resume playback;
      // for prev/next buttons the caller decides whether to force play
      // (see ytJumpCue).
      if (autoplay && ytPlayer.getPlayerState() !== PS.PLAYING) {
        ytPlayer.playVideo();
      }
    }

    // Prev/next subtitle jump used by the cue buttons.  In line-by-line
    // study (auto-pause on) stepping while paused plays the next
    // sentence right away ("autopause"); otherwise the play state is
    // kept so the user can scrub through subtitles without forcing
    // playback ("never").
    function ytJumpCue(delta) {
      if (!ytPlayerReady || !ytPlayer || !CUES.length) return;
      var n = CUES.length;
      var target = ytCueIndex < 0 ? 0 : ytCueIndex + delta;
      if (target < 0) target = 0;
      if (target >= n) target = n - 1;
      ytSeekToCue(target, options.jumpCueAutoplay !== "never" ? ytAutoPause : false);
    }

    /* ------------------------------------------------------------------ */
    /* Controls                                                            */
    /* ------------------------------------------------------------------ */

    function ytTogglePlay() {
      if (!ytPlayer) return;
      // Backends that can start before their metadata has loaded (the
      // HTML5 audio element) allow playback before ytPlayerReady is set;
      // for the rest, wait for ready or the button would do nothing.
      if (!ytPlayerReady) {
        if (options.playBeforeReady) {
          try {
            if (typeof ytPlayer.load === "function") ytPlayer.load();
            if (typeof ytPlayer.playVideo === "function") ytPlayer.playVideo();
          } catch (e) { /* ignore */ }
        }
        return;
      }
      if (ytPlaying) ytPlayer.pauseVideo();
      else ytPlayer.playVideo();
    }

    function ytUpdatePlayBtn() {
      if (!els.playBtn) return;
      els.playBtn.classList.toggle("playing", ytPlaying);
    }

    function ytSetRate(delta) {
      if (!ytPlayerReady || !ytPlayer) return;
      ytRate = Math.min(2, Math.max(0.25, +(ytRate + delta).toFixed(2)));
      try {
        ytPlayer.setPlaybackRate(ytRate);
      } catch (e) { /* ignore */ }
      els.rateInd.textContent = ytRate.toFixed(2).replace(/\.?0+$/, "");
      if (els.rateInd.textContent === "") els.rateInd.textContent = "1";
    }

    function ytResetRate() {
      ytRate = 1.0;
      if (ytPlayerReady && ytPlayer) {
        try { ytPlayer.setPlaybackRate(1.0); } catch (e) { /* ignore */ }
      }
      els.rateInd.textContent = "1";
    }

    function ytSavePosition(t) {
      if (!ytPlayerReady || !ytPlayer || !BOOK_ID) return;
      var pos = typeof t === "number" ? t : (ytPlayer.getCurrentTime() || 0);
      if (pos === 0) return;
      $.ajax({
        url: "/read/save_youtube_player_data",
        method: "POST",
        data: JSON.stringify({ bookid: BOOK_ID, position: pos }),
        contentType: "application/json; charset=utf-8",
      });
    }

    // Final save when the page is hidden or closed.  Regular ajax calls
    // are dropped mid-flight on unload; fetch with keepalive survives it.
    function ytSavePositionBeacon() {
      if (!ytPlayerReady || !ytPlayer || !BOOK_ID) return;
      var pos = ytPlayer.getCurrentTime() || 0;
      if (pos <= 0) return;
      try {
        fetch("/read/save_youtube_player_data", {
          method: "POST",
          headers: { "Content-Type": "application/json; charset=utf-8" },
          body: JSON.stringify({ bookid: BOOK_ID, position: pos }),
          keepalive: true,
        }).catch(function () { /* best effort */ });
      } catch (e) { /* ignore */ }
    }

    function bindControls() {
      if (els.playBtn) {
        els.playBtn.addEventListener("click", ytTogglePlay);
      }
      if (els.prevCueBtn) {
        els.prevCueBtn.addEventListener("click", function () { ytJumpCue(-1); });
      }
      if (els.nextCueBtn) {
        els.nextCueBtn.addEventListener("click", function () { ytJumpCue(1); });
      }
      if (els.timeline) {
        els.timeline.addEventListener("pointerdown", function () {
          ytDragging = true;
        });
        els.timeline.addEventListener("input", function () {
          els.curTimeEl.textContent = ytFmtTime(Number(els.timeline.value));
        });
        els.timeline.addEventListener("pointerup", function () {
          ytDragging = false;
          if (ytPlayerReady && ytPlayer)
            ytPlayer.seekTo(Number(els.timeline.value), true);
        });
        els.timeline.addEventListener("keyup", function () {
          if (ytPlayerReady && ytPlayer)
            ytPlayer.seekTo(Number(els.timeline.value), true);
        });
      }
      var incBtn = document.getElementById("yt-rate-inc");
      var decBtn = document.getElementById("yt-rate-dec");
      if (incBtn) incBtn.addEventListener("click", function () { ytSetRate(0.25); });
      if (decBtn) decBtn.addEventListener("click", function () { ytSetRate(-0.25); });
      if (els.rateInd) els.rateInd.addEventListener("click", ytResetRate);
      if (els.loopBtn) {
        els.loopBtn.addEventListener("click", function () {
          ytLoop = !ytLoop;
          els.loopBtn.classList.toggle("on", ytLoop);
          // "Press loop to keep looping": if the media is currently
          // paused (e.g. auto-paused at the end of a sentence), turning
          // the loop on resumes playback so the sentence starts looping
          // immediately.  Turning it off does not pause.
          if (ytLoop && ytPlayerReady && ytPlayer && !ytPlaying) {
            ytPlayer.playVideo();
          }
        });
      }
      if (els.autoPauseBtn) {
        els.autoPauseBtn.addEventListener("click", function () {
          ytAutoPause = !ytAutoPause;
          els.autoPauseBtn.classList.toggle("on", ytAutoPause);
        });
      }
      if (els.fullscreenBtn) {
        els.fullscreenBtn.addEventListener("click", ytToggleFullscreen);
      }
      // Audio-only mode toggle + settings dropdown (video books).
      if (els.audioModeCb) {
        if (options.audioModeEnabled) {
          els.audioModeCb.checked =
            localStorage.getItem(AUDIO_MODE_STORAGE_KEY) === "1";
          els.audioModeCb.addEventListener("change", ytApplyAudioMode);
          // Apply the persisted mode on load so a returning video book
          // opens as an audio-only screen immediately.
          ytApplyAudioMode();
        } else {
          // Not a video backend; keep the setting hidden/irrelevant.
          els.audioModeCb.disabled = true;
        }
      }
      if (els.settingsBtn && els.settingsDropdown) {
        els.settingsBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          els.settingsDropdown.hidden = !els.settingsDropdown.hidden;
        });
        // Close the settings menu when clicking anywhere outside it.
        document.addEventListener("click", function (e) {
          var wrap = document.querySelector(".yt-settings-wrap");
          if (wrap && !wrap.contains(e.target)) {
            els.settingsDropdown.hidden = true;
          }
        });
      }
      // Keep the fullscreen button state in sync with the browser,
      // e.g. when the user presses Esc to exit fullscreen.
      var fsHandler = function () {
        var isFs = document.fullscreenElement || document.webkitFullscreenElement;
        if (els.fullscreenBtn) els.fullscreenBtn.classList.toggle("on", !!isFs);
      };
      document.addEventListener("fullscreenchange", fsHandler);
      document.addEventListener("webkitfullscreenchange", fsHandler);
      if (els.transcriptBtn) {
        els.transcriptBtn.addEventListener("click", function () {
          var isOpen = els.transcript.style.display !== "none";
          if (isOpen) {
            els.transcript.style.display = "none";
            els.transcriptBtn.classList.remove("on");
          } else {
            els.transcript.style.display = "block";
            els.transcriptBtn.classList.add("on");
            // Center the current line when the panel is opened.
            var scrollFn = options.transcriptScroll === "fancy"
              ? ytFancyScrollToActiveRow
              : ytSimpleScrollToActiveRow;
            // Wait for layout to settle after display:none -> block,
            // then scroll the active row into view.
            requestAnimationFrame(function () {
              requestAnimationFrame(function () {
                scrollFn();
              });
            });
          }
        });
      }
    }

    // Simple version: the panel has just been laid out, so scroll
    // straight to the (known or first) active row.
    function ytSimpleScrollToActiveRow() {
      var idx = ytCueIndex;
      if (idx < 0) idx = 0;
      var row = els.transcriptList
        ? els.transcriptList.querySelector("#yt-transcript-row-" + idx)
        : null;
      if (row && els.transcriptList) {
        var rowRect = row.getBoundingClientRect();
        var listRect = els.transcriptList.getBoundingClientRect();
        var rowTopInList = rowRect.top - listRect.top + els.transcriptList.scrollTop;
        var target =
          rowTopInList -
          els.transcriptList.clientHeight / 2 +
          rowRect.height / 2;
        target = Math.max(
          0,
          Math.min(target, els.transcriptList.scrollHeight - els.transcriptList.clientHeight)
        );
        els.transcriptList.scrollTop = target;
      }
    }

    // Robust version: forces a reflow, infers the current row from the
    // play position when no cue is active yet, and retries a few times
    // over 500ms to handle slow DOM rendering.
    function ytFancyScrollToActiveRow(attempt) {
      var attemptNo = attempt || 0;
      if (!els.transcriptList) return;
      // Force a reflow to ensure display:none -> block has completed layout.
      // Without this, clientHeight can still be 0 even after double-rAF.
      var containerHeight = els.transcriptList.clientHeight;
      void els.transcriptList.offsetHeight; // Trigger reflow
      containerHeight = els.transcriptList.clientHeight;

      var idx = ytCueIndex;
      // If no cue is active yet, infer from the best available position:
      //   1. the backend's currentTime (could be set via a START_POS seek)
      //   2. otherwise the saved START_POS from the last visit
      if (idx < 0) {
        var t = 0;
        if (ytPlayer && typeof ytPlayer.getCurrentTime === "function") {
          t = ytPlayer.getCurrentTime() || 0;
        }
        if (t <= 0 && START_POS > 0) {
          t = START_POS;
        }
        if (t > 0) {
          for (var k = CUES.length - 1; k >= 0; k--) {
            if ((CUES[k].start || 0) <= t) {
              idx = k;
              break;
            }
          }
        }
        if (idx < 0) idx = 0;
      }
      var row = els.transcriptList.querySelector("#yt-transcript-row-" + idx);
      if (row) {
        // offsetTop is relative to offsetParent (BODY when the list has
        // position:static), so use getBoundingClientRect to get the row's
        // position within the scrollable list instead.
        var rowRect = row.getBoundingClientRect();
        var listRect = els.transcriptList.getBoundingClientRect();
        var rowTopInList = rowRect.top - listRect.top + els.transcriptList.scrollTop;
        var target = rowTopInList - containerHeight / 2 + rowRect.height / 2;
        // Clamp target to valid scroll range
        target = Math.max(0, Math.min(target, els.transcriptList.scrollHeight - containerHeight));
        els.transcriptList.scrollTop = target;
      } else if (attemptNo < 3) {
        // Row not found yet -- retry after 100ms
        setTimeout(function () {
          ytFancyScrollToActiveRow(attemptNo + 1);
        }, 100);
      }
    }

    // Fullscreen the video wrapper (more reliable than an iframe,
    // which needs its own allowfullscreen attribute). The wrapper is
    // filled by the video, so the picture scales up correctly.  Only
    // rendered for video books.
    function ytToggleFullscreen() {
      var el = els.videoWrap || els.container;
      if (!el) return;
      var isFs = document.fullscreenElement || document.webkitFullscreenElement;
      if (isFs) {
        if (document.exitFullscreen) document.exitFullscreen();
        else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
      } else {
        if (el.requestFullscreen) el.requestFullscreen();
        else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
      }
    }

    // Audio-only mode for video books (the gear button menu).  Hides
    // the video area so the reading screen matches an MP3 book:
    // controls + scrolling subtitle, no video picture.  The hidden
    // <video> element / iframe keeps playing its audio track.
    // Persists per browser.
    function ytApplyAudioMode() {
      if (!els.videoWrap || !els.audioModeCb) return;
      var on = !!els.audioModeCb.checked;
      els.container.classList.toggle("yt-audio-mode", on);
      localStorage.setItem(AUDIO_MODE_STORAGE_KEY, on ? "1" : "0");
      // Close the gear menu: the change event comes from a click inside
      // the dropdown, so the outside-click closer never fires.
      if (els.settingsDropdown) els.settingsDropdown.hidden = true;
      // Hiding/showing the video changes the height available to the text
      // area (#thetext), so re-flow the fit-to-screen groups and re-centre
      // the side navigation instead of leaving them sized for the old
      // layout.  A short timeout instead of requestAnimationFrame: rAF
      // callbacks are suspended in occluded / background windows, which
      // would leave the reflow undone until the tab is focused again.
      setTimeout(function () {
        if (typeof _splitToScreens === "function") _splitToScreens();
        if (typeof _layout_side_nav === "function") _layout_side_nav();
      }, 50);
    }

    /* ------------------------------------------------------------------ */
    /* Subtitle word interactions (same as the reading text)               */
    /* ------------------------------------------------------------------ */

    // Apply status color classes to the scrolling-subtitle word spans.
    // The subtitle ALWAYS shows word colors, regardless of the
    // show_highlights setting, so the user can see at a glance which
    // words they know (status 1-5), which are unknown (status 0), and
    // which are ignored/well-known (status 98/99, no color).
    function ytApplySubtitleStatusColors() {
      if (!els.subtitle) return;
      if (typeof apply_status_class !== "function") return;
      $(els.subtitle).find("span.word").each(function () {
        apply_status_class($(this));
      });
    }

    function bindSubtitleInteractions() {
      if (!els.subtitle) return;
      var t = $(els.subtitle);
      if (typeof word_clicked !== "function") return;

      // Bind the same interaction model as the main text (#thetext) so
      // the subtitle supports both single-word clicks and drag-select
      // multiword term creation.  select_ended() handles the single-
      // click case (same start/end element) by calling word_clicked,
      // so no separate "click" handler is needed.
      if (typeof _isUserUsingMobile === "function" && _isUserUsingMobile()) {
        // Mobile: long-press to start/end a multiword selection.
        t.on("touchstart", ".word", touch_started);
        t.on("touchend", ".word", touch_ended);
      } else {
        // Desktop: mouse drag to select a range.
        t.on("mousedown", ".word", handle_select_started);
        t.on("mouseover", ".word", handle_select_over);
        t.on("mouseup", ".word", handle_select_ended);
        t.on("mouseover", ".word", hover_over);
        t.on("mouseout", ".word", hover_out);
        // Hover pronunciation (same as the main text #thetext): speak
        // the hovered word while playback is stopped or paused.  The
        // shared engine in tts.js is resolved at event time because
        // tts.js loads after this script.
        t.on("mouseover", ".word", function () {
          if (window.luteHoverSpeakStart)
            window.luteHoverSpeakStart(
              this.innerText || this.textContent || "",
              function () { return ytPlaying; }
            );
        });
        t.on("mouseout", ".word", function () {
          if (window.luteHoverSpeakCancel) window.luteHoverSpeakCancel();
        });
      }

      if (options.replayOnNonWordClick) {
        // Tap anywhere on the line that isn't a word (padding, marquee
        // gaps, punctuation spans) to replay the current line from its
        // start -- a quick re-listen while checking word statuses on the
        // mini player.  Taps on words keep their term-popup behavior, and
        // a drag-selection across words must not replay: its click lands
        // on this common ancestor, and the selection check in
        // click_ends_selection() is what catches it (the native selection
        // is often already empty by then -- see lute-cursor.js).
        t.on("click", function (e) {
          if (e.target.closest && e.target.closest(".word")) return;
          if (typeof click_ends_selection === "function" && click_ends_selection(e)) return;
          if (ytCueIndex < 0) return;
          ytSeekToCue(ytCueIndex, true);
        });
      }

      // Status colors are always applied (see ytApplySubtitleStatusColors),
      // so we do NOT bind the hover-based add/remove that the main text
      // uses when show_highlights is off.

      if (typeof tooltip_textitem_hover_content === "function" &&
          typeof _get_tooltip_pos === "function") {
        t.tooltip({
          position: _get_tooltip_pos(),
          items: ".word",
          show: { easing: "easeOutCirc" },
          // Close with no fade: a fading card stays clickable over the
          // subtitle line below it (see lute.js / styles.css).
          hide: false,
          content: function (setContent) {
            tooltip_textitem_hover_content($(this), setContent);
          },
        });
      }
    }

    /* ------------------------------------------------------------------ */
    /* Word cache                                                          */
    /* ------------------------------------------------------------------ */

    function ytEscapeHtml(s) {
      var d = document.createElement("div");
      d.textContent = s;
      return d.innerHTML;
    }

    // Fetch the tokenized word HTML for all cues.  Adapters decide when
    // to call this: eagerly (small books) or only as a fallback (the
    // expensive tokenization must not block the initial page render).
    // While loading, the subtitle shows plain cue text.
    //
    // The `reactivate` argument: when true (the default), re-inject the
    // current cue's word spans after the data arrives (needed on the
    // initial load to switch from plain text to word spans).  Pass false
    // after a term status update: the current cue already shows word
    // spans, and re-rendering it would replace the whole subtitle DOM
    // (causing a brief visual font change).  Only the WORDS cache needs
    // refreshing so future cues pick up the new status classes.
    function ytLoadSubtitleWords(reactivate) {
      if (!BOOK_ID) return;
      $.ajax({
        url: "/read/youtube_subtitle_words/" + BOOK_ID,
        method: "GET",
        dataType: "json",
      }).done(function (data) {
        if (Array.isArray(data) && data.length) {
          WORDS.length = 0;
          WORDS.push.apply(WORDS, data);
          if (reactivate !== false && ytCueIndex >= 0) ytActivateCue(ytCueIndex);
        }
      });
    }

    /* ------------------------------------------------------------------ */
    /* Keyboard + term-status events                                       */
    /* ------------------------------------------------------------------ */

    function bindKeys() {
      window.addEventListener("keydown", function (e) {
        if (e.code === "Space" && options.spaceNeedsReady && !ytPlayerReady) return;
        if (e.code === "Space" &&
            e.target &&
            (e.target.tagName === "INPUT" ||
             e.target.tagName === "TEXTAREA" ||
             e.target.isContentEditable)) {
          return;
        }
        if (e.code === "Space") {
          e.preventDefault();
          if (ytPlayer) ytTogglePlay();
        }
      });

      if (options.savePositionBeacon) {
        // Persist the position when the page goes away (tab to
        // background, navigation, close) -- the polling throttle alone
        // could lose up to one save interval of progress.
        document.addEventListener("visibilitychange", function () {
          if (document.hidden) ytSavePositionBeacon();
        });
        window.addEventListener("pagehide", ytSavePositionBeacon);
      }
    }

    // After a term status update, lute.js reloads #thetext and dispatches
    // lute:status-updated with {bookId, termText} in the event detail.
    // Events for other books are ignored here.  options.onStatusEvent
    // (if given) runs synchronously at event time; the debounced
    // options.onStatusUpdated hook then decides how to re-sync the
    // subtitle words (the strategy differs per backend).
    var ytStatusRefreshTimer = null;
    function bindStatusUpdates() {
      window.addEventListener("lute:status-updated", function (e) {
        if (!els.subtitle) return;
        var detail = e.detail || {};
        if (detail.bookId && BOOK_ID &&
            Number(detail.bookId) !== Number(BOOK_ID)) {
          return;
        }
        if (options.onStatusEvent) options.onStatusEvent(detail);
        if (ytStatusRefreshTimer) clearTimeout(ytStatusRefreshTimer);
        var termText = detail.termText || null;
        ytStatusRefreshTimer = setTimeout(function () {
          ytStatusRefreshTimer = null;
          if (options.onStatusUpdated) options.onStatusUpdated(termText);
        }, 300);
      });
    }

    // "Player never became ready" fallback, on a 15s fuse.  The message
    // hook returns the text to show, or null to stay quiet (e.g. embed
    // mode, where the remote player is working).
    function bindNotReadyFallback() {
      window.setTimeout(function () {
        if (els.loading && !ytPlayerReady && !ytEmbedMode) {
          var msg = options.notReadyMessage ? options.notReadyMessage() : null;
          if (msg) {
            els.loading.textContent = msg;
            els.loading.style.display = "block";
          }
        }
      }, 15000);
    }

    /* ------------------------------------------------------------------ */
    /* Public API (what the per-backend player files drive)                */
    /* ------------------------------------------------------------------ */

    return {
      PS: PS,
      cues: CUES,
      words: WORDS,
      bookId: BOOK_ID,
      startPos: START_POS,
      els: els,
      fmtTime: ytFmtTime,
      escapeHtml: ytEscapeHtml,

      // Handlers to pass to the backend constructor.
      backendHandlers: {
        onReady: ytOnReady,
        onStateChange: ytOnStateChange,
        onError: ytOnError,
      },

      setPlayer: function (p) { ytPlayer = p; },
      player: function () { return ytPlayer; },
      isReady: function () { return ytPlayerReady; },
      isPlaying: function () { return ytPlaying; },
      cueIndex: function () { return ytCueIndex; },
      setEmbedMode: function (on) { ytEmbedMode = !!on; },
      isEmbedMode: function () { return ytEmbedMode; },

      activateCue: ytActivateCue,
      deactivateCue: ytDeactivateCue,
      seekToCue: ytSeekToCue,
      loadSubtitleWords: ytLoadSubtitleWords,
      applySubtitleStatusColors: ytApplySubtitleStatusColors,
      updatePlayBtn: ytUpdatePlayBtn,

      buildTranscript: buildTranscript,
      bindControls: bindControls,
      bindSubtitleInteractions: bindSubtitleInteractions,
      bindKeys: bindKeys,
      bindStatusUpdates: bindStatusUpdates,
      bindNotReadyFallback: bindNotReadyFallback,
    };
  };
})();
