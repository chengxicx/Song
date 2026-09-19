/* Media player for lute reading pages (book_type == "bilibili").

   This is the Bilibili counterpart of youtube-player.js.  It shares the
   exact same UI (controls, scrolling subtitle, transcript panel) and the
   same element ids (yt-player-container, yt-play-btn, ...) as the YouTube /
   MP3 player, but drives an embedded Bilibili iframe
   (player.bilibili.com/player.html) over the postMessage API instead of
   the YouTube IFrame API.

   Provides:
   - play/pause, seek timeline, playback rate controls
   - a video-quality menu (settings gear), defaulting to the cheapest
     rendition Bilibili offers for the video: the stream is relayed
     through a narrow egress, so the low end plays unless the reader
     deliberately chooses otherwise.  Adaptive switching is off for the
     same reason -- it would climb back up on its own.
   - single-sentence loop and auto-pause-at-end-of-sentence
   - a single-line scrolling subtitle synced to the media, whose words
     reuse the reading-page tokenization and click-to-lookup behavior
   - a Transcript panel with bidirectional control:
       media -> transcript: highlight + smooth-center the current line
       transcript -> media: clicking a line/timestamp seeks to its start

   Data (bilibiliUrl, cues, words, ...) is injected by
   templates/read/bilibili_player.html via window.LUTE_YT_DATA.

   Backend note: the video is NOT an embedded iframe.  It is an HTML5
   <video> driven by dash.js from a DASH manifest our own server builds
   (lute/read/bilibili_stream.py), so playback stays under our control
   and subtitle sync works.  An earlier version of this comment claimed
   the iframe player was driven over postMessage; that is not possible --
   the modern player exposes no postMessage API, which is exactly why the
   DASH relay exists.  The iframe is only used as a last-resort fallback
   (see ytUseEmbedPlayer) and loses subtitle sync when it is.
*/

