/**
 * Harness for tests/unit/dict/test_dict_settings_js.py.
 *
 * Runs the real lute/static/js/dict-settings.js against a stub DOM,
 * fetch and XMLHttpRequest.  What needs pinning is the contract with
 * lute/dict/routes.py: the card reads `dictionaries[].filename`,
 * `.title`, `.entries`, `.size_mb`, `.source`, `.url`, `.is_default`,
 * plus `task_id`, `state`, `processed`, `error`, `dictionary.entries`
 * and the remove payload's `result`/`removed`/`message`.  A renamed
 * field on either side does not throw -- it renders an empty card, or a
 * poll that never settles -- and no Python test can see that.
 *
 * One scenario per process; a JSON payload on stdin:
 *
 *   {"scenario": "poll", "importStatus": [ {...}, ... ]}
 *
 * stdout: one JSON object with the results the Python test asserts on.
 */

const fs = require("fs");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const modPath = process.argv[2];

/* ---------- a very small DOM ---------- */

const CARD_IDS = [
  "dict_settings_list",
  "dict_settings_msg",
  "dict_settings_file",
  "dict_settings_name",
  "dict_settings_import",
];

// Every message the card shows, in order.  The last one is what stays
// on screen, so a test that cares about an intermediate line ("building
// x.db", "4,000 entries read") needs the history, not the final value.
const messageLog = [];

class FakeEl {
  constructor(id) {
    this.id = id;
    this._text = "";
    this.innerHTML = "";
    this.value = "";
    this.attrs = {};
    this.style = {};
    this.disabled = false;
    this.files = [];
  }
  get textContent() {
    return this._text;
  }
  set textContent(value) {
    this._text = value;
    if (this.id === "dict_settings_msg") messageLog.push(value);
  }
  setAttribute(name, value) {
    this.attrs[name] = value;
  }
  getAttribute(name) {
    return this.attrs[name];
  }
}

const elements = {};
CARD_IDS.forEach(id => {
  elements[id] = new FakeEl(id);
});
if (input.selectedFile !== undefined) {
  elements.dict_settings_file.files = [input.selectedFile];
}
if (input.typedName !== undefined) {
  elements.dict_settings_name.value = input.typedName;
}

const document = {
  getElementById: id => elements[id] || null,
  addEventListener: () => {},
};

const confirmAnswers = Array.isArray(input.confirm)
  ? input.confirm.slice()
  : null;
let confirmCalls = 0;
const window = {
  confirm: () => {
    confirmCalls += 1;
    if (confirmAnswers === null) {
      return input.confirm === undefined ? true : input.confirm;
    }
    return confirmAnswers.length ? confirmAnswers.shift() : true;
  },
};

/* ---------- stubbed network ---------- */

const fetchLog = [];
const statusQueue = (input.importStatus || []).slice();

function jsonResponse(body, status) {
  return {
    status: status || 200,
    ok: (status || 200) < 300,
    json: () => Promise.resolve(body),
  };
}

function fetch(url, opts) {
  fetchLog.push({
    url: url,
    method: (opts && opts.method) || "GET",
    body: opts && opts.body ? opts.body.fields.slice() : null,
  });
  if (url.indexOf("/dict/status") === 0) {
    return Promise.resolve(
      jsonResponse(
        input.statusBody || { result: "success", dictionaries: [] }
      )
    );
  }
  if (url.indexOf("/dict/import_status") === 0) {
    if (statusQueue.length === 0) {
      return Promise.resolve(jsonResponse({ state: "finished", dictionary: {} }));
    }
    return Promise.resolve(jsonResponse(statusQueue.shift()));
  }
  if (url.indexOf("/dict/remove") === 0) {
    // An array runs one plan per call (last one repeats), so the
    // "a language still uses it" flow can be driven end to end.
    const plans = Array.isArray(input.removeBody)
      ? input.removeBody
      : [input.removeBody || { body: { result: "success", removed: true } }];
    const plan = plans.length > 1 ? plans.shift() : plans[0];
    return Promise.resolve(jsonResponse(plan.body, plan.status));
  }
  return Promise.reject(new Error("unexpected url " + url));
}

const uploadProgress = [];
const xhrLog = [];
const formDataLog = [];

