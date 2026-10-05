/**
 * Harness for tests/unit/read/test_grammar_target_js.py.
 *
 * Runs the real lute/static/js/lute-commands.js and calls the two helpers
 * that decide which grammar card the term form's Grammar button jumps to:
 *
 *   grammarRunCoversWord(run, wordEl)
 *   grammarPickTarget(boundItems, targetSentence, targetWordEl)
 *
 * No browser: runCells only needs querySelectorAll(".textitem") and
 * textContent, so a handful of stub elements stand in for the reading page.
 * The rules under test are pure -- which card wins when several examples
 * share a sentence -- and neither a Python test (no DOM) nor a browser test
 * (the grammar engine decides what is on screen) can isolate them.
 *
 * stdout: JSON with the answers.
 */

const fs = require("fs");

const modPath = process.argv[2];

/* ---------- stub elements ---------- */

class El {
  constructor(tag, text) {
    this.tagName = (tag || "").toUpperCase();
    this.textContent = text || "";
    this.id = "";
    this.cells = null; // the .textitem children, when this is a sentence
  }
  querySelectorAll(sel) {
    return sel === ".textitem" && this.cells ? this.cells : [];
  }
  contains(node) {
    if (node === this) return true;
    return this.cells ? this.cells.indexOf(node) !== -1 : false;
  }
  getAttribute() {
    return null;
  }
}

// The reader's cells each carry their own leading space, so the concatenation
// is the sentence verbatim -- which is what runCells builds.
function sentenceWith(words) {
  const el = new El("span", words.join(""));
  el.cells = words.map((w) => new El("span", w));
  return { el: el, cells: el.cells };
}

const SENTENCE = "This short guide should get you going.";

const main = sentenceWith(["This", " short", " guide", " should", " get", " you", " going."]);
const other = sentenceWith(["Welcome", " to", " Lute", "!"]);

const shouldCell = main.cells[3];
const start = SENTENCE.indexOf("should");

// Two examples on the same sentence: one marks the clicked word, one marks
// an earlier word of the same sentence.
const coveringRun = { example: SENTENCE, spans: [[start, start + 6]], nodes: [main.el] };
const earlyRun = { example: SENTENCE, spans: [[0, 4]], nodes: [main.el] };

function card(id, run) {
  const el = new El("div");
  el.id = id;
  return { itemEl: el, examples: [{ runs: [run], nodes: run.nodes }] };
}

const early = card("early", earlyRun);
const covering = card("covering", coveringRun);
const unrelated = card("unrelated", {
  example: "Welcome to Lute!",
  spans: [],
  nodes: [other.el],
});

function name(picked) {
  return picked ? picked.itemEl.id : null;
}

/* ---------- the module under test ---------- */

global.document = {
  getElementById: () => null,
  querySelectorAll: () => [],
  createElement: (t) => new El(t),
  addEventListener: () => {},
  body: new El("body"),
};
global.window = { addEventListener: () => {}, removeEventListener: () => {} };

eval(fs.readFileSync(modPath, "utf8")); // eslint-disable-line no-eval

process.stdout.write(
  JSON.stringify({
    covers_clicked_word: grammarRunCoversWord(coveringRun, shouldCell),
    covers_another_word: grammarRunCoversWord(earlyRun, shouldCell),
    covers_nothing_without_a_word: grammarRunCoversWord(coveringRun, null),
    // The covering card is second in panel order and must still win.
    pick_prefers_covering: name(grammarPickTarget([early, covering], main.el, shouldCell)),
    // Nothing covers the clicked word: fall back to the first card on the
    // sentence, in panel order.
    pick_falls_back_to_first_on_sentence: name(
      grammarPickTarget([early, covering], main.el, other.cells[0])
    ),
    // A sentence no card mentions leaves nothing to jump to.
    pick_none_when_sentence_unmatched: name(
      grammarPickTarget([early, covering], other.el, shouldCell)
    ),
    // A media example may be split across adjacent sentence nodes.
    pick_matches_a_split_sentence: name(
      grammarPickTarget(
        [
          {
            itemEl: unrelated.itemEl,
            examples: [
              {
                runs: [{ example: SENTENCE, spans: [[start, start + 6]], nodes: [other.el, main.el] }],
                nodes: [other.el, main.el],
              },
            ],
          },
        ],
        main.el,
        shouldCell
      )
    ),
  })
);
