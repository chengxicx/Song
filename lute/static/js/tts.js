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
 *     event delegation.
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

  function selectBestVoiceForLang(voices, targetLang) {
    if (!voices || voices.length === 0) return null;
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
    const keywords = ["online", "natural", "neural", "google", "microsoft"];
    for (const kw of keywords) {
      const found = matched.find(function (v) {
        return v.name.toLowerCase().includes(kw);
      });
      if (found) return found;
    }
    return matched[0];
  }

  function getSelectedVoice() {
    if (!("speechSynthesis" in window)) return null;

    const voiceSelect = document.getElementById("tts-voice-btn");
    if (voiceSelect && voiceSelect.dataset.voiceName) {
      const voices = window.speechSynthesis.getVoices();
      const found = voices.find(function (v) {
        return v.name === voiceSelect.dataset.voiceName;
      });
      if (found) return found;
    }

    if (globalCache.selectedVoice) {
      const voices = window.speechSynthesis.getVoices();
      const found = voices.find(function (v) {
        return v.name === globalCache.selectedVoice;
      });
      if (found) return found;
    }

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
  function speakNow(cleanText, onStarted, langOverride, onEnded) {
    let activeVoice = getSelectedVoice();
    const voices = window.speechSynthesis.getVoices();
    const detectedLang = langOverride || getCurrentLangCode();

    speakEpoch += 1;
    const epoch = speakEpoch;

    if (!activeVoice && voices.length > 0) {
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

    // onStarted lets the caller (the sentence 🔊 button) drop its
    // "waiting" marker the moment sound actually begins, instead of
    // guessing how long the engine will take to warm up.
    if (typeof onStarted === "function") {
      utterance.onstart = function () {
        try { onStarted(); } catch (_) {}
      };
    }
    // onEnded lets a caller chain another utterance onto this one
    // (shadowing's "correct word, then what was heard").  A cancel()
    // also fires onend/onerror on the displaced utterance -- the epoch
    // check keeps a superseded speaker from chain-starting over the
    // speech that displaced it.
    if (typeof onEnded === "function") {
      let ended = false;
      const fire = function () {
        if (ended || speakEpoch !== epoch) return;
        ended = true;
        try { onEnded(); } catch (_) {}
      };
      utterance.onend = fire;
      utterance.onerror = fire;
    }

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
      speakText(reading, null, langOverride);
    } else if (term) {
      speakText(term, null, langOverride);
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
      // A split piece renders only its own column's fragment; speak the
      // whole word the pieces share (data-tts-text, see textitem.html).
      const spoken =
        wordSpan.getAttribute("data-tts-text") ||
        wordSpan.innerText ||
        wordSpan.textContent ||
        "";
      luteHoverSpeakStart(
        spoken,
        function () { return ttsPlaying; },
        hoverReadingFor(wordSpan)
      );
    });

    textDiv.addEventListener("mouseout", function (e) {
      const wordSpan = e.target.closest("span.word, span[id^=\"w\"]");
      if (wordSpan && !wordSpan.contains(e.relatedTarget)) {
        luteHoverSpeakCancel();
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

      const cleanSentence = cleanSentenceText(
        tempRow.innerText || tempRow.textContent || ""
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

