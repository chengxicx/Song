"use strict";

/**
 * The settings page's "Local dictionaries (MDX)" card.
 *
 * Four endpoints (see lute/dict/routes.py):
 *
 *   GET  /dict/status                  -> {result, default_filename,
 *                                           dictionaries: [{...}, ...]}
 *   POST /dict/import                  -> {result, task_id, source, filename}
 *   GET  /dict/import_status/<task_id> -> {result, state, processed, error,
 *                                           dictionary}
 *   POST /dict/remove  {name, force}   -> {result, removed, filename,
 *                                           dictionaries}
 *
 * The dictionaries are a *pool* -- a folder on the server -- and every
 * one of them is addressed by its file name, which is the same string a
 * language stores in Language.local_dict.  So this card only lists what
 * /dict/status reports; it never invents a name.
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
    ["dict_settings_import", "dict_settings_file", "dict_settings_name"]
      .forEach(id => {
        const el = _el(id);
        if (el) el.disabled = busy;
      });
  }

  /**
   * Escape text that came out of a dictionary's metadata sidecar (its
   * title and source file name are whatever the .mdx carried) before it
   * goes into the list's innerHTML.
   */
  function _esc(text) {
    return String(text === null || text === undefined ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /**
   * The facts about one dictionary: entries, size, provenance.  Empty
   * when nothing is known -- a dictionary built with the CLI tool and
   * dropped into the folder has no metadata sidecar.
   */
  function describe(d) {
    if (!d) return "";
    const parts = [];
    if (d.entries !== null && d.entries !== undefined) {
      parts.push(d.entries.toLocaleString() + " entries");
    }
    if (d.size_mb !== null && d.size_mb !== undefined) {
      parts.push(d.size_mb + " MB");
    }
    if (d.source) parts.push("imported from " + d.source);
    if (d.imported_at) parts.push("on " + d.imported_at);
    return parts.join(" \u00b7 ");
  }

  /**
   * One line describing a dictionary: its title (or file name) plus
   * describe().  A missing key must not throw and blank the card.
   */
  function statusText(d) {
    if (!d || !d.filename) return "No dictionary imported yet.";
    const label = d.title || d.filename;
    const facts = describe(d);
    return facts ? label + " \u00b7 " + facts : label;
  }

  /**
   * One row: what the dictionary is, where to download it, and a Remove
   * button.
   *
   * The button is an inline onclick rather than a delegated listener,
   * matching the card's other buttons.  The file name is safe to embed
   * that way: the server only accepts pool names (lute/dict/service.py
   * _POOL_NAME_RE), which cannot contain a quote or a separator.
   */
  function rowHtml(d) {
    // Styled inline like the rest of the card rather than with new
    // classes in styles.css: this is a server-level admin card, and the
    // theme files are the wrong place for it.
    const name = d.filename;
    const facts = describe(d);
    const parts = [
      '<div style="border-top: 1px solid #ddd; padding: 6px 0;">',
      "<b>" + _esc(d.title || name) + "</b>",
    ];
    if (d.is_default) {
      parts.push(
        ' <span style="font-size: 0.8em; border: 1px solid #999;' +
        ' border-radius: 3px; padding: 0 4px;">phone default</span>'
      );
    }
    parts.push(
      '<div style="font-size: 0.85em; color: #666;">' + _esc(name) +
      (facts ? " \u00b7 " + _esc(facts) : "") + "</div>"
    );
    if (d.url) {
      parts.push(
        '<div style="font-size: 0.85em; margin: 2px 0 4px 0;">' +
        '<a href="' + _esc(d.url) + '">' + _esc(d.url) + "</a></div>"
      );
    }
    parts.push(
      '<button type="button" class="btn btn-danger" onclick="DictSettings.remove(\'' +
      name + "'); return false;\">Remove</button>"
    );
    parts.push("</div>");
    return parts.join("");
  }

  function render(dictionaries) {
    const list = _el("dict_settings_list");
    if (!list) return;
    const dicts = dictionaries || [];
    if (dicts.length === 0) {
      list.innerHTML = "<i>No dictionary imported yet.</i>";
      return;
    }
    list.innerHTML = dicts.map(rowHtml).join("");
  }

  function refresh() {
    return fetch("/dict/status")
      .then(r => r.json())
      .then(d => {
        render(d.dictionaries);
        return d.dictionaries;
      })
      .catch(() => {
        _setMessage("Could not load the dictionary list.", false);
        return null;
      });
  }

  /**
   * Upload one file, reporting progress as a 0..1 fraction.  ``name``
   * is the pool file name to store it under; empty means the server
   * derives one from the .mdx file's name.
   */
  function upload(file, name, onProgress) {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append("mdx", file);
      if (name) form.append("name", name);
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

  function importFile(file, name) {
    if (!file) {
      _setMessage("Choose an .mdx file first.", false);
      return Promise.resolve();
    }
    _setBusy(true);
    _setMessage("Uploading " + file.name + "\u2026", true);
    return upload(file, name, fraction => {
      _setMessage(
        "Uploading " + file.name + " \u2014 " + Math.round(fraction * 100) + "%",
        true
      );
    })
      .then(d => {
        _setMessage(
          "Converting " + (d.source || file.name) + " into " +
          (d.filename || "the dictionary") + "\u2026 this can take a few minutes.",
          true
        );
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
    const nameInput = _el("dict_settings_name");
    return importFile(
      input && input.files ? input.files[0] : null,
      nameInput ? nameInput.value : ""
    );
  }

  /**
   * Delete one dictionary.  A dictionary a language still reads is not
   * deleted until the server's refusal is confirmed -- otherwise a
   * language would silently lose its gloss.
   */
  function remove(name, force) {
    if (!force && !window.confirm(
      "Delete " + name + "?  The phone app can no longer download it, and " +
      "any language using it loses its dictionary gloss."
    )) {
      return Promise.resolve();
    }
    _setBusy(true);
    const form = new FormData();
    form.append("name", name);
    if (force) form.append("force", "1");
    return fetch("/dict/remove", { method: "POST", body: form })
      .then(r => r.json().then(body => ({ status: r.status, body: body })))
      .then(res => {
        if (res.body && res.body.result === "in_use") {
          if (window.confirm(res.body.message + "  Delete it anyway?")) {
            return remove(name, true);
          }
          return refresh();
        }
        _setMessage(
          res.body && res.body.removed
            ? "Dictionary removed."
            : "There was nothing to remove.",
          true
        );
        return refresh();
      })
      .catch(() => _setMessage("Could not remove the dictionary.", false))
      .then(() => _setBusy(false));
  }

  return {
    describe,
    statusText,
    rowHtml,
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