class FakeXHR {
  constructor() {
    this.upload = {};
    this.status = 0;
    this.responseText = "";
  }
  open(method, url) {
    this.method = method;
    this.url = url;
    xhrLog.push({ method: method, url: url });
  }
  send(form) {
    // The default plan echoes the name the card asked for, the way the
    // server does (it derives one from the .mdx when none was sent).
    const asked = (form.fields || []).find(f => f[0] === "name");
    const plan = input.xhr || {
      status: 200,
      body: {
        result: "success",
        task_id: "t1",
        source: "fixture.mdx",
        filename: asked ? asked[1] : "fixture.db",
      },
    };
    if (this.upload.onprogress) {
      this.upload.onprogress({ lengthComputable: true, loaded: 1, total: 4 });
      uploadProgress.push(elements.dict_settings_msg.textContent);
    }
    this.status = plan.status;
    this.responseText = JSON.stringify(plan.body);
    if (this.onload) this.onload();
  }
}

class FakeFormData {
  constructor() {
    this.fields = [];
    formDataLog.push(this);
  }
  append(key, value) {
    this.fields.push([key, value]);
  }
}

/* ---------- load the module ---------- */

const source = fs.readFileSync(modPath, "utf8");
const factory = new Function(
  "document",
  "window",
  "fetch",
  "XMLHttpRequest",
  "FormData",
  "setTimeout",
  "module",
  source + "\nreturn DictSettings;"
);
const DictSettings = factory(
  document,
  window,
  fetch,
  FakeXHR,
  FakeFormData,
  setTimeout,
  { exports: {} }
);

/* ---------- scenarios ---------- */

function snapshot() {
  const out = {};
  CARD_IDS.forEach(id => {
    out[id] = {
      text: elements[id].textContent,
      html: elements[id].innerHTML,
      value: elements[id].value,
      style: elements[id].style,
      disabled: elements[id].disabled,
      href: elements[id].attrs.href || null,
    };
  });
  return out;
}

async function run() {
  const result = { scenario: input.scenario };

  if (input.scenario === "status_text") {
    result.text = DictSettings.statusText(input.status);
    result.described = DictSettings.describe(input.status);
    return result;
  }

  if (input.scenario === "row_html") {
    result.html = DictSettings.rowHtml(input.status);
    return result;
  }

  if (input.scenario === "render") {
    DictSettings.render(input.dictionaries);
    result.dom = snapshot();
    return result;
  }

  if (input.scenario === "poll") {
    try {
      result.resolved = await DictSettings.poll("t1", 0);
    } catch (e) {
      result.rejected = e.message;
    }
    result.messages = elements.dict_settings_msg.textContent;
    return result;
  }

  if (input.scenario === "import") {
    await DictSettings.importFile({ name: "fixture.mdx" }, input.name);
    result.dom = snapshot();
    result.progress = uploadProgress;
    result.calls = fetchLog;
    result.uploads = xhrLog;
    result.fields = formDataLog.map(f => f.fields);
    result.messageLog = messageLog;
    return result;
  }

  if (input.scenario === "import_selected") {
    await DictSettings.importSelected();
    result.dom = snapshot();
    result.uploads = xhrLog;
    result.fields = formDataLog.map(f => f.fields);
    result.messageLog = messageLog;
    return result;
  }

  if (input.scenario === "import_without_a_file") {
    await DictSettings.importFile(null);
    result.dom = snapshot();
    return result;
  }

  if (input.scenario === "remove") {
    await DictSettings.remove(input.removeName || "fixture.db");
    result.dom = snapshot();
    result.calls = fetchLog;
    result.fields = formDataLog.map(f => f.fields);
    result.confirms = confirmCalls;
    return result;
  }

  if (input.scenario === "remove_cancelled") {
    await DictSettings.remove(input.removeName || "fixture.db");
    result.dom = snapshot();
    result.calls = fetchLog;
    result.confirms = confirmCalls;
    return result;
  }

  throw new Error("unknown scenario " + input.scenario);
}

run()
  .then(result => {
    process.stdout.write(JSON.stringify(result));
  })
  .catch(e => {
    process.stdout.write(JSON.stringify({ error: e.message, stack: e.stack }));
    process.exitCode = 1;
  });
