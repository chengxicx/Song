/* tts-translate.js
   --------------------------------------------------------------
   Auto-translate: #translation auto-fill in the term form,
   plus client-side translation with several API fallbacks.
   --------------------------------------------------------------
   Split out of tts.js.  These scripts are deliberately NOT
   wrapped in an IIFE: they share one global scope and are loaded
   in order (see lute/templates/read/index.html), so splitting the
   file changes nothing at runtime.
*/
"use strict";
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

  // MyMemory returns its error messages (HTTP 403 etc.) as
  // responseData.translatedText; never show those as translations.
  var MYMEMORY_ERROR_PREFIXES = [
    "PLEASE SELECT",
    "MYMEMORY WARNING",
    "QUERY LENGTH LIMIT EXCEEDED",
    "INVALID SOURCE OR TARGET LANGUAGE",
    "INVALID LANGUAGE PAIR"
  ];

  function isMyMemoryError(result) {
    var upper = (result || "").replace(/^\s+|\s+$/g, "").toUpperCase();
    for (var i = 0; i < MYMEMORY_ERROR_PREFIXES.length; i++) {
      if (upper.indexOf(MYMEMORY_ERROR_PREFIXES[i]) === 0) return true;
    }
    return false;
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
      var status = parseInt(data && data.responseStatus, 10);
      if (!isNaN(status) && status !== 200) return "";
      if (data && data.responseData && data.responseData.translatedText) {
        var result = data.responseData.translatedText;
        if (result && result.toLowerCase() === text.toLowerCase()) return "";
        if (isMyMemoryError(result)) return "";
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
    // Same full language tag: nothing to translate (e.g. reading
    // language equals the browser UI language).  Distinct tags such
    // as zh-HK -> zh-CN are still translated.
    if (sl && tl && sl.toLowerCase() === tl.toLowerCase()) {
      return Promise.resolve("");
    }
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

