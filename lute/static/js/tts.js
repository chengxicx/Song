/**
 * Edge-TTS + SpeechSynthesis voice synthesis and Google auto-translation
 * integration for the Lute reading page.
 *
 * The review session page loads this file too, for window.luteTtsSpeak
 * alone -- it speaks each card in the language of its own term
 * (speakText's optional lang argument) and uses none of the player.
 *
 * The TTS player mirrors the YouTube / MP3 player UI and behaviour:
 *   - play / pause, prev / next sentence, seek timeline, playback rate
 *   - single-sentence loop and auto-pause-at-end-of-sentence
 *   - a single-line scrolling subtitle synced to the speech, reusing
 *     the reading-page tokenization and click-to-lookup behavior
 *   - a Transcript panel with bidirectional control:
 *       speech -> transcript: highlight + smooth-center the current line
 *       transcript -> speech: clicking a line seeks playback to its start
 *   - a voice-selection button placed in the leftmost control area
 *
 * Audio backend: browser SpeechSynthesis API (primary) with a fallback
 * to the backend /tts/<lang>/<text> endpoint (edge-tts) when the
 * browser does not expose SpeechSynthesis.
 *
 * Auxiliary features retained from the previous TTS module:
 *   - Sentence-level 🔊 buttons at each paragraph / sentence row.
 *   - Word hover pronunciation (configurable delay, 0-300 ms) via
 *     event delegation, served from the backend /tts/ cache when the
 *     browser voice has an edge-tts twin (section 2).
 *   - Auto-translation: when a term edit form opens with an empty
 *     #translation, the translation is fetched and auto-filled.
 */
  "use strict";

  // ------------------------------------------------------------------
  // 0. Language detection
  // ------------------------------------------------------------------

  // Shared cache for cross-frame communication
  if (!window.top.__LUTE_TTS_CACHE__) {
    window.top.__LUTE_TTS_CACHE__ = {
      trans: {},
      lastWord: "",
      sl: "en",
      tl: "",
      selectedVoice: "",
      lastSpokenWord: "",
      detectedLang: "",
    };
  }
  const globalCache = window.top.__LUTE_TTS_CACHE__;

  // Read SL from: local input -> shared cache -> default
  const SL_INPUT = document.getElementById("tts_lang");
  if (SL_INPUT) {
    globalCache.sl = SL_INPUT.value || globalCache.sl || "en";
  }
  let SL = globalCache.sl || "en";

  // Read TL from: per-language setting -> navigator.language -> shared cache -> default
  var TL_INPUT =
    (window.top && window.top.document &&
      window.top.document.getElementById("tts_target_lang")) ||
    document.getElementById("tts_target_lang");
  if (TL_INPUT && TL_INPUT.value) {
    globalCache.tl = TL_INPUT.value;
  } else {
    globalCache.tl = navigator.language || globalCache.tl || "zh-CN";
  }
  let TL = globalCache.tl;

  // Cached language detection – only runs once per page load.
  let _cachedLang = null;
  function detectTextLanguage() {
    if (_cachedLang) return _cachedLang;
    if (SL_INPUT && SL_INPUT.value) {
      _cachedLang = SL_INPUT.value;
      return _cachedLang;
    }
    const textDiv =
      (window.top && window.top.document.getElementById("thetext")) ||
      document.getElementById("thetext");
    if (textDiv) {
      const content = textDiv.innerText || textDiv.textContent || "";
      if (content) {
        const sample = content.slice(0, 500);
        if (/[\u3040-\u309F\u30A0-\u30FF]/.test(sample)) { _cachedLang = "ja"; return _cachedLang; }
        if (/[\uAC00-\uD7AF\u1100-\u11FF]/.test(sample)) { _cachedLang = "ko"; return _cachedLang; }
        if (/[\u0900-\u097F]/.test(sample)) { _cachedLang = "hi"; return _cachedLang; }
        if (/[\u0600-\u06FF]/.test(sample)) { _cachedLang = "ar"; return _cachedLang; }
        if (/[\u0400-\u04FF]/.test(sample)) { _cachedLang = "ru"; return _cachedLang; }
        if (/[řěščžňů]/i.test(sample)) { _cachedLang = "cs"; return _cachedLang; }
        if (/[ğşı]/i.test(sample)) { _cachedLang = "tr"; return _cachedLang; }
        if (/[äöüß]/i.test(sample)) { _cachedLang = "de"; return _cachedLang; }
        if (/[ñ¿¡]/i.test(sample)) { _cachedLang = "es"; return _cachedLang; }
        if (/[œæ]/i.test(sample) ||
            (/[éèêàç]/i.test(sample) &&
              /\b(le|la|les|un|une|et|est|du|des)\b/i.test(sample))) { _cachedLang = "fr"; return _cachedLang; }
        if (/\b(the|and|is|in|to|of|that|it|was|for|on|are)\b/i.test(sample)) { _cachedLang = "en"; return _cachedLang; }
      }
    }
    _cachedLang = SL;
    return _cachedLang;
  }

  function getCurrentLangCode() {
    return detectTextLanguage();
  }

  // ------------------------------------------------------------------
  // 0b. User settings (read from LUTE_USER_SETTINGS)
  // ------------------------------------------------------------------

  function getSetting(key, defaultValue) {
    try {
      if (typeof LUTE_USER_SETTINGS !== "undefined" && LUTE_USER_SETTINGS[key] !== undefined) {
        var val = LUTE_USER_SETTINGS[key];
        if (val === "1" || val === 1 || val === true) return true;
        if (val === "0" || val === 0 || val === false) return false;
        return val;
      }
    } catch (_) {}
    return defaultValue;
  }

  var SETTINGS = {
    hoverPronunciation: getSetting("tts_hover_pronunciation", true),
    clickPronunciation: getSetting("tts_click_pronunciation", true),
    showControlPanel: getSetting("tts_show_control_panel", true),
    showSentenceButtons: getSetting("tts_show_sentence_buttons", true),
  };

  // Hover delay (ms) before speaking a hovered word, configurable 0-300.
  SETTINGS.hoverDelay = parseInt(getSetting("tts_hover_delay", 200), 10);
  if (!Number.isFinite(SETTINGS.hoverDelay)) SETTINGS.hoverDelay = 200;
  SETTINGS.hoverDelay = Math.min(300, Math.max(0, SETTINGS.hoverDelay));

  // ------------------------------------------------------------------
  // 1. Speech synthesis (primary: browser SpeechSynthesis,
  //    fallback: backend /tts/ endpoint)
  // ------------------------------------------------------------------

  let globalSpeed = 1.0;
  let hoverTimer = null;
  // Guards the "wait for voices to load" retry so it only runs once.
  let _voicesWaitActive = false;
  let _pendingWaitText = null;
  let _pendingWaitOnStarted = null;
  // Bumped on every speakNow: a queued sequence uses it to notice that
  // the user started some other speech mid-sequence and stop queueing.
  let speakEpoch = 0;

  // Whether a voice can actually produce sound.
  //
  // The one exception to "every listed voice speaks" is Chrome's remote
  // "Google <language>" voices: Chrome removed remote synthesis in M71
  // but the names stayed in getVoices(), and speak() with one neither
  // plays a sound nor fires onstart / onend / onerror -- the reader just
  // goes silent, with no error to report.
  //
  // localService === false is NOT a reason to skip a voice on its own:
  // Edge's far better "Microsoft ... Online (Natural)" voices are remote
  // too, yet they work (and on Windows they are often the ONLY voices
  // for a given language).  So the exclusion is deliberately narrowed to
  // remote *Google* voices.  Google's TTS voices on Android/ChromeOS are
  // local (localService true) and keep working.  Browsers that don't
  // define localService at all (Safari) never match this test.
  const _DEAD_REMOTE_VOICE_RE = /^google\b/i;

  function isSpeakableVoice(v) {
    if (!v) return false;
    if (v.localService === false && _DEAD_REMOTE_VOICE_RE.test(v.name)) {
      return false;
    }
    return true;
  }

  // Voices that reported a synthesis error in this page.  A remote voice
  // that fails is silent and takes a couple of seconds to fail, so
  // retrying it on every sentence would leave a gap before each one --
  // see speakNow() and tts-playcue's fallback.  Remembering the failure
  // keeps the rest of the page on a working voice.  Cleared by a reload:
  // a server-side outage may well be over by the next visit, and there
  // is no other way to notice.
  var _failedVoices = new Set();
  function markVoiceFailed(name) {
    if (name) _failedVoices.add(name);
  }

  function selectBestVoiceForLang(voices, targetLang) {
    if (!voices || voices.length === 0) return null;
    // Never auto-pick a voice that cannot speak -- on macOS Chrome the
    // preferred "Google <language>" entry is one of these, which is why
    // the reader used to be silent while a bare speechSynthesis.speak()
    // (no explicit voice) worked fine.
    voices = voices.filter(isSpeakableVoice);
    if (voices.length === 0) return null;
    const normLang = function (s) { return (s || "").toLowerCase(); };
    const target = normLang(targetLang);
    if (!target) return null;
    // Cantonese alias: devices disagree on the tag. macOS voices are
    // keyed "yue-HK" (Sinji) while the language TTS setting uses
    // "zh-HK" (the edge-tts voices are all keyed zh-HK), and iOS /
    // Windows expose their Cantonese voice as "zh-HK". When one side
    // is missing, match the other so a real Cantonese voice is picked
    // instead of falling through to the device default.
    const isZhHk = function (s) { return /^zh(-hant)?-hk/.test(s); };
    const isYue = function (s) { return /^yue/.test(s); };
    let matched = voices.filter(function (v) {
      return normLang(v.lang).startsWith(target);
    });
    if (matched.length === 0 && isZhHk(target)) {
      matched = voices.filter(function (v) { return isYue(normLang(v.lang)); });
    }
    if (matched.length === 0 && isYue(target)) {
      matched = voices.filter(function (v) { return isZhHk(normLang(v.lang)); });
    }
    if (matched.length === 0 && target === "sa") {
      matched = voices.filter(function (v) {
        return normLang(v.lang).startsWith("hi");
      });
    }
    if (matched.length === 0) return null;
    // The natural / neural voices sound far better than the system ones,
    // so they stay the default choice.  What is new here is the failure
    // list: a remote voice that already reported a synthesis error on
    // this page is not offered again (see _failedVoices), so the rest of
    // the book is not spent waiting a couple of seconds per sentence for
    // the same failure.
    const alive = matched.filter(function (v) {
      return !_failedVoices.has(v.name);
    });
    const keywords = ["online", "natural", "neural", "google", "microsoft"];
    for (const kw of keywords) {
      const found = alive.find(function (v) {
        return v.name.toLowerCase().includes(kw);
      });
      if (found) return found;
    }
    if (alive.length > 0) return alive[0];
    // Everything for this language has failed at least once already:
    // hand back a local voice rather than letting the engine pick its
    // own default.
    return selectLocalVoiceForLang(voices, targetLang);
  }

  // The best local voice for a language, ignoring remote ones entirely.
  // Used when a remote voice was in play and produced no sound: the cue
  // is retried with this instead of being skipped in silence.
  function selectLocalVoiceForLang(voices, targetLang) {
    if (!voices || voices.length === 0) return null;
    const target = (targetLang || "").toLowerCase().slice(0, 2);
    if (!target) return null;
    const local = voices.filter(function (v) {
      return isSpeakableVoice(v) && v.localService !== false &&
        (v.lang || "").toLowerCase().slice(0, 2) === target;
    });
    return local.length > 0 ? local[0] : null;
  }

  // Resolve a remembered voice name to a live voice object.  macOS's
  // novelty voices ("Eddy", "Flo", "Grandma", "Anna", ...) exist in
  // dozens of locales under the SAME name, so a bare name can resolve
  // to the wrong language's twin -- a Japanese book read by the German
  // Eddy.  "name|lang" pairs (see the player's save) match exactly;
  // bare names prefer a voice of the book's language and only then
  // fall back to the first same-named one.
  function ttsFindStoredVoice(voices, stored, preferLang) {
    if (!stored || !voices || voices.length === 0) return null;
    const pipe = stored.indexOf("|");
    const name = pipe >= 0 ? stored.slice(0, pipe) : stored;
    const lang = pipe >= 0 ? stored.slice(pipe + 1) : "";
    const named = voices.filter(function (v) {
      return v.name === name && isSpeakableVoice(v);
    });
    if (named.length === 0) return null;
    if (lang) {
      const exact = named.find(function (v) { return v.lang === lang; });
      if (exact) return exact;
    }
    if (preferLang) {
      const pl = String(preferLang).toLowerCase();
      const inLang = named.find(function (v) {
        return (v.lang || "").toLowerCase().indexOf(pl) === 0;
      });
      if (inLang) return inLang;
    }
    return named[0];
  }

  // The explicitly chosen voice, if this device still offers it and it
  // can actually speak.  A remembered remote ("Google ...") voice is
  // deliberately ignored here -- it would silence every utterance -- so
  // callers fall through to selectBestVoiceForLang and get a local one.
  function getSelectedVoice() {
    if (!("speechSynthesis" in window)) return null;

    const voices = window.speechSynthesis.getVoices();
    const voiceSelect = document.getElementById("tts-voice-btn");
    const stored =
      (voiceSelect && voiceSelect.dataset.voiceName) ||
      globalCache.selectedVoice ||
      "";
    // A voice that already failed here is treated as "not chosen": the
    // caller then falls through to selectBestVoiceForLang, which knows
    // to skip it as well.
    const found = ttsFindStoredVoice(voices, stored, getCurrentLangCode());
    if (found && !_failedVoices.has(found.name)) return found;

    return null;
  }

  // Speak a single short utterance now, picking the best available
  // voice for the current language (never the default mechanical one
  // when a suitable voice exists).
  //
  // langOverride: BCP-47 tag to speak in, for callers whose text is not
  // the reading page's -- the review session speaks each card in its
  // own term's language (see lute-review.js).  The page's own detection
  // is the fallback.
  function speakNow(cleanText, onStarted, langOverride, onEnded, forceLocal, retryDepth) {
    let activeVoice = forceLocal ? null : getSelectedVoice();
    const voices = window.speechSynthesis.getVoices();
    const detectedLang = langOverride || getCurrentLangCode();

    speakEpoch += 1;
    const epoch = speakEpoch;

    if (forceLocal) {
      activeVoice = selectLocalVoiceForLang(voices, detectedLang);
    } else if (!activeVoice && voices.length > 0) {
      activeVoice = selectBestVoiceForLang(voices, detectedLang);
    }

    const utterance = new SpeechSynthesisUtterance();
    utterance.text = cleanText;
    if (activeVoice) {
      utterance.voice = activeVoice;
      utterance.lang = activeVoice.lang;
    } else {
      utterance.lang = detectedLang;
    }
    utterance.rate = globalSpeed;

    // Tracked so the error path below can tell "never made a sound" from
    // "failed part way through, at least one sample was played".
    let started = false;

    // onStarted lets the caller (the sentence 🔊 button) drop its
    // "waiting" marker the moment sound actually begins, instead of
    // guessing how long the engine will take to warm up.
    utterance.onstart = function () {
      started = true;
      if (typeof onStarted === "function") {
        try { onStarted(); } catch (_) {}
      }
    };
    // onEnded lets a caller chain another utterance onto this one
    // (shadowing's "correct word, then what was heard").  A cancel()
    // also fires onend/onerror on the displaced utterance -- the epoch
    // check keeps a superseded speaker from chain-starting over the
    // speech that displaced it.
    let chainEnded = null;
    if (typeof onEnded === "function") {
      let ended = false;
      chainEnded = function () {
        if (ended || speakEpoch !== epoch) return;
        ended = true;
        try { onEnded(); } catch (_) {}
      };
      utterance.onend = chainEnded;
    }

    // A remote voice that fails is completely silent: Edge's
    // "Microsoft <name> Online (Natural)" answers speak() with
    // "synthesis-failed" and plays nothing, so a tap on a word or a
    // sentence 🔊 just looks broken.  Remember the failure, then retry
    // with the next best voice -- another natural voice if the device has
    // one, otherwise a local one.  A cancel/interrupt is not a failure
    // (it means something newer took over), so only real synthesis
    // errors are retried.
    const isLocalVoice = !!activeVoice && activeVoice.localService !== false;
    utterance.onerror = function (ev) {
      if (started || speakEpoch !== epoch) return;
      const err = ev && ev.error;
      const failed =
        err === "synthesis-failed" ||
        err === "network" ||
        err === "synthesis-unavailable" ||
        err === "voice-unavailable" ||
        err === "language-unavailable" ||
        err === "audio-busy";
      if (failed && !isLocalVoice) {
        markVoiceFailed(activeVoice && activeVoice.name);
        // Bumps speakEpoch, so the chained onEnded cannot double-fire.
        // depth keeps a device where every voice fails from looping.
        const next = typeof retryDepth === "number" ? retryDepth + 1 : 1;
        speakNow(cleanText, onStarted, langOverride, onEnded, next >= 2, next);
        return;
      }
      if (chainEnded) chainEnded();
    };

    // New speech takes over: pause any cached clip and drop any
    // pending backend play of an older hover -- exactly what cancel()
    // does to a displaced utterance.
    stopTtsCacheAudio();
    _ttsBackendEpoch += 1;
    try {
      window.speechSynthesis.cancel();
    } catch (_) {}

    setTimeout(function () {
      window.speechSynthesis.speak(utterance);
    }, 20);
  }

  // Lightweight speak used by hover / click pronunciation and the
  // auto-translation flow (single short utterance, no player state).
  // langOverride is optional -- see speakNow.
  function speakText(text, onStarted, langOverride) {
    let cleanText = text.replace(/[#＃]/g, "").trim();
    if (!cleanText) return;

    if ("speechSynthesis" in window) {
      const voices = window.speechSynthesis.getVoices();
      const hasVoice =
        getSelectedVoice() || (voices && voices.length > 0);

      if (!hasVoice) {
        if (_voicesWaitActive) {
          // A wait is already running; just remember the latest text
          // so the natural voice speaks the word the user most recently
          // hovered.
          _pendingWaitText = cleanText;
          _pendingWaitOnStarted = onStarted || null;
          return;
        }
        // getVoices() populates asynchronously in Chromium/Edge. If
        // they aren't ready yet, wait briefly for onvoiceschanged so
        // the very first pronunciation doesn't fall back to the
        // default mechanical voice.
        _voicesWaitActive = true;
        _pendingWaitText = cleanText;
        _pendingWaitOnStarted = onStarted || null;
        const start = Date.now();
        const timer = setInterval(function () {
          const v = window.speechSynthesis.getVoices();
          const ready = getSelectedVoice() || (v && v.length > 0);
          if (ready || Date.now() - start > 1500) {
            clearInterval(timer);
            _voicesWaitActive = false;
            const t = _pendingWaitText || cleanText;
            _pendingWaitText = null;
            const cb = _pendingWaitOnStarted;
            _pendingWaitOnStarted = null;
            speakNow(t, cb, langOverride);
          }
        }, 100);
        return;
      }

      speakNow(cleanText, onStarted, langOverride);
      return;
    }

    // --- Fallback: backend /tts/ endpoint ---
    const lang = langOverride || getCurrentLangCode();
    const url = "/tts/" + lang + "/" + encodeURIComponent(cleanText);
    const audio = new Audio(url);
    audio.playbackRate = globalSpeed;
    // This path really does wait on the network, so report both success
    // (playback began) and failure as "the wait is over" -- otherwise a
    // blocked autoplay would leave the caller's marker up until its own
    // safety timeout.
    let notified = false;
    const notifyStarted = function () {
      if (notified) return;
      notified = true;
      if (typeof onStarted === "function") {
        try { onStarted(); } catch (_) {}
      }
    };
    audio.addEventListener("playing", notifyStarted, { once: true });
    audio.play().catch(notifyStarted);
  }

  // ------------------------------------------------------------------
  // 2. Term pronunciation via the backend /tts/ cache
  // ------------------------------------------------------------------

  // SpeechSynthesis re-synthesizes on every call, and its remote voices
  // (Edge's "Microsoft ... Online (Natural)") pay a full round trip to
  // Microsoft's service each time -- the API offers no way to cache the
  // audio, so re-hovering a word waited all over again.
  //
  // The backend /tts/ endpoint answers with the very same Microsoft
  // neural voices ("Microsoft Xiaoxiao Online (Natural)" and edge-tts's
  // zh-CN-XiaoxiaoNeural are one and the same speaker) as
  // content-addressed mp3s with a server-side disk cache and immutable
  // HTTP cache headers.  Term-level speech (hover, click, review cards,
  // the form speaker button) therefore goes backend-first whenever the
  // browser voice can be mapped to its edge-tts twin: fetched once,
  // replayed from an in-memory blob cache afterwards.  Anything else --
  // a local system voice, voices not loaded yet, a backend failure --
  // falls back to the old SpeechSynthesis path, which stays the only
  // speaker for sentence-level audio (the 🔊 buttons and the player) so
  // the voice picker keeps working exactly as before.

  // url -> blob objectURL, insertion-ordered.  Word clips are a few KB,
  // so the cap bounds memory well under a MB per hundred words; the
  // URLs are never revoked -- eviction only happens long after the
  // clip has played, and revoking one under a still-playing audio
  // would cut it off.
  const _ttsBlobCache = new Map();
  const _TTS_BLOB_CACHE_MAX = 400;
  // url -> in-flight fetch promise, so the hover prefetch and the
  // debounced speak that follows share one request.
  const _ttsBlobFetches = new Map();
  // Mapped edge-tts voice candidates the backend rejected (a wrong
  // mapping, or a text that voice refuses): not offered again on this
  // page; a reload tries them afresh.
  const _ttsBadVoices = new Set();
  // Bumped whenever newer speech takes over (a newer hover, any
  // speakNow), so a slow backend answer for an older hover is dropped
  // instead of talking over what replaced it.
  let _ttsBackendEpoch = 0;
  // The clip now playing from the cache; new speech pauses it the way
  // speechSynthesis.cancel() displaces an utterance.
  let _ttsCacheAudio = null;

  function ttsBackendUrl(text, lang, voice) {
    let url = "/tts/" + lang + "/" + encodeURIComponent(text);
    if (voice) url += "?voice=" + encodeURIComponent(voice);
    return url;
  }

  function fetchTtsBlob(url) {
    const hit = _ttsBlobCache.get(url);
    if (hit) return Promise.resolve(hit);
    const inflight = _ttsBlobFetches.get(url);
    if (inflight) return inflight;
    const p = fetch(url)
      .then(function (r) {
        if (!r.ok) throw new Error("tts " + r.status);
        return r.blob();
      })
      .then(function (blob) {
        _ttsBlobFetches.delete(url);
        const objUrl = URL.createObjectURL(blob);
        _ttsBlobCache.set(url, objUrl);
        if (_ttsBlobCache.size > _TTS_BLOB_CACHE_MAX) {
          _ttsBlobCache.delete(_ttsBlobCache.keys().next().value);
        }
        return objUrl;
      })
      .catch(function () {
        // Network down, backend erroring (422 for a fragment it cannot
        // voice), whatever: null sends the caller to SpeechSynthesis,
        // and the next hover may try again.
        _ttsBlobFetches.delete(url);
        return null;
      });
    _ttsBlobFetches.set(url, p);
    return p;
  }

  function stopTtsCacheAudio() {
    if (!_ttsCacheAudio) return;
    const a = _ttsCacheAudio;
    _ttsCacheAudio = null;
    try { a.pause(); } catch (_) {}
  }

  function playTtsBlob(objUrl) {
    stopTtsCacheAudio();
    try { window.speechSynthesis.cancel(); } catch (_) {}
    const audio = new Audio(objUrl);
    audio.playbackRate = globalSpeed;
    _ttsCacheAudio = audio;
    audio.addEventListener("ended", function () {
      if (_ttsCacheAudio === audio) _ttsCacheAudio = null;
    });
    const p = audio.play();
    if (p && p.catch) {
      p.catch(function () {
        if (_ttsCacheAudio === audio) _ttsCacheAudio = null;
      });
    }
  }

  // The browser voice term speech would use right now: the explicitly
  // chosen one, or the same auto-pick speakNow applies.
  function currentBrowserVoice() {
    if (!("speechSynthesis" in window)) return null;
    const chosen = getSelectedVoice();
    if (chosen) return chosen;
    const voices = window.speechSynthesis.getVoices();
    if (!voices || voices.length === 0) return null;
    return selectBestVoiceForLang(voices, getCurrentLangCode());
  }

  // The edge-tts voice that is the same speaker as the browser voice
  // that would speak.  Edge's remote voices are named
  // "Microsoft <Person>[ <Extra>] Online (Natural)" and edge-tts's ids
  // are <locale>-<Person><Extra>Neural, so the person name plus the
  // voice's own locale rebuilds the id ("Ava Multilingual" collapses
  // to AvaMultilingual).  Only the Online (Natural) voices have a
  // twin -- local system voices keep the browser path.  Returns null
  // when the active voice has no twin, speaks another language, or was
  // already rejected by the backend on this page.
  function mappedEdgeVoice(langTag) {
    const v = currentBrowserVoice();
    if (!v || !v.name || !v.lang) return null;
    const want = (langTag || "").split("-")[0].toLowerCase();
    const got = (v.lang || "").split("-")[0].toLowerCase();
    if (!want || want !== got) return null;
    const m = v.name.match(/Microsoft\s+(.+?)\s+Online/i);
    if (!m) return null;
    const candidate = v.lang + "-" + m[1].replace(/\s+/g, "") + "Neural";
    if (!/^[A-Za-z]{2,4}-[A-Za-z0-9]{2,6}-[A-Za-z]+Neural$/.test(candidate)) {
      return null;
    }
    if (_ttsBadVoices.has(candidate)) return null;
    return candidate;
  }

  // Term-level pronunciation through the backend cache.  Returns true
  // when the backend is engaged (sound now or in flight); false sends
  // the caller to the browser voice as before.  Without SpeechSynthesis
  // at all (mobile webviews) the backend's default voice for the
  // language is used -- the same voice the old /tts/ fallback picked,
  // except cached now.
  function speakTermViaBackend(text, langOverride) {
    if (!window.fetch || typeof Audio === "undefined") return false;
    const lang = langOverride || getCurrentLangCode();
    let voice = "";
    if ("speechSynthesis" in window) {
      voice = mappedEdgeVoice(lang);
      if (!voice) return false;
    }
    const url = ttsBackendUrl(text, lang, voice);
    const epoch = ++_ttsBackendEpoch;
    // A hung backend must not swallow the hover: after 3s give up and
    // let SpeechSynthesis take it -- uncached, but it talks.
    const timeout = new Promise(function (resolve) {
      setTimeout(function () { resolve(null); }, 3000);
    });
    Promise.race([fetchTtsBlob(url), timeout]).then(function (objUrl) {
      if (epoch !== _ttsBackendEpoch) return;
      if (objUrl) {
        playTtsBlob(objUrl);
        return;
      }
      if (voice) {
        // The candidate was rejected, or the backend is too slow to be
        // worth waiting for again: either way the browser voice is the
        // safer bet on this page.
        _ttsBadVoices.add(voice);
      }
      if ("speechSynthesis" in window) {
        speakText(text, null, langOverride);
      }
    });
    return true;
  }

  // Fire the backend fetch the moment a word is hovered, so the
  // synthesis happens during the hover delay and the debounced speak
  // finds the clip already cached.  Skipped when the backend would not
  // be used anyway (hover pronunciation off, no edge-tts twin), and
  // debounced briefly so a mouse swept across the text does not
  // prefetch every word it crosses.
  let _hoverPrefetchTimer = null;
  function scheduleHoverPrefetch(rawText, readingText) {
    if (!SETTINGS.hoverPronunciation) return;
    if (!window.fetch || typeof Audio === "undefined") return;
    const term = (rawText || "").replace(/[#＃]/g, "").trim();
    const reading = (readingText || "").trim();
    const text =
      reading && /[\u3040-\u309F\u30A0-\u30FF]/.test(reading) ? reading : term;
    if (!text) return;
    const lang = getCurrentLangCode();
    let voice = "";
    if ("speechSynthesis" in window) {
      voice = mappedEdgeVoice(lang);
      if (!voice) return;
    }
    clearTimeout(_hoverPrefetchTimer);
    _hoverPrefetchTimer = setTimeout(function () {
      fetchTtsBlob(ttsBackendUrl(text, lang, voice));
    }, 60);
  }

  function cancelHoverPrefetch() {
    clearTimeout(_hoverPrefetchTimer);
  }

  // Speak a term, preferring its annotated reading (the pronunciation /
  // romanization field) when that reading exists and is kana.  Kanji
  // terms whose stored reading differs from what the TTS engine guesses
  // (names, irregular readings) then sound like the annotation instead
  // of the engine's guess.  Romaji / pinyin annotations are never
  // spoken -- engines read Latin text with the wrong sounds -- so those
  // fall back to the term text.
  function speakTermText(termText, readingText, langOverride) {
    const term = (termText || "").replace(/[#＃]/g, "").trim();
    const reading = (readingText || "").trim();
    if (reading && /[\u3040-\u309F\u30A0-\u30FF]/.test(reading)) {
      if (!speakTermViaBackend(reading, langOverride)) speakText(reading, null, langOverride);
    } else if (term) {
      if (!speakTermViaBackend(term, langOverride)) speakText(term, null, langOverride);
    }
  }

  // speakText with an onEnded callback: same voice selection and
  // fallback paths, but resolves when the sound finishes, not when it
  // starts.  Used to chain utterances (shadowing plays the correct
  // word and then the misheard one back to back).
  function speakTextEnd(text, onEnded, langOverride) {
    const cleanText = (text || "").replace(/[#＃]/g, "").trim();
    const done = function () {
      if (typeof onEnded === "function") {
        try { onEnded(); } catch (_) {}
      }
    };
    if (!cleanText) {
      done();
      return;
    }

    if ("speechSynthesis" in window) {
      const voices = window.speechSynthesis.getVoices();
      if (getSelectedVoice() || (voices && voices.length > 0)) {
        speakNow(cleanText, null, langOverride, done);
        return;
      }
      // Voices not loaded yet (first seconds in Chromium): speak via
      // the plain path and resolve on a rough timer rather than
      // stalling the sequence entirely.
      speakText(cleanText, null, langOverride);
      setTimeout(done, 1200);
      return;
    }

    // --- Fallback: backend /tts/ endpoint ---
    const lang = langOverride || getCurrentLangCode();
    const url = "/tts/" + lang + "/" + encodeURIComponent(cleanText);
    const audio = new Audio(url);
    audio.playbackRate = globalSpeed;
    let fired = false;
    const fire = function () {
      if (!fired) {
        fired = true;
        done();
      }
    };
    audio.addEventListener("ended", fire, { once: true });
    audio.addEventListener("error", fire, { once: true });
    audio.play().catch(fire);
  }

  // Speak a list of short items back to back: [{text, reading}, ...].
  // The kana-reading preference of speakTermText applies per item.
  // Any other speech started mid-sequence (the epoch bump in speakNow)
  // stops the rest of the queue -- the user took over.
  function speakSequence(items, langOverride) {
    const list = (items || [])
      .map(function (it) {
        const term = ((it && it.text) || "").replace(/[#＃]/g, "").trim();
        const reading = ((it && it.reading) || "").trim();
        if (reading && /[\u3040-\u309F\u30A0-\u30FF]/.test(reading)) {
          return reading;
        }
        return term;
      })
      .filter(function (t) {
        return t;
      });
    const step = function (i) {
      if (i >= list.length) return;
      speakTextEnd(list[i], function () {
        step(i + 1);
      }, langOverride);
    };
    step(0);
  }
  window.luteTtsSpeakSequence = speakSequence;

  // Annotated reading for a hovered word, when the term popup cache has
  // already fetched one -- lute-tooltip.js copies the reading out of
  // each cached popup (termpopup.html's hidden .termpopup-reading
  // holder) into this map, keyed by word id.
  function hoverReadingFor(wordSpan) {
    const wid = wordSpan.getAttribute("data-wid");
    if (!wid) return "";
    const readings = window.LUTE_TERM_READINGS || {};
    return readings[wid] || "";
  }

  // ------------------------------------------------------------------
  // 3. Text helpers
  // ------------------------------------------------------------------

  function cleanSentenceText(rawText) {
    return rawText
      .replace(/[#＃]/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  // Speakable text from a rendered reading-page node.
  //
  // lute-furigana.js wraps Han-script words in
  // <ruby>word<rt>reading</rt></ruby>, and an <rt> reading counts as
  // text: innerText / textContent of 普通's span is "普通ふつう".  A TTS
  // engine speaks that as the word twice -- once from the kanji, once
  // from the kana -- so anything that speaks DOM text (hover
  // pronunciation, the sentence 🔊, the player's cues) has to strip the
  // readings first.  The reading is pronunciation, not text to read;
  // the term's own reading is preferred anyway once the term popup has
  // cached it (see speakTermText).
  function ttsPlainText(node) {
    if (!node) return "";
    if (node.nodeType === 3) return node.textContent || "";
    const clone = node.cloneNode(true);
    if (clone.querySelectorAll) {
      clone.querySelectorAll("rt, rp").forEach(function (el) { el.remove(); });
    }
    return clone.textContent || "";
  }

  // What to speak for a hovered word span.  A split piece renders only
  // its own column's fragment; speak the whole word the pieces share
  // (data-tts-text, see textitem.html).  data-text is the parser's
  // canonical token and never carries a furigana reading, so it is
  // preferred over the rendered text -- falling back to raw innerText
  // here would speak "普通ふつう".  The rt-stripped rendered text is
  // the last resort for nodes without either attribute.  Shared with
  // the player subtitles (tts-player.js / media-player-base.js), which
  // resolve it on window at event time.
  function spokenTextForWordSpan(wordSpan) {
    return (
      wordSpan.getAttribute("data-tts-text") ||
      (wordSpan.dataset.text
        ? wordSpan.dataset.text.replace(/\u200B/g, "")
        : "") ||
      ttsPlainText(wordSpan)
    );
  }
  window.luteSpokenTextForWordSpan = spokenTextForWordSpan;

  // ------------------------------------------------------------------
  // 4. Sentence 🔊 button injection
  // ------------------------------------------------------------------

  function injectSentencePlayButtons() {
    const textDiv = document.getElementById("thetext");
    if (!textDiv) return;

    function hasReadableText(el) {
      // Skip "ghost" sentences that carry nothing speakable: blank text
      // (whitespace / zero-width spaces) or only punctuation (e.g. a
      // lone closing quote "」" split into its own sentence). Injecting
      // a play button there renders a stray 🔊 with no speech to read.
      const text = cleanSentenceText(el.textContent || "");
      if (text === "") return false;
      // Require at least one letter or digit; purely-punctuation
      // sentences ("」", "。」"...) have no readable content.
      return /[\p{L}\p{N}]/u.test(text);
    }

    const sentences = textDiv.querySelectorAll(".textsentence");
    if (sentences.length > 0) {
      sentences.forEach(function (s) {
        if (s.querySelector(".lute-sentence-play-btn")) return;
        if (!hasReadableText(s)) return;
        const btn = document.createElement("span");
        btn.className = "lute-sentence-play-btn";
        btn.innerText = "🔊";
        btn.style.cssText =
          "display:inline-block;cursor:pointer;margin-right:4px;" +
          "user-select:none;font-size:14px;vertical-align:middle;";
        if (s.firstChild) s.insertBefore(btn, s.firstChild);
        else s.appendChild(btn);
      });
      return;
    }

    const rows = textDiv.querySelectorAll(".textrow, p");
    rows.forEach(function (row) {
      if (row.querySelector(".lute-sentence-play-btn")) return;
      if (!hasReadableText(row)) return;
      const btn = document.createElement("span");
      btn.className = "lute-sentence-play-btn";
      btn.innerText = "🔊";
      btn.style.cssText =
        "display:inline-block;cursor:pointer;margin-right:8px;" +
        "user-select:none;font-size:14px;";
      if (row.firstChild) row.insertBefore(btn, row.firstChild);
      else row.appendChild(btn);
    });
  }

  // ------------------------------------------------------------------
  // 5. Event delegation (word hover + sentence click)
  // ------------------------------------------------------------------

  // Shared hover-pronunciation engine. Debounced: the word must stay
  // hovered for SETTINGS.hoverDelay ms before it is spoken, and the
  // isPlaying predicate is checked at hover time and again when the
  // delay fires, so a hover that runs into playback never interrupts
  // the media audio.  readingText (optional) is the term's annotated
  // reading; when present and kana it is spoken instead of the word
  // text (see speakTermText).  Also used by the media-player subtitles
  // (youtube-player.js / bilibili-player.js), which resolve the
  // window.* globals at event time because tts.js loads after them.
  function luteHoverSpeakStart(rawText, isPlayingFn, readingText) {
    if (!SETTINGS.hoverPronunciation) return;
    if (isPlayingFn && isPlayingFn()) return;
    const cleanText = (rawText || "").replace(/[#＃]/g, "").trim();
    if (!cleanText) return;
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(function () {
      if (!(isPlayingFn && isPlayingFn())) {
        speakTermText(cleanText, readingText);
      }
    }, SETTINGS.hoverDelay);
  }

  function luteHoverSpeakCancel() {
    clearTimeout(hoverTimer);
  }

  window.luteHoverSpeakStart = luteHoverSpeakStart;
  window.luteHoverSpeakCancel = luteHoverSpeakCancel;
  // Direct one-shot speak for UI buttons, e.g. the term form's
  // pronunciation-row speaker button (click once = one utterance).
  window.luteTtsSpeak = speakText;
  // Term-aware speak for UI buttons and card playback: prefers the
  // term's kana annotated reading when one exists (see speakTermText).
  window.luteTtsSpeakTerm = speakTermText;
  // The setting reader, for pages that carry their own TTS switch: the
  // review session reads review_speak_cards with it (lute-review.js),
  // so "0"/"false"/absent are parsed exactly as they are here.
  window.luteTtsSetting = getSetting;

  // Tap markers (press / ack / pending) are implemented once in lute.js
  // and published as window.luteTapFeedback -- see the "Tap feedback"
  // section there.  Resolved at call time because tts.js is also loaded
  // on the term form page, which has no lute.js.  When it is missing the
  // button simply behaves as before.
  function tapFx() {
    return window.luteTapFeedback || null;
  }

  function setupEventDelegation() {
    const textDiv = document.getElementById("thetext");
    if (!textDiv || textDiv.dataset.delegated === "true") return;
    textDiv.dataset.delegated = "true";

    // Sentence 🔊 press feedback.  Pointer events cover mouse and touch
    // with one handler, so the button answers the finger the instant it
    // lands -- before the click, which the browser only fires on release.
    textDiv.addEventListener("pointerdown", function (e) {
      const btn = e.target.closest(".lute-sentence-play-btn");
      const fx = tapFx();
      if (btn && fx) fx.press(btn);
    });

    textDiv.addEventListener("pointerup", function (e) {
      const btn = e.target.closest(".lute-sentence-play-btn");
      const fx = tapFx();
      if (btn && fx) fx.release(btn);
    });

    // Touch stolen (scroll started, browser gesture, notification pulled
    // down): no click is coming, so don't leave the button marked.
    textDiv.addEventListener("pointercancel", function (e) {
      const btn = e.target.closest(".lute-sentence-play-btn");
      const fx = tapFx();
      if (btn && fx) fx.clear(btn);
    });

    // Word hover pronunciation
    textDiv.addEventListener("mouseover", function (e) {
      const wordSpan = e.target.closest("span.word, span[id^=\"w\"]");
      if (!wordSpan) return;
      const rawText = spokenTextForWordSpan(wordSpan);
      const readingText = hoverReadingFor(wordSpan);
      luteHoverSpeakStart(rawText, function () { return ttsPlaying; }, readingText);
      scheduleHoverPrefetch(rawText, readingText);
    });

    textDiv.addEventListener("mouseout", function (e) {
      const wordSpan = e.target.closest("span.word, span[id^=\"w\"]");
      if (wordSpan && !wordSpan.contains(e.relatedTarget)) {
        luteHoverSpeakCancel();
        cancelHoverPrefetch();
      }
    });

    // Sentence play button click -- plays just that sentence through
    // the lightweight speakText() (does not engage the full player).
    textDiv.addEventListener("click", function (e) {
      const btn = e.target.closest(".lute-sentence-play-btn");
      if (!btn) return;
      e.stopPropagation();

      const row = btn.parentElement;
      if (!row) return;

      const tempRow = row.cloneNode(true);
      const icon = tempRow.querySelector(".lute-sentence-play-btn");
      if (icon) icon.remove();

      // ttsPlainText drops the furigana <rt> readings, which would
      // otherwise be spoken right after each word ("普通ふつう").
      const cleanSentence = cleanSentenceText(
        ttsPlainText(tempRow)
      );
      if (cleanSentence) {
        // Stop the full player if it's running so the hover/sentence
        // utterance isn't drowned out by the active cue.
        if (ttsPlaying) ttsStop();

        const fx = tapFx();
        if (!fx) {
          speakText(cleanSentence);
          return;
        }

        // Acknowledge the click right away, then keep the 🔊 marked while
        // the speech engine warms up / the /tts/ endpoint is fetched --
        // exactly the "did it register?" gap this button used to have.
        // The marker is dropped the moment sound actually starts (see
        // the onStarted hook in speakText), or by lute.js's own safety
        // timeout if that never fires.
        fx.ack(btn, false);
        fx.pending(btn, fx.ACK_MS);
        speakText(cleanSentence, function () { fx.clear(btn); });
      }
    });
  }

