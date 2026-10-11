/**
 * Harness for tests/unit/dict/test_dict_settings_js.py.
 *
 * Runs the real lute/static/js/dict-settings.js against a stub DOM,
 * fetch and XMLHttpRequest.  What needs pinning is the contract with
 * lute/dict/routes.py: the card reads `status.installed`, `status.title`,
 * `status.entries`, `status.size_mb`, `status.source`, `status.url`,
 * `task_id`, `state`, `processed`, `error` and `dictionary.entries`.  A
 * renamed field on either side does not throw -- it renders an empty
 * card, or a poll that never settles -- and no Python test can see that.
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
  "dict_settings_status",
  "dict_settings_installed",
  "dict_settings_url",
  "dict_settings_msg",
  "dict_settings_file",
  "dict_settings_import",
  "dict_settings_remove",
];

class FakeEl {
  constructor(id) {
    this.id = id;
    this.textContent = "";
    this.attrs = {};
    this.style = {};
    this.disabled = false;
    this.files = [];
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
if (input.installed === true) {
  elements.dict_settings_installed.style.display = "none";
}

const document = {
  getElementById: id => elements[id] || null,
  addEventListener: () => {},
};

const window = {
  confirm: () => (input.confirm === undefined ? true : input.confirm),
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
  fetchLog.push({ url: url, method: (opts && opts.method) || "GET" });
  if (url.indexOf("/dict/status") === 0) {
    return Promise.resolve(
      jsonResponse(input.statusBody || { result: "success", status: {} })
    );
  }
  if (url.indexOf("/dict/import_status") === 0) {
    if (statusQueue.length === 0) {
      return Promise.resolve(jsonResponse({ state: "finished", dictionary: {} }));
    }
    return Promise.resolve(jsonResponse(statusQueue.shift()));
  }
  if (url.indexOf("/dict/remove") === 0) {
    return Promise.resolve(jsonResponse(input.removeBody || { result: "success", removed: true }));
  }
  return Promise.reject(new Error("unexpected url " + url));
}

const uploadProgress = [];
const xhrLog = [];

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
  send() {
    const plan = input.xhr || { status: 200, body: { result: "success", task_id: "t1", source: "fixture.mdx" } };
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
  append() {}
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
    return result;
  }

  if (input.scenario === "render") {
    DictSettings.render(input.status);
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
    await DictSettings.importFile({ name: "fixture.mdx" });
    result.dom = snapshot();
    result.progress = uploadProgress;
    result.calls = fetchLog;
    result.uploads = xhrLog;
    return result;
  }

  if (input.scenario === "import_without_a_file") {
    await DictSettings.importFile(null);
    result.dom = snapshot();
    return result;
  }

  if (input.scenario === "remove") {
    await DictSettings.remove();
    result.dom = snapshot();
    result.calls = fetchLog;
    return result;
  }

  if (input.scenario === "remove_cancelled") {
    await DictSettings.remove();
    result.dom = snapshot();
    result.calls = fetchLog;
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
