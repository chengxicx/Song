/* Media player for lute reading pages (book_type == "bilibili").

   The Bilibili counterpart of youtube-player.js, sharing the same UI
   (controls, scrolling subtitle, transcript panel), the same element ids
   and the shared engine in media-player-base.js.

   Backend note: the video is NOT an embedded iframe.  It is an HTML5
   <video> driven by dash.js from a DASH manifest our own server builds
   (lute/read/bilibili_stream.py), so playback stays under our control
   and subtitle sync works.  An earlier version of this comment claimed
   the iframe player was driven over postMessage; that is not possible --
   the modern player exposes no postMessage API, which is exactly why the
   DASH relay exists.  The iframe is only used as a last-resort fallback
   (see ytUseEmbedPlayer) and loses subtitle sync when it is.

   This file adds on top of the shared engine:
   - a video-quality menu (settings gear), defaulting to the cheapest
     rendition Bilibili offers for the video: the stream is relayed
     through a narrow egress, so the low end plays unless the reader
     deliberately chooses otherwise.  Adaptive switching is off for the
     same reason -- it would climb back up on its own.
   - the embed fallback for hosts where the stream relay is unavailable.

   Data (bilibiliUrl, mpdUrl, cues, words, ...) is injected by
   templates/read/bilibili_player.html via window.LUTE_YT_DATA.
*/

