/* Media player for lute reading pages (book_type == "youtube" or "mp3").

   Supports two backends that share the same UI (controls, scrolling
   subtitle, transcript panel):
   - YouTube IFrame API player, for book_type == "youtube"
   - HTML5 <audio> element, for book_type == "mp3"

   Provides:
   - play/pause, seek timeline, playback rate controls
   - single-sentence loop and auto-pause-at-end-of-sentence
   - a single-line scrolling subtitle synced to the media, whose words
     reuse the reading-page tokenization and click-to-lookup behavior
   - a Transcript panel with bidirectional control:
       media -> transcript: highlight + smooth-center the current line
       transcript -> media: clicking a line/timestamp seeks to its start

   Data (videoId / audioUrl, cues, words, ...) is injected by
   templates/read/youtube_player.html via window.LUTE_YT_DATA.
*/

(function () {
  "use strict";

  // Mirror of window.YT.PlayerState, used by the HTML5 audio backend
  // when the YouTube IFrame API script is not loaded (MP3 books).
  var PS = window.YT && window.YT.PlayerState
    ? window.YT.PlayerState
    : { UNSTARTED: -1, ENDED: 0, PLAYING: 1, PAUSED: 2, BUFFERING: 3, CUED: 5 };

  var YT_DATA = window.LUTE_YT_DATA || {};
  var CUES = Array.isArray(YT_DATA.cues) ? YT_DATA.cues : [];
  var WORDS = Array.isArray(YT_DATA.words) ? YT_DATA.words : [];
  var BOOK_ID = YT_DATA.bookId;
  var START_POS = parseFloat(YT_DATA.startPos) || 0;

  // True when this is an MP3 audio book (no YouTube video id).  The
  // player backend is selected based on this flag.
  var USE_AUDIO_BACKEND = YT_DATA.backend === "audio";
  var USE_VIDEO_BACKEND = YT_DATA.backend === "video";

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

  var ytContainer = document.getElementById("yt-player-container");
  var ytVideoWrap = document.querySelector(".yt-player-video-wrap");
  var ytPlayBtn = document.getElementById("yt-play-btn");
  var ytPrevCueBtn = document.getElementById("yt-prev-cue-btn");
  var ytNextCueBtn = document.getElementById("yt-next-cue-btn");
  var ytTimeline = document.getElementById("yt-timeline");
  var ytCurTimeEl = document.getElementById("yt-current-time");
  var ytDurationEl = document.getElementById("yt-duration");
  var ytRateInd = document.getElementById("yt-rate-indicator");
  var ytLoopBtn = document.getElementById("yt-loop-btn");
  var ytAutoPauseBtn = document.getElementById("yt-autopause-btn");
  var ytFullscreenBtn = document.getElementById("yt-fullscreen-btn");
  var ytTranscriptBtn = document.getElementById("yt-transcript-btn");
  var ytTranscript = document.getElementById("yt-transcript");
  var ytTranscriptList = document.getElementById("yt-transcript-list");
  var ytSubtitle = document.getElementById("yt-scrolling-subtitle-inner");
  var ytLoading = document.getElementById("yt-player-loading");
  var ytSettingsBtn = document.getElementById("yt-settings-btn");
  var ytSettingsDropdown = document.getElementById("yt-settings-dropdown");
  var ytAudioModeCb = document.getElementById("yt-audio-mode-cb");
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

  /* ------------------------------------------------------------------ */
  /* Backend abstraction                                                 */
  /* ------------------------------------------------------------------ */

  // Wrap an HTML5 <audio> element to look like a YT.Player instance,
  // so the rest of this file (which was written for the YT IFrame API)
  // can drive an MP3 audio book unchanged.
  function LuteAudioPlayer(audioEl, handlers) {
    var ready = false;
    var fakeState = PS.PAUSED;
    var pollTimer = null;

    function fireReady() {
      if (ready) return;
      ready = true;
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      if (typeof handlers.onReady === "function") handlers.onReady();
    }

    function fireStateChange(s) {
      fakeState = s;
      if (typeof handlers.onStateChange === "function") handlers.onStateChange({ data: s });
    }

    function checkReady() {
      if (ready) {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        return;
      }
      // readyState >= 1 means HAVE_METADATA (duration is available).
      if ((isFinite(audioEl.duration) && audioEl.duration > 0) || audioEl.readyState >= 1) {
        fireReady();
      }
    }

    audioEl.addEventListener("loadedmetadata", fireReady);
    audioEl.addEventListener("durationchange", fireReady);
    audioEl.addEventListener("play", function () {
      // If somehow onReady hasn't fired when the user plays, fire it now.
      checkReady();
      fireStateChange(PS.PLAYING);
    });
    audioEl.addEventListener("pause", function () { fireStateChange(PS.PAUSED); });
    audioEl.addEventListener("ended", function () { fireStateChange(PS.ENDED); });
    audioEl.addEventListener("canplay", fireReady);
    audioEl.addEventListener("error", function () {
      if (typeof handlers.onError === "function") handlers.onError();
    });

    return {
      // The audio element might already have its metadata cached; in
      // that case loadedmetadata won't fire again, so check on init
      // and keep polling for a short while to be safe.
      _maybeFireReady: function () {
        checkReady();
        if (!ready && !pollTimer) {
          // Poll up to ~5 seconds (250ms * 20) to catch slow metadata loads.
          var attempts = 0;
          pollTimer = setInterval(function () {
            attempts += 1;
            checkReady();
            if (ready || attempts >= 20) {
              if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
            }
          }, 250);
        }
      },
      load: function () { audioEl.load(); },
      playVideo: function () {
        // Before playing, be sure onReady has fired (it initializes
        // the timeline max, duration display, and starts ytPoll).
        checkReady();
        var p = audioEl.play();
        if (p && p.catch) p.catch(function () {});
      },
      pauseVideo: function () { audioEl.pause(); },
      getCurrentTime: function () { return audioEl.currentTime || 0; },
      getDuration: function () { return audioEl.duration || 0; },
      seekTo: function (t) { try { audioEl.currentTime = t; } catch (e) { /* ignore */ } },
      getPlaybackRate: function () { return audioEl.playbackRate || 1; },
      setPlaybackRate: function (r) { try { audioEl.playbackRate = r; } catch (e) { /* ignore */ } },
      getPlayerState: function () { return fakeState; },
    };
  }

  /* ------------------------------------------------------------------ */
  /* Player creation                                                    */
  /* ------------------------------------------------------------------ */

  function createYoutubePlayer() {
    if (ytPlayer) return;
    if (USE_AUDIO_BACKEND || USE_VIDEO_BACKEND) {
      // Both the audio and video backends wrap an HTML5 media element
      // (<audio> / <video>), which share the same HTMLMediaElement API,
      // so LuteAudioPlayer drives them both unchanged.
      var mediaEl = USE_VIDEO_BACKEND
        ? document.getElementById("yt-video-player")
        : document.getElementById("yt-audio-player");
      if (!mediaEl) return;
      ytPlayer = LuteAudioPlayer(mediaEl, {
        onReady: ytOnReady,
        onStateChange: ytOnStateChange,
        onError: ytOnError,
      });
      // Some browsers cache metadata for fast loads; ensure onReady
      // fires in that case too.
      ytPlayer._maybeFireReady();
      return;
    }

    if (!window.YT || !window.YT.Player) return;
    if (!YT_DATA.videoId) {
      if (ytLoading) {
        ytLoading.textContent =
          "Unable to load the YouTube player: no video id.";
      }
      return;
    }
    ytPlayer = new window.YT.Player("yt-player", {
      videoId: YT_DATA.videoId,
      playerVars: {
        enablejsapi: 1,
        playsinline: 1,
        rel: 0,
        controls: 0,
        modestbranding: 1,
        iv_load_policy: 3,
      },
      events: {
        onReady: ytOnReady,
        onStateChange: ytOnStateChange,
        onError: ytOnError,
      },
    });
  }

  function ytOnReady() {
    ytPlayerReady = true;
    if (ytLoading) ytLoading.style.display = "none";
    if (START_POS > 0) {
      try { ytPlayer.seekTo(START_POS, true); } catch (e) { /* ignore */ }
    }
    ytDuration = ytPlayer.getDuration() || 0;
    ytDurationEl.textContent = ytFmtTime(ytDuration);
    ytTimeline.max = ytDuration || 1000;
    ytUpdatePlayBtn();
    window.setInterval(ytPoll, 250);
  }

  function ytOnStateChange(event) {
    ytPlaying = event.data === PS.PLAYING;
    ytUpdatePlayBtn();
    if (event.data === PS.PLAYING) {
      // The API resets the rate on (re)load; restore our setting.
      try {
        if (Math.abs(ytPlayer.getPlaybackRate() - ytRate) > 0.01)
          ytPlayer.setPlaybackRate(ytRate);
      } catch (e) { /* ignore */ }
    }
    if (event.data === PS.PAUSED) {
      ytSavePosition();
    }
  }

  function ytOnError() {
    if (ytLoading) {
      ytLoading.textContent =
        USE_AUDIO_BACKEND
          ? "Unable to play this audio file. The transcript below is still available."
          : "Unable to play this video. The transcript below is still available.";
      ytLoading.style.display = "block";
    }
  }

  /* ------------------------------------------------------------------ */
  /* Poll loop: sync timeline, subtitle, transcript, loop / auto-pause  */
  /* ------------------------------------------------------------------ */

  function ytPoll() {
    if (!ytPlayerReady || !ytPlayer) return;
    var t = ytPlayer.getCurrentTime() || 0;
    var dur = ytPlayer.getDuration() || 0;
    if (dur > 0) {
      ytDuration = dur;
      ytTimeline.max = dur;
      ytDurationEl.textContent = ytFmtTime(dur);
    }

    if (!ytDragging) {
      ytTimeline.value = t;
      ytTimeline.style.backgroundSize =
        (dur > 0 ? (t / dur) * 100 : 0) + "% 100%";
    }
    ytCurTimeEl.textContent = ytFmtTime(t);

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

  // Save position roughly every 2 seconds.  Called from the main poll
  // loop and from the early-return loop / auto-pause branches so the
  // position is still persisted when playback is paused at a cue end.
  function _ytMaybeSavePosition(t) {
    if (t - ytLastSavedT >= 2) {
      ytLastSavedT = t;
      ytSavePosition(t);
    }
  }

  function ytActivateCue(idx) {
    // Single-line scrolling subtitle, reusing the reading-page word spans.
    // If the word HTML hasn't been loaded yet (WORDS is empty), fall
    // back to the plain cue text so the user sees something immediately.
    if (ytSubtitle) {
      // Clear any in-progress drag-selection: the old word spans are
      // about to be replaced, so selection_start_el would point to a
      // detached element.
      if (typeof clear_newmultiterm_elements === "function")
        clear_newmultiterm_elements();
      var html = WORDS[idx];
      if (!html) {
        var cue = CUES[idx];
        html = cue ? ytEscapeHtml(cue.text || "") : "";
      }
      ytSubtitle.innerHTML = html;
      ytSubtitle.scrollLeft = 0;
      ytIsRtl = ytSubtitle.getAttribute("dir") === "rtl";
      // Defer measurement until the next frame so the browser has laid
      // out the freshly-injected word spans.  Measuring synchronously
      // right after innerHTML = ... often reports zero overflow (or
      // stale numbers from the previous cue) because style/layout is
      // still pending, which causes the marquee to start at the wrong
      // size and visually "jump" once the layout finally settles.
      window.requestAnimationFrame(function () {
        var overflow = ytSubtitle.scrollWidth - ytSubtitle.clientWidth;
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
    var rows = ytTranscriptList
      ? ytTranscriptList.querySelectorAll(".yt-transcript-row")
      : [];
    for (var r = 0; r < rows.length; r++) {
      rows[r].classList.toggle("active", r === idx);
    }
    var row = rows[idx];
    if (row && ytTranscript && ytTranscript.style.display !== "none") {
      // Use getBoundingClientRect (not offsetTop) because the list
      // container has position:static so offsetTop is relative to BODY.
      var rowRect = row.getBoundingClientRect();
      var listRect = ytTranscriptList.getBoundingClientRect();
      var rowTopInList = rowRect.top - listRect.top + ytTranscriptList.scrollTop;
      var containerH = ytTranscriptList.clientHeight;
      var target = rowTopInList - containerH / 2 + rowRect.height / 2;
      target = Math.max(0, Math.min(target, ytTranscriptList.scrollHeight - containerH));
      ytTranscriptList.scrollTo({
        top: target,
        behavior: "smooth",
      });
    }
  }

  function ytDeactivateCue() {
    var rows = ytTranscriptList
      ? ytTranscriptList.querySelectorAll(".yt-transcript-row")
      : [];
    for (var r = 0; r < rows.length; r++) {
      rows[r].classList.remove("active");
    }
    if (ytSubtitle) {
      if (typeof clear_newmultiterm_elements === "function")
        clear_newmultiterm_elements();
      ytSubtitle.innerHTML = "";
      ytMarqueeOverflow = 0;
    }
  }

  function ytUpdateMarquee(t) {
    if (ytCueIndex < 0 || ytMarqueeOverflow <= 0 || !ytSubtitle) return;
    var cue = CUES[ytCueIndex];
    if (!cue) return;
    var dur = Math.max(0.5, (cue.end || 0) - (cue.start || 0));
    var progress = Math.min(1, Math.max(0, (t - cue.start) / dur));
    ytSubtitle.scrollLeft = ytMarqueeOverflow * progress;
  }

  /* ------------------------------------------------------------------ */
  /* Transcript panel                                                    */
  /* ------------------------------------------------------------------ */

  function buildTranscript() {
    if (!ytTranscriptList) return;
    ytTranscriptList.innerHTML = "";
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
      row.addEventListener("click", function () {
        ytSeekToCue(i, true);
      });
      ytTranscriptList.appendChild(row);
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
    // for prev/next buttons keep the current play state so the user
    // can scrub through subtitles without forcing play.
    if (autoplay && ytPlayer.getPlayerState() !== PS.PLAYING) {
      ytPlayer.playVideo();
    }
  }

  // Prev/next subtitle jump used by the cue buttons.
  function ytJumpCue(delta) {
    if (!ytPlayerReady || !ytPlayer || !CUES.length) return;
    var n = CUES.length;
    var target = ytCueIndex < 0 ? 0 : ytCueIndex + delta;
    if (target < 0) target = 0;
    if (target >= n) target = n - 1;
    ytSeekToCue(target, false);
  }

  /* ------------------------------------------------------------------ */
  /* Controls                                                            */
  /* ------------------------------------------------------------------ */

  function ytTogglePlay() {
    if (!ytPlayer) return;
    // For the audio backend, allow playback even before ytPlayerReady is
    // set (loadedmetadata may not fire until play/load is called).  This
    // breaks the deadlock where the user clicks play but nothing happens
    // because the metadata hasn't loaded yet.
    if (!ytPlayerReady) {
      if (USE_AUDIO_BACKEND) {
        try {
          if (typeof ytPlayer.load === "function") ytPlayer.load();
          // For audio backend, load() triggers loadedmetadata which sets ytPlayerReady to true
          // Once ready, we can play immediately
          if (typeof ytPlayer.playVideo === "function") {
            ytPlayer.playVideo();
          }
        } catch (e) { /* ignore */ }
      } else {
        return;
      }
    } else {
      if (ytPlaying) ytPlayer.pauseVideo();
      else ytPlayer.playVideo();
    }
  }

  function ytUpdatePlayBtn() {
    if (!ytPlayBtn) return;
    ytPlayBtn.classList.toggle("playing", ytPlaying);
  }

  function ytSetRate(delta) {
    if (!ytPlayerReady || !ytPlayer) return;
    ytRate = Math.min(2, Math.max(0.25, +(ytRate + delta).toFixed(2)));
    try {
      ytPlayer.setPlaybackRate(ytRate);
    } catch (e) { /* ignore */ }
    ytRateInd.textContent = ytRate.toFixed(2).replace(/\.?0+$/, "");
    if (ytRateInd.textContent === "") ytRateInd.textContent = "1";
  }

  function ytResetRate() {
    ytRate = 1.0;
    if (ytPlayerReady && ytPlayer) {
      try { ytPlayer.setPlaybackRate(1.0); } catch (e) { /* ignore */ }
    }
    ytRateInd.textContent = "1";
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

  function bindControls() {
    if (ytPlayBtn) {
      ytPlayBtn.addEventListener("click", ytTogglePlay);
    }
    if (ytPrevCueBtn) {
      ytPrevCueBtn.addEventListener("click", function () { ytJumpCue(-1); });
    }
    if (ytNextCueBtn) {
      ytNextCueBtn.addEventListener("click", function () { ytJumpCue(1); });
    }
    if (ytTimeline) {
      ytTimeline.addEventListener("pointerdown", function () {
        ytDragging = true;
      });
      ytTimeline.addEventListener("input", function () {
        ytCurTimeEl.textContent = ytFmtTime(Number(ytTimeline.value));
      });
      ytTimeline.addEventListener("pointerup", function () {
        ytDragging = false;
        if (ytPlayerReady && ytPlayer)
          ytPlayer.seekTo(Number(ytTimeline.value), true);
      });
      ytTimeline.addEventListener("keyup", function () {
        if (ytPlayerReady && ytPlayer)
          ytPlayer.seekTo(Number(ytTimeline.value), true);
      });
    }
    var incBtn = document.getElementById("yt-rate-inc");
    var decBtn = document.getElementById("yt-rate-dec");
    if (incBtn) incBtn.addEventListener("click", function () { ytSetRate(0.25); });
    if (decBtn) decBtn.addEventListener("click", function () { ytSetRate(-0.25); });
    if (ytRateInd) ytRateInd.addEventListener("click", ytResetRate);
    if (ytLoopBtn) {
      ytLoopBtn.addEventListener("click", function () {
        ytLoop = !ytLoop;
        ytLoopBtn.classList.toggle("on", ytLoop);
        // "Press loop to keep looping": if the media is currently
        // paused (e.g. auto-paused at the end of a sentence), turning
        // the loop on resumes playback so the sentence starts looping
        // immediately.  Turning it off does not pause.
        if (ytLoop && ytPlayerReady && ytPlayer && !ytPlaying) {
          ytPlayer.playVideo();
        }
      });
    }
    if (ytAutoPauseBtn) {
      ytAutoPauseBtn.addEventListener("click", function () {
        ytAutoPause = !ytAutoPause;
        ytAutoPauseBtn.classList.toggle("on", ytAutoPause);
      });
    }
    if (ytFullscreenBtn) {
      ytFullscreenBtn.addEventListener("click", ytToggleFullscreen);
    }
    // Audio-only mode toggle + settings dropdown (online video and
    // YouTube books).
    if (ytAudioModeCb) {
      if (!USE_AUDIO_BACKEND) {
        ytAudioModeCb.checked =
          localStorage.getItem(AUDIO_MODE_STORAGE_KEY) === "1";
        ytAudioModeCb.addEventListener("change", ytApplyAudioMode);
        // Apply the persisted mode on load so a returning video book
        // opens as an audio-only screen immediately.
        ytApplyAudioMode();
      } else {
        // Not a video backend; keep the setting hidden/irrelevant.
        ytAudioModeCb.disabled = true;
      }
    }
    if (ytSettingsBtn && ytSettingsDropdown) {
      ytSettingsBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        ytSettingsDropdown.hidden = !ytSettingsDropdown.hidden;
      });
      // Close the settings menu when clicking anywhere outside it.
      document.addEventListener("click", function (e) {
        var wrap = document.querySelector(".yt-settings-wrap");
        if (wrap && !wrap.contains(e.target)) {
          ytSettingsDropdown.hidden = true;
        }
      });
    }
    // Keep the fullscreen button state in sync with the browser,
    // e.g. when the user presses Esc to exit fullscreen.
    var fsHandler = function () {
      var isFs = document.fullscreenElement || document.webkitFullscreenElement;
      if (ytFullscreenBtn) ytFullscreenBtn.classList.toggle("on", !!isFs);
    };
    document.addEventListener("fullscreenchange", fsHandler);
    document.addEventListener("webkitfullscreenchange", fsHandler);
    if (ytTranscriptBtn) {
      ytTranscriptBtn.addEventListener("click", function () {
        var isOpen = ytTranscript.style.display !== "none";
        if (isOpen) {
          ytTranscript.style.display = "none";
          ytTranscriptBtn.classList.remove("on");
        } else {
          ytTranscript.style.display = "block";
          ytTranscriptBtn.classList.add("on");
          // Center the current line when the panel is opened.
          // Strategy:
          //   1) Use double-rAF to wait for layout after display:none -> block.
          //   2) If ytCueIndex < 0 (no playback yet), infer from currentTime.
          //   3) Retry up to 3 times over 500ms to handle slow DOM rendering.
          //   4) Log a debug message if scrolling fails so we can investigate.
          var tryScroll = function (attempt) {
            console.log("[YouTube Player] tryScroll attempt", attempt, "ytCueIndex:", ytCueIndex, "ytPlaying:", ytPlaying);
            if (!ytTranscriptList) {
              console.warn("[YouTube Player] ytTranscriptList not found on attempt", attempt);
              return;
            }
            // Force a reflow to ensure display:none -> block has completed layout.
            // Without this, clientHeight can still be 0 even after double-rAF.
            var containerHeight = ytTranscriptList.clientHeight;
            console.log("[YouTube Player] containerHeight before reflow:", containerHeight);
            void ytTranscriptList.offsetHeight; // Trigger reflow
            containerHeight = ytTranscriptList.clientHeight;
            console.log("[YouTube Player] containerHeight after reflow:", containerHeight);

            var idx = ytCueIndex;
            // If no cue is active yet, infer from the best available position:
            //   1. If audio element exists, use its currentTime (could be set via START_POS seek)
            //   2. Otherwise fall back to START_POS (saved playback position from last visit)
            if (idx < 0) {
              var t = 0;
              if (ytPlayer && typeof ytPlayer.getCurrentTime === "function") {
                t = ytPlayer.getCurrentTime() || 0;
              }
              // If player reports t=0 (freshly loaded or haven't seeked yet),
              // try the saved START_POS since the user may be returning.
              if (t <= 0 && START_POS > 0) {
                t = START_POS;
              }
              if (t > 0) {
                console.log("[YouTube Player] ytCueIndex < 0, inferring idx from position t=", t, "CUES length:", CUES.length);
                for (var k = CUES.length - 1; k >= 0; k--) {
                  if ((CUES[k].start || 0) <= t) {
                    idx = k;
                    break;
                  }
                }
              }
              if (idx < 0) idx = 0;
              console.log("[YouTube Player] inferred idx:", idx);
            }
            if (idx < 0) idx = 0;
            var row = ytTranscriptList.querySelector("#yt-transcript-row-" + idx);
            console.log("[YouTube Player] row:", row, "idx:", idx, "CUES length:", CUES.length);
            if (row) {
                  // offsetTop is relative to offsetParent (BODY when
                  // the list has position:static), so use
                  // getBoundingClientRect to get the row's position
                  // within the scrollable list instead.
                  var rowRect = row.getBoundingClientRect();
                  var listRect = ytTranscriptList.getBoundingClientRect();
                  var rowTopInList = rowRect.top - listRect.top + ytTranscriptList.scrollTop;
                  var target = rowTopInList - containerHeight / 2 + rowRect.height / 2;
                  // Clamp target to valid scroll range
                  target = Math.max(0, Math.min(target, ytTranscriptList.scrollHeight - containerHeight));
                  ytTranscriptList.scrollTop = target;
            } else if (attempt < 3) {
              // Row not found yet — retry after 100ms
              console.log("[YouTube Player] row not found, retrying...");
              setTimeout(function () {
                tryScroll(attempt + 1);
              }, 100);
            } else {
              console.warn("[YouTube Player] Failed to find row #" + idx + " after 3 attempts, CUES length:", CUES.length);
            }
          };
          // Wait for layout to settle after display:none -> block,
          // then scroll the active row into view.
          requestAnimationFrame(function () {
            requestAnimationFrame(function () {
              tryScroll(0);
            });
          });
        }  // close else
      });  // close addEventListener
    }  // close if (ytTranscriptBtn)
  }  // close bindControls

  // Fullscreen the video wrapper (more reliable than the iframe,
  // which needs its own allowfullscreen attribute). The iframe fills
  // the wrapper, so the video scales up correctly.  Only used for
  // YouTube books -- the fullscreen button isn't rendered for MP3.
  function ytToggleFullscreen() {
    var el = ytVideoWrap || ytContainer;
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

  // Audio-only mode for online video and YouTube books (the gear
  // button menu).  Hides the video area so the reading screen matches
  // an MP3 book: controls + scrolling subtitle, no video picture.
  // The hidden <video> element / YouTube iframe keeps playing its
  // audio track.  Persists per browser.
  function ytApplyAudioMode() {
    if (!ytVideoWrap || !ytAudioModeCb) return;
    var on = !!ytAudioModeCb.checked;
    if (USE_AUDIO_BACKEND) on = false;
    ytContainer.classList.toggle("yt-audio-mode", on);
    localStorage.setItem(AUDIO_MODE_STORAGE_KEY, on ? "1" : "0");
    // Close the gear menu: the change event comes from a click inside
    // the dropdown, so the outside-click closer never fires.
    if (ytSettingsDropdown) ytSettingsDropdown.hidden = true;
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
  /* Subtitle word interactions (same as the reading text)              */
  /* ------------------------------------------------------------------ */

  // Apply status color classes to the scrolling-subtitle word spans.
  // The subtitle ALWAYS shows word colors, regardless of the
  // show_highlights setting, so the user can see at a glance which
  // words they know (status 1-5), which are unknown (status 0), and
  // which are ignored/well-known (status 98/99, no color).
  function ytApplySubtitleStatusColors() {
    if (!ytSubtitle) return;
    if (typeof apply_status_class !== "function") return;
    $(ytSubtitle).find("span.word").each(function () {
      apply_status_class($(this));
    });
  }

  function bindSubtitleInteractions() {
    if (!ytSubtitle) return;
    var t = $(ytSubtitle);
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
        content: function (setContent) {
          tooltip_textitem_hover_content($(this), setContent);
        },
      });
    }
  }

  /* ------------------------------------------------------------------ */
  /* Lazy-load subtitle word HTML                                       */
  /* ------------------------------------------------------------------ */

  function ytEscapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  // Fetch the tokenized word HTML for all cues.  This is deferred so
  // the expensive tokenization doesn't block the initial page
  // render.  While loading, the subtitle shows plain cue text.
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
  /* Keyboard + init                                                     */
  /* ------------------------------------------------------------------ */

  function bindKeys() {
    window.addEventListener("keydown", function (e) {
      if (e.code === "Space" && !ytPlayerReady && !USE_AUDIO_BACKEND) return;
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
  }

  function init() {
    if (!ytContainer) return;
    buildTranscript();
    bindControls();
    bindSubtitleInteractions();
    bindKeys();
    ytLoadSubtitleWords();

    // Create the player immediately for the media backends (the
    // <audio>/<video> element is already in the DOM); for YouTube, wait
    // for the IFrame API to be ready.
    if (USE_AUDIO_BACKEND || USE_VIDEO_BACKEND) {
      createYoutubePlayer();
    } else {
      var create = function () { createYoutubePlayer(); };
      if (window.YT_IS_READY) {
        create();
      } else if (window.YT_READY_CALLBACKS) {
        window.YT_READY_CALLBACKS.push(create);
      } else {
        // The api script hasn't defined the callback (e.g. blocked);
        // try once more after a delay.
        window.setTimeout(create, 1500);
      }
    }

    // Fallback message if the player never becomes ready.
    window.setTimeout(function () {
      if (ytLoading && !ytPlayerReady) {
        ytLoading.textContent = USE_AUDIO_BACKEND
          ? "Unable to load the audio player. The transcript below is still available."
          : (USE_VIDEO_BACKEND
            ? "Unable to load the video player. The transcript below is still available."
            : "Unable to load the YouTube player. The transcript below is still available.");
        ytLoading.style.display = "block";
      }
    }, 15000);

    // After a term status update, lute.js reloads #thetext and the server
    // invalidates its subtitle word-HTML cache.  The subtitle words are
    // tokenized from the media cues, which is a DIFFERENT text from the
    // page content (#thetext), so their data-wid values don't exist in
    // #thetext and we can't copy status classes from there.  Instead,
    // re-fetch the subtitle word HTML and re-render the current cue so
    // the new status classes appear.  (Re-rendering is safe now: the
    // font-resize observer in text-options.js no longer stamps subtitle
    // words with the reading-pane font size.)
    window.addEventListener("lute:status-updated", function () {
      if (!ytSubtitle) return;
      ytLoadSubtitleWords(true);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
