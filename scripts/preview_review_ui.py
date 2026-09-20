"""
Generate a standalone preview of the review UI (criteria builder +
session card) for design review.

Not part of the app: it writes one HTML file that loads the *real*
review.css and lute-review-criteria.js, so the controls look right.

**This is a mock, not a test.**  Because it is hand-written it cannot
catch a mismatch with the real templates, nor with the order the page
loads things in -- it once looked perfect while the builder was dead in
the app, since the real page renders the module's JSON tags *after*
loading the script.  To check the real page, render the server's own
HTML and run the JS against it (see the lute-ui-verify skill).

Run:  venv/bin/python scripts/preview_review_ui.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lute.review import criteria_builder as cb  # noqa: E402

DEMO_LANGUAGES = ["Japanese", "Spanish", "English"]
DEMO_TAGS = ["vocab", "n2", "mining", "keigo"]

META = {
    "fields": cb.FIELD_SPECS,
    "op_labels": cb._OP_LABELS,  # pylint: disable=protected-access
    "statuses": [
        {"value": str(k), "label": f"{k} - {v}"}
        for k, v in sorted(cb.STATUS_LABELS.items())
    ],
    "languages": DEMO_LANGUAGES,
    "tags": DEMO_TAGS,
    "has_options": cb._HAS_OPTIONS,  # pylint: disable=protected-access
    "presets": cb.presets(DEMO_LANGUAGES, DEMO_TAGS),
}

INITIAL = cb.parse_criteria(cb.default_criteria())

OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".workbuddy-ai",
    "preview",
    "review-ui-preview.html",
)

# Sample card content, so the preview shows a realistic card.
TERM = "犬"
READING = "いぬ"
SENTENCE = "彼は<b>犬</b>を飼っている。"
TRANSLATION = "dog"

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review UI preview</title>
<link rel="stylesheet" href="../../lute/static/css/review.css">
<style>
  /* Minimal stand-ins for the app shell (base.html + styles.css), so
     the preview is self-contained but still uses the real review.css. */
  body {
    --background-color: #f7f3ea;
    --panel-bg: #ffffff;
    --font-color: #2c2a29;
    --btn-accent: #007aff;
    --btn-accent-hover: #0071e3;
    --form-border-color: #ddd;
    --form-border-radius: 3px;
    background: var(--background-color);
    color: var(--font-color);
    font: 100%/1.4 -apple-system, BlinkMacSystemFont, "SF Pro Text",
      "Helvetica Neue", "PingFang SC", sans-serif;
    margin: 0;
    padding: 24px 28px 80px;
  }
  .wrap { max-width: 880px; margin: 0 auto; }
  h1 { font-size: 1.5em; margin: 0 0 4px; }
  h2 { font-size: 1.15em; margin: 40px 0 4px; }
  h3 { font-size: 1em; margin: 26px 0 8px; }
  .sub { color: #6b6863; margin: 0 0 8px; }
  .note { color: #6b6863; font-size: 0.9em; }
  hr.sep { border: none; border-top: 1px solid #e2ddd3; margin: 40px 0 0; }
  .btn {
    font: inherit; padding: 6px 14px; border-radius: 6px; cursor: pointer;
    border: 1px solid transparent;
  }
  .btn-primary { background: var(--btn-accent); color: #fff; border-color: var(--btn-accent); }
  .btn-secondary { background: transparent; color: var(--font-color); border-color: #cfc9bd; }
  .form-control, select, input[type=text], input[type=number] {
    font: inherit; padding: 5px 8px; border: 1px solid var(--form-border-color);
    border-radius: var(--form-border-radius); background: #fff; color: var(--font-color);
  }
  .form-control-label { font-weight: 600; }
  table#reviewspec td { padding: 6px 12px 6px 0; vertical-align: top; }
  table.settingstable { border-collapse: collapse; }
  table.settingstable td { padding: 6px 12px 10px 0; vertical-align: top; }
  .form-action-row { display: flex; gap: 8px; }
  .form-largetextarea { font-family: ui-monospace, Menlo, monospace; font-size: 0.9em; }
  .before-after { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
  .ba-box { border: 1px dashed #cfc9bd; border-radius: 8px; padding: 12px 14px; }
  .ba-box h4 { margin: 0 0 8px; font-size: 0.85em; text-transform: uppercase;
    letter-spacing: .05em; color: #6b6863; }
  .old-card { border: 1px solid #ddd; padding: 18px; text-align: center; border-radius: 4px; }
  .old-front { font-size: 2.2em; margin: 20px 0; }
  .old-actions button { margin: 0 5px; }
  code { background: rgba(0,0,0,.05); padding: 1px 4px; border-radius: 3px; }
  ul.note { padding-left: 18px; }
  ul.note li { margin-bottom: 5px; }
</style>
</head>
<body>
<div class="wrap">

<h1>Review UI &mdash; proposed redesign</h1>
<p class="sub">Live preview. This page loads the real
<code>review.css</code> and <code>lute-review-criteria.js</code>, so the
controls below are the ones the app renders.</p>

<hr class="sep">
<h2>1. Criteria: type it &rarr; pick it</h2>
<p class="sub">Instead of a blank textarea, the rule is built from
dropdowns. The presets are the default options; the generated string is
shown live, and the raw form is still there under Advanced.</p>

<form id="review_spec_form" method="POST" onsubmit="return false;">
  <table id="reviewspec">
  <tbody>
    <tr>
      <td class="form-control-label">Name</td>
      <td>
        <input type="text" class="form-control" value="Level 2 and up"
               style="min-width: 240px;">
        <div class="review-hint">A label for this rule, e.g. "Level 2 and up".</div>
      </td>
    </tr>
    <tr>
      <td>Which terms?</td>
      <td>
        <div id="criteria_builder" class="criteria-builder">
          <div class="criteria-preset">
            <label for="criteria_preset">Start from</label>
            <select id="criteria_preset"></select>
            <span class="review-hint">Pick one to fill the conditions, then adjust.</span>
          </div>
          <div class="criteria-head">
            <span>Include a term when</span>
            <select id="criteria_joiner">
              <option value="and">all of these are true</option>
              <option value="or">any of these is true</option>
            </select>
          </div>
          <div id="criteria_rows"></div>
          <button type="button" class="btn btn-secondary" id="criteria_add">
            + Add condition
          </button>
          <p class="criteria-warning" id="criteria_warning" hidden></p>
        </div>

        <details id="criteria_advanced">
          <summary>Advanced: write the criteria directly</summary>
          <p class="note">Criteria use the same syntax as Anki exports:
            <code>status &gt; 1</code>, <code>language == "Japanese"</code>,
            <code>tags:["vocab"]</code>, <code>parents.count &gt;= 1</code>,
            <code>has:image</code>, combined with <code>and</code> / <code>or</code>.</p>
          <textarea class="form-largetextarea" id="criteria_text" rows="3" cols="60"></textarea>
        </details>

        <p class="criteria-preview">Saved as: <code id="criteria_preview"></code></p>
      </td>
    </tr>
    <tr>
      <td>Card types</td>
      <td>
        <p><input type="checkbox" checked> Recognition (see the word, recall the meaning)</p>
        <p><input type="checkbox"> Recall, typing (see the meaning, type the word)</p>
        <p><input type="checkbox" checked> Cloze (the word is blanked out of a real sentence)</p>
        <div class="review-hint">Cloze cards need the term to appear in a
          sentence you have read; terms without one are skipped on sync.</div>
      </td>
    </tr>
  </tbody>
  </table>
</form>
<p class="note">Try it: pick a preset, switch a field to
<em>Language</em> or <em>Term has tag</em>, add a condition, flip the
joiner to <em>or</em>. The line at the bottom is exactly what gets saved.
Then paste something the builder cannot express into Advanced (e.g.
<code>status &gt;= 2 and tags:["a"] or language == "X"</code>) to see the
fallback: the builder greys out and the text is kept verbatim.</p>

<hr class="sep">
<h2>2. The card</h2>
<p class="sub">One fixed card shell with three regions &mdash; question,
answer, grading &mdash; so nothing moves when you reveal. Grade buttons
use Anki's colours, and every action has a key.</p>

<h3>Recognition &mdash; question and answer</h3>
<div class="rv-topbar" style="max-width: none; margin-bottom: 10px;">
  <div class="rv-progress">
    <div class="rv-progress-track"><div class="rv-progress-fill" style="width: 33%"></div></div>
    <span class="rv-progress-text">4 / 12</span>
  </div>
  <button class="btn btn-secondary">Undo <kbd>Ctrl+Z</kbd></button>
</div>
<div class="before-after">
  <div class="ba-box">
    <h4>Question</h4>
    <div class="rv-card">
      <div class="rv-card-head">
        <span class="rv-badge">Recognition</span>
        <span class="rv-prompt">Recall the meaning</span>
      </div>
      <div class="rv-question"><div class="rv-term">__TERM__</div></div>
      <div class="rv-actions">
        <button class="btn btn-secondary">Show answer <kbd>Space</kbd></button>
      </div>
    </div>
  </div>
  <div class="ba-box">
    <h4>After revealing</h4>
    <div class="rv-card">
      <div class="rv-card-head">
        <span class="rv-badge">Recognition</span>
        <span class="rv-prompt">Recall the meaning</span>
      </div>
      <div class="rv-question"><div class="rv-term">__TERM__</div></div>
      <div class="rv-answer">
        <p class="rv-reading">__READING__</p>
        <p class="rv-sentence">__SENTENCE__</p>
        <p class="rv-translation">__TRANSLATION__</p>
      </div>
      <div class="rv-grades">
        <button class="rv-grade rv-grade-1"><span class="rv-grade-key">1</span><span class="rv-grade-label">Again (1m)</span></button>
        <button class="rv-grade rv-grade-2"><span class="rv-grade-key">2</span><span class="rv-grade-label">Hard (10m)</span></button>
        <button class="rv-grade rv-grade-3"><span class="rv-grade-key">3</span><span class="rv-grade-label">Good (1d)</span></button>
        <button class="rv-grade rv-grade-4"><span class="rv-grade-key">4</span><span class="rv-grade-label">Easy (4d)</span></button>
      </div>
    </div>
  </div>
</div>

<h3>Cloze, typed answer checked</h3>
<div class="rv-card" style="max-width: 620px; margin: 0 auto;">
  <div class="rv-card-head">
    <span class="rv-badge">Cloze</span>
    <span class="rv-prompt">Fill in the blank</span>
  </div>
  <div class="rv-question">
    <div class="rv-sentence rv-sentence-front">
      彼は<span class="cloze-blank">[...]</span>を飼っている。
    </div>
  </div>
  <input type="text" class="rv-typing" value="__TERM__" disabled>
  <div class="rv-answer">
    <p class="rv-banner rv-ok">Correct</p>
    <div class="rv-term rv-answer-term">__TERM__</div>
    <p class="rv-reading">__READING__</p>
    <p class="rv-sentence">__SENTENCE__</p>
    <p class="rv-translation">__TRANSLATION__</p>
  </div>
  <div class="rv-grades">
    <button class="btn btn-primary">Next <kbd>Space</kbd></button>
  </div>
</div>

<hr class="sep">
<h2>3. Undo, and the settings page</h2>
<p class="sub">Grading used to be one-way. The pre-review state is now saved
alongside each review log, so the last grade can be reversed exactly &mdash;
and the daily new-card allowance comes back with it.</p>

<h3>Review settings</h3>
<div class="ba-box" style="max-width: 720px;">
  <table class="settingstable">
    <tr>
      <td class="form-control-label">Desired retention</td>
      <td>
        <input type="number" step="0.01" min="0.5" max="0.99" value="0.9"
               class="form-control">
        <div class="review-hint">The share of cards FSRS expects you to still
          remember when they come up, between 0.5 and 0.99. Higher means shorter
          intervals and more reviews per day; 0.9 is the usual starting point.</div>
      </td>
    </tr>
    <tr>
      <td class="form-control-label">Max new cards per day</td>
      <td>
        <input type="number" step="1" min="0" value="20" class="form-control">
        <div class="review-hint">How many never-seen cards one day's sessions may
          introduce. 0 turns new cards off, so you only revise what is already
          queued.</div>
      </td>
    </tr>
  </table>
  <div class="form-action-row" style="margin-top: 12px;">
    <button class="btn btn-primary">Save</button>
    <button class="btn btn-secondary">Cancel</button>
  </div>
</div>
<p class="note">Reachable from the dashboard's <em>Settings</em> button.
Both values were previously only settable in the database or by CLI.</p>

<hr class="sep">
<h2>4. What changed on the card</h2>
<div class="before-after">
  <div class="ba-box">
    <h4>Before</h4>
    <div class="old-card">
      <div class="old-front">__TERM__</div>
      <div class="old-actions">
        <button class="btn btn-secondary">Check</button>
        <button class="btn btn-secondary">Show answer</button>
      </div>
    </div>
    <ul class="note">
      <li>Recognition cards offer a "Check" button with nothing to check.</li>
      <li>Four identical grey buttons &mdash; no sense of Again vs Easy.</li>
      <li>No keys, no progress bar, no hint of which card type you are on.</li>
      <li>The answer is appended below the buttons, so they shift on reveal.</li>
      <li>Nothing distinguishes "how far in am I".</li>
    </ul>
  </div>
  <div class="ba-box">
    <h4>After</h4>
    <ul class="note">
      <li>One action per card type: recognition only reveals.</li>
      <li>Again / Hard / Good / Easy carry Anki's colours and their interval.</li>
      <li><kbd>Space</kbd> reveals, <kbd>1</kbd>&ndash;<kbd>4</kbd> grade,
        <kbd>Enter</kbd> checks a typed answer.</li>
      <li>Progress bar, card-type badge, and a prompt naming the task.</li>
      <li>The answer sits in its own bordered region; the buttons never move.</li>
      <li>Typed answers get a clear Correct / Not quite banner.</li>
    </ul>
  </div>
</div>

</div>

<script type="application/json" id="criteria_meta">__META__</script>
<script type="application/json" id="criteria_initial">__INITIAL__</script>
<script src="../../lute/static/js/lute-review-criteria.js"></script>
</body>
</html>
"""


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    html = TEMPLATE
    for key, value in (
        ("__META__", json.dumps(META)),
        ("__INITIAL__", json.dumps(INITIAL)),
        ("__SENTENCE__", SENTENCE),
        ("__TRANSLATION__", TRANSLATION),
        ("__READING__", READING),
        ("__TERM__", TERM),
    ):
        html = html.replace(key, value)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
