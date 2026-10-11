"use strict";

/**
 * The settings page's "Local dictionary (MDX)" card.
 *
 * Four endpoints (see lute/dict/routes.py):
 *
 *   GET  /dict/status                  -> {result, status}
 *   POST /dict/import                  -> {result, task_id, source}
 *   GET  /dict/import_status/<task_id> -> {result, state, processed, error,
 *                                           dictionary}
 *   POST /dict/remove                  -> {result, removed, status}
 *
 * The field names are a contract with the Python side and a mismatch
 * fails silently (a card that renders nothing, or a poll that never
 * finishes), so tests/unit/dict/test_dict_settings_js.py pins them.
 *
 * The upload goes through XMLHttpRequest rather than fetch: a dictionary
 * is tens of megabytes and the browser only reports upload progress on
 * XHR.  The conversion that follows runs on the server, so it is polled.
 */
const DictSettings = (function () {

  // How often the conversion is polled.  Overridable so the JS test can
  // run the state machine without waiting on real timers.
  const POLL_INTERVAL_MS = 1500;

  function _el(id) {
    return document.getElementById(id);
  }

  function _setMessage(text, ok) {
    const box = _el("dict_settings_msg");
    if (!box) return;
    box.textContent = text || "";
    box.style.color = ok === false ? "#b00" : (ok === true ? "#070" : "");
  }

  function _setBusy(busy) {
    ["dict_settings_import", "dict_settings_remove", "dict_settings_file"]
      .forEach(id => {
        const el = _el(id);
        if (el) el.disabled = busy;
      });
  }

  /**
   * One line describing the installed dictionary, or that there is none.
   */
  function statusText(status) {
    if (!status || !status.installed) {
      return "No dictionary imported yet.";
    }
    const parts = [];
    // The title is only known when the import recorded it; a dictionary
    // built with the CLI tool and dropped into place has none, so fall
    // back to the file name rather than leading with a bare size.
    if (status.title) parts.push(status.title);
    else if (status.filename) parts.push(status.filename);
    if (status.entries !== null && status.entries !== undefined) {
      parts.push(status.entries.toLocaleString() + " entries");
    }
    if (status.size_mb !== null && status.size_mb !== undefined) {
      parts.push(status.size_mb + " MB");
    }
    let text = parts.join(" \u00b7 ");
    if (status.source) {
      text += " \u2014 imported from " + status.source;
    }
    if (status.imported_at) {
      text += " on " + status.imported_at;
    }
    return text;
  }

  function render(status) {
    const text = _el("dict_settings_status");
    if (text) text.textContent = statusText(status);

    const installed = _el("dict_settings_installed");
    if (installed) installed.style.display = status && status.installed ? "" : "none";

    const url = _el("dict_settings_url");
    if (url && status && status.url) {
      url.textContent = status.url;
      url.setAttribute("href", status.url);
    }

    const removeBtn = _el("dict_settings_remove");
    if (removeBtn) removeBtn.disabled = !(status && status.installed);
  }

  function refresh() {
    return fetch("/dict/status")
      .then(r => r.json())
      .then(d => {
        render(d.status);
        return d.status;
      })
      .catch(() => {
        _setMessage("Could not load the dictionary status.", false);
        return null;
      });
  }

  /**
   * Upload one file, reporting progress as a 0..1 fraction.
   */
  function upload(file, onProgress) {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append("mdx", file);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/dict/import");
      xhr.upload.onprogress = e => {
        if (onProgress && e.lengthComputable && e.total > 0) {
          onProgress(e.loaded / e.total);
        }
      };
      xhr.onload = () => {
        let body = {};
        try {
          body = JSON.parse(xhr.responseText);
        } catch (e) {
          body = {};
        }
        if (xhr.status >= 200 && xhr.status < 300 && body.task_id) {
          resolve(body);
        } else {
          reject(new Error(body.message || "The import could not be started."));
        }
      };
      xhr.onerror = () => reject(new Error("Upload failed -- the server did not answer."));
      xhr.send(form);
    });
  }

  /**
   * Poll one conversion task to its terminal state.  Resolves with the
   * final payload; rejects with the server's error message.
   */
  function poll(taskId, intervalMs) {
    const wait = intervalMs === undefined ? POLL_INTERVAL_MS : intervalMs;
    return new Promise((resolve, reject) => {
      const tick = () => {
        fetch("/dict/import_status/" + encodeURIComponent(taskId))
          .then(r => r.json())
          .then(d => {
            if (d.state === "finished") {
              resolve(d);
              return;
            }
            if (d.state === "error") {
              reject(new Error(d.error || "The conversion failed."));
              return;
            }
            const read = d.processed
              ? d.processed.toLocaleString() + " entries read"
              : "starting";
            _setMessage("Converting\u2026 " + read + ".  This can take a few minutes.", true);
            setTimeout(tick, wait);
          })
          .catch(() => reject(new Error("Lost contact with the server during the conversion.")));
      };
      tick();
    });
  }

  function importFile(file) {
    if (!file) {
      _setMessage("Choose an .mdx file first.", false);
      return Promise.resolve();
    }
    _setBusy(true);
    _setMessage("Uploading " + file.name + "\u2026", true);
    return upload(file, fraction => {
      _setMessage(
        "Uploading " + file.name + " \u2014 " + Math.round(fraction * 100) + "%",
        true
      );
    })
      .then(d => {
        _setMessage("Converting " + (d.source || file.name) + "\u2026 this can take a few minutes.", true);
        return poll(d.task_id);
      })
      .then(d => {
        const n = d.dictionary && d.dictionary.entries;
        _setMessage(
          n ? "Imported " + n.toLocaleString() + " entries." : "Dictionary imported.",
          true
        );
        return refresh();
      })
      .catch(e => {
        _setMessage(e.message, false);
      })
      .then(() => _setBusy(false));
  }

  function importSelected() {
    const input = _el("dict_settings_file");
    return importFile(input && input.files ? input.files[0] : null);
  }

  function remove() {
    if (!window.confirm(
      "Delete the installed dictionary?  The phone app will no longer be " +
      "able to download it, and terms with no translation will lose their " +
      "dictionary gloss."
    )) {
      return Promise.resolve();
    }
    _setBusy(true);
    return fetch("/dict/remove", { method: "POST" })
      .then(r => r.json())
      .then(d => {
        _setMessage(d.removed ? "Dictionary removed." : "There was nothing to remove.", true);
        return refresh();
      })
      .catch(() => _setMessage("Could not remove the dictionary.", false))
      .then(() => _setBusy(false));
  }

  return {
    statusText,
    render,
    refresh,
    upload,
    poll,
    importFile,
    importSelected,
    remove,
  };
})();

document.addEventListener("DOMContentLoaded", () => DictSettings.refresh());