(function () {
  "use strict";

  var PS = window.LuteMediaPlayerStates;

  var YT_DATA = window.LUTE_YT_DATA || {};
  var BILI_URL = YT_DATA.bilibiliUrl || "";
  var MPD_URL = YT_DATA.mpdUrl || "";

  var player = null;
  var ytEmbedMode = false;

  /* ------------------------------------------------------------------ */
  /* Backend: HTML5 <video> driven by dash.js (direct DASH stream)       */
  /* ------------------------------------------------------------------ */

  // Wrap an HTML5 <video> element so it looks like a YT.Player instance,
  // letting the shared engine drive it unchanged.  Instead of embedding
  // Bilibili's official iframe player (which refuses to load on
  // non-whitelisted domains), we play the video's raw DASH stream via
  // dash.js.  The stream is served through our own proxy endpoints
  // (see lute/read/bilibili_stream.py), so the browser never talks to
  // Bilibili directly and the domain-whitelist restriction is bypassed.
  function LuteBilibiliPlayer(videoEl, handlers) {
    var ready = false;
    var playing = false;
    var dashPlayer = null; // dashjs.MediaPlayer
    var pendingSeek = player ? player.startPos : 0;

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
        dashPlayer = dashjs.MediaPlayer().create();
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
          dashPlayer.updateSettings({
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
        dashPlayer.on("manifestLoaded", ytScheduleQualityControls);
        dashPlayer.on("streamInitialized", ytScheduleQualityControls);
        dashPlayer.on("error", function () {
          if (typeof handlers.onError === "function") handlers.onError();
        });
        dashPlayer.initialize(videoEl, MPD_URL, false);
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
        if (dashPlayer) dashPlayer.initialize(videoEl, MPD_URL, false);
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
          return (dashPlayer && dashPlayer.getBitrateInfoListFor("video")) || [];
        } catch (e) {
          return [];
        }
      },
      setVideoQuality: function (qualityIndex) {
        try {
          dashPlayer.setQualityFor("video", qualityIndex, true);
        } catch (e) { /* ignore */ }
      },
      getVideoQuality: function () {
        try {
          return dashPlayer ? dashPlayer.getQualityFor("video") : null;
        } catch (e) {
          return null;
        }
      },
    };
  }

  /* ------------------------------------------------------------------ */
  /* Player creation                                                     */
  /* ------------------------------------------------------------------ */

  function createBackendPlayer(api) {
    var videoEl = document.getElementById("bili-player");
    if (!videoEl) return;
    if (!MPD_URL) {
      // No stream endpoint (unparseable URL, or the server could not
      // build the manifest): go straight to the embed player.
      ytUseEmbedPlayer(api);
      if (!ytEmbedMode && api.els.loading) {
        api.els.loading.textContent =
          "Unable to load the Bilibili player: no video stream available.";
      }
      return;
    }
    var backend = LuteBilibiliPlayer(videoEl, api.backendHandlers);
    api.setPlayer(backend);
    backend._init();
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
  function ytDisableTransport(off) {
    var ids = [
      "yt-play-btn", "yt-prev-cue-btn", "yt-next-cue-btn",
      "yt-rate-dec", "yt-rate-inc", "yt-loop-btn", "yt-autopause-btn",
    ];
    for (var i = 0; i < ids.length; i++) {
      var el = document.getElementById(ids[i]);
      if (el) el.disabled = !!off;
    }
    if (player && player.els.timeline) player.els.timeline.disabled = !!off;
  }

  function ytUseEmbedPlayer(api) {
    if (ytEmbedMode || !BILI_URL || !api.els.videoWrap) return;
    ytEmbedMode = true;
    api.setEmbedMode(true);

    var frame = document.createElement("iframe");
    frame.className = "bili-player-frame bili-embed-frame";
    frame.src = BILI_URL;
    frame.setAttribute("allowfullscreen", "true");
    frame.setAttribute("scrolling", "no");
    frame.setAttribute("frameborder", "0");

    var videoEl = document.getElementById("bili-player");
    if (videoEl) {
      videoEl.style.display = "none";
      api.els.videoWrap.insertBefore(frame, videoEl);
    } else {
      api.els.videoWrap.appendChild(frame);
    }

    if (api.els.container) api.els.container.classList.add("bili-embed-active");
    ytDisableTransport(true);
    // Quality is Bilibili's own business in this mode; our rendition menu
    // would be a lie.
    if (api.els.qualityRow) api.els.qualityRow.hidden = true;

    // Bilibili's player owns its own transport, so this state must be
    // reported OUTSIDE the video: the loading overlay is inset:0 and
    // stacks above the iframe, which would swallow every click and make
    // the embed look frozen.  Hide the overlay and use the sibling
    // notice instead.
    if (api.els.loading) api.els.loading.style.display = "none";
    if (api.els.embedNotice) {
      api.els.embedNotice.textContent =
        "Song cannot relay this video, so it is playing in Bilibili's embed " +
        "player -- use that player's own controls. Subtitle sync " +
        "(auto-scroll, loop, auto-pause) is not available in this mode; " +
        "the transcript below still works.";
      api.els.embedNotice.hidden = false;
    }
  }

  function ytOnError(api) {
    ytUseEmbedPlayer(api);
    if (api.els.loading && !ytEmbedMode) {
      api.els.loading.textContent =
        "Unable to play this video. The transcript below is still available.";
      api.els.loading.style.display = "block";
    }
  }

  /* ------------------------------------------------------------------ */
  /* Video quality menu                                                  */
  /* ------------------------------------------------------------------ */

  // Remembered video quality, stored as the rendition height (e.g. 480)
  // rather than as a rendition index: an index only means something
  // within one manifest's rendition list, while a height stays
  // meaningful across videos.  Absent means "the lowest one", which is
  // the intended default here -- the stream is relayed through a narrow
  // egress (see lute/utils/outbound_proxy.py), so the cheap end plays
  // unless the reader deliberately asks for more.
  var QUALITY_STORAGE_KEY = "biliVideoQuality";

  function ytQualityLabel(item) {
    if (item.height) return item.height + "p";
    if (item.bitrate) return Math.round(item.bitrate / 1000) + "k";
    return String(item.qualityIndex);
  }

  // Video renditions, cheapest first.  Read from dash.js rather than from
  // a hardcoded list, so the menu shows exactly what this video offers.
  function ytQualityOptions(api) {
    var p = api.player();
    var list = (p && p.getVideoBitrates()) || [];
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
  function ytPopulateQualityControls(api) {
    if (!api.els.qualitySelect || !api.els.qualityRow) return false;
    var items = ytQualityOptions(api);
    if (!items.length) return false;

    api.els.qualitySelect.innerHTML = "";
    for (var i = 0; i < items.length; i++) {
      var opt = document.createElement("option");
      opt.value = String(items[i].qualityIndex);
      opt.textContent = ytQualityLabel(items[i]);
      if (items[i].bitrate) {
        opt.title = Math.round(items[i].bitrate / 1000) + " kbps";
      }
      api.els.qualitySelect.appendChild(opt);
    }

    // A menu with a single entry is not a choice, and in embed mode
    // Bilibili's own player owns quality, so show nothing in either case.
    api.els.qualityRow.hidden = ytEmbedMode || items.length < 2;
    if (api.els.qualityRow.hidden) return true;

    var saved = parseInt(localStorage.getItem(QUALITY_STORAGE_KEY), 10);
    var pick = null;
    if (!isNaN(saved)) {
      for (var j = 0; j < items.length; j++) {
        if (items[j].height === saved) { pick = items[j]; break; }
      }
    }
    if (!pick) pick = items[0]; // the cheap default
    api.els.qualitySelect.value = String(pick.qualityIndex);
    ytApplyVideoQuality(api, pick.qualityIndex);
    return true;
  }

  // Retry a few times: dash.js surfaces the manifest before it surfaces
  // the renditions, and neither event is guaranteed to be the last word.
  function ytScheduleQualityControls() {
    if (!player) return;
    var tries = 0;
    function attempt() {
      tries += 1;
      if (ytPopulateQualityControls(player)) return;
      if (tries < 8) window.setTimeout(attempt, 400);
    }
    attempt();
  }

  function ytApplyVideoQuality(api, qualityIndex) {
    var p = api.player();
    if (!p || typeof p.setVideoQuality !== "function") return;
    // Video ABR is off (see LuteBilibiliPlayer.init), so this pick holds
    // instead of being overridden as soon as the throughput estimate
    // improves -- which, over the relay, it never should.
    p.setVideoQuality(qualityIndex);
  }

  function ytOnQualityChange(api) {
    if (!api.els.qualitySelect) return;
    var qi = parseInt(api.els.qualitySelect.value, 10);
    if (isNaN(qi)) return;
    var items = ytQualityOptions(api);
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
    ytApplyVideoQuality(api, qi);
    // The change came from a click inside the menu, so the outside-click
    // closer never fires; close it here (same as the audio-only toggle).
    if (api.els.settingsDropdown) api.els.settingsDropdown.hidden = true;
  }

  /* ------------------------------------------------------------------ */
  /* Init                                                                */
  /* ------------------------------------------------------------------ */

  function init() {
    player = LuteMediaPlayer({
      // dash.js can attach and play as soon as initialize() is called;
      // the loadedmetadata listener may not fire before play, matching
      // the audio backend's behaviour.
      playBeforeReady: false,
      spaceNeedsReady: true,
      saveIntervalSec: 2,
      jumpCueAutoplay: "never",
      audioModeEnabled: true,
      transcriptScroll: "simple",
      savePositionBeacon: false,
      replayOnNonWordClick: false,

      handleError: function () {
        ytOnError(player);
      },

      notReadyMessage: function () {
        return "Unable to load the Bilibili player. The transcript below is still available.";
      },

      // The in-place class refresh below must run synchronously at
      // event time (it reads the just-reloaded #thetext); only the
      // cache refetch is debounced into onStatusUpdated.
      onStatusEvent: function () {
        ytRefreshSubtitleStatusInPlace(player);
      },

      onStatusUpdated: function (termText) {
        ytRefreshSubtitleAfterTermUpdate(player, termText);
      },
    });

    var api = player;
    if (!api.els.container) return;

    api.buildTranscript();
    api.bindControls();
    api.bindSubtitleInteractions();
    api.bindKeys();
    api.bindStatusUpdates();
    api.bindNotReadyFallback();

    if (api.els.qualitySelect) {
      api.els.qualitySelect.addEventListener("change", function () {
        ytOnQualityChange(api);
      });
    }

    api.loadSubtitleWords();

    createBackendPlayer(api);
  }

  // Update the status class on the existing subtitle word spans
  // in-place, using the already-reloaded #thetext as the source of
  // truth (runs synchronously on each lute:status-updated event).
  function ytRefreshSubtitleStatusInPlace(api) {
    $(api.els.subtitle).find("span.word").each(function () {
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
  }

  // Refresh the WORDS cache after a term status update (debounced):
  // without re-rendering the current cue, so future cues pick up the
  // new status classes.
  function ytRefreshSubtitleAfterTermUpdate(api, termText) {
    var refreshCache = function () { api.loadSubtitleWords(false); };
    if (termText) {
      $.ajax({
        url: "/read/youtube_subtitle_words/" + api.bookId,
        data: { term: termText },
        method: "GET",
        dataType: "json",
      }).done(function (data) {
        if (data && !Array.isArray(data) && data.cues) {
          for (var k in data.cues) {
            if (!Object.prototype.hasOwnProperty.call(data.cues, k)) continue;
            var i = Number(k);
            if (isFinite(i) && i >= 0 && i < api.words.length)
              api.words[i] = data.cues[k];
          }
          // "patched: false" means the server had no cached entry
          // (e.g. a multiword-term save invalidated it), so cues
          // beyond the patched ones may be out of sync too.
          if (data.patched === false) refreshCache();
          return;
        }
        refreshCache();
      });
    } else {
      refreshCache();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
