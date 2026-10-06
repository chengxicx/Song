/* lute-recorder.js
   --------------------------------------------------------------
   Shared microphone capture and shadowing-task plumbing, used by
   both the reading page's shadowing panel (lute-shadowing.js) and
   the review session's shadowing cards (lute-review.js).

   Two pieces:

   - LuteRecorder wraps one MediaRecorder capture: mic permission,
     mimetype picking, an optional duration cap, and stop/cancel.
     The caller renders the UI (button state, timers) from the
     callbacks -- this file intentionally knows nothing about panels.

   - LuteRecorder.scoreTake submits a finished blob to
     /read/shadowing/transcribe and polls the task to completion,
     so both callers see the same submit/poll semantics (transient
     network errors tolerated, 10-minute ceiling for a cold model
     download).

   Like the read page's scripts this shares one global scope, but
   everything is namespaced under window.LuteRecorder.
   --------------------------------------------------------------
*/
"use strict";

window.LuteRecorder = (function () {
  const REC_CAP_MS = 30000;

  function canRecord() {
    return !!(
      window.isSecureContext &&
      navigator.mediaDevices &&
      navigator.mediaDevices.getUserMedia &&
      typeof MediaRecorder !== "undefined"
    );
  }

  function pickMimeType() {
    if (typeof MediaRecorder === "undefined") return "";
    const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
    for (let i = 0; i < candidates.length; i++) {
      try {
        if (MediaRecorder.isTypeSupported(candidates[i])) return candidates[i];
      } catch (_) {}
    }
    return "";
  }

  function extForMime(mime) {
    mime = mime || "";
    if (mime.indexOf("mp4") !== -1 || mime.indexOf("m4a") !== -1) return "mp4";
    if (mime.indexOf("ogg") !== -1) return "ogg";
    return "webm";
  }

  function permissionMessage(err) {
    if (err && (err.name === "NotAllowedError" || err.name === "SecurityError")) {
      return "Microphone permission denied.  Allow microphone access for this site to use shadowing.";
    }
    if (err && err.name === "NotFoundError") {
      return "No microphone found.";
    }
    return (
      "Could not start recording" +
      (err && err.message ? ": " + err.message : ".")
    );
  }

  function teardownStream(stream) {
    if (stream) {
      stream.getTracks().forEach(function (t) {
        try {
          t.stop();
        } catch (_) {}
      });
    }
  }

  class Recorder {
    /* opts: { capMs (0 = uncapped), onStop(blob, mime), onFail(msg) }.
       onStop/onFail are optional; a capture that ends with no listener
       is simply dropped. */
    constructor(opts) {
      const o = opts || {};
      this.capMs = typeof o.capMs === "number" ? o.capMs : REC_CAP_MS;
      this.onStop = o.onStop || null;
      this.onFail = o.onFail || null;
      this._stream = null;
      this._recorder = null;
      this._chunks = [];
      this.recording = false;
      this._starting = false;
    }

    async start() {
      if (this.recording || this._starting) return false;
      if (!canRecord()) {
        if (this.onFail) {
          this.onFail(
            "Microphone recording needs a secure context (open Lute on localhost or HTTPS) and a browser with MediaRecorder support."
          );
        }
        return false;
      }
      // A second click during the getUserMedia await would otherwise
      // start two captures; collapse it.
      this._starting = true;
      try {
        let stream;
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (err) {
          if (this.onFail) this.onFail(permissionMessage(err));
          return false;
        }
        this._stream = stream;
        this._chunks = [];
        const mime = pickMimeType();
        let recorder;
        try {
          recorder = mime
            ? new MediaRecorder(stream, { mimeType: mime })
            : new MediaRecorder(stream);
        } catch (err) {
          teardownStream(this._stream);
          this._stream = null;
          if (this.onFail) this.onFail(permissionMessage(err));
          return false;
        }
        this._recorder = recorder;
        this.recording = true;
        recorder.ondataavailable = (e) => {
          if (e.data && e.data.size > 0) this._chunks.push(e.data);
        };
        recorder.onstop = () => this._finished();
        if (this.capMs > 0) {
          setTimeout(() => {
            if (this.recording) this.stop();
          }, this.capMs);
        }
        recorder.start();
        return true;
      } finally {
        this._starting = false;
      }
    }

    stop() {
      if (!this.recording) return;
      this.recording = false;
      const rec = this._recorder;
      if (rec && rec.state === "recording") {
        try {
          rec.stop();
        } catch (_) {
          this._cleanup();
        }
      } else {
        this._cleanup();
      }
    }

    /* Drop the capture without scoring it. */
    cancel() {
      if (!this.recording) {
        this._cleanup();
        return;
      }
      this.recording = false;
      const rec = this._recorder;
      this._recorder = null;
      if (rec) {
        rec.onstop = null;
        try {
          if (rec.state !== "inactive") rec.stop();
        } catch (_) {}
      }
      this._chunks = [];
      this._cleanup();
    }

    _cleanup() {
      teardownStream(this._stream);
      this._stream = null;
      this._recorder = null;
    }

    _finished() {
      const rec = this._recorder;
      const chunks = this._chunks;
      const mime = rec ? rec.mimeType : "";
      this._recorder = null;
      this._chunks = [];
      this.recording = false;
      this._cleanup();
      const blob = new Blob(chunks, { type: mime || "audio/webm" });
      if (!blob.size) {
        if (this.onFail) this.onFail("The recording was empty — try again.");
        return;
      }
      if (this.onStop) this.onStop(blob, mime);
    }
  }

  /* Submit one take and poll it to completion.
     args: { blob, languageId, tokens, fullText, model, source, bookId,
             onProgress(state, elapsedSecs) }.
     Resolves { state: "finished", result } or { state: "error", error }. */
  async function scoreTake(args) {
    const fd = new FormData();
    fd.append(
      "audio",
      args.blob,
      "shadowing." + extForMime(args.blob.type)
    );
    fd.append("language_id", args.languageId != null ? String(args.languageId) : "");
    fd.append("tokens", JSON.stringify(args.tokens || []));
    if (args.fullText) fd.append("full_text", args.fullText);
    if (args.model) fd.append("model", args.model);
    if (args.source) fd.append("source", args.source);
    if (args.bookId) fd.append("book_id", String(args.bookId));

    let taskId = null;
    try {
      const resp = await fetch("/read/shadowing/transcribe", {
        method: "POST",
        body: fd,
      });
      let data = {};
      try {
        data = await resp.json();
      } catch (_) {}
      if (!resp.ok) {
        return { state: "error", error: data.error || "Request failed (" + resp.status + ")" };
      }
      taskId = data.task_id;
    } catch (err) {
      return { state: "error", error: "Network error: " + err };
    }
    if (!taskId) {
      return { state: "error", error: "No task id returned by the server." };
    }
    return pollTask(taskId, args.onProgress || null);
  }

  // Poll a scoring task until it settles.  Transient network errors are
  // tolerated (the backend worker keeps running); the task is lost only
  // if the server restarted ("unknown").  First runs can take minutes --
  // the model may be downloading on the server -- so callers can show
  // the elapsed wait via onProgress instead of failing early.
  async function pollTask(taskId, onProgress) {
    const started = Date.now();
    const MAX_MS = 10 * 60 * 1000;
    while (Date.now() - started < MAX_MS) {
      await new Promise(function (r) {
        setTimeout(r, 1500);
      });
      let data;
      try {
        const resp = await fetch("/read/shadowing/status/" + taskId);
        data = await resp.json();
      } catch (_) {
        continue;
      }
      if (data.state === "finished") {
        return { state: "finished", result: data.result };
      }
      if (data.state === "error") {
        return { state: "error", error: data.error || "Transcription failed." };
      }
      if (data.state === "unknown") {
        return {
          state: "error",
          error: "The scoring task was lost (server restart?) — try again.",
        };
      }
      if (onProgress) onProgress(data.state, Math.floor((Date.now() - started) / 1000));
    }
    return { state: "error", error: "Transcription timed out." };
  }

  // One click on a misread word: put it into the learning pile so the
  // review queue picks it up.  Resolves {outcome, term_id, term_status};
  // see /read/shadowing/mark_unknown for the outcomes.
  async function markWord(languageId, text) {
    const resp = await fetch("/read/shadowing/mark_unknown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        language_id: parseInt(languageId, 10) || 0,
        text: text,
      }),
    });
    let data = {};
    try {
      data = await resp.json();
    } catch (_) {}
    if (!resp.ok) {
      throw data.error || "Request failed (" + resp.status + ")";
    }
    return data;
  }

  return {
    Recorder: Recorder,
    canRecord: canRecord,
    pickMimeType: pickMimeType,
    extForMime: extForMime,
    permissionMessage: permissionMessage,
    scoreTake: scoreTake,
    markWord: markWord,
  };
})();
