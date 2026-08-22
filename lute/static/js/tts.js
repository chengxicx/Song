/**
 * Edge-TTS + SpeechSynthesis voice synthesis and Google auto-translation
 * integration for the Lute reading page.
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
 *   - Word hover pronunciation (200 ms delay) via event delegation.
 *   - Auto-translation: when a term edit form opens with an empty
 *     #translation, the translation is fetched and auto-filled.
 */
(function () {
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

  // Read TL from: navigator.language -> shared cache -> default
  globalCache.tl = navigator.language || globalCache.tl || "zh-CN";
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

  // ------------------------------------------------------------------
  // 1. Speech synthesis (primary: browser SpeechSynthesis,
  //    fallback: backend /tts/ endpoint)
  // ------------------------------------------------------------------

  let globalSpeed = 1.0;
  let hoverTimer = null;
  // Guards the "wait for voices to load" retry so it only runs once.
  let _voicesWaitActive = false;
  let _pendingWaitText = null;

  function selectBestVoiceForLang(voices, targetLang) {
    if (!voices || voices.length === 0) return null;
    let matched = voices.filter(function (v) {
      return v.lang.toLowerCase().startsWith(targetLang);
    });
    if (matched.length === 0 && targetLang === "sa") {
      matched = voices.filter(function (v) {
        return v.lang.toLowerCase().startsWith("hi");
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
  function speakNow(cleanText) {
    let activeVoice = getSelectedVoice();
    const voices = window.speechSynthesis.getVoices();
    const detectedLang = getCurrentLangCode();

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

    try {
      window.speechSynthesis.cancel();
    } catch (_) {}

    setTimeout(function () {
      window.speechSynthesis.speak(utterance);
    }, 20);
  }

  // Lightweight speak used by hover / click pronunciation and the
  // auto-translation flow (single short utterance, no player state).
  function speakText(text) {
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
          return;
        }
        // getVoices() populates asynchronously in Chromium/Edge. If
        // they aren't ready yet, wait briefly for onvoiceschanged so
        // the very first pronunciation doesn't fall back to the
        // default mechanical voice.
        _voicesWaitActive = true;
        _pendingWaitText = cleanText;
        const start = Date.now();
        const timer = setInterval(function () {
          const v = window.speechSynthesis.getVoices();
          const ready = getSelectedVoice() || (v && v.length > 0);
          if (ready || Date.now() - start > 1500) {
            clearInterval(timer);
            _voicesWaitActive = false;
            const t = _pendingWaitText || cleanText;
            _pendingWaitText = null;
            speakNow(t);
          }
        }, 100);
        return;
      }

      speakNow(cleanText);
      return;
    }

    // --- Fallback: backend /tts/ endpoint ---
    const lang = getCurrentLangCode();
    const url = "/tts/" + lang + "/" + encodeURIComponent(cleanText);
    const audio = new Audio(url);
    audio.playbackRate = globalSpeed;
    audio.play().catch(function () {});
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

    const sentences = textDiv.querySelectorAll(".textsentence");
    if (sentences.length > 0) {
      sentences.forEach(function (s) {
        if (s.querySelector(".lute-sentence-play-btn")) return;
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

  function setupEventDelegation() {
    const textDiv = document.getElementById("thetext");
    if (!textDiv || textDiv.dataset.delegated === "true") return;
    textDiv.dataset.delegated = "true";

    // Word hover pronunciation
    if (SETTINGS.hoverPronunciation) {
      textDiv.addEventListener("mouseover", function (e) {
        const wordSpan = e.target.closest("span.word, span[id^=\"w\"]");
        if (!wordSpan) return;
        const text =
          wordSpan.innerText || wordSpan.textContent || "";
        const cleanText = text.replace(/[#＃]/g, "").trim();
        if (!cleanText) return;
        clearTimeout(hoverTimer);
        hoverTimer = setTimeout(function () {
          // Don't fire hover pronunciation while the TTS player is
          // playing a full-text stream -- it would interrupt it.
          if (!ttsPlaying) {
            speakText(cleanText);
          }
        }, 200);
      });

      textDiv.addEventListener("mouseout", function (e) {
        const wordSpan = e.target.closest("span.word, span[id^=\"w\"]");
        if (wordSpan && !wordSpan.contains(e.relatedTarget)) {
          clearTimeout(hoverTimer);
        }
      });
    }

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
        speakText(cleanSentence);
      }
    });
  }

  // ------------------------------------------------------------------
  // 6. Auto-translate (term form observer)
  // ------------------------------------------------------------------

  let _isFilling = false;

  function forceFill(el, text) {
    if (!el) return;
    _isFilling = true;
    try {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype,
        "value"
      ).set;
      setter.call(el, text);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    } catch (_) {
      el.value = text;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }
    setTimeout(function () { _isFilling = false; }, 50);
  }

  // ------------------------------------------------------------------
  // 6b. Client-side translation with multiple API fallbacks
  // ------------------------------------------------------------------

  function withTimeout(promise, ms) {
    var timer = new Promise(function (_, reject) {
      setTimeout(function () { reject(new Error("timeout")); }, ms);
    });
    return Promise.race([promise, timer]).catch(function () { return ""; });
  }

  function translateViaGoogle(sl, tl, text) {
    var url =
      "https://translate.googleapis.com/translate_a/single" +
      "?client=gtx&sl=" + sl + "&tl=" + tl + "&dt=t&q=" +
      encodeURIComponent(text);
    return withTimeout(
      fetch(url).then(function (r) { return r.json(); }),
      4000
    ).then(function (data) {
      if (data && data[0] && data[0][0]) {
        var result = data[0][0][0] || "";
        if (result && result.toLowerCase() === text.toLowerCase()) return "";
        return result;
      }
      return "";
    }).catch(function () { return ""; });
  }

  function translateViaMyMemory(sl, tl, text) {
    var langpair = sl + "|" + tl;
    var url =
      "https://api.mymemory.translated.net/get?q=" +
      encodeURIComponent(text) +
      "&langpair=" + encodeURIComponent(langpair);
    return withTimeout(
      fetch(url).then(function (r) { return r.json(); }),
      8000
    ).then(function (data) {
      if (data && data.responseData && data.responseData.translatedText) {
        var result = data.responseData.translatedText;
        if (result && result.toLowerCase() === text.toLowerCase()) return "";
        return result;
      }
      return "";
    }).catch(function () { return ""; });
  }

  function translateViaBackend(sl, tl, text) {
    var url =
      "/api/translate/" + sl + "/" + tl + "/" + encodeURIComponent(text);
    return withTimeout(
      fetch(url).then(function (r) { return r.json(); }),
      8000
    ).then(function (data) {
      if (data && data.translation) return data.translation;
      return "";
    }).catch(function () { return ""; });
  }

  function translateText(sl, tl, text) {
    return translateViaGoogle(sl, tl, text).then(function (result) {
      if (result) return result;
      return translateViaMyMemory(sl, tl, text);
    }).then(function (result) {
      if (result) return result;
      return translateViaBackend(sl, tl, text);
    }).catch(function () { return ""; });
  }

  // Debounced form checker – only runs when the form is actually visible.
  let _formCheckTimer = null;
  let _lastFormState = "";

  function processTranslationFlow() {
    // Quick check: is there a term form visible?
    const docs = [document];
    try {
      if (window.top && window.top.document && window.top.document !== document) {
        docs.push(window.top.document);
      }
    } catch (_) {}

    for (const doc of docs) {
      const textInput =
        doc.getElementById("text") ||
        doc.querySelector('input[name="text"]');
      const translationInput =
        doc.getElementById("translation") ||
        doc.querySelector('textarea[name="translation"]');

      if (!textInput || !textInput.value) continue;

      const word = textInput.value.trim();

      // Quick fingerprint to skip if nothing changed
      const formState = word + "|" + (translationInput ? translationInput.value : "");
      if (formState === _lastFormState) return;
      _lastFormState = formState;

      // Speak the word (only from the top/main frame)
      var isTopFrame = (window === window.top);
      if (isTopFrame && SETTINGS.clickPronunciation && globalCache.lastWord !== word) {
        speakText(word);
        globalCache.lastWord = word;
      }

      if (
        translationInput &&
        (!translationInput.value ||
          translationInput.value === "Translating...")
      ) {
        if (_isFilling) continue;

        if (globalCache.trans[word]) {
          forceFill(translationInput, globalCache.trans[word]);
        } else {
          forceFill(translationInput, "Translating...");
          const sl = getCurrentLangCode();
          const tl = TL;

          translateText(sl, tl, word)
            .then(function (translated) {
              if (translated) {
                globalCache.trans[word] = translated;
                let freshTarget =
                  doc.getElementById("translation") ||
                  doc.querySelector('textarea[name="translation"]');
                if (!freshTarget) {
                  freshTarget =
                    document.getElementById("translation") ||
                    document.querySelector('textarea[name="translation"]');
                }
                forceFill(freshTarget, translated);
              } else {
                let freshTarget =
                  doc.getElementById("translation") ||
                  doc.querySelector('textarea[name="translation"]');
                if (!freshTarget) {
                  freshTarget =
                    document.getElementById("translation") ||
                    document.querySelector('textarea[name="translation"]');
                }
                if (freshTarget && freshTarget.value === "Translating...") {
                  forceFill(freshTarget, "");
                }
              }
            })
            .catch(function () {
              let freshTarget =
                doc.getElementById("translation") ||
                doc.querySelector('textarea[name="translation"]');
              if (freshTarget && freshTarget.value === "Translating...") {
                forceFill(freshTarget, "");
              }
            });
        }
      }
      return;
    }
  }

  // Debounced form check – avoids excessive scanning.
  function debouncedFormCheck() {
    if (_formCheckTimer) clearTimeout(_formCheckTimer);
    _formCheckTimer = setTimeout(function () {
      _formCheckTimer = null;
      processTranslationFlow();
    }, 150);
  }

  /* ================================================================
     7. TTS PLAYER (mirrors the YouTube / MP3 player UI and behaviour)
     ================================================================ */

  // Player state (mirrors youtube-player.js).
  let ttsContainer = null;
  let ttsPlayBtn = null;
  let ttsPrevCueBtn = null;
  let ttsNextCueBtn = null;
  let ttsTimeline = null;
  let ttsCurTimeEl = null;
  let ttsDurationEl = null;
  let ttsRateInd = null;
  let ttsLoopBtn = null;
  let ttsAutoPauseBtn = null;
  let ttsTranscriptBtn = null;
  let ttsTranscript = null;
  let ttsTranscriptList = null;
  let ttsSubtitle = null;
  let ttsVoiceBtn = null;
  let ttsVoiceDropdown = null;
  let ttsVoiceLabel = null;

  // Cue data: built from #thetext sentences.
  //   { text, html, start, end, duration, actualDuration }
  // `start` / `end` are virtual times in seconds, accumulated across
  // all cues. `duration` is the initial estimate (character-based),
  // `actualDuration` is filled in once the cue has played through.
  let ttsCues = [];
  let ttsCueIndex = -1;
  let ttsPlaying = false;
  let ttsPaused = false;
  let ttsLoop = false;
  let ttsAutoPause = false;
  let ttsRate = 1.0;
  let ttsDragging = false;
  let ttsTotalDuration = 0;
  let ttsVirtualTime = 0;          // current virtual playhead (seconds)
  let ttsCueStartedAt = 0;         // performance.now() when current cue started
  let ttsCurrentUtterance = null;
  let ttsPollTimer = null;
  // Voice-list load detection. On mobile browsers getVoices() can stay
  // empty for a long time (chromium populates it lazily), and
  // onvoiceschanged may never fire. Track the empty->non-empty
  // transition so we rebuild the dropdown the moment voices arrive.
  let _voicesEmpty = true;
  let _voicePollTimer = null;
  // How long to keep showing "Loading voices\u2026" on mobile before we
  // assume this device simply has no system TTS voices. On many mobile
  // browsers getVoices() stays empty forever (and onvoiceschanged never
  // fires), so without this the gear would spin forever.
  const VOICE_TIMEOUT_MS = 4000;
  // Timestamp of when we first began waiting for voices; null until the
  // placeholder is first shown. Reset when voices actually arrive.
  let _voiceWaitStart = null;
  // Pseudo voice offered when getVoices() never populates (Android
  // Edge/Chromium bug: speak() works fine with the system default voice
  // while getVoices() stays empty forever). Selecting it means "don't
  // set utterance.voice", which is exactly how working playback already
  // sounds on those browsers.
  const DEVICE_DEFAULT_VOICE = {
    name: "Device default voice",
    lang: "",
    synthetic: true,
  };
  let ttsMarqueeOverflow = 0;
  let ttsIsRtl = false;
  // HTML last injected into the subtitle. Used to skip redundant
  // rebuilds when the same cue repeats (e.g. single-sentence loop), so
  // that a hovered word and its open tooltip are left untouched.
  let ttsLastSubtitleHtml = null;

  // Estimated seconds-per-character for duration guessing, split by
  // script class. The numbers are conservative so the timeline doesn't
  // jump backwards once real timing arrives.
  const CHAR_RATE_CJK = 0.18;   // ~360 chars/min for Japanese/Korean/Chinese
  const CHAR_RATE_OTHER = 0.07; // ~85 wpm for latin scripts (avg 5 chars/word)

  function ttsFmtTime(secs) {
    if (!isFinite(secs) || secs < 0) secs = 0;
    secs = Math.floor(secs);
    var h = Math.floor(secs / 3600);
    var m = Math.floor((secs % 3600) / 60);
    var s = secs % 60;
    var mm = m < 10 ? "0" + m : "" + m;
    var ss = s < 10 ? "0" + s : "" + s;
    return h > 0 ? h + ":" + mm + ":" + ss : m + ":" + ss;
  }

  function ttsEstimateDuration(text, rate) {
    if (!text) return 0.5;
    const cjk = (text.match(/[\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]/g) || []).length;
    const other = text.length - cjk;
    // The SpeechSynthesis rate scales roughly linearly; clamp so very
    // slow rates don't blow the timeline up to hours.
    const effRate = Math.max(0.5, rate || 1.0);
    return Math.max(0.5, (cjk * CHAR_RATE_CJK + other * CHAR_RATE_OTHER) / effRate);
  }

  // Build the cue list from the .textsentence elements in #thetext.
  // Each cue's `html` is the sentence's innerHTML (textitem spans
  // included), so the scrolling subtitle reuses the reading-page
  // tokenization and click-to-lookup behaviour -- no extra backend
  // request needed.
  function ttsBuildCues() {
    ttsCues = [];
    ttsTotalDuration = 0;
    ttsCueIndex = -1;
    ttsVirtualTime = 0;

    const textDiv = document.getElementById("thetext");
    if (!textDiv) return;

    const sentences = textDiv.querySelectorAll(".textsentence");
    let acc = 0;
    sentences.forEach(function (s) {
      // Clone so we can strip the 🔊 button without mutating the page.
      const clone = s.cloneNode(true);
      clone.querySelectorAll(".lute-sentence-play-btn").forEach(function (b) {
        b.remove();
      });
      const text = cleanSentenceText(
        clone.innerText || clone.textContent || ""
      );
      if (!text) return;
      const html = clone.innerHTML;
      const dur = ttsEstimateDuration(text, ttsRate);
      ttsCues.push({
        text: text,
        html: html,
        start: acc,
        end: acc + dur,
        duration: dur,
        actualDuration: null,
      });
      acc += dur;
    });

    ttsTotalDuration = acc;
    if (ttsDurationEl) ttsDurationEl.textContent = ttsFmtTime(ttsTotalDuration);
    if (ttsTimeline) ttsTimeline.max = ttsTotalDuration || 1000;
    ttsBuildTranscript();
  }

  // Recompute cue start/end times after a rate change or after a cue
  // learns its real duration. Keeps the timeline consistent without
  // rebuilding the cue list (which would wipe actualDuration).
  function ttsRecomputeTimeline() {
    let acc = 0;
    ttsCues.forEach(function (c) {
      c.start = acc;
      const dur = c.actualDuration != null ? c.actualDuration : c.duration;
      c.end = acc + dur;
      acc = c.end;
    });
    ttsTotalDuration = acc;
    if (ttsDurationEl) ttsDurationEl.textContent = ttsFmtTime(ttsTotalDuration);
    if (ttsTimeline) ttsTimeline.max = ttsTotalDuration || 1000;
  }

  /* ------------------------------------------------------------------
   * Playback engine (SpeechSynthesis with edge-tts fallback)
   * ------------------------------------------------------------------ */

  function ttsCancelSpeech() {
    if ("speechSynthesis" in window) {
      try { window.speechSynthesis.cancel(); } catch (_) {}
    }
    ttsCurrentUtterance = null;
  }

  // Play a single cue through SpeechSynthesis. Sets up boundary / end
  // handlers that drive the virtual playhead and advance to the next
  // cue when finished.
  function ttsPlayCue(idx) {
    if (idx < 0 || idx >= ttsCues.length) {
      ttsStop();
      return;
    }
    const cue = ttsCues[idx];
    if (!cue) return;

    ttsCueIndex = idx;
    ttsActivateCue(idx);

    const cleanText = cue.text.replace(/[#＃]/g, "").trim();
    if (!cleanText) {
      // Skip empty cue (shouldn't happen, but be safe) and advance.
      ttsAdvance();
      return;
    }

    ttsCueStartedAt = performance.now();
    ttsVirtualTime = cue.start;

    if ("speechSynthesis" in window) {
      ttsCancelSpeech();
      const utterance = new SpeechSynthesisUtterance();
      utterance.text = cleanText;

      let activeVoice = getSelectedVoice();
      const voices = window.speechSynthesis.getVoices();
      const detectedLang = getCurrentLangCode();
      if (!activeVoice && voices.length > 0) {
        activeVoice = selectBestVoiceForLang(voices, detectedLang);
      }
      if (activeVoice) {
        utterance.voice = activeVoice;
        utterance.lang = activeVoice.lang;
      } else {
        utterance.lang = detectedLang;
      }
      utterance.rate = ttsRate;

      // Play is a user gesture -- on mobile this is often what finally
      // makes getVoices() populate. Re-check right away so the voice
      // dropdown updates instead of staying on "Loading voices…".
      ttsPollVoices();

      // boundary events give us word-level progress within a cue.
      // Not all browsers fire them (Chrome does, Safari partial, FF
      // not at all), so we also poll elapsed time as a fallback.
      utterance.onboundary = function (ev) {
        if (ttsCueIndex !== idx) return; // stale event
        if (ev.name === "word" || ev.name === "sentence") {
          // Use the charIndex to estimate progress through the cue.
          const total = cleanText.length || 1;
          const frac = Math.min(1, Math.max(0, (ev.charIndex || 0) / total));
          const cueDur = cue.actualDuration != null
            ? cue.actualDuration
            : cue.duration;
          ttsVirtualTime = cue.start + frac * cueDur;
        }
      };
      utterance.onend = function () {
        if (ttsCueIndex !== idx) return; // stale event (user jumped)
        // Record the real duration so the timeline is accurate on
        // subsequent plays and after rate changes.
        const elapsed = (performance.now() - ttsCueStartedAt) / 1000;
        if (elapsed > 0.3 && isFinite(elapsed)) {
          cue.actualDuration = elapsed;
          ttsRecomputeTimeline();
        }
        ttsVirtualTime = cue.end;
        ttsAdvance();
      };
      utterance.onerror = function () {
        if (ttsCueIndex !== idx) return;
        ttsAdvance();
      };

      ttsCurrentUtterance = utterance;
      // Small delay (matches the previous module) avoids a Chrome bug
      // where rapid cancel() + speak() can leave synthesis stuck.
      setTimeout(function () {
        if (ttsCueIndex !== idx) return; // user jumped while we waited
        try { window.speechSynthesis.speak(utterance); }
        catch (e) { ttsAdvance(); }
      }, 20);
      return;
    }

    // --- Fallback: backend /tts/ endpoint ---
    const lang = getCurrentLangCode();
    const url = "/tts/" + lang + "/" + encodeURIComponent(cleanText);
    const audio = new Audio(url);
    audio.playbackRate = ttsRate;
    ttsCurrentUtterance = {
      _audio: audio,
      stop: function () {
        audio.pause();
        audio.removeAttribute("src");
        audio.load();
      },
    };
    audio.addEventListener("timeupdate", function () {
      if (ttsCueIndex !== idx) return;
      ttsVirtualTime = cue.start + (audio.currentTime || 0);
    });
    audio.addEventListener("ended", function () {
      if (ttsCueIndex !== idx) return;
      cue.actualDuration = audio.duration || cue.duration;
      ttsRecomputeTimeline();
      ttsVirtualTime = cue.end;
      ttsAdvance();
    });
    audio.addEventListener("error", function () {
      if (ttsCueIndex !== idx) return;
      ttsAdvance();
    });
    audio.play().catch(function () { ttsAdvance(); });
  }

  // Decide what to play after the current cue ends.
  function ttsAdvance() {
    if (!ttsPlaying) return;
    const curIdx = ttsCueIndex;
    const curCue = ttsCues[curIdx];

    // Loop takes precedence over auto-pause (same as YouTube player).
    if (ttsLoop && curCue) {
      ttsPlayCue(curIdx);
      return;
    }
    if (ttsAutoPause) {
      // Pause at the end of the current sentence and seek back to its
      // start so pressing play replays it.
      if (curCue) {
        ttsVirtualTime = curCue.start;
      }
      ttsPaused = true;
      ttsPlaying = false;
      ttsUpdatePlayBtn();
      if (ttsTimeline && curCue) ttsTimeline.value = curCue.start;
      if (ttsCurTimeEl) ttsCurTimeEl.textContent = ttsFmtTime(ttsVirtualTime);
      return;
    }
    const next = curIdx + 1;
    if (next >= ttsCues.length) {
      // End of stream.
      ttsStop();
      return;
    }
    ttsPlayCue(next);
  }

  function ttsTogglePlay() {
    if (ttsPlaying && !ttsPaused) {
      // Pause: cancel speech but remember position.
      ttsPaused = true;
      ttsPlaying = false;
      ttsCancelSpeech();
      ttsUpdatePlayBtn();
      return;
    }
    if (ttsPaused) {
      // Resume from the start of the current cue (SpeechSynthesis
      // doesn't support resume-from-offset, so replay the cue).
      ttsPaused = false;
      ttsPlaying = true;
      ttsUpdatePlayBtn();
      const idx = ttsCueIndex >= 0 ? ttsCueIndex : 0;
      ttsPlayCue(idx);
      return;
    }
    // Fresh start.
    if (ttsCues.length === 0) ttsBuildCues();
    if (ttsCues.length === 0) return;
    ttsPlaying = true;
    ttsPaused = false;
    ttsUpdatePlayBtn();
    // If the playhead is at the end, restart from the beginning.
    let startIdx = ttsCueIndex >= 0 ? ttsCueIndex : 0;
    if (ttsVirtualTime >= ttsTotalDuration - 0.1) startIdx = 0;
    ttsPlayCue(startIdx);
  }

  function ttsStop() {
    ttsCancelSpeech();
    ttsPlaying = false;
    ttsPaused = false;
    ttsCueIndex = -1;
    ttsVirtualTime = 0;
    if (ttsTimeline) ttsTimeline.value = 0;
    if (ttsCurTimeEl) ttsCurTimeEl.textContent = ttsFmtTime(0);
    ttsDeactivateCue();
    ttsUpdatePlayBtn();
  }

  function ttsUpdatePlayBtn() {
    if (!ttsPlayBtn) return;
    ttsPlayBtn.classList.toggle("playing", ttsPlaying && !ttsPaused);
  }

  function ttsJumpCue(delta) {
    if (!ttsCues.length) return;
    let target = ttsCueIndex < 0 ? 0 : ttsCueIndex + delta;
    if (target < 0) target = 0;
    if (target >= ttsCues.length) target = ttsCues.length - 1;
    ttsSeekToCue(target, false);
  }

  // Jump the playhead to a cue start. If `autoplay` is true (clicked
  // from the transcript), playback resumes; otherwise the current
  // play/pause state is preserved.
  function ttsSeekToCue(i, autoplay) {
    if (!ttsCues[i]) return;
    const cue = ttsCues[i];
    const wasPlaying = ttsPlaying && !ttsPaused;
    ttsCancelSpeech();
    ttsCueIndex = i;
    ttsVirtualTime = cue.start;
    if (ttsTimeline) ttsTimeline.value = cue.start;
    if (ttsCurTimeEl) ttsCurTimeEl.textContent = ttsFmtTime(cue.start);
    ttsActivateCue(i);
    if (autoplay && !wasPlaying) {
      ttsPlaying = true;
      ttsPaused = false;
      ttsUpdatePlayBtn();
    }
    if (wasPlaying || autoplay) {
      ttsPlayCue(i);
    }
  }

  // Seek to an arbitrary virtual time (used by the timeline drag).
  function ttsSeekToTime(t) {
    if (!ttsCues.length) return;
    let idx = -1;
    for (let i = 0; i < ttsCues.length; i++) {
      if (t >= ttsCues[i].start && t < ttsCues[i].end) {
        idx = i;
        break;
      }
    }
    if (idx < 0) {
      // After the last cue end -> clamp to the last cue.
      if (t >= ttsTotalDuration) idx = ttsCues.length - 1;
      else idx = 0;
    }
    const wasPlaying = ttsPlaying && !ttsPaused;
    ttsCancelSpeech();
    ttsCueIndex = idx;
    ttsVirtualTime = t;
    if (ttsTimeline) ttsTimeline.value = t;
    if (ttsCurTimeEl) ttsCurTimeEl.textContent = ttsFmtTime(t);
    ttsActivateCue(idx);
    if (wasPlaying) ttsPlayCue(idx);
  }

  function ttsSetRate(delta) {
    const newRate = Math.min(2, Math.max(0.5, +(ttsRate + delta).toFixed(2)));
    if (newRate === ttsRate) return;
    ttsRate = newRate;
    globalSpeed = ttsRate;
    // Re-estimate durations for cues that haven't been measured yet.
    ttsCues.forEach(function (c) {
      if (c.actualDuration == null) {
        c.duration = ttsEstimateDuration(c.text, ttsRate);
      }
    });
    ttsRecomputeTimeline();
    if (ttsRateInd) {
      let label = ttsRate.toFixed(2).replace(/\.?0+$/, "");
      if (label === "") label = "1";
      ttsRateInd.textContent = label;
    }
    // If we're playing, restart the current cue at the new rate so
    // SpeechSynthesis picks it up.
    if (ttsPlaying && !ttsPaused && ttsCueIndex >= 0) {
      ttsPlayCue(ttsCueIndex);
    }
  }

  function ttsResetRate() {
    ttsRate = 1.0;
    globalSpeed = ttsRate;
    ttsCues.forEach(function (c) {
      if (c.actualDuration == null) {
        c.duration = ttsEstimateDuration(c.text, ttsRate);
      }
    });
    ttsRecomputeTimeline();
    if (ttsRateInd) ttsRateInd.textContent = "1";
    if (ttsPlaying && !ttsPaused && ttsCueIndex >= 0) {
      ttsPlayCue(ttsCueIndex);
    }
  }

  /* ------------------------------------------------------------------
   * Poll loop: drive the virtual playhead, timeline, subtitle scroll
   * ------------------------------------------------------------------ */

  function ttsPoll() {
    if (!ttsCues.length) return;
    // If we're playing and have a current cue, advance the virtual
    // playhead based on elapsed wall-clock time. boundary events
    // (when available) give finer-grained updates; this is the
    // fallback for browsers that don't fire them.
    if (ttsPlaying && !ttsPaused && ttsCueIndex >= 0) {
      const cue = ttsCues[ttsCueIndex];
      if (cue) {
        const elapsed = (performance.now() - ttsCueStartedAt) / 1000;
        const cueDur = cue.actualDuration != null ? cue.actualDuration : cue.duration;
        const frac = Math.min(1, Math.max(0, elapsed / cueDur));
        // Only advance virtual time -- never move it backwards (the
        // boundary handler may have set a more accurate value).
        const proposed = cue.start + frac * cueDur;
        if (proposed > ttsVirtualTime) ttsVirtualTime = proposed;
      }
    }

    if (!ttsDragging) {
      if (ttsTimeline) {
        ttsTimeline.value = ttsVirtualTime;
        const pct = ttsTotalDuration > 0
          ? (ttsVirtualTime / ttsTotalDuration) * 100
          : 0;
        ttsTimeline.style.backgroundSize = pct + "% 100%";
      }
      if (ttsCurTimeEl) ttsCurTimeEl.textContent = ttsFmtTime(ttsVirtualTime);
    }
    ttsUpdateMarquee(ttsVirtualTime);
  }

  /* ------------------------------------------------------------------
   * Subtitle + transcript rendering
   * ------------------------------------------------------------------ */

  function ttsEscapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  // Inject the current cue's word HTML into the scrolling subtitle,
  // apply status colors, and measure marquee overflow for the
  // horizontal scroll animation.
  function ttsActivateCue(idx) {
    if (ttsSubtitle) {
      const cue = ttsCues[idx];
      const html = cue ? cue.html : "";
      const unchanged = html === ttsLastSubtitleHtml;
      // Skip a redundant rebuild when the cue is unchanged (most commonly
      // a single-sentence loop replaying the same cue).
      // Rebuilding innerHTML detaches the hovered .word element, which
      // closes its open term-detail tooltip; the freshly created element
      // then immediately re-fires mouseenter and reopens it, so the popup
      // flickers (disappear → reappear) even though the mouse never moved.
      // Only reset the last-known HTML when we actually redraw.
      if (!unchanged) {
        if (typeof clear_newmultiterm_elements === "function")
          clear_newmultiterm_elements();
        // Close any open term-detail tooltip BEFORE rebuilding the
        // subtitle.  Replacing innerHTML while the cursor is over a
        // word detaches the tooltip's target, so the tooltip loses its
        // mouseleave event and a stray popup is left floating at the
        // word or at the document top-left corner.
        if (typeof _hide_element_message_tooltips === "function")
          _hide_element_message_tooltips();
        ttsSubtitle.innerHTML = html || "";
        ttsLastSubtitleHtml = html;
      }
      ttsSubtitle.scrollLeft = 0;
      ttsIsRtl = ttsSubtitle.getAttribute("dir") === "rtl";
      window.requestAnimationFrame(function () {
        const overflow = ttsSubtitle.scrollWidth - ttsSubtitle.clientWidth;
        ttsMarqueeOverflow = ttsIsRtl ? 0 : Math.max(0, overflow);
      });
      ttsApplySubtitleStatusColors();
    }

    // Transcript highlight + smooth scroll to center.
    const rows = ttsTranscriptList
      ? ttsTranscriptList.querySelectorAll(".yt-transcript-row")
      : [];
    for (let r = 0; r < rows.length; r++) {
      rows[r].classList.toggle("active", r === idx);
    }
    const row = rows[idx];
    if (row && ttsTranscript && ttsTranscript.style.display !== "none") {
      const rowRect = row.getBoundingClientRect();
      const listRect = ttsTranscriptList.getBoundingClientRect();
      const rowTopInList = rowRect.top - listRect.top + ttsTranscriptList.scrollTop;
      const containerH = ttsTranscriptList.clientHeight;
      let target = rowTopInList - containerH / 2 + rowRect.height / 2;
      target = Math.max(0, Math.min(target, ttsTranscriptList.scrollHeight - containerH));
      ttsTranscriptList.scrollTo({ top: target, behavior: "smooth" });
    }
  }

  function ttsDeactivateCue() {
    const rows = ttsTranscriptList
      ? ttsTranscriptList.querySelectorAll(".yt-transcript-row")
      : [];
    for (let r = 0; r < rows.length; r++) {
      rows[r].classList.remove("active");
    }
    if (ttsSubtitle) {
      if (typeof clear_newmultiterm_elements === "function")
        clear_newmultiterm_elements();
      // Close any open term-detail tooltip before emptying the
      // subtitle, so a popup opened over the cleared content can't
      // linger.
      if (typeof _hide_element_message_tooltips === "function")
        _hide_element_message_tooltips();
      ttsSubtitle.innerHTML = "";
      // Reset the change-tracking so the next activation of an equal
      // cue always redraws (we just cleared the subtitle).
      ttsLastSubtitleHtml = null;
      ttsMarqueeOverflow = 0;
    }
  }

  function ttsUpdateMarquee(t) {
    if (ttsCueIndex < 0 || ttsMarqueeOverflow <= 0 || !ttsSubtitle) return;
    const cue = ttsCues[ttsCueIndex];
    if (!cue) return;
    const dur = Math.max(0.5, (cue.end || 0) - (cue.start || 0));
    const progress = Math.min(1, Math.max(0, (t - cue.start) / dur));
    ttsSubtitle.scrollLeft = ttsMarqueeOverflow * progress;
  }

  // Apply status color classes to the subtitle word spans (same as
  // the YouTube player, so the subtitle always shows word colors).
  function ttsApplySubtitleStatusColors() {
    if (!ttsSubtitle) return;
    if (typeof apply_status_class !== "function") return;
    $(ttsSubtitle).find("span.word").each(function () {
      apply_status_class($(this));
    });
  }

  function ttsBindSubtitleInteractions() {
    if (!ttsSubtitle) return;
    if (typeof word_clicked !== "function") return;
    const t = $(ttsSubtitle);

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

  function ttsBuildTranscript() {
    if (!ttsTranscriptList) return;
    ttsTranscriptList.innerHTML = "";
    ttsCues.forEach(function (cue, i) {
      const row = document.createElement("div");
      row.className = "yt-transcript-row";
      row.id = "tts-transcript-row-" + i;

      const ts = document.createElement("span");
      ts.className = "yt-transcript-ts";
      ts.textContent = ttsFmtTime(cue.start);
      ts.title = "Jump to " + ttsFmtTime(cue.start);

      const txt = document.createElement("span");
      txt.className = "yt-transcript-text";
      txt.textContent = cue.text || "";

      row.appendChild(ts);
      row.appendChild(txt);
      row.addEventListener("click", function () {
        ttsSeekToCue(i, true);
      });
      ttsTranscriptList.appendChild(row);
    });
  }

  /* ------------------------------------------------------------------
   * Voice selection dropdown
   * ------------------------------------------------------------------ */

  function ttsPopulateVoiceList() {
    if (!ttsVoiceDropdown || !("speechSynthesis" in window)) return;
    const voices = window.speechSynthesis.getVoices();

    // getVoices() loads asynchronously and on mobile browsers it is
    // frequently still empty when the user first opens the voice menu
    // (Chromium only populates it lazily, sometimes not until a gesture,
    // and occasionally never). If we just returned here the dropdown
    // would stay an empty ~0-height box, so opening the gear appears to
    // do nothing and no voice can be selected. Show a placeholder instead;
    // it is replaced automatically once voices arrive (the voiceschanged
    // listener and the delayed repopulate timers call this function again
    // and rebuild the real list over it).
    if (!voices.length) {
      ensureVoicePlaceholder();
      return;
    }
    // Voices landed (eventually): clear the timeout clock and drop any
    // leftover placeholder state so the real list renders below.
    _voiceWaitStart = null;

    const detectedLang = getCurrentLangCode();
    const recommended = selectBestVoiceForLang(voices, detectedLang);

    // getVoices() loads asynchronously in Chromium/Edge, so the
    // init-time selection in ttsInitPlayer() often finds an empty list
    // and the recommended voice is never stored. Auto-select it here
    // (once voices exist) so the FIRST hover pronunciation uses the
    // natural voice instead of the default mechanical one.
    if (
      !globalCache.selectedVoice &&
      !(ttsVoiceBtn && ttsVoiceBtn.dataset.voiceName) &&
      recommended
    ) {
      ttsSelectVoice(recommended);
    }

    const currentName =
      (ttsVoiceBtn && ttsVoiceBtn.dataset.voiceName) ||
      globalCache.selectedVoice ||
      (recommended && recommended.name) ||
      "";

    // Group voices by language for easier scanning.
    voices.sort(function (a, b) { return a.lang.localeCompare(b.lang); });

    ttsVoiceDropdown.innerHTML = "";
    voices.forEach(function (voice) {
      const opt = document.createElement("div");
      opt.className = "tts-voice-option";
      opt.setAttribute("role", "option");
      opt.dataset.voiceName = voice.name;
      opt.textContent = "[" + voice.lang + "] " + voice.name;
      if (voice.name === currentName) opt.classList.add("selected");
      opt.addEventListener("click", function (e) {
        e.stopPropagation();
        ttsSelectVoice(voice);
      });
      ttsVoiceDropdown.appendChild(opt);
    });

    // Make sure the button title reflects the current voice (the
    // button is icon-only, so the title tooltip is the only place
    // the voice name is shown).
    if (currentName && ttsVoiceBtn) {
      ttsVoiceBtn.title = "Voice: " + ttsShortVoiceName(currentName);
    }

    // Whenever the list is (re)built, center the currently-used voice.
    // onvoiceschanged + the delayed populate timers rebuild the list
    // after initial load, which would otherwise reset the scroll; so we
    // re-apply the scroll here rather than only on the toggle click.
    if (!ttsVoiceDropdown.hidden) {
      ttsScrollToSelectedVoice();
    }
  }

  // Renders a "loading voices" row when speechSynthesis hasn't reported
  // its voices yet. Without it the menu opens as an empty (invisible) box
  // on mobile; once voices load they overwrite the placeholder.
  // Repeatedly called by the voices poller, so it is also responsible for
  // retiring the "Loading voices…" state: if voices never arrive within
  // VOICE_TIMEOUT_MS we stop waiting forever and tell the user no system
  // voices exist on this device, with a tappable Retry for browsers that
  // only populate voices after another user gesture.
  function ensureVoicePlaceholder() {
    if (!ttsVoiceDropdown) return;
    let el = ttsVoiceDropdown.querySelector(".tts-voice-placeholder");
    if (!el) {
      // If the menu already holds a real voice list, never add a placeholder.
      if (ttsVoiceDropdown.childElementCount > 0) return;
      el = document.createElement("div");
      el.className = "tts-voice-option tts-voice-placeholder";
      el.setAttribute("role", "status");
      ttsVoiceDropdown.appendChild(el);
      // Bind the retry affordance once. The row acts as a manual button
      // only after voices have timed out, so tap/cancel noise is harmless.
      el.addEventListener("click", function (e) {
        e.stopPropagation();
        ttsRetryVoiceLoad();
      });
    }
    if (_voiceWaitStart == null) _voiceWaitStart = Date.now();
    const waited = Date.now() - _voiceWaitStart;
    if (waited < VOICE_TIMEOUT_MS) {
      el.textContent = "Loading voices\u2026";
      el.classList.remove("tts-voice-retry");
      el.setAttribute("role", "status");
    } else {
      // Voices never landed. On some mobile browsers they only appear
      // after a fresh interaction, so show the real state + Retry instead
      // of an eternal spinner.
      el.textContent =
        "Voice list unavailable on this browser. Tap to retry.";
      el.classList.add("tts-voice-retry");
      el.setAttribute("role", "button");

      // Android Edge/Chromium quirk: getVoices() stays empty forever
      // even though speak() works with the system default voice (that is
      // why playback sounds fine). Offer that default as an explicit,
      // selectable entry so the menu is usable instead of a dead end.
      let def = ttsVoiceDropdown.querySelector(".tts-voice-device-default");
      if (!def) {
        def = document.createElement("div");
        def.className = "tts-voice-option tts-voice-device-default";
        def.setAttribute("role", "option");
        def.dataset.voiceName = DEVICE_DEFAULT_VOICE.name;
        def.textContent = "[default] Use device default voice";
        def.addEventListener("click", function (e) {
          e.stopPropagation();
          ttsSelectVoice(DEVICE_DEFAULT_VOICE);
        });
        ttsVoiceDropdown.appendChild(def);
      }
      const usingDefault =
        globalCache.selectedVoice === DEVICE_DEFAULT_VOICE.name ||
        (ttsVoiceBtn && ttsVoiceBtn.dataset.voiceName) === DEVICE_DEFAULT_VOICE.name;
      def.classList.toggle("selected", !!usingDefault);
    }
  }

  // Manual Retry for the "no voices" row. Resets the wait clock and re-
  // primes the speech engine as if the user just tapped the gear (some
  // mobile engines only populate voices from inside a user gesture).
  function ttsRetryVoiceLoad() {
    _voiceWaitStart = Date.now();
    _voicesEmpty = true;
    ttsCoaxVoicesByGesture();
    ensureVoicePlaceholder();
  }

  // Scroll the voice dropdown so the currently-used voice is centered,
  // so the user doesn't have to hunt for it by scrolling.
  function ttsScrollToSelectedVoice() {
    if (!ttsVoiceDropdown || ttsVoiceDropdown.hidden) return;
    const selected = ttsVoiceDropdown.querySelector(".tts-voice-option.selected");
    if (!selected) return;
    ttsVoiceDropdown.scrollTop = 0;
    const optTop = selected.offsetTop - ttsVoiceDropdown.offsetTop;
    ttsVoiceDropdown.scrollTop =
      optTop - ttsVoiceDropdown.clientHeight / 2 + selected.offsetHeight / 2;
  }

  function ttsShortVoiceName(fullName) {
    if (!fullName) return "Voice";
    if (fullName === DEVICE_DEFAULT_VOICE.name) return "Device default";
    // Trim common noise: "Microsoft ... Online (Natural) ..." -> "...".
    let s = fullName
      .replace(/\s*\(Natural\)\s*/gi, " ")
      .replace(/\s+Online\s*-\s*/g, " ")
      .replace(/Microsoft\s+/gi, "")
      .replace(/Google\s+/gi, "")
      .trim();
    if (s.length > 18) s = s.slice(0, 16) + "…";
    return s || "Voice";
  }

  function ttsSelectVoice(voice) {
    if (!voice) return;
    if (ttsVoiceBtn) {
      ttsVoiceBtn.dataset.voiceName = voice.name;
      if (ttsVoiceLabel) ttsVoiceLabel.textContent = ttsShortVoiceName(voice.name);
    }
    globalCache.selectedVoice = voice.name;
    // Update dropdown selected state.
    if (ttsVoiceDropdown) {
      ttsVoiceDropdown.querySelectorAll(".tts-voice-option").forEach(function (opt) {
        opt.classList.toggle("selected", opt.dataset.voiceName === voice.name);
      });
    }
    // Hide the dropdown.
    if (ttsVoiceDropdown) ttsVoiceDropdown.hidden = true;
    // If we're playing, restart the current cue with the new voice.
    if (ttsPlaying && !ttsPaused && ttsCueIndex >= 0) {
      ttsPlayCue(ttsCueIndex);
    }
  }

  function ttsToggleVoiceDropdown() {
    if (!ttsVoiceDropdown) return;
    const willOpen = ttsVoiceDropdown.hidden;
    ttsVoiceDropdown.hidden = !willOpen;
    if (willOpen) {
      // Opening the gear IS a user gesture. Prime the synthesis engine
      // (silent speak) FIRST -- on Edge Android the voice list only fills
      // in after the engine has run a real speak(), and reading it too
      // early can leave it empty. Then populate + poll to pick it up.
      ttsPrimeVoiceEngine();
      ttsPopulateVoiceList();
      // Coax getVoices() to populate with a few polls right after the tap.
      ttsCoaxVoicesByGesture();
      // Jump straight to the currently-used voice.
      ttsScrollToSelectedVoice();
    }
  }

  // Poll getVoices() a handful of times right after a user gesture
  // (gear tap). Some mobile browsers only populate the voice list
  // after an interaction; this gives the async load a chance to land.
  function ttsCoaxVoicesByGesture() {
    if (!("speechSynthesis" in window)) return;
    // On Edge (Android) the voice list only appears once the synthesis
    // engine has truly been initialized via speak() -- and a premature
    // getVoices() call can keep it broken until reload. Prime the engine
    // (silent speak) from inside this user gesture first, then poll.
    ttsPrimeVoiceEngine();
    let tries = 0;
    const timer = setInterval(function () {
      tries++;
      const voices = window.speechSynthesis.getVoices();
      if (voices.length) {
        clearInterval(timer);
        ttsPopulateVoiceList();
      } else if (tries >= 12) {
        // ~3 seconds; stop and let the persistent poller take over.
        clearInterval(timer);
      }
    }, 250);
  }

  // Force the speechSynthesis engine to initialize. On mobile Edge /
  // Chromium the getVoices() list only ever fills in AFTER the engine has
  // run a real speak(), and until then it reports no voices (and can even
  // stay broken if getVoices() is called too early). Speaking a silent
  // (volume 0, empty) utterance from inside a user gesture boots the engine
  // without making any sound, after which getVoices() returns the real list.
  function ttsPrimeVoiceEngine() {
    if (!("speechSynthesis" in window)) return;
    // Voices are live already -- nothing to warm up.
    if (window.speechSynthesis.getVoices().length > 0) return;
    // Engine is speaking or has queued speech; it's initialized and the
    // running utterance is the user's playback. Never cancel that.
    if (window.speechSynthesis.speaking || window.speechSynthesis.pending) return;
    try {
      // Non-empty text: some engines silently ignore zero-length
      // utterances, which would leave the engine un-primed forever.
      const warmup = new SpeechSynthesisUtterance(".");
      warmup.volume = 0;
      warmup.rate = 10;
      // Idle, so a cancel here is safe and also clears a wedged queue.
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(warmup);
      // Cancel shortly after so a zero-length warmup can't wedge the speech
      // queue on engines that keep a "pending" utterance around, then refresh
      // the dropdown in case voices just appeared.
      setTimeout(function () {
        try { window.speechSynthesis.cancel(); } catch (_) {}
        ttsPollVoices();
      }, 300);
    } catch (_) {}
  }

  // Lightweight, always-on poll that (re)builds the voice dropdown the
  // moment getVoices() finally reports voices. This is what rescues the
  // mobile case where onvoiceschanged never fires and the placeholder
  // would otherwise show forever.
  function ttsPollVoices() {
    if (!ttsVoiceDropdown || !("speechSynthesis" in window)) return;
    const voices = window.speechSynthesis.getVoices();
    const nowEmpty = voices.length === 0;

    // Empty -> non-empty transition: voices just landed.
    if (_voicesEmpty && !nowEmpty) {
      _voicesEmpty = false;
      ttsPopulateVoiceList();
      return;
    }
    _voicesEmpty = nowEmpty;

    // Menu open: keep a (re)built list or a visible placeholder in sync.
    if (!ttsVoiceDropdown.hidden) {
      if (nowEmpty) {
        ensureVoicePlaceholder();
      } else if (
        ttsVoiceDropdown.childElementCount === 1 &&
        ttsVoiceDropdown.querySelector(".tts-voice-placeholder")
      ) {
        ttsPopulateVoiceList();
      }
    }
  }

  // Close the voice dropdown when the user clicks outside of it.
  function ttsSetupVoiceDismiss() {
    document.addEventListener("click", function (e) {
      if (!ttsVoiceDropdown || ttsVoiceDropdown.hidden) return;
      const wrap = e.target.closest(".tts-voice-wrap");
      if (!wrap) ttsVoiceDropdown.hidden = true;
    });
  }

  /* ------------------------------------------------------------------
   * Controls wiring
   * ------------------------------------------------------------------ */

  function ttsBindControls() {
    if (ttsPlayBtn) ttsPlayBtn.addEventListener("click", ttsTogglePlay);
    if (ttsPrevCueBtn) ttsPrevCueBtn.addEventListener("click", function () { ttsJumpCue(-1); });
    if (ttsNextCueBtn) ttsNextCueBtn.addEventListener("click", function () { ttsJumpCue(1); });

    if (ttsTimeline) {
      ttsTimeline.addEventListener("pointerdown", function () { ttsDragging = true; });
      ttsTimeline.addEventListener("input", function () {
        if (ttsCurTimeEl) ttsCurTimeEl.textContent = ttsFmtTime(Number(ttsTimeline.value));
      });
      ttsTimeline.addEventListener("pointerup", function () {
        ttsDragging = false;
        ttsSeekToTime(Number(ttsTimeline.value));
      });
      ttsTimeline.addEventListener("keyup", function () {
        ttsSeekToTime(Number(ttsTimeline.value));
      });
    }

    const incBtn = document.getElementById("tts-rate-inc");
    const decBtn = document.getElementById("tts-rate-dec");
    if (incBtn) incBtn.addEventListener("click", function () { ttsSetRate(0.25); });
    if (decBtn) decBtn.addEventListener("click", function () { ttsSetRate(-0.25); });
    if (ttsRateInd) ttsRateInd.addEventListener("click", ttsResetRate);

    if (ttsLoopBtn) {
      ttsLoopBtn.addEventListener("click", function () {
        ttsLoop = !ttsLoop;
        ttsLoopBtn.classList.toggle("on", ttsLoop);
        // "Press loop to keep looping": if the player is currently
        // paused at the end of a sentence (auto-pause fired), turning
        // loop on resumes playback so the sentence starts looping.
        if (ttsLoop && ttsPaused && ttsCueIndex >= 0) {
          ttsPaused = false;
          ttsPlaying = true;
          ttsUpdatePlayBtn();
          ttsPlayCue(ttsCueIndex);
        }
      });
    }
    if (ttsAutoPauseBtn) {
      ttsAutoPauseBtn.addEventListener("click", function () {
        ttsAutoPause = !ttsAutoPause;
        ttsAutoPauseBtn.classList.toggle("on", ttsAutoPause);
      });
    }
    if (ttsTranscriptBtn) {
      ttsTranscriptBtn.addEventListener("click", function () {
        const isOpen = ttsTranscript.style.display !== "none";
        if (isOpen) {
          ttsTranscript.style.display = "none";
          ttsTranscriptBtn.classList.remove("on");
        } else {
          ttsTranscript.style.display = "block";
          ttsTranscriptBtn.classList.add("on");
          // Center the current line when the panel opens.
          requestAnimationFrame(function () {
            requestAnimationFrame(function () {
              const idx = ttsCueIndex >= 0 ? ttsCueIndex : 0;
              ttsActivateCue(idx);
            });
          });
        }
      });
    }
    if (ttsVoiceBtn) {
      ttsVoiceBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        ttsToggleVoiceDropdown();
      });
    }
  }

  function ttsBindKeys() {
    window.addEventListener("keydown", function (e) {
      if (e.code !== "Space") return;
      if (!ttsContainer) return;
      if (e.target &&
          (e.target.tagName === "INPUT" ||
           e.target.tagName === "TEXTAREA" ||
           e.target.isContentEditable)) {
        return;
      }
      // Don't hijack Space when the focus is inside the right pane
      // (term form / dictionaries) -- let it scroll there.
      if (e.target && e.target.closest && e.target.closest("#read_pane_right")) {
        return;
      }
      e.preventDefault();
      ttsTogglePlay();
    });
  }

  /* ------------------------------------------------------------------
   * Init / boot for the TTS player
   * ------------------------------------------------------------------ */

  function ttsCacheElements() {
    ttsContainer = document.getElementById("tts-player-container");
    if (!ttsContainer) return false;
    ttsPlayBtn = document.getElementById("tts-play-btn");
    ttsPrevCueBtn = document.getElementById("tts-prev-cue-btn");
    ttsNextCueBtn = document.getElementById("tts-next-cue-btn");
    ttsTimeline = document.getElementById("tts-timeline");
    ttsCurTimeEl = document.getElementById("tts-current-time");
    ttsDurationEl = document.getElementById("tts-duration");
    ttsRateInd = document.getElementById("tts-rate-indicator");
    ttsLoopBtn = document.getElementById("tts-loop-btn");
    ttsAutoPauseBtn = document.getElementById("tts-autopause-btn");
    ttsTranscriptBtn = document.getElementById("tts-transcript-btn");
    ttsTranscript = document.getElementById("tts-transcript");
    ttsTranscriptList = document.getElementById("tts-transcript-list");
    ttsSubtitle = document.getElementById("tts-scrolling-subtitle-inner");
    ttsVoiceBtn = document.getElementById("tts-voice-btn");
    ttsVoiceDropdown = document.getElementById("tts-voice-dropdown");
    ttsVoiceLabel = ttsVoiceBtn
      ? ttsVoiceBtn.querySelector(".tts-voice-label")
      : null;
    return true;
  }

  function ttsInitPlayer() {
    if (!ttsCacheElements()) return;
    // The container is rendered by the template but its visibility
    // is controlled by the TTS Player toggle (reading_menu).  The
    // element itself is always present in the DOM so JS can find it.
    ttsBindControls();
    ttsBindSubtitleInteractions();
    ttsBindKeys();
    ttsSetupVoiceDismiss();

    // Build the cue list from the current page content.  This is
    // deferred until #thetext has its real content -- on initial load
    // it's ajaxed in by goto_relative_page().
    ttsBuildCuesWhenReady();

    // Drive the virtual playhead.
    if (ttsPollTimer) clearInterval(ttsPollTimer);
    ttsPollTimer = setInterval(ttsPoll, 250);

    // SpeechSynthesis voices may load asynchronously.
    if ("speechSynthesis" in window) {
      _voicesEmpty = window.speechSynthesis.getVoices().length === 0;
      window.speechSynthesis.getVoices();
      window.speechSynthesis.onvoiceschanged = ttsPopulateVoiceList;
      // Some browsers don't fire onvoiceschanged reliably -- try a
      // few times shortly after init.
      [200, 600, 1500].forEach(function (ms) {
        setTimeout(ttsPopulateVoiceList, ms);
      });
      // Always-on poller: rescues mobile browsers where getVoices()
      // stays empty / onvoiceschanged never fires. Rebuilds the
      // dropdown the instant voices arrive.
      if (_voicePollTimer) clearInterval(_voicePollTimer);
      _voicePollTimer = setInterval(ttsPollVoices, 400);
    }

    // Pick up the recommended voice for the current language if the
    // user hasn't chosen one yet.
    if (!globalCache.selectedVoice && "speechSynthesis" in window) {
      const voices = window.speechSynthesis.getVoices();
      if (voices.length) {
        const rec = selectBestVoiceForLang(voices, getCurrentLangCode());
        if (rec) ttsSelectVoice(rec);
      }
    }
  }

  // Wait until #thetext has real sentence spans before building cues.
  // On initial page load #thetext contains "..." (a placeholder) and
  // is filled in by goto_relative_page() -> /read/start_reading ajax.
  function ttsBuildCuesWhenReady() {
    const textDiv = document.getElementById("thetext");
    if (textDiv && textDiv.querySelector(".textsentence")) {
      ttsBuildCues();
      return;
    }
    // Retry for up to ~10 seconds.
    let attempts = 0;
    const t = setInterval(function () {
      const div = document.getElementById("thetext");
      if (div && div.querySelector(".textsentence")) {
        clearInterval(t);
        ttsBuildCues();
      } else if (++attempts > 40) {
        clearInterval(t);
      }
    }, 250);
  }

  /* ------------------------------------------------------------------
   * Re-build cues when the page text changes (ajax navigation).
   * ------------------------------------------------------------------ */

  let _textObserver = null;
  function ttsStartTextObserver() {
    const textDiv = document.getElementById("thetext");
    if (!textDiv || _textObserver) return;
    _textObserver = new MutationObserver(function (mutations) {
      let needsRebuild = false;
      for (const m of mutations) {
        if (m.addedNodes.length > 0) { needsRebuild = true; break; }
      }
      if (!needsRebuild) return;
      // Debounce -- goto_relative_page does multiple DOM writes.
      if (ttsStartTextObserver._t) clearTimeout(ttsStartTextObserver._t);
      ttsStartTextObserver._t = setTimeout(function () {
        ttsStartTextObserver._t = null;
        // Stop any playback before rebuilding -- the old cue indices
        // are stale after #thetext is replaced.
        ttsCancelSpeech();
        ttsPlaying = false;
        ttsPaused = false;
        ttsCueIndex = -1;
        ttsVirtualTime = 0;
        ttsUpdatePlayBtn();
        ttsBuildCues();
        // Re-apply status colours to the new subtitle word spans.
        ttsApplySubtitleStatusColors();
      }, 150);
    });
    _textObserver.observe(textDiv, { childList: true });
  }

  /* ================================================================
     8. TTS toggles (sidebar quick controls)
     ================================================================ */

  let ttsPlayerVisible = true;
  let ttsSentenceButtonsVisible = true;

  function setTtsPlayerVisible(visible) {
    ttsPlayerVisible = visible;
    const toggle = document.getElementById("tts-player-toggle");
    const container = document.body;

    if (ttsContainer) {
      ttsContainer.style.display = visible ? "" : "none";
    }
    if (toggle) {
      toggle.checked = visible;
    }
    if (container) {
      if (visible) {
        container.classList.add("tts-player-active");
      } else {
        container.classList.remove("tts-player-active");
      }
    }

    try {
      localStorage.setItem("ttsPlayerVisible", visible ? "1" : "0");
    } catch (_) {}

    var val = visible ? "1" : "0";
    try {
      fetch("/settings/set/tts_show_control_panel/" + val, { method: "POST" });
    } catch (_) {}

    // Stop playback when the player is hidden mid-stream.
    if (!visible && (ttsPlaying || ttsPaused)) {
      ttsStop();
    }
  }

  function setTtsSentenceButtonsVisible(visible) {
    ttsSentenceButtonsVisible = visible;
    const btns = document.querySelectorAll(".lute-sentence-play-btn");
    const toggle = document.getElementById("tts-sentence-buttons-toggle");
    const container = document.body;

    btns.forEach(function (btn) {
      btn.style.display = visible ? "" : "none";
    });
    if (toggle) {
      toggle.checked = visible;
    }
    if (container) {
      if (visible) {
        container.classList.add("tts-sentence-buttons-active");
      } else {
        container.classList.remove("tts-sentence-buttons-active");
      }
    }

    try {
      localStorage.setItem("ttsSentenceButtonsVisible", visible ? "1" : "0");
    } catch (_) {}

    var val = visible ? "1" : "0";
    try {
      fetch("/settings/set/tts_show_sentence_buttons/" + val, { method: "POST" });
    } catch (_) {}
  }

  function setupTtsPlayerToggle() {
    const toggle = document.getElementById("tts-player-toggle");
    if (!toggle) return;

    // Detect YouTube / MP3 books: they render a dedicated
    // .youtube-player-container player and the #book_audio_file hidden
    // input is explicitly left empty (the Jinja template sets it to
    // the empty string for youtube/mp3 types).
    var isYouTubeOrMp3 = function () {
      // The YouTube player is included for youtube/mp3 books; the TTS
      // player is included for everything else.  When the YouTube
      // player is present we hide the TTS player by default to avoid
      // having two competing players on screen.
      if (document.getElementById("yt-player-container")) return true;
      var audioInput = document.getElementById("book_audio_file");
      if (audioInput && (audioInput.value || "").trim() === "") {
        if (document.getElementById("ytContainer")) return true;
        if (typeof window.CUES !== "undefined") return true;
      }
      return false;
    };

    let saved = null;
    try {
      saved = localStorage.getItem("ttsPlayerVisible");
    } catch (_) {}

    var initialVisible;
    if (saved !== null) {
      initialVisible = saved !== "0";
    } else if (isYouTubeOrMp3()) {
      initialVisible = false;
    } else {
      initialVisible = SETTINGS.showControlPanel;
    }
    ttsPlayerVisible = initialVisible;

    toggle.checked = initialVisible;
    if (initialVisible) {
      document.body.classList.add("tts-player-active");
    } else {
      document.body.classList.remove("tts-player-active");
    }
    if (ttsContainer) {
      ttsContainer.style.display = initialVisible ? "" : "none";
    }

    toggle.addEventListener("change", function () {
      setTtsPlayerVisible(toggle.checked);
    });
  }

  function setupTtsSentenceButtonsToggle() {
    const toggle = document.getElementById("tts-sentence-buttons-toggle");
    if (!toggle) return;

    let saved = null;
    try {
      saved = localStorage.getItem("ttsSentenceButtonsVisible");
    } catch (_) {}

    var initialVisible;
    if (saved !== null) {
      initialVisible = saved !== "0";
    } else {
      initialVisible = SETTINGS.showSentenceButtons;
    }
    ttsSentenceButtonsVisible = initialVisible;

    toggle.checked = initialVisible;
    if (initialVisible) {
      document.body.classList.add("tts-sentence-buttons-active");
    } else {
      document.body.classList.remove("tts-sentence-buttons-active");
    }

    toggle.addEventListener("change", function () {
      setTtsSentenceButtonsVisible(toggle.checked);
    });
  }

  /* ------------------------------------------------------------------
   * 9. Lightweight UI observer (sentence buttons + event delegation)
   * ------------------------------------------------------------------ */

  let _uiObserver = null;
  let _uiDebounceTimer = null;
  function startUIObserver() {
    if (_uiObserver) return;
    const textDiv = document.getElementById("thetext");
    if (!textDiv) return;

    _uiObserver = new MutationObserver(function (mutations) {
      let needsUpdate = false;
      for (const m of mutations) {
        if (m.addedNodes.length > 0) { needsUpdate = true; break; }
      }
      if (!needsUpdate) return;

      if (_uiDebounceTimer) clearTimeout(_uiDebounceTimer);
      _uiDebounceTimer = setTimeout(function () {
        _uiDebounceTimer = null;
        if (SETTINGS.showSentenceButtons) injectSentencePlayButtons();
        setupEventDelegation();
      }, 100);
    });

    _uiObserver.observe(textDiv, { childList: true });
  }

  // Form observer: lightweight — only fires on body childList changes
  // (e.g., when Lute opens a term form iframe).
  let _formObserver = null;
  function startFormObserver() {
    if (_formObserver) return;
    _formObserver = new MutationObserver(function () {
      debouncedFormCheck();
    });
    _formObserver.observe(document.body, { childList: true, subtree: false });
  }

  // Override inject functions to respect toggle states.
  const _origInjectSentenceButtons = injectSentencePlayButtons;
  injectSentencePlayButtons = function () {
    if (!SETTINGS.showSentenceButtons) return;
    _origInjectSentenceButtons();
    if (!ttsSentenceButtonsVisible) {
      document.querySelectorAll(".lute-sentence-play-btn").forEach(function (btn) {
        btn.style.display = "none";
      });
    }
  };

  /* ------------------------------------------------------------------
   * 10. Boot
   * ------------------------------------------------------------------ */

  function boot() {
    setupTtsPlayerToggle();
    setupTtsSentenceButtonsToggle();

    // Initialise the full TTS player if its container is on the page.
    // (For YouTube / MP3 books the container isn't rendered and we
    // fall back to the legacy small panel behaviour.)
    if (document.getElementById("tts-player-container")) {
      ttsInitPlayer();
      ttsStartTextObserver();
    }

    if (document.getElementById("thetext")) {
      if (SETTINGS.showSentenceButtons) injectSentencePlayButtons();
      setupEventDelegation();
      startUIObserver();
    }

    startFormObserver();
    processTranslationFlow();

    // After a term status update, lute.js reloads #thetext.  The cue
    // list rebuild is handled by the #thetext MutationObserver, but
    // we also re-apply status colours to the subtitle.
    window.addEventListener("lute:status-updated", function () {
      ttsApplySubtitleStatusColors();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
