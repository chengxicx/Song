/* YouTube video player for lute reading pages (book_type == "youtube").

   Provides:
   - play/pause, seek timeline, playback rate controls
   - single-sentence loop and auto-pause-at-end-of-sentence
   - a single-line scrolling subtitle synced to the video, whose words
     reuse the reading-page tokenization and click-to-lookup behavior
   - a Transcript panel with bidirectional control:
       video -> transcript: highlight + smooth-center the current line
       transcript -> video: clicking a line/timestamp seeks to its start

   Data (videoId, cues, words, ...) is injected by
   templates/read/youtube_player.html via window.LUTE_YT_DATA.
*/

(function () {
  "use strict";

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
  /* Player creation                                                    */
  /* ------------------------------------------------------------------ */

  function createYoutubePlayer() {
    if (!window.YT || !window.YT.Player || ytPlayer) return;
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
    ytPlaying = event.data === window.YT.PlayerState.PLAYING;
    ytUpdatePlayBtn();
    if (event.data === window.YT.PlayerState.PLAYING) {
      // The API resets the rate on (re)load; restore our setting.
      try {
        if (Math.abs(ytPlayer.getPlaybackRate() - ytRate) > 0.01)
          ytPlayer.setPlaybackRate(ytRate);
      } catch (e) { /* ignore */ }
    }
    if (event.data === window.YT.PlayerState.PAUSED) {
      ytSavePosition();
    }
  }

  function ytOnError() {
    if (ytLoading) {
      ytLoading.textContent =
        "Unable to play this video. The transcript below is still available.";
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

    // Video -> transcript/subtitle: find the active cue.
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
      var html = WORDS[idx];
      if (!html) {
        var cue = CUES[idx];
        html = cue ? ytEscapeHtml(cue.text || "") : "";
      }
      ytSubtitle.innerHTML = html;
      ytSubtitle.scrollLeft = 0;
      ytIsRtl = ytSubtitle.getAttribute("dir") === "rtl";
      var overflow = ytSubtitle.scrollWidth - ytSubtitle.clientWidth;
      ytMarqueeOverflow = ytIsRtl ? 0 : Math.max(0, overflow);
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
      var target =
        row.offsetTop - ytTranscriptList.clientHeight / 2 +
        row.offsetHeight / 2;
      ytTranscriptList.scrollTo({
        top: Math.max(0, target),
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

  // Transcript -> video: jump the playhead to the cue start.
  function ytSeekToCue(i, autoplay) {
    if (!ytPlayerReady || !ytPlayer || !CUES[i]) return;
    var cue = CUES[i];
    ytPlayer.seekTo(cue.start, true);
    ytCueIndex = i;
    ytActivateCue(i);
    // When jumping from the transcript list, resume playback;
    // for prev/next buttons keep the current play state so the user
    // can scrub through subtitles without forcing play.
    if (autoplay && ytPlayer.getPlayerState() !== window.YT.PlayerState.PLAYING) {
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
    if (!ytPlayerReady || !ytPlayer) return;
    if (ytPlaying) ytPlayer.pauseVideo();
    else ytPlayer.playVideo();
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
        // "Press loop to keep looping": if the video is currently
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
          if (ytCueIndex >= 0 && ytTranscriptList) {
            var row = ytTranscriptList.querySelector(
              "#yt-transcript-row-" + ytCueIndex
            );
            if (row) {
              var target =
                row.offsetTop - ytTranscriptList.clientHeight / 2 +
                row.offsetHeight / 2;
              ytTranscriptList.scrollTo({ top: Math.max(0, target) });
            }
          }
        }
      });
    }
  }

  // Fullscreen the video wrapper (more reliable than the iframe,
  // which needs its own allowfullscreen attribute). The iframe fills
  // the wrapper, so the video scales up correctly.
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

    t.on("click", ".word", function (e) {
      e.stopPropagation();
      word_clicked($(this), e);
    });

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
  function ytLoadSubtitleWords() {
    if (!BOOK_ID) return;
    $.ajax({
      url: "/read/youtube_subtitle_words/" + BOOK_ID,
      method: "GET",
      dataType: "json",
    }).done(function (data) {
      if (Array.isArray(data) && data.length) {
        WORDS.length = 0;
        WORDS.push.apply(WORDS, data);
        // Re-activate the current cue so the subtitle updates from
        // plain text to clickable word spans.
        if (ytCueIndex >= 0) ytActivateCue(ytCueIndex);
      }
    });
  }

  /* ------------------------------------------------------------------ */
  /* Keyboard + init                                                     */
  /* ------------------------------------------------------------------ */

  function bindKeys() {
    window.addEventListener("keydown", function (e) {
      if (e.code === "Space" && !ytPlayerReady) return;
      if (e.code === "Space" &&
          e.target &&
          (e.target.tagName === "INPUT" ||
           e.target.tagName === "TEXTAREA" ||
           e.target.isContentEditable)) {
        return;
      }
      if (e.code === "Space") {
        e.preventDefault();
        if (ytPlayerReady) ytTogglePlay();
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

    // Create the player when the IFrame API is ready.
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

    // Fallback message if the player never becomes ready.
    window.setTimeout(function () {
      if (ytLoading && !ytPlayerReady) {
        ytLoading.textContent =
          "Unable to load the YouTube player. The transcript below is still available.";
      }
    }, 15000);

    // After a term status update, lute.js reloads #thetext.  The
    // server-side subtitle cache is invalidated at the same time, so
    // re-fetch the subtitle words to pick up fresh data-status-class
    // values and re-apply colors.
    window.addEventListener("lute:status-updated", function () {
      ytLoadSubtitleWords();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
