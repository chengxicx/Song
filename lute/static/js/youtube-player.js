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
  var ytPlayBtn = document.getElementById("yt-play-btn");
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
    if (idx !== ytCueIndex) {
      ytCueIndex = idx;
      if (idx >= 0) ytActivateCue(idx);
      else ytDeactivateCue();
    }

    // Single-sentence loop / auto-pause.
    if (ytPlaying && ytCueIndex >= 0) {
      var cue = CUES[ytCueIndex];
      if (ytAutoPause && t >= cue.end) {
        ytPlayer.pauseVideo();
        ytPlayer.seekTo(cue.start, true);
      } else if (ytLoop && t >= cue.end) {
        ytPlayer.seekTo(cue.start, true);
      }
    }

    ytUpdateMarquee(t);

    // Save position roughly every 2 seconds.
    if (t - ytLastSavedT >= 2) {
      ytLastSavedT = t;
      ytSavePosition(t);
    }
  }

  function ytActivateCue(idx) {
    // Single-line scrolling subtitle, reusing the reading-page word spans.
    if (ytSubtitle) {
      ytSubtitle.innerHTML = WORDS[idx] || "";
      ytSubtitle.scrollLeft = 0;
      ytIsRtl = ytSubtitle.getAttribute("dir") === "rtl";
      var overflow = ytSubtitle.scrollWidth - ytSubtitle.clientWidth;
      ytMarqueeOverflow = ytIsRtl ? 0 : Math.max(0, overflow);
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
        ytSeekToCue(i);
      });
      ytTranscriptList.appendChild(row);
    });
  }

  // Transcript -> video: jump the playhead to the cue start.
  function ytSeekToCue(i) {
    if (!ytPlayerReady || !ytPlayer || !CUES[i]) return;
    var cue = CUES[i];
    ytPlayer.seekTo(cue.start, true);
    ytCueIndex = i;
    ytActivateCue(i);
    if (ytPlayer.getPlayerState() !== window.YT.PlayerState.PLAYING) {
      ytPlayer.playVideo();
    }
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
      });
    }
    if (ytAutoPauseBtn) {
      ytAutoPauseBtn.addEventListener("click", function () {
        ytAutoPause = !ytAutoPause;
        ytAutoPauseBtn.classList.toggle("on", ytAutoPause);
      });
    }
    if (ytFullscreenBtn) {
      ytFullscreenBtn.addEventListener("click", function () {
        if (!ytPlayerReady || !ytPlayer) return;
        var iframe = ytPlayer.getIframe();
        if (iframe.requestFullscreen) iframe.requestFullscreen();
        else if (iframe.webkitRequestFullscreen) iframe.webkitRequestFullscreen();
      });
    }
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
              ".yt-transcript-row-" + ytCueIndex
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

  /* ------------------------------------------------------------------ */
  /* Subtitle word interactions (same as the reading text)              */
  /* ------------------------------------------------------------------ */

  function bindSubtitleInteractions() {
    if (!ytSubtitle) return;
    var t = $(ytSubtitle);
    if (typeof word_clicked !== "function") return;

    t.on("click", ".word", function (e) {
      e.stopPropagation();
      word_clicked($(this), e);
    });

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
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