(function () {
  "use strict";

  // Player states, named as in the YouTube IFrame API because the shared
  // player logic below is written against that set.
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
  var MPD_URL = YT_DATA.mpdUrl || "";

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
  // Debounce timer for the incremental subtitle refresh (see the
  // lute:status-updated listener at the bottom of this file).
  var ytStatusRefreshTimer = null;

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
  var ytEmbedNotice = document.getElementById("bili-embed-notice");
  var ytSettingsBtn = document.getElementById("yt-settings-btn");
  var ytSettingsDropdown = document.getElementById("yt-settings-dropdown");
  var ytAudioModeCb = document.getElementById("yt-audio-mode-cb");
  var AUDIO_MODE_STORAGE_KEY = "ytAudioMode";
  var ytQualityRow = document.getElementById("yt-quality-row");
  var ytQualitySelect = document.getElementById("yt-quality-select");
  // Remembered video quality, stored as the rendition height (e.g. 480)
  // rather than as a rendition index: an index only means something
  // within one manifest's rendition list, while a height stays
  // meaningful across videos.  Absent means "the lowest one", which is
  // the intended default here -- the stream is relayed through a narrow
  // egress (see lute/utils/outbound_proxy.py), so the cheap end plays
  // unless the reader deliberately asks for more.
  var QUALITY_STORAGE_KEY = "biliVideoQuality";


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
  /* Backend: HTML5 <video> driven by dash.js (direct DASH stream)      */
  /* ------------------------------------------------------------------ */

  // Wrap an HTML5 <video> element so it looks like a YT.Player instance,
  // letting the shared player logic below drive it unchanged.  Instead of
  // embedding Bilibili's official iframe player (which refuses to load on
  // non-whitelisted domains), we play the video's raw DASH stream via
  // dash.js.  The stream is served through our own proxy endpoints
  // (see lute/read/bilibili_stream.py), so the browser never talks to
  // Bilibili directly and the domain-whitelist restriction is bypassed.
  function LuteBilibiliPlayer(videoEl, handlers) {
    var ready = false;
    var playing = false;
    var player = null; // dashjs.MediaPlayer
    var pendingSeek = START_POS;

    function fireReady() {
      if (ready) return;
      ready = true;
      if (typeof handlers.onReady === "function") handlers.onReady();
    }

    function fireStateChange(s) {
      if (typeof handlers.onStateChange === "function") {
        handlers.onStateChange({ data: s });
      }
    }

    function onPlay() {
      playing = true;
      fireStateChange(PS.PLAYING);
    }
    function onPause() {
      playing = false;
      fireStateChange(PS.PAUSED);
    }
    function onEnded() {
      playing = false;
      fireStateChange(PS.ENDED);
    }
    function onBuffering() {
      fireStateChange(PS.BUFFERING);
    }

    function bindVideo() {
      videoEl.addEventListener("play", onPlay);
      videoEl.addEventListener("pause", onPause);
      videoEl.addEventListener("ended", onEnded);
      videoEl.addEventListener("playing", onPlay);
      videoEl.addEventListener("waiting", onBuffering);
      videoEl.addEventListener("stalled", onBuffering);
      videoEl.addEventListener("loadedmetadata", function () {
        if (pendingSeek > 0 && isFinite(videoEl.duration)) {
          try { videoEl.currentTime = pendingSeek; } catch (e) { /* ignore */ }
        }
      });
    }

    function init() {
      bindVideo();
      if (typeof dashjs === "undefined" || typeof dashjs.MediaPlayer === "undefined") {
        if (typeof handlers.onError === "function") {
          handlers.onError("dashjs failed to load");
        }
        return;
      }
      try {
        player = dashjs.MediaPlayer().create();
        // Steer playback at the cheap end.  ABR measures throughput and
        // climbs as far as it can, which is exactly wrong here: the
        // stream is relayed through an SSH tunnel, so a higher rendition
        // costs the far end's uplink and buys nothing (these are
        // subtitle-reading videos).  "initialBitrate: video 1" (kbps)
        // makes the very first pick the cheapest rendition, before we
        // know the list of them; switching it back off avoids any later
        // climb.  Audio keeps ABR -- it is one small track either way.
        // In dash.js 4.7 this is settings-only: setAutoSwitchQualityFor
        // no longer exists.
        try {
          player.updateSettings({
            streaming: {
              abr: {
                autoSwitchBitrate: { video: false },
                initialBitrate: { video: 1 },
              },
            },
          });
        } catch (e) { /* an older/newer dash.js: the pick below still holds */ }
        // The rendition list only exists once the stream is set up, and
        // it is not always there on the first event, so try again on each
        // of them and give up after a few attempts.
        player.on("manifestLoaded", ytScheduleQualityControls);
        player.on("streamInitialized", ytScheduleQualityControls);
        player.on("error", function () {
          if (typeof handlers.onError === "function") handlers.onError();
        });
        player.initialize(videoEl, MPD_URL, false);
        // Mark the player ready as soon as dash.js has attached itself;
        // duration is filled in by the poll loop once the manifest loads.
        fireReady();
      } catch (e) {
        if (typeof handlers.onError === "function") handlers.onError(e);
      }
    }

    return {
      _maybeFireReady: fireReady,
      _onIframeLoad: fireReady,
      load: function () {
        if (player) player.initialize(videoEl, MPD_URL, false);
      },
      playVideo: function () {
        var p = videoEl.play();
        if (p && typeof p.catch === "function") p.catch(function () { /* ignore */ });
      },
      pauseVideo: function () {
        videoEl.pause();
      },
      getCurrentTime: function () {
        return videoEl.currentTime || 0;
      },
      getDuration: function () {
        return videoEl.duration || 0;
      },
      seekTo: function (t) {
        videoEl.currentTime = t;
      },
      getPlaybackRate: function () { return videoEl.playbackRate; },
      setPlaybackRate: function (r) { videoEl.playbackRate = r; },
      getPlayerState: function () {
        return playing ? PS.PLAYING : PS.PAUSED;
      },
      getLoadedFraction: function () {
        // Fraction of the media buffered ahead, for the timeline's
        // buffered band.
        var d = videoEl.duration;
        if (!isFinite(d) || d <= 0) return 0;
        var b = videoEl.buffered;
        var end = 0;
        for (var i = 0; i < b.length; i++) {
          if (b.end(i) > end) end = b.end(i);
        }
        return Math.min(1, end / d);
      },
      _init: init,
      // Video renditions as dash.js sees them.  Each entry carries
      // qualityIndex (what setQualityFor expects), bitrate and height.
      // Empty until the manifest has been parsed.
      getVideoBitrates: function () {
        try {
          return (player && player.getBitrateInfoListFor("video")) || [];
        } catch (e) {
          return [];
        }
      },
      setVideoQuality: function (qualityIndex) {
        try {
          player.setQualityFor("video", qualityIndex, true);
        } catch (e) { /* ignore */ }
      },
      getVideoQuality: function () {
        try {
          return player ? player.getQualityFor("video") : null;
        } catch (e) {
          return null;
        }
      },
    };
  }

  /* ------------------------------------------------------------------ */
  /* Player creation                                                    */
  /* ------------------------------------------------------------------ */

  function createYoutubePlayer() {
    if (ytPlayer) return;
    var videoEl = document.getElementById("bili-player");
    if (!videoEl) return;
    if (!MPD_URL) {
      // No stream endpoint (unparseable URL, or the server could not
      // build the manifest): go straight to the embed player.
      ytUseEmbedPlayer();
      if (!ytEmbedMode && ytLoading) {
        ytLoading.textContent =
          "Unable to load the Bilibili player: no video stream available.";
      }
      return;
    }
    ytPlayer = LuteBilibiliPlayer(videoEl, {
      onReady: ytOnReady,
      onStateChange: ytOnStateChange,
      onError: ytOnError,
    });
    ytPlayer._init();
  }

  function ytOnReady() {
    ytPlayerReady = true;
    if (ytLoading && !ytEmbedMode) ytLoading.style.display = "none";
    if (START_POS > 0) {
      try { ytPlayer.seekTo(START_POS, true); } catch (e) { /* ignore */ }
    }
    ytDuration = ytPlayer.getDuration() || 0;
    ytDurationEl.textContent = ytFmtTime(ytDuration);
    ytTimeline.max = ytDuration || 1000;
    ytUpdatePlayBtn();
    window.setInterval(ytPoll, 250);
  }

  // Buffering feedback: while the video buffers (weak network, seek
  // past the buffered range), show the loading overlay so the frozen
  // timeline isn't mistaken for a crash.
  var ytBufferingVisible = false;
  function ytShowBuffering(show) {
    if (!ytLoading || !ytPlayerReady || ytEmbedMode) return;
    if (show) {
      if (!ytBufferingVisible) {
        ytBufferingVisible = true;
        ytLoading.textContent = "Buffering...";
        ytLoading.style.display = "block";
      }
    } else if (ytBufferingVisible) {
      ytBufferingVisible = false;
      ytLoading.style.display = "none";
    }
  }

  function ytOnStateChange(event) {
    ytPlaying = event.data === PS.PLAYING;
    ytUpdatePlayBtn();
    ytShowBuffering(event.data === PS.BUFFERING);
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

  /* ------------------------------------------------------------------ */
  /* Embed fallback                                                      */
  /* ------------------------------------------------------------------ */

  // When our own stream relay cannot be used, fall back to Bilibili's
  // official embed player.  The manifest endpoint fails whenever the
  // server cannot reach Bilibili at all -- Bilibili's API answers HTTP
  // 412 ("request was banned") to datacenter and overseas addresses and
  // no header or cookie changes that, so on such a host the relay is
  // permanently unavailable unless an accepted egress is configured
  // (LUTE_BILIBILI_PROXY, see lute/utils/outbound_proxy.py).
  //
  // The embed player still works there because the *browser* loads it,
  // from an IP Bilibili accepts.  The trade-off is subtitle sync: the
  // modern player exposes no postMessage API, so the parent page can
  // neither read currentTime nor seek it (verified against
  // player.bilibili.com and its core bundle -- the only "message"
  // listeners there belong to jQuery's setImmediate polyfill and to
  // EME/DRM).  The transcript therefore stays readable but no longer
  // follows playback, and the transport controls become inert.
  var ytEmbedMode = false;

  function ytDisableTransport(off) {
    var ids = [
      "yt-play-btn", "yt-prev-cue-btn", "yt-next-cue-btn",
      "yt-rate-dec", "yt-rate-inc", "yt-loop-btn", "yt-autopause-btn",
    ];
    for (var i = 0; i < ids.length; i++) {
      var el = document.getElementById(ids[i]);
      if (el) el.disabled = !!off;
    }
    if (ytTimeline) ytTimeline.disabled = !!off;
  }

  function ytUseEmbedPlayer() {
    if (ytEmbedMode || !BILI_URL || !ytVideoWrap) return;
    ytEmbedMode = true;

    var frame = document.createElement("iframe");
    frame.className = "bili-player-frame bili-embed-frame";
    frame.src = BILI_URL;
    frame.setAttribute("allowfullscreen", "true");
    frame.setAttribute("scrolling", "no");
    frame.setAttribute("frameborder", "0");

    var videoEl = document.getElementById("bili-player");
    if (videoEl) {
      videoEl.style.display = "none";
      ytVideoWrap.insertBefore(frame, videoEl);
    } else {
      ytVideoWrap.appendChild(frame);
    }

    if (ytContainer) ytContainer.classList.add("bili-embed-active");
    ytDisableTransport(true);
    // Quality is Bilibili's own business in this mode; our rendition menu
    // would be a lie.
    if (ytQualityRow) ytQualityRow.hidden = true;

    // Bilibili's player owns its own transport, so this state must be
    // reported OUTSIDE the video: the loading overlay is inset:0 and
    // stacks above the iframe, which would swallow every click and make
    // the embed look frozen.  Hide the overlay and use the sibling
    // notice instead.
    if (ytLoading) ytLoading.style.display = "none";
    if (ytEmbedNotice) {
      ytEmbedNotice.textContent =
        "Song cannot relay this video, so it is playing in Bilibili's embed " +
        "player -- use that player's own controls. Subtitle sync " +
        "(auto-scroll, loop, auto-pause) is not available in this mode; " +
        "the transcript below still works.";
      ytEmbedNotice.hidden = false;
    }
  }

  function ytOnError() {
    ytUseEmbedPlayer();
    if (ytLoading && !ytEmbedMode) {
      ytLoading.textContent =
        "Unable to play this video. The transcript below is still available.";
      ytLoading.style.display = "block";
    }
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
      ytTimeline.max = dur;
      ytDurationEl.textContent = ytFmtTime(dur);
    }

    if (!ytDragging) {
      ytTimeline.value = t;
      var bufferedPct = 0;
      try { bufferedPct = Math.round((ytPlayer.getLoadedFraction() || 0) * 100); }
      catch (e) { /* ignore */ }
      ytTimeline.style.backgroundSize =
        (dur > 0 ? (t / dur) * 100 : 0) + "% 100%, " + bufferedPct + "% 100%";
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
      // An open term popup is anchored to one of the word spans the
      // innerHTML write below detaches; close it first or its card stays
      // on screen as one more stray block.
      if (typeof _hide_element_message_tooltips === "function")
        _hide_element_message_tooltips();
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
      // The word spans are about to go: close the popup of whichever of
      // them has one open, or it would float on over the empty subtitle.
      if (typeof _hide_element_message_tooltips === "function")
        _hide_element_message_tooltips();
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
      // Clicking a line jumps to it and plays -- but not when the click
      // was the end of a selection, nor the first click of a
      // double-click, which selects a word to copy.  bind_line_click()
      // handles both (see lute-cursor.js).
      bind_line_click(row, function () {
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
    // Audio-only mode toggle + settings dropdown (same as the YouTube /
    // online-video players).
    if (ytAudioModeCb) {
      ytAudioModeCb.checked =
        localStorage.getItem(AUDIO_MODE_STORAGE_KEY) === "1";
      ytAudioModeCb.addEventListener("change", ytApplyAudioMode);
      // Apply the persisted mode on load so a returning video book
      // opens as an audio-only screen immediately.
      ytApplyAudioMode();
    }
    if (ytQualitySelect) {
      ytQualitySelect.addEventListener("change", ytOnQualityChange);
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

  // Audio-only mode (the gear button menu), mirroring youtube-player.js:
  // hide the video area so the reading screen matches an MP3 book
  // (controls + scrolling subtitle), while the hidden <video> keeps
  // playing its audio track.  Persists per browser.
  function ytApplyAudioMode() {
    if (!ytVideoWrap || !ytAudioModeCb) return;
    var on = !!ytAudioModeCb.checked;
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

  function ytQualityLabel(item) {
    if (item.height) return item.height + "p";
    if (item.bitrate) return Math.round(item.bitrate / 1000) + "k";
    return String(item.qualityIndex);
  }

  // Video renditions, cheapest first.  Read from dash.js rather than from
  // a hardcoded list, so the menu shows exactly what this video offers.
  function ytQualityOptions() {
    var list = (ytPlayer && ytPlayer.getVideoBitrates()) || [];
    var items = [];
    for (var i = 0; i < list.length; i++) {
      var it = list[i];
      if (it && typeof it.qualityIndex === "number") items.push(it);
    }
    items.sort(function (a, b) { return (a.bitrate || 0) - (b.bitrate || 0); });
    return items;
  }

  // (Re)build the quality menu and apply the rendition to start on: the
  // reader's remembered choice when this video offers it, otherwise the
  // cheapest one.  Returns true once it has something to show, so the
  // caller can retry -- the rendition list is not guaranteed to be ready
  // on the first event dash.js fires.
  function ytPopulateQualityControls() {
    if (!ytQualitySelect || !ytQualityRow) return false;
    var items = ytQualityOptions();
    if (!items.length) return false;

    ytQualitySelect.innerHTML = "";
    for (var i = 0; i < items.length; i++) {
      var opt = document.createElement("option");
      opt.value = String(items[i].qualityIndex);
      opt.textContent = ytQualityLabel(items[i]);
      if (items[i].bitrate) {
        opt.title = Math.round(items[i].bitrate / 1000) + " kbps";
      }
      ytQualitySelect.appendChild(opt);
    }

    // A menu with a single entry is not a choice, and in embed mode
    // Bilibili's own player owns quality, so show nothing in either case.
    ytQualityRow.hidden = ytEmbedMode || items.length < 2;
    if (ytQualityRow.hidden) return true;

    var saved = parseInt(localStorage.getItem(QUALITY_STORAGE_KEY), 10);
    var pick = null;
    if (!isNaN(saved)) {
      for (var j = 0; j < items.length; j++) {
        if (items[j].height === saved) { pick = items[j]; break; }
      }
    }
    if (!pick) pick = items[0]; // the cheap default
    ytQualitySelect.value = String(pick.qualityIndex);
    ytApplyVideoQuality(pick.qualityIndex);
    return true;
  }

  // Retry a few times: dash.js surfaces the manifest before it surfaces
  // the renditions, and neither event is guaranteed to be the last word.
  function ytScheduleQualityControls() {
    var tries = 0;
    function attempt() {
      tries += 1;
      if (ytPopulateQualityControls()) return;
      if (tries < 8) window.setTimeout(attempt, 400);
    }
    attempt();
  }

  function ytApplyVideoQuality(qualityIndex) {
    if (!ytPlayer || typeof ytPlayer.setVideoQuality !== "function") return;
    // Video ABR is off (see LuteBilibiliPlayer.init), so this pick holds
    // instead of being overridden as soon as the throughput estimate
    // improves -- which, over the relay, it never should.
    ytPlayer.setVideoQuality(qualityIndex);
  }

  function ytOnQualityChange() {
    if (!ytQualitySelect) return;
    var qi = parseInt(ytQualitySelect.value, 10);
    if (isNaN(qi)) return;
    var items = ytQualityOptions();
    for (var i = 0; i < items.length; i++) {
      if (items[i].qualityIndex === qi) {
        // Remembered as a height, not an index: an index only means
        // something inside one manifest, a height carries across videos.
        if (items[i].height) {
          localStorage.setItem(QUALITY_STORAGE_KEY, String(items[i].height));
        }
        break;
      }
    }
    ytApplyVideoQuality(qi);
    // The change came from a click inside the menu, so the outside-click
    // closer never fires; close it here (same as the audio-only toggle).
    if (ytSettingsDropdown) ytSettingsDropdown.hidden = true;
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
  /* Lazy-load subtitle word HTML                                       */
  /* ------------------------------------------------------------------ */

  function ytEscapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

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
        // Re-activate the current cue so the subtitle updates from
        // plain text to clickable word spans.  Passed false after a
        // term status update: the current cue already shows word
        // spans, and re-rendering it would replace the whole subtitle
        // DOM (causing a brief visual font change).
        if (reactivate !== false && ytCueIndex >= 0) ytActivateCue(ytCueIndex);
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

    // Fallback message if the player never becomes ready.  Skipped in
    // embed mode, where the player is ready by definition and bringing
    // this overlay back would cover the iframe after 15 seconds.
    window.setTimeout(function () {
      if (ytLoading && !ytPlayerReady && !ytEmbedMode) {
        ytLoading.textContent =
          "Unable to load the Bilibili player. The transcript below is still available.";
        ytLoading.style.display = "block";
      }
    }, 15000);

    // After a term status update, lute.js reloads #thetext with fresh
    // data-status-class attributes.  Instead of re-fetching all subtitle
    // word HTML (which requires a server round-trip + replaces the entire
    // subtitle DOM, causing a brief visual font change), we update the
    // status class on the existing subtitle word spans in-place using
    // the already-reloaded #thetext as the source of truth, and refresh
    // the WORDS cache in the background (without re-rendering) so future
    // cues also pick up the new status classes.
    //
    // With a termText in the event detail (single-word term saves), the
    // server can re-render just the cues containing that term instead of
    // the whole book, so patch WORDS incrementally and skip the
    // multi-megabyte background refetch.  bookId lets players of other
    // books ignore the event.
    window.addEventListener("lute:status-updated", function (e) {
      if (!ytSubtitle) return;
      var detail = e.detail || {};
      if (detail.bookId && BOOK_ID &&
          Number(detail.bookId) !== Number(BOOK_ID)) {
        return;
      }
      var termText = detail.termText || null;
      if (ytStatusRefreshTimer) clearTimeout(ytStatusRefreshTimer);
      ytStatusRefreshTimer = setTimeout(function () {
        ytStatusRefreshTimer = null;
        if (termText) {
          $.ajax({
            url: "/read/youtube_subtitle_words/" + BOOK_ID,
            data: { term: termText },
            method: "GET",
            dataType: "json",
          }).done(function (data) {
            if (data && !Array.isArray(data) && data.cues) {
              for (var k in data.cues) {
                if (!Object.prototype.hasOwnProperty.call(data.cues, k)) continue;
                var i = Number(k);
                if (isFinite(i) && i >= 0 && i < WORDS.length)
                  WORDS[i] = data.cues[k];
              }
              // "patched: false" means the server had no cached entry
              // (e.g. a multiword-term save invalidated it), so cues
              // beyond the patched ones may be out of sync too.
              if (data.patched === false) ytLoadSubtitleWords(false);
              return;
            }
            ytLoadSubtitleWords(false);
          });
        } else {
          ytLoadSubtitleWords(false);
        }
      }, 300);
      $(ytSubtitle).find("span.word").each(function () {
        var wid = $(this).data("wid");
        if (!wid) return;
        var src = $("#thetext").find('[data-wid="' + wid + '"]');
        if (!src.length) return;
        var newStatus = src.attr("data-status-class") || "";
        // Update the jQuery data cache as well: apply_status_class() reads
        // the status via .data("status-class"), which is cached on first
        // read.  Setting only the data-status-class attribute would leave
        // the stale pre-save status in the cache, so the color never changes.
        $(this).data("status-class", newStatus);
        // Drop the previously-applied status class before adding the new one
        // so a change between non-adjacent statuses (e.g. 5 -> 0) doesn't
        // leave the old background color behind.
        $(this).removeClass(function (i, cls) {
          return (cls.match(/\bstatus\d+\b/g) || []).join(" ");
        });
        if (newStatus) $(this).addClass(newStatus);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();