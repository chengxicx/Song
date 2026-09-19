/* Media player for lute reading pages (book_type == "youtube" or "mp3").

   Backend selection for this book type:
   - YouTube IFrame API player, for book_type == "youtube"
   - HTML5 <audio>/<video> element, for book_type == "mp3" / "netease" /
     "video" (wrapped to look like a YT.Player by LuteAudioPlayer below)

   Everything else -- controls, scrolling subtitle, transcript panel,
   loop/auto-pause, term-status syncing -- is the shared engine in
   media-player-base.js; this file only wires the backends and the lazy
   subtitle-word fetching that only the unified player needs.

   Data (videoId / audioUrl, cues, words, ...) is injected by
   templates/read/youtube_player.html via window.LUTE_YT_DATA.
*/

(function () {
  "use strict";

  var PS = window.LuteMediaPlayerStates;

  var USE_AUDIO_BACKEND = window.LUTE_YT_DATA
    ? window.LUTE_YT_DATA.backend === "audio"
    : false;
  var USE_VIDEO_BACKEND = window.LUTE_YT_DATA
    ? window.LUTE_YT_DATA.backend === "video"
    : false;

  var player = null;

  /* ------------------------------------------------------------------ */
  /* Backend: HTML5 <audio>/<video> wrapped as a YT.Player               */
  /* ------------------------------------------------------------------ */

  // Wrap an HTML5 media element to look like a YT.Player instance,
  // so the shared engine (written for the YT IFrame API) can drive an
  // MP3 audio book unchanged.  <audio> and <video> share the same
  // HTMLMediaElement API, so one wrapper drives both.
  function LuteAudioPlayer(mediaEl, handlers) {
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
      if ((isFinite(mediaEl.duration) && mediaEl.duration > 0) || mediaEl.readyState >= 1) {
        fireReady();
      }
    }

    mediaEl.addEventListener("loadedmetadata", fireReady);
    mediaEl.addEventListener("durationchange", fireReady);
    mediaEl.addEventListener("play", function () {
      // If somehow onReady hasn't fired when the user plays, fire it now.
      checkReady();
      fireStateChange(PS.PLAYING);
    });
    mediaEl.addEventListener("playing", function () {
      // Playback is actually flowing again after a stall/buffer.
      checkReady();
      fireStateChange(PS.PLAYING);
    });
    mediaEl.addEventListener("pause", function () { fireStateChange(PS.PAUSED); });
    mediaEl.addEventListener("ended", function () { fireStateChange(PS.ENDED); });
    mediaEl.addEventListener("canplay", fireReady);
    mediaEl.addEventListener("waiting", function () {
      // Not enough buffered data ahead: surface the buffering state so
      // the UI can show feedback instead of a frozen progress bar.
      fireStateChange(PS.BUFFERING);
    });
    mediaEl.addEventListener("stalled", function () {
      fireStateChange(PS.BUFFERING);
    });
    mediaEl.addEventListener("error", function () {
      if (typeof handlers.onError === "function") handlers.onError();
    });

    return {
      // The media element might already have its metadata cached; in
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
      load: function () { mediaEl.load(); },
      playVideo: function () {
        // Before playing, be sure onReady has fired (it initializes
        // the timeline max, duration display, and starts ytPoll).
        checkReady();
        var p = mediaEl.play();
        if (p && p.catch) p.catch(function () {});
      },
      pauseVideo: function () { mediaEl.pause(); },
      getCurrentTime: function () { return mediaEl.currentTime || 0; },
      getDuration: function () { return mediaEl.duration || 0; },
      seekTo: function (t) { try { mediaEl.currentTime = t; } catch (e) { /* ignore */ } },
      getPlaybackRate: function () { return mediaEl.playbackRate || 1; },
      setPlaybackRate: function (r) { try { mediaEl.playbackRate = r; } catch (e) { /* ignore */ } },
      getPlayerState: function () { return fakeState; },
      getLoadedFraction: function () {
        // Fraction of the media buffered ahead, for the timeline's
        // buffered band.
        var d = mediaEl.duration;
        if (!isFinite(d) || d <= 0) return 0;
        var b = mediaEl.buffered;
        var end = 0;
        for (var i = 0; i < b.length; i++) {
          if (b.end(i) > end) end = b.end(i);
        }
        return Math.min(1, end / d);
      },
    };
  }

  /* ------------------------------------------------------------------ */
  /* Backend: YouTube IFrame API                                         */
  /* ------------------------------------------------------------------ */

  function createYoutubeIframePlayer(api) {
    if (!window.YT || !window.YT.Player) return false;
    var videoId = window.LUTE_YT_DATA && window.LUTE_YT_DATA.videoId;
    if (!videoId) {
      if (api.els.loading) {
        api.els.loading.textContent =
          "Unable to load the YouTube player: no video id.";
      }
      return false;
    }
    api.setPlayer(new window.YT.Player("yt-player", {
      videoId: videoId,
      playerVars: {
        enablejsapi: 1,
        playsinline: 1,
        rel: 0,
        controls: 0,
        modestbranding: 1,
        iv_load_policy: 3,
      },
      events: api.backendHandlers,
    }));
    return true;
  }

  var backendCreated = false;

  function createBackendPlayer(api) {
    if (backendCreated) return;
    if (USE_AUDIO_BACKEND || USE_VIDEO_BACKEND) {
      var mediaEl = USE_VIDEO_BACKEND
        ? document.getElementById("yt-video-player")
        : document.getElementById("yt-audio-player");
      if (!mediaEl) return;
      var wrapper = LuteAudioPlayer(mediaEl, api.backendHandlers);
      api.setPlayer(wrapper);
      backendCreated = true;
      // Some browsers cache metadata for fast loads; ensure onReady
      // fires in that case too.
      wrapper._maybeFireReady();
      return;
    }
    var iframeOk = createYoutubeIframePlayer(api);
    if (iframeOk !== false) backendCreated = true;
  }

  /* ------------------------------------------------------------------ */
  /* Lazy-load subtitle word HTML                                        */
  /* ------------------------------------------------------------------ */

  // Subtitle word HTML is fetched in windows around the play position:
  // a long book renders to several MB of word spans, which is far too
  // much to fetch and parse up front.
  var WORDS_WINDOW = 40;
  var WORDS_TOTAL = 0;
  var WORDS_REQUESTED = [];
  // Set when a term save invalidated the server's cue cache and the
  // response couldn't be applied incrementally ("patched: false"):
  // the whole window is re-fetched once the media is paused.
  var ytNeedsFullSubtitleRefresh = false;

  // Fetch the tokenized word HTML for a window of cues around `idx`.
  // The full-book payload is several MB, so loading all of it up front
  // costs a multi-megabyte JSON parse plus the memory to hold it --
  // very noticeable on a slow device.  Instead each cue's words arrive
  // shortly before they're needed; until then the subtitle falls back
  // to plain cue text (see the engine's activateCue).
  function ytEnsureWordsAround(api, idx) {
    if (!api.bookId || idx < 0) return;
    var half = Math.floor(WORDS_WINDOW / 2);
    var from = Math.max(0, idx - half);
    var to = idx + half;
    if (WORDS_TOTAL > 0) to = Math.min(to, WORDS_TOTAL - 1);
    if (ytWordsRangeRequested(from, to)) return;

    // Record the range before the request so a burst of cue changes
    // (e.g. seeking) doesn't fire one request per cue.
    WORDS_REQUESTED.push([from, to]);
    $.ajax({
      url: "/read/youtube_subtitle_words/" + api.bookId,
      method: "GET",
      data: { from: from, to: to },
      dataType: "json",
    })
      .done(function (data) {
        if (!data || !data.cues) return;
        ytApplyWordsWindow(api, data);
      })
      .fail(function () {
        // Let a later cue change retry this window.
        for (var i = WORDS_REQUESTED.length - 1; i >= 0; i--) {
          if (WORDS_REQUESTED[i][0] === from && WORDS_REQUESTED[i][1] === to) {
            WORDS_REQUESTED.splice(i, 1);
            break;
          }
        }
      });
  }

  function ytWordsRangeRequested(from, to) {
    for (var i = 0; i < WORDS_REQUESTED.length; i++) {
      var r = WORDS_REQUESTED[i];
      if (r[0] <= from && r[1] >= to) return true;
    }
    return false;
  }

  // Store a {total, cues} window response, then refresh the visible
  // subtitle if the cue it is showing just got its word spans.
  function ytApplyWordsWindow(api, data) {
    var total = data.total || 0;
    if (total > 0) {
      WORDS_TOTAL = total;
      while (api.words.length < total) api.words.push(null);
    }
    var hadCurrent = api.cueIndex() >= 0 && !!api.words[api.cueIndex()];
    for (var k in data.cues) {
      if (!Object.prototype.hasOwnProperty.call(data.cues, k)) continue;
      var i = Number(k);
      if (!isFinite(i) || i < 0 || i >= api.words.length) continue;
      api.words[i] = data.cues[k];
    }
    if (api.cueIndex() >= 0 && !hadCurrent && api.words[api.cueIndex()])
      api.activateCue(api.cueIndex());
  }

  // Patch WORDS in place with a {"cues": {index: html}, "patched": bool}
  // incremental response, then re-inject the current cue when it was
  // touched (or when asked to).  A "patched: false" response means the
  // server had no cached entry for the book, so our WORDS copy may be
  // wholly out of sync — schedule a full refresh for the next pause.
  function ytApplySubtitlePatch(api, data, reactivate) {
    if (!data || Array.isArray(data)) return false;
    var cues = data.cues;
    if (!cues) return false;
    var touchedCurrent = false;
    for (var k in cues) {
      if (!Object.prototype.hasOwnProperty.call(cues, k)) continue;
      var i = Number(k);
      // With windowed loading WORDS may not have been sized yet, so a
      // patch for a far-away cue simply grows the sparse array.
      if (!isFinite(i) || i < 0) continue;
      api.words[i] = cues[k];
      if (i === api.cueIndex()) touchedCurrent = true;
    }
    if (data.patched === false) ytNeedsFullSubtitleRefresh = true;
    if ((reactivate || touchedCurrent) && api.cueIndex() >= 0)
      api.activateCue(api.cueIndex());
    return true;
  }

  // Incremental refresh after a term save: the server re-renders just
  // the cues containing the term (patching its own cache) and returns
  // their HTML — a few hundred bytes instead of the multi-megabyte
  // full refetch, with no full-book re-tokenization while the audio
  // is streaming.  Falls back to re-rendering the current cue alone,
  // then to a full reload.
  function ytRefreshSubtitleForTerm(api, termText) {
    if (!api.bookId) return;
    var fetchCurrentCue = function () {
      if (api.cueIndex() < 0) {
        api.loadSubtitleWords(true);
        return;
      }
      $.ajax({
        url: "/read/youtube_subtitle_words/" + api.bookId,
        data: { cue: api.cueIndex() },
        method: "GET",
        dataType: "json",
      })
        .done(function (data) {
          if (!ytApplySubtitlePatch(api, data, true)) api.loadSubtitleWords(true);
        })
        .fail(function () {
          api.loadSubtitleWords(true);
        });
    };
    if (termText) {
      $.ajax({
        url: "/read/youtube_subtitle_words/" + api.bookId,
        data: { term: termText },
        method: "GET",
        dataType: "json",
      })
        .done(function (data) {
          if (!ytApplySubtitlePatch(api, data, true)) fetchCurrentCue();
        })
        .fail(fetchCurrentCue);
    } else {
      fetchCurrentCue();
    }
  }

  /* ------------------------------------------------------------------ */
  /* Init                                                                */
  /* ------------------------------------------------------------------ */

  function init() {
    player = LuteMediaPlayer({
      // The HTML5 backends can start before their metadata loads; the
      // YouTube iframe cannot.
      playBeforeReady: USE_AUDIO_BACKEND,
      spaceNeedsReady: !USE_AUDIO_BACKEND,
      saveIntervalSec: 15,
      jumpCueAutoplay: "autopause",
      // The gear-menu audio-only mode makes sense only for real video
      // books; audio books have no video area to hide.
      audioModeEnabled: !USE_AUDIO_BACKEND,
      transcriptScroll: "fancy",
      savePositionBeacon: true,
      replayOnNonWordClick: true,

      // Make sure the words for this cue (and its neighbours) are on
      // their way; until they land the plain-text fallback shows.
      beforeActivateCue: function (idx) {
        ytEnsureWordsAround(player, idx);
      },

      handleError: function () {
        if (player.els.loading) {
          player.els.loading.textContent = USE_AUDIO_BACKEND
            ? "Unable to play this audio file. The transcript below is still available."
            : "Unable to play this video. The transcript below is still available.";
          player.els.loading.style.display = "block";
        }
      },

      notReadyMessage: function () {
        return USE_AUDIO_BACKEND
          ? "Unable to load the audio player. The transcript below is still available."
          : USE_VIDEO_BACKEND
            ? "Unable to load the video player. The transcript below is still available."
            : "Unable to load the YouTube player. The transcript below is still available.";
      },

      // If subtitle colors may be out of sync across cues (bulk status
      // update or cache invalidation -- see ytApplySubtitlePatch),
      // re-fetch the current window once the media is paused: the
      // server rebuild can be slow, so keep it off the path that
      // competes with the audio stream.  Skipped when playback resumed
      // within the delay.
      onPaused: function () {
        if (!ytNeedsFullSubtitleRefresh) return;
        window.setTimeout(function () {
          if (player.isPlaying() || !ytNeedsFullSubtitleRefresh) return;
          ytNeedsFullSubtitleRefresh = false;
          // Drop the remembered ranges so the window is really
          // re-fetched rather than served from what we already have.
          WORDS_REQUESTED.length = 0;
          for (var i = 0; i < player.words.length; i++) player.words[i] = null;
          ytEnsureWordsAround(player, player.cueIndex() >= 0 ? player.cueIndex() : 0);
        }, 2000);
      },

      // After a term status update, lute.js reloads #thetext and
      // dispatches lute:status-updated (the book filtering and
      // debouncing are handled by the engine).  The subtitle words are
      // tokenized from the media cues, which is a DIFFERENT text from
      // the page content (#thetext), so their data-wid values don't
      // exist in #thetext and we can't copy status classes from there.
      // Instead, re-render just the affected cues:
      //  - with a termText, the server re-renders the cues containing
      //    it (its cache is patched in place by the save itself);
      //  - without one (bulk updates, deletions), fall back to the
      //    current cue, and flag a full refresh for the next pause,
      //    when the refetch doesn't compete with the audio stream for
      //    bandwidth.
      onStatusUpdated: function (termText) {
        ytRefreshSubtitleForTerm(player, termText);
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

    // Only the words around the saved playback position: a full fetch
    // would pull several MB of HTML before the first line is readable.
    var startIdx = 0;
    if (api.startPos > 0) {
      for (var w = api.cues.length - 1; w >= 0; w--) {
        if ((api.cues[w].start || 0) <= api.startPos) {
          startIdx = w;
          break;
        }
      }
    }
    ytEnsureWordsAround(api, startIdx);

    // Create the player immediately for the media backends (the
    // <audio>/<video> element is already in the DOM); for YouTube, wait
    // for the IFrame API to be ready.
    if (USE_AUDIO_BACKEND || USE_VIDEO_BACKEND) {
      createBackendPlayer(api);
    } else {
      var create = function () { createBackendPlayer(api); };
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
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
