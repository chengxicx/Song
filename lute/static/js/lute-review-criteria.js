/**
 * Review-spec criteria builder.
 *
 * Turns the free-text criteria DSL into dropdowns: a 'Start from'
 * preset list, plus rows of [field] [operator] [value] joined by
 * and/or.  The generated string is written into the real `criteria`
 * textarea, which stays the field that gets submitted, so the server
 * side is unchanged.
 *
 * The textarea is also the escape hatch: criteria the builder can't
 * express (mixed and/or, future syntax) keep working -- the page just
 * switches to raw mode and stops regenerating.
 */
window.LuteReviewCriteria = (function () {
  "use strict";

  function read_json(id) {
    const node = document.getElementById(id);
    if (!node) return null;
    try {
      return JSON.parse(node.textContent);
    } catch (e) {
      return null;
    }
  }

  const META = read_json("criteria_meta") || {};
  const INITIAL = read_json("criteria_initial");

  const state = {
    rows: [],
    joiner: "and",
    raw: false,
  };

  let els = {};

  function field_spec(name) {
    return (META.fields || []).find((f) => f.name === name) || null;
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function add_options(select, options) {
    options.forEach((o) => {
      const opt = document.createElement("option");
      opt.value = o.value;
      opt.textContent = o.label;
      select.appendChild(opt);
    });
  }

  /* ---------- serialization (mirrors lute/review/criteria_builder.py) ---------- */

  function quote(v) {
    return '"' + String(v).replace(/"/g, "").trim() + '"';
  }

  function values_of(row) {
    return (row.values || []).map((v) => String(v).trim()).filter((v) => v);
  }

  function clause(row) {
    const spec = field_spec(row.field);
    if (!spec) return "";
    const op = spec.ops.indexOf(row.op) >= 0 ? row.op : spec.ops[0];
    const vals = values_of(row);

    if (spec.value_kind === "int" || spec.value_kind === "status") {
      const v = vals[0];
      if (v == null || !/^-?\d+$/.test(v)) return "";
      return row.field + " " + op + " " + parseInt(v, 10);
    }
    if (spec.value_kind === "language") {
      if (!vals[0]) return "";
      return "language " + op + " " + quote(vals[0]);
    }
    if (spec.value_kind === "tag") {
      const tags = vals.filter((v, i) => vals.indexOf(v) === i);
      if (tags.length === 0) return "";
      if (tags.length === 1) return row.field + ":" + quote(tags[0]);
      return row.field + ":[" + tags.map(quote).join(", ") + "]";
    }
    if (spec.value_kind === "has") {
      if (!vals[0]) return "";
      return "has:" + vals[0];
    }
    return "";
  }

  function serialize_rows(joiner, rows) {
    const parts = rows.map(clause).filter((c) => c !== "");
    const j = joiner === "or" ? "or" : "and";
    return parts.join(" " + j + " ");
  }

  function serialize() {
    return serialize_rows(state.joiner, state.rows);
  }

  /* ---------- value controls ---------- */

  function value_control(spec, value, onchange) {
    const vals = values_of({ values: value == null ? [] : [value] });
    const current = vals[0] == null ? "" : vals[0];

    if (spec.value_kind === "tag") {
      // Tags are free text with a datalist: any tag can be typed,
      // several are comma-separated.
      const input = el("input", "cb-value cb-tags");
      input.type = "text";
      input.setAttribute("list", "criteria_tag_options");
      input.placeholder = "tag, tag, ...";
      input.value = (value || []).join(", ");
      input.addEventListener("input", () => onchange(input.value));
      return input;
    }

    const select = el("select", "cb-value");
    if (spec.value_kind === "status") {
      add_options(select, [{ value: "", label: "(status)" }].concat(META.statuses || []));
    } else if (spec.value_kind === "language") {
      add_options(
        select,
        [{ value: "", label: "(language)" }].concat(
          (META.languages || []).map((n) => ({ value: n, label: n }))
        )
      );
    } else if (spec.value_kind === "has") {
      add_options(
        select,
        [{ value: "", label: "(choose)" }].concat(
          (META.has_options || []).map((n) => ({ value: n, label: n }))
        )
      );
    } else {
      add_options(select, [{ value: "", label: "(number)" }]);
    }
    select.value = current;
    select.addEventListener("change", () => onchange(select.value));
    return select;
  }

  /* ---------- row rendering ---------- */

  function render_rows() {
    const box = els.rows;
    box.innerHTML = "";

    if (state.rows.length === 0) {
      box.appendChild(
        el("p", "criteria-empty", "No conditions -- every learning term is included.")
      );
      return;
    }

    state.rows.forEach((row, idx) => {
      const spec = field_spec(row.field) || META.fields[0];
      const line = el("div", "criteria-row");

      const fieldsel = el("select", "cb-field");
      add_options(
        fieldsel,
        (META.fields || []).map((f) => ({ value: f.name, label: f.label }))
      );
      fieldsel.value = spec.name;
      fieldsel.addEventListener("change", () => {
        const next = field_spec(fieldsel.value);
        state.rows[idx] = { field: next.name, op: next.ops[0], values: [] };
        render_rows();
        sync();
      });

      const opsel = el("select", "cb-op");
      add_options(
        opsel,
        spec.ops.map((o) => ({
          value: o,
          label: (META.op_labels || {})[o] || o,
        }))
      );
      opsel.value = spec.ops.indexOf(row.op) >= 0 ? row.op : spec.ops[0];
      opsel.addEventListener("change", () => {
        state.rows[idx].op = opsel.value;
        sync();
      });

      const value = value_control(spec, row.values, (v) => {
        state.rows[idx].values =
          spec.value_kind === "tag"
            ? String(v).split(",").map((s) => s.trim())
            : [v];
        sync();
      });

      const remove = el("button", "cb-remove", "\u00D7");
      remove.type = "button";
      remove.title = "Remove this condition";
      remove.addEventListener("click", () => {
        state.rows.splice(idx, 1);
        render_rows();
        sync();
      });

      line.appendChild(fieldsel);
      line.appendChild(opsel);
      line.appendChild(value);
      line.appendChild(remove);
      box.appendChild(line);
    });
  }

  function render_presets() {
    const sel = els.preset;
    sel.innerHTML = "";
    sel.appendChild(new Option("(choose a starting point)", ""));

    const groups = {};
    (META.presets || []).forEach((p) => {
      if (!groups[p.group]) {
        groups[p.group] = document.createElement("optgroup");
        groups[p.group].label = p.group;
        sel.appendChild(groups[p.group]);
      }
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.label;
      opt.dataset.criteria = p.criteria;
      groups[p.group].appendChild(opt);
    });

    sel.addEventListener("change", () => {
      const p = (META.presets || []).find((x) => x.id === sel.value);
      if (!p) return;
      state.raw = false;
      state.rows = JSON.parse(JSON.stringify(p.rows));
      state.joiner = p.joiner;
      els.joiner.value = p.joiner;
      render_rows();
      sync();
    });
  }

  /* ---------- the two views stay in step ---------- */

  function set_raw_mode(is_raw, message) {
    state.raw = is_raw;
    els.builder.classList.toggle("criteria-raw", is_raw);
    els.warning.hidden = !is_raw;
    if (message) els.warning.textContent = message;
    // Text the builder can't show came from no preset.
    if (is_raw && els.preset) els.preset.value = "";
  }

  /**
   * Point 'Start from' at the preset these conditions came from, and
   * back at the placeholder once they have been edited by hand.  Every
   * preset option carries its own criteria string, so this is an exact
   * match against what would be saved.
   */
  function sync_preset_selection(text) {
    if (!els.preset) return;
    const match = Array.prototype.find.call(
      els.preset.options || [],
      (o) => o.dataset && o.dataset.criteria === text
    );
    els.preset.value = match ? match.value : "";
  }

  function sync() {
    if (state.raw) return;
    const text = serialize();
    els.text.value = text;
    els.preview.textContent = text || "(all learning terms)";
    sync_preset_selection(text);
  }

  function on_raw_input() {
    const text = els.text.value.trim();
    const parsed = window.LuteReviewCriteria._parse(text);
    if (parsed) {
      state.rows = parsed.rows;
      state.joiner = parsed.joiner;
      els.joiner.value = parsed.joiner;
      set_raw_mode(false, "");
      render_rows();
      els.preview.textContent = text || "(all learning terms)";
      sync_preset_selection(serialize());
    } else {
      set_raw_mode(
        true,
        "These criteria use syntax the condition builder can't show. " +
          "They will be saved exactly as typed; pick a starting point " +
          "above to go back to conditions."
      );
      els.preview.textContent = text || "(all learning terms)";
    }
  }

  /**
   * Client-side mirror of criteria_builder.parse_criteria.  Returns
   * null when the text can't be shown as rows.
   */
  function parse(text) {
    const s = (text || "").trim();
    if (s === "") return { joiner: "and", rows: [] };

    const parts = [];
    const joiners = new Set();
    let buf = "";
    let in_quote = false;
    let in_bracket = false;
    for (let i = 0; i < s.length; i++) {
      const ch = s[i];
      if (ch === '"') {
        in_quote = !in_quote;
        buf += ch;
        continue;
      }
      if (!in_quote) {
        if (ch === "[") in_bracket = true;
        else if (ch === "]") in_bracket = false;
        if (!in_bracket) {
          const kw = [" and ", " or "].find((k) => s.startsWith(k, i));
          if (kw) {
            parts.push(buf.trim());
            joiners.add(kw.trim());
            buf = "";
            i += kw.length - 1;
            continue;
          }
        }
      }
      buf += ch;
    }
    if (in_quote || in_bracket) return null;
    parts.push(buf.trim());
    if (joiners.size > 1) return null;

    const rows = [];
    for (const part of parts) {
      const row = parse_part(part);
      if (!row) return null;
      rows.push(row);
    }
    return { joiner: joiners.size ? [...joiners][0] : "and", rows: rows };
  }

  function parse_part(part) {
    if (!part) return null;
    let m = part.match(/^(status|parents\.count)\s*(<=|>=|<>|!=|==|=|<|>)\s*(-?\d+)$/);
    if (m) {
      let op = m[2];
      if (op === "=") op = "==";
      if (op === "<>") op = "!=";
      return { field: m[1], op: op, values: [m[3]] };
    }
    m = part.match(/^language\s*(:|==|=|!=)\s*"([^"]*)"$/);
    if (m) {
      return {
        field: "language",
        op: m[1] === "!=" ? "!=" : "==",
        values: [m[2]],
      };
    }
    m = part.match(/^(tags|parents\.tags|all\.tags)\s*:\s*(.+)$/);
    if (m) {
      const tags = (m[2].match(/"([^"]*)"/g) || []).map((t) => t.slice(1, -1));
      if (tags.length === 0) return null;
      return { field: m[1], op: ":", values: tags };
    }
    m = part.match(/^has\s*:\s*(\w+)$/);
    if (m) {
      if ((META.has_options || []).indexOf(m[1]) < 0) return null;
      return { field: "has", op: ":", values: [m[1]] };
    }
    return null;
  }

  /* ---------- init ---------- */

  function init() {
    els = {
      builder: document.getElementById("criteria_builder"),
      rows: document.getElementById("criteria_rows"),
      joiner: document.getElementById("criteria_joiner"),
      preset: document.getElementById("criteria_preset"),
      text: document.getElementById("criteria_text"),
      preview: document.getElementById("criteria_preview"),
      warning: document.getElementById("criteria_warning"),
      advanced: document.getElementById("criteria_advanced"),
      add: document.getElementById("criteria_add"),
      form: document.getElementById("review_spec_form"),
    };
    if (!els.builder || !els.text) return;
    if (!(META.fields || []).length) return;

    // Datalist for tag values.
    const dl = el("datalist", null);
    dl.id = "criteria_tag_options";
    (META.tags || []).forEach((t) => {
      const o = document.createElement("option");
      o.value = t;
      dl.appendChild(o);
    });
    document.body.appendChild(dl);

    render_presets();

    els.joiner.value = state.joiner;
    els.joiner.addEventListener("change", () => {
      state.joiner = els.joiner.value;
      sync();
    });

    els.add.addEventListener("click", () => {
      if (state.raw) {
        // Adding a condition means leaving raw mode; ask first.
        if (!window.confirm("Replace the advanced criteria with conditions?")) return;
        state.rows = [];
        set_raw_mode(false, "");
      }
      const first = META.fields[0];
      state.rows.push({ field: first.name, op: first.ops[0], values: [] });
      render_rows();
      sync();
    });

    let timer = null;
    els.text.addEventListener("input", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(on_raw_input, 250);
    });

    if (els.form) {
      els.form.addEventListener("submit", () => {
        if (!state.raw) els.text.value = serialize();
      });
    }

    if (INITIAL === null) {
      on_raw_input();
    } else {
      state.rows = INITIAL.rows;
      state.joiner = INITIAL.joiner;
      els.joiner.value = state.joiner;
      render_rows();
      sync();
    }
  }

  document.addEventListener("DOMContentLoaded", init);

  return {
    init: init,
    _parse: parse,
    _serialize: serialize,
    _serialize_rows: serialize_rows,
  };
})();
