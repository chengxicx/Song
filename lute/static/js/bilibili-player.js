/* Media player for lute reading pages (book_type == "bilibili").

   This is the Bilibili counterpart of youtube-player.js.  It shares the
   exact same UI (controls, scrolling subtitle, transcript panel) and the
   same element ids (yt-player-container, yt-play-btn, ...) as the YouTube /
   MP3 player, but drives an embedded Bilibili iframe
   (player.bilibili.com/player.html) over the postMessage API instead of
   the YouTube IFrame API.

   Provides:
   - play/pause, seek timeline, playback rate controls
   - single-sentence loop and auto-pause-at-end-of-sentence
   - a single-line scrolling subtitle synced to the media, whose words
     reuse the reading-page tokenization and click-to-lookup behavior
   - a Transcript panel with bidirectional control:
       media -> transcript: highlight + smooth-center the current line
       transcript -> media: clicking a line/timestamp seeks to its start

   Data (bilibiliUrl, cues, words, ...) is injected by
   templates/read/bilibili_player.html via window.LUTE_YT_DATA.
*/

(function () {
  "use strict";

  // Bilibili iframe player states (mirrors the message types we handle).
  var PS = {
    UNSTARTED: -1,
    ENDED: 0,
    PLAYING: 1,
    PAUSED: 2,
    BUFFERING: 3,
    CUED: 5,
  };

  var YT_DATA = window.LUTE_YT_DATA || {};
  var CUES = Array.isArray(YT_DATA.cues) ? YT_DATA.cues : [];
  var WORDS = Array.isArray(YT_DATA.words) ? YT_DATA.words : [];
  var BOOK_ID = YT_DATA.bookId;
  var START_POS = parseFloat(YT_DATA.startPos) || 0;
  var BILI_URL = YT_DATA.bilibiliUrl || "";

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
  /* Backend: Bilibili iframe over postMessage                           */
  /* ------------------------------------------------------------------ */

  // Wrap the Bilibili iframe so it looks like a YT.Player instance,
  // letting the shared player logic below drive it unchanged.  The
  // Bilibili embed player (player.bilibili.com/player.html) is remote
  // and cross-origin, so we control it exclusively via postMessage and
  // track its state from the messages it posts back to us.
  function LuteBilibiliPlayer(iframeEl, handlers) {
    var ready = false;
    var currentT = 0;
    var duration = 0;
    var playing = false;
    var rate = 1;
    var win = null;
    var pollTimer = null;

    if (iframeEl && iframeEl.contentWindow) {
      win = iframeEl.contentWindow;
    }

    function post(msg) {
      // Use the live window so a reload of the iframe's document doesn't
      // leave us talking to a stale window.
      var w = iframeEl && iframeEl.contentWindow;
      if (!w) return;
      try {
        w.postMessage(JSON.stringify(msg), "*");
      } catch (e) { /* ignore */ }
    }

    function fireReady() {
      if (ready) return;
      ready = true;
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      if (typeof handlers.onReady === "function") handlers.onReady();
    }

    function fireStateChange(s) {
      if (typeof handlers.onStateChange === "function") {
        handlers.onStateChange({ data: s });
      }
    }

    // The Bilibili embed player posts its events as JSON.  It signals
    // that it is initialized and ready to accept commands with a
    // "playerInitDone" message (some builds also post "ready").  We treat
    // either — or any playback/duration/time message, which can only
    // arrive once the player is live — as the ready trigger, so the
    // loading overlay is cleared as soon as the player is usable.
    function looksReady(d) {
      return d.type === "playerInitDone" ||
        d.type === "ready" ||
        d.type === "playing" ||
        d.type === "play" ||
        d.type === "duration" ||
        d.type === "timeupdate";
    }

    function numFrom(d, keys) {
      for (var i = 0; i < keys.length; i++) {
        var v = d[keys[i]];
        if (typeof v === "number") return v;
      }
      if (d.data && typeof d.data === "object") {
        for (var j = 0; j < keys.length; j++) {
          var v2 = d.data[keys[j]];
          if (typeof v2 === "number") return v2;
        }
      }
      return null;
    }

    function handleMessage(ev) {
      if (ev.source !== win && ev.source !== (iframeEl && iframeEl.contentWindow)) {
        return;
      }
      var d = ev.data;
      if (typeof d === "string") {
        try { d = JSON.parse(d); } catch (e) { return; }
      }
      if (!d || typeof d.type !== "string") return;

      if (looksReady(d)) fireReady();

      switch (d.type) {
        case "playing":
        case "play":
          playing = true;
          fireStateChange(PS.PLAYING);
          break;
        case "pause":
        case "paused":
          playing = false;
          fireStateChange(PS.PAUSED);
          break;
        case "ended":
          playing = false;
          fireStateChange(PS.ENDED);
          break;
        case "timeupdate":
        case "time":
          var t = numFrom(d, ["value", "currentTime", "time"]);
          if (t !== null) currentT = t;
          break;
        case "duration":
          var dur = numFrom(d, ["value", "duration"]);
          if (dur !== null) duration = dur;
          break;
        case "playbackRateChange":
          var r = numFrom(d, ["value"]);
          if (r !== null) rate = r;
          break;
        case "error":
          if (typeof handlers.onError === "function") handlers.onError(d.value);
          break;
      }
    }

    window.addEventListener("message", handleMessage);

    // Poll until the iframe signals ready (the player posts a "ready"
    // message once the video is loaded).  Also nudge the remote player
    // to report its current time / duration so the timeline fills in.
    if (win) {
      pollTimer = setInterval(function () {
        if (ready) {
          if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
          return;
        }
        post({ type: "getCurrentTime" });
        post({ type: "getDuration" });
      }, 250);
      // Give up after ~15s so the loading overlay falls back.
      window.setTimeout(function () {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      }, 15000);
    }

    return {
      _maybeFireReady: function () {
        // Nothing to do: readiness comes from the iframe postMessage
        // events.  Kept for interface parity.
      },
      _onIframeLoad: fireReady,
      load: function () { post({ type: "reload" }); },
      playVideo: function () {
        post({ type: "play" });
      },
      pauseVideo: function () {
        post({ type: "pause" });
      },
      getCurrentTime: function () {
        post({ type: "getCurrentTime" });
        return currentT;
      },
      getDuration: function () {
        post({ type: "getDuration" });
        return duration;
      },
      seekTo: function (t) {
        currentT = t;
        post({ type: "seek", value: t });
      },
      getPlaybackRate: function () { return rate; },
      setPlaybackRate: function (r) {
        rate = r;
        post({ type: "setPlaybackRate", value: r });
      },
      getPlayerState: function () {
        return playing ? PS.PLAYING : PS.PAUSED;
      },
    };
  }

  /* ------------------------------------------------------------------ */
  /* Player creation                                                    */
  /* ------------------------------------------------------------------ */

  function createYoutubePlayer() {
    if (ytPlayer) return;
    if (!BILI_URL) {
      if (ytLoading) {
        ytLoading.textContent =
          "Unable to load the Bilibili player: no video URL.";
      }
      return;
    }
    var iframeEl = document.getElementById("bili-player");
    if (!iframeEl) return;
    ytPlayer = LuteBilibiliPlayer(iframeEl, {
      onReady: ytOnReady,
      onStateChange: ytOnStateChange,
      onError: ytOnError,
    });
    // The Bilibili embed posts a "playerInitDone" message once it is
    // ready, but fall back to the iframe's DOM load event so the loading
    // overlay is cleared even if postMessage events are missed.  The src
    // is set here (not in the HTML) so the load listener is attached
    // before the iframe starts loading; otherwise the load event would
    // fire before we attach the listener and the player would never be
    // marked ready.
    iframeEl.addEventListener("load", function () {
      if (ytPlayer && typeof ytPlayer._onIframeLoad === "function") {
        ytPlayer._onIframeLoad();
      }
    });
    iframeEl.src = BILI_URL;
    ytPlayer._maybeFireReady();
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
      // The remote player may reset the rate on load; restore ours.
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
        "Unable to play this video. The transcript below is still available.";
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

    // Single-sentence loop / auto-pause (see youtube-player.js for the
    // ordering rationale: this must be checked against the cue the user
    // is currently watching, before ytCueIndex is advanced).
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

  function _ytMaybeSavePosition(t) {
    if (t - ytLastSavedT >= 2) {
      ytLastSavedT = t;
      ytSavePosition(t);
    }
  }

  function ytActivateCue(idx) {
    if (ytSubtitle) {
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
      window.requestAnimationFrame(function () {
        var overflow = ytSubtitle.scrollWidth - ytSubtitle.clientWidth;
        ytMarqueeOverflow = ytIsRtl ? 0 : Math.max(0, overflow);
      });
      ytApplySubtitleStatusColors();
    }

    var rows = ytTranscriptList
      ? ytTranscriptList.querySelectorAll(".yt-transcript-row")
      : [];
    for (var r = 0; r < rows.length; r++) {
      rows[r].classList.toggle("active", r === idx);
    }
    var row = rows[idx];
    if (row && ytTranscript && ytTranscript.style.display !== "none") {
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

  function ytSeekToCue(i, autoplay) {
    if (!ytPlayerReady || !ytPlayer || !CUES[i]) return;
    var cue = CUES[i];
    ytPlayer.seekTo(cue.start, true);
    ytCueIndex = i;
    ytActivateCue(i);
    if (autoplay && ytPlayer.getPlayerState() !== PS.PLAYING) {
      ytPlayer.playVideo();
    }
  }

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
    if (!ytPlayerReady) return;
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
          requestAnimationFrame(function () {
            requestAnimationFrame(function () {
              var idx = ytCueIndex;
              if (idx < 0) idx = 0;
              var row = ytTranscriptList
                ? ytTranscriptList.querySelector("#yt-transcript-row-" + idx)
                : null;
              if (row && ytTranscriptList) {
                var rowRect = row.getBoundingClientRect();
                var listRect = ytTranscriptList.getBoundingClientRect();
                var rowTopInList =
                  rowRect.top - listRect.top + ytTranscriptList.scrollTop;
                var target =
                  rowTopInList -
                  ytTranscriptList.clientHeight / 2 +
                  rowRect.height / 2;
                target = Math.max(
                  0,
                  Math.min(target, ytTranscriptList.scrollHeight - ytTranscriptList.clientHeight)
                );
                ytTranscriptList.scrollTop = target;
              }
            });
          });
        }
      });
    }
  }

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
    if (typeof _isUserUsingMobile === "function" && _isUserUsingMobile()) {
      t.on("touchstart", ".word", touch_started);
      t.on("touchend", ".word", touch_ended);
    } else {
      t.on("mousedown", ".word", handle_select_started);
      t.on("mouseover", ".word", handle_select_over);
      t.on("mouseup", ".word", handle_select_ended);
      t.on("mouseover", ".word", hover_over);
      t.on("mouseout", ".word", hover_out);
    }
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

    createYoutubePlayer();

    // Fallback message if the player never becomes ready.
    window.setTimeout(function () {
      if (ytLoading && !ytPlayerReady) {
        ytLoading.textContent =
          "Unable to load the Bilibili player. The transcript below is still available.";
        ytLoading.style.display = "block";
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