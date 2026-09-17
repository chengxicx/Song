/* lute-commands.js
   --------------------------------------------------------------
   Sentence translation, theme/highlight toggles, page operations,
   hotkeys, and bulk status updates.
   --------------------------------------------------------------
   Split out of lute.js.  Every script under static/js shares one
   global scope and is loaded in order (see templates/base.html),
   so splitting the file changes nothing at runtime.
*/


/** SENTENCE TRANSLATIONS *************************/

// LUTE_SENTENCE_LOOKUP_DICTS is rendered in templates/read/index.html.
// Hitting "t" repeatedly cycles through the uris.  Moving to a new
// sentence resets the order.

var LUTE_LAST_SENTENCE_TRANSLATION_TEXT = '';
var LUTE_CURR_SENTENCE_TRANSLATION_DICT_INDEX = 0;

/** Cycle through the LUTE_SENTENCE_LOOKUP_DICTS.
 * If the current sentence is the same as the last translation,
 * move to the next sentence dictionary; otherwise start the cycle
 * again (from index 0).
 */
let _get_translation_dict_index = function(sentence) {
  const dict_count = LUTE_SENTENCE_LOOKUP_DICTS.length;
  if (dict_count == 0)
    return 0;
  let new_index = LUTE_CURR_SENTENCE_TRANSLATION_DICT_INDEX;
  if (LUTE_LAST_SENTENCE_TRANSLATION_TEXT != sentence) {
    // New sentence, start at beginning.
    new_index = 0;
  }
  else {
    // Same sentence, next dict.
    new_index += 1;
    if (new_index >= dict_count)
      new_index = 0;
  }
  LUTE_LAST_SENTENCE_TRANSLATION_TEXT = sentence;
  LUTE_CURR_SENTENCE_TRANSLATION_DICT_INDEX = new_index;
  return new_index;
}


let show_translation_for_text = function(text) {
  if (text == '')
    return;

  if (LUTE_SENTENCE_LOOKUP_DICTS.length == 0) {
    console.log('No sentence translation dictionaries configured.');
    return;
  }

  const dict_index = _get_translation_dict_index(text);
  const dict = LUTE_SENTENCE_LOOKUP_DICTS[dict_index];

  const lookup = encodeURIComponent(text);
  let url = dict.url.replace('[LUTE]', lookup);
  url = url.replace('###', lookup);  // TODO remove_old_###_placeholder: remove
  if (dict.dicttype == "popuphtml") {
    let settings = 'width=800, height=600, scrollbars=yes, menubar=no, resizable=yes, status=no';
    if (LUTE_USER_SETTINGS.open_popup_in_new_tab)
      settings = null;
    LutePopups.open_popup(url, settings);
  }
  else {
    top.frames.wordframe.location.href = url;
    $('#read_pane_right').css('grid-template-rows', '1fr 0');
  }

};


/**
 * Get all selected words, post their IDs.
 */
function send_selected_terms_to_anki() {
  let elements = $('span.kwordmarked').toArray().concat($('span.wordhover').toArray());
  if (elements.length == 0)
    return;
  const word_ids = elements.map(el => $(el).data("wid"));

  function _get_sentence(el) {
    const el_sentence_id = $(el).data('sentence-id');
    const selector = `span.textitem[data-sentence-id="${el_sentence_id}"]`;
    const tis = $(selector).toArray();
    return _get_textitems_text(tis);
  }

  function _get_sentences_dict(elements) {
    ret = {};
    elements.forEach(function(el) {
      const wid = $(el).data("wid");
      ret[wid] = _get_sentence(el);
    });
    return ret;
  }
  const termid_sentences = _get_sentences_dict(elements);

  function add_tooltip(term_id, results) {
    $('.ui-tooltip').remove();
    elements.forEach(function(el) {
      if ($(el).data("wid") == term_id) {
        _show_element_message_tooltip(el, "Anki exports", results, 0);
      }
    });
  }

  const ANKI_CONNECT_URL = LUTE_USER_SETTINGS["ankiconnect_url"];
  LuteAnki.post_anki_cards(
    ANKI_CONNECT_URL, word_ids, termid_sentences, add_tooltip
  ).catch(error => {
    console.error("ERROR:", error);
    alert(error.message);
  });
}


// Get all the word ids on the current page, open new tab with just those terms.
function open_term_list_for_current_page() {
  const ids = new Set();
  $('span.word').each(function () {
    ids.add($(this).data("wid"));
  });
  if (ids.length == 0)
    return; // Nothing to do.

  const idarray = Array.from(ids);
  const idlist = idarray.join('+');

  // Pass the bookid and pagenum so datatables can
  // use those for the savedState key.
  const bookid = $("#book_id").val();
  const pagenum = $("#page_num").val();
  const url = `/term/index?bookid=${bookid}&pagenum=${pagenum}&termids=${idlist}`;
  window.open(url);
}


/** Show the translation using the next dictionary. */
function handle_translate(span_attribute) {
  const tis = get_textitems_spans(span_attribute);
  const text = _get_textitems_text(tis);
  show_translation_for_text(text);
}


/**
 * 调用后端 /read/grammar_analysis 分析当前页语法。结果不是悬浮浮层，而是
 * 直接渲染在阅读页右侧栏 #read_pane_right 内，占满整列（顶部词形编辑区 +
 * 底部词典区）。点击单词会恢复右侧栏默认的词形表单 + 词典视图。
 */
function open_grammar_analysis() {
  const bookid = $("#book_id").val();
  const pagenum = $("#page_num").val();
  // Analyse only the current sub-screen: the reader splits one Lute page
  // into screen-height groups and only the active group's <p> elements are
  // visible.  Collect their text (skip manga/pdf, which have no text nodes).
  let snippet = "";
  const theText = document.getElementById("thetext");
  if (theText && !theText.classList.contains("manga-text-container")) {
    // Join paragraphs with a newline so subtitle/transcript lines (which
    // usually carry no 。/！) stay separable on the analysis side instead of
    // collapsing the whole page into one pseudo-sentence.
    snippet = Array.from(theText.querySelectorAll(":scope > p"))
      .filter(function (p) { return p.style.display !== "none" && p.textContent; })
      .map(function (p) { return p.textContent; })
      .join("\n");
  }
  const url = `/read/grammar_analysis/${bookid}/${pagenum}` +
    (snippet ? "?text=" + encodeURIComponent(snippet) : "");
  const pane = document.getElementById("read_pane_right");

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[c];
    });
  }

  // Remove the analysis view and restore the right pane to its default
  // (term-form + dictionary) state.  Exposed globally so clicking a word
  // (LuteTermFormOpened) can restore the default view.
  function closeGrammarAnalysis() {
    $("#grammar-analysis-panel").remove();
    if (!pane) return;
    pane.classList.remove("grammar-mode");
    if (window.matchMedia && window.matchMedia("(max-width: 980px)").matches) {
      pane.classList.remove("open-dict");
      pane.style.removeProperty("transform");
      pane.style.removeProperty("opacity");
      const btm = document.querySelector(".btm-margin-container");
      if (btm) {
        btm.classList.remove("open-dict");
        btm.style.removeProperty("margin-top");
      }
    }
  }
  window.closeGrammarAnalysis = closeGrammarAnalysis;

  // Drop any previous analysis before rendering a new one.
  closeGrammarAnalysis();

  // On small screens the pane is translated off-screen until a term opens;
  // bring it up so the analysis is visible.
  if (pane && window.matchMedia && window.matchMedia("(max-width: 980px)").matches) {
    pane.classList.add("open-dict");
    pane.style.transform = "translateY(0)";
    pane.style.opacity = "1";
    const btm = document.querySelector(".btm-margin-container");
    if (btm) btm.classList.add("open-dict");
  }

  function header() {
    return (
      '<div class="grammar-analysis-panel__header">' +
      '<span class="grammar-analysis-panel__title">Grammar Analysis</span>' +
      '<span id="grammar-count" class="grammar-analysis-panel__badge">…</span>' +
      '<button type="button" class="grammar-analysis-panel__close" aria-label="Close">&times;</button>' +
      "</div>"
    );
  }

  const panel = $("<div/>", {
    id: "grammar-analysis-panel",
    class: "grammar-analysis-panel",
  }).html(header() + '<div class="grammar-analysis-panel__body"><div class="grammar-analysis-panel__state">Analyzing…</div></div>');

  if (pane) {
    pane.classList.add("grammar-mode");
    pane.appendChild(panel[0]);
  } else {
    $("body").append(panel);
  }

  // Start the panel at its own stored size (independent of the reading text
  // size).  On first open, seed it from the reader's font size.
  if (window.convertPixelsToRem) {
    let gfs = parseFloat(localStorage.getItem("grammarFontSize"));
    if (!isFinite(gfs)) {
      const base = document.querySelector("#thetext span.textitem");
      const defaultPx = base ? parseFloat(window.getComputedStyle(base).fontSize) : 14;
      gfs = parseFloat(localStorage.getItem("fontSize")) || defaultPx;
    }
    panel[0].style.fontSize = window.convertPixelsToRem(clamp(gfs, 8, 60)) + "rem";
  }

  panel.find(".grammar-analysis-panel__close").on("click", window.closeGrammarAnalysis);

  $.getJSON(url)
    .done(function (data) {
      let bodyHtml;
      if (!data || data.length === 0) {
        bodyHtml = '<div class="grammar-analysis-panel__state">No known grammar points detected on this page.</div>';
      } else {
        // Render one example sentence, wrapping the backend-reported matched
        // words (exact character offsets) in <mark>.
        function renderExample(ex) {
          var s = String(ex.sentence || "").replace(/🔊/g, "");
          var ranges = (ex.matches || [])
            .map(function (m) { return [m.start, m.end]; })
            .filter(function (r) { return r[0] >= 0 && r[1] > r[0] && r[1] <= s.length; });
          if (!ranges.length) return escapeHtml(s);
          ranges.sort(function (a, b) { return a[0] - b[0]; });
          var merged = [ranges[0]];
          for (var i = 1; i < ranges.length; i++) {
            var last = merged[merged.length - 1];
            if (ranges[i][0] <= last[1]) last[1] = Math.max(last[1], ranges[i][1]);
            else merged.push(ranges[i]);
          }
          var html = "", pos = 0;
          merged.forEach(function (r) {
            html += escapeHtml(s.slice(pos, r[0])) +
              '<mark class="grammar-item__match">' + escapeHtml(s.slice(r[0], r[1])) + "</mark>";
            pos = r[1];
          });
          return html + escapeHtml(s.slice(pos));
        }
        const items = data
          .map(function (g) {
            const level = g.level ? '<span class="grammar-item__level">' + escapeHtml(g.level) + "</span>" : "";
            const desc = g.desc ? '<div class="grammar-item__desc">' + escapeHtml(g.desc) + "</div>" : "";
            const examples = (g.examples || []).map(function (ex) {
              return '<div class="grammar-item__example">' + renderExample(ex) + "</div>";
            }).join("");
            return (
              '<div class="grammar-item grammar-item--' +
              escapeHtml(g.level || "N") + '">' +
              '<div class="grammar-item__head">' +
              level +
              '<span class="grammar-item__name">' + escapeHtml(g.name) + "</span>" +
              "</div>" +
              desc +
              '<div class="grammar-item__examples">' + examples + "</div>" +
              "</div>"
            );
          }).join("");
        bodyHtml = '<div class="grammar-analysis-panel__body">' + items + "</div>";
      }
      panel.html(header() + bodyHtml);
      panel.find("#grammar-count").text(
        data.length + (data.length === 1 ? " point" : " points")
      );
      panel.find(".grammar-analysis-panel__close").on("click", window.closeGrammarAnalysis);

      // Cross-highlight: hovering a grammar entry on the right draws a blue
      // ring around the whole example sentence and an amber glow around the
      // matched words inside it -- never the whole paragraph.
      var textRoot = document.getElementById("thetext");
      if (textRoot) {
        var ringLayer = document.createElement("div");
        ringLayer.className = "grammar-ring-layer";
        document.body.appendChild(ringLayer);
        var activeRings = [];

        function updateActiveRings() {
          activeRings.forEach(function (r) {
            var u = unionRect(r.cells.map(function (el) { return el.getBoundingClientRect(); }));
            if (u) positionRing(r.ring, u);
          });
        }
        window.addEventListener("scroll", updateActiveRings, { passive: true });
        window.addEventListener("resize", updateActiveRings);

        function hideAllRings() {
          activeRings.forEach(function (r) { r.ring.style.display = "none"; });
          activeRings = [];
        }

        function unionRect(rects) {
          var top = Infinity, left = Infinity, bottom = -Infinity, right = -Infinity;
          rects.forEach(function (r) {
            if (!r.width && !r.height) return;
            top = Math.min(top, r.top);
            left = Math.min(left, r.left);
            bottom = Math.max(bottom, r.bottom);
            right = Math.max(right, r.right);
          });
          if (top === Infinity) return null;
          return { top: top, left: left, width: right - left, height: bottom - top };
        }

        function positionRing(ring, rect, pad) {
          if (pad === undefined) pad = 3;
          ring.style.display = "block";
          ring.style.left = (rect.left - pad) + "px";
          ring.style.top = (rect.top - pad) + "px";
          ring.style.width = (rect.width + pad * 2) + "px";
          ring.style.height = (rect.height + pad * 2) + "px";
        }

        // Match text with only letters/numbers so punctuation or the 🔊
        // marker never interferes with locating a phrase.
        function stripText(t) {
          return (t || "").replace(/🔊/g, "").replace(/[^\p{L}\p{N}]/gu, "");
        }

        // Full text for offset matching: keep punctuation so the backend's
        // character offsets line up with the rendered cells; drop only the
        // reader's display artifacts (🔊 / zero-width space).
        function cleanText(t) {
          return (t || "").replace(/🔊/g, "").replace(/\u200b/gi, "");
        }

        // Media-driven books (mp3/subtitles) split one example across several
        // adjacent phrase nodes, so work on .textsentence (else .textrow/p).
        var phraseNodes = textRoot.querySelectorAll(".textsentence");
        if (!phraseNodes.length) {
          phraseNodes = textRoot.querySelectorAll(".textrow, p");
        }
        var sentences = Array.prototype.slice.call(phraseNodes).map(function (el) {
          return { el: el, t: stripText(el.textContent) };
        });

        // All .textitem cells of a node run, with their cleaned text and
        // offsets inside the run's concatenated text.
        function runCells(nodes) {
          var cells = [];
          nodes.forEach(function (n) {
            var items = n.querySelectorAll(".textitem");
            if (items.length) {
              items.forEach(function (it) {
                cells.push({ el: it, t: cleanText(it.textContent) });
              });
            } else {
              cells.push({ el: n, t: cleanText(n.textContent) });
            }
          });
          return cells;
        }

        // Ring the whole sentence that contains a match (blue outline), and
        // draw one amber box around each contiguous run of matched words,
        // using the backend's exact character offsets.  Adjacent cells (a
        // word plus its trailing space) merge into a single box so a phrase
        // like "는 것" never looks like several separate boxes.  Falls back
        // to ringing the whole run when the example text can't be located.
        function planRun(run) {
          var example = run.example || "";
          var spans = run.spans || [[0, example.length]];
          var cells = runCells(run.nodes);
          var plan = [];
          if (example && cells.length) {
            var full = "", starts = [];
            cells.forEach(function (c) { starts.push(full.length); full += c.t; });
            var pos = full.indexOf(example);
            if (pos !== -1) {
              var wordBoxes = [];
              spans.forEach(function (sp) {
                var s = pos + sp[0], e = pos + sp[1];
                // Cell indices overlapping the span, then grouped into
                // contiguous runs (consecutive cells) for one box per run.
                var idxs = [];
                cells.forEach(function (c, idx) {
                  var cStart = starts[idx], cEnd = cStart + c.t.length;
                  if (cEnd > s && cStart < e) idxs.push(idx);
                });
                for (var i = 0; i < idxs.length; i++) {
                  var j = i;
                  while (j + 1 < idxs.length && idxs[j + 1] === idxs[j] + 1) j++;
                  var runEls = [];
                  for (var k = i; k <= j; k++) runEls.push(cells[idxs[k]].el);
                  var u = unionRect(runEls.map(function (el) { return el.getBoundingClientRect(); }));
                  if (u) wordBoxes.push({ rect: u, els: runEls });
                  i = j;
                }
              });
              var all = cells.map(function (c) { return c.el.getBoundingClientRect(); });
              var uAll = unionRect(all);
              if (uAll) {
                plan.push({
                  rect: uAll,
                  els: cells.map(function (c) { return c.el; }),
                  wordBoxes: wordBoxes
                });
              }
            }
          }
          if (!plan.length) {
            var all2 = cells.map(function (c) { return c.el.getBoundingClientRect(); });
            var u2 = unionRect(all2);
            if (u2) plan.push({ rect: u2, els: cells.map(function (c) { return c.el; }), wordBoxes: [] });
          }
          return plan;
        }

        // Contiguous runs of sentence nodes whose combined text contains the
        // target phrase (ignoring whitespace and punctuation).  Prefer a
        // single sentence node that contains the phrase outright; only when
        // none does (a phrase split across adjacent nodes, e.g. in media
        // books) do we span the minimal set of sentences.
        function findRuns(want) {
          var nodes = [];
          var i;
          for (i = 0; i < sentences.length; i++) {
            if (sentences[i].t.indexOf(want) !== -1) {
              nodes.push(sentences[i].el);
            }
          }
          if (nodes.length) return nodes;
          for (i = 0; i < sentences.length; i++) {
            var acc = sentences[i].t;
            for (var j = i + 1; j < sentences.length; j++) {
              acc += sentences[j].t;
              if (acc.indexOf(want) !== -1) {
                for (var k = i; k <= j; k++) nodes.push(sentences[k].el);
                return nodes;
              }
              if (acc.length > want.length + 60) break;
            }
          }
          return nodes;
        }

        var dataItems = data || [];
        Array.prototype.forEach.call(
          panel[0].querySelectorAll(".grammar-item"),
          function (item, itemIdx) {
            var g = dataItems[itemIdx];
            if (!g) return;
            var exampleEls = item.querySelectorAll(".grammar-item__example");
            function showRings(runs) {
              hideAllRings();
              var rings = [];
              runs.forEach(function (run) {
                planRun(run).forEach(function (p) {
                  var ring = document.createElement("div");
                  ring.className = "grammar-ring";
                  ringLayer.appendChild(ring);
                  positionRing(ring, p.rect);
                  rings.push({ ring: ring, cells: p.els });
                  // One amber box per contiguous run of matched words.
                  p.wordBoxes.forEach(function (wb) {
                    var w = document.createElement("div");
                    w.className = "grammar-word-ring";
                    ringLayer.appendChild(w);
                    positionRing(w, wb.rect, 1);
                    rings.push({ ring: w, cells: wb.els });
                  });
                });
              });
              activeRings = rings;
            }

            // Hovering one example highlights only that example's sentence
            // (blue ring + amber boxes on its matched words) -- never every
            // example of the item at once.  Hovering the item head shows the
            // first example.
            var firstRuns = null;
            Array.prototype.forEach.call(exampleEls, function (exEl, exIdx) {
              var ex = (g.examples || [])[exIdx] || {};
              // The backend reports exact matched-word offsets inside the
              // example sentence; fall back to the whole sentence when no
              // match info is available.
              var example = cleanText(ex.sentence || exEl.textContent);
              if (!example) return;
              var spans = (ex.matches || [])
                .map(function (m) { return [m.start, m.end]; })
                .filter(function (r) { return r[0] >= 0 && r[1] > r[0] && r[1] <= example.length; });
              if (!spans.length) spans = [[0, example.length]];
              var nodes = findRuns(stripText(example));
              if (!nodes.length) return;
              var runs = [{ example: example, spans: spans, nodes: nodes }];
              if (!firstRuns) firstRuns = runs;
              exEl.addEventListener("mouseenter", function () { showRings(runs); });
              exEl.addEventListener("mouseleave", hideAllRings);
            });
            if (!firstRuns) return;
            var headEl = item.querySelector(".grammar-item__head");
            if (headEl) {
              headEl.addEventListener("mouseenter", function () { showRings(firstRuns); });
              headEl.addEventListener("mouseleave", hideAllRings);
            }
          }
        );
      }
    })
    .fail(function () {
      panel.html(header() + '<div class="grammar-analysis-panel__body"><div class="grammar-analysis-panel__state">Analysis failed. Please try again.</div></div>');
      panel.find(".grammar-analysis-panel__close").on("click", window.closeGrammarAnalysis);
    });
}


/** THEMES AND HIGHLIGHTS *************************/
/* Change to the next theme, and reload the page. */
function next_theme() {
  $.ajax({
    url: '/theme/next',
    type: 'post',
    dataType: 'JSON',
    contentType: 'application/json',
    success: function(response) {
      location.reload();
    },
    error: function(response, status, err) {
      const msg = {
        response: response,
        status: status,
        error: err
      };
      console.log(`failed: ${JSON.stringify(msg, null, 2)}`);
    }
  });

}

/* Toggle between the paired Default (dark) and Default-Light (light) themes,
   then reload the page. Used by the dark/light mode toggle button. */
function toggle_dark_theme() {
  $.ajax({
    url: '/theme/toggle_dark',
    type: 'post',
    dataType: 'JSON',
    contentType: 'application/json',
    success: function(response) {
      location.reload();
    },
    error: function(response, status, err) {
      const msg = {
        response: response,
        status: status,
        error: err
      };
      console.log(`failed: ${JSON.stringify(msg, null, 2)}`);
    }
  });
}

function toggleFocus() {
  const focusChk = document.getElementById("focus");
  const event = new Event("change");
  focusChk.checked = !focusChk.checked;
  focusChk.dispatchEvent(event);
}

/* Toggle highlighting, and reload the page. */
function toggle_highlight() {
  $.ajax({
    url: '/theme/toggle_highlight',
    type: 'post',
    dataType: 'JSON',
    contentType: 'application/json',
    success: function(response) {
      location.reload();
    },
    error: function(response, status, err) {
      const msg = {
        response: response,
        status: status,
        error: err
      };
      console.log(`failed: ${JSON.stringify(msg, null, 2)}`);
    }
  });
}


function _page_data() {
  return {
    bookid: $("#book_id").val(),
    pagenum: $("#page_num").val()
  };
}

function delete_current_page() {
  if (!confirm("Delete current page?"))
    return;
  const d = _page_data()
  window.location = `/read/delete_page/${d.bookid}/${d.pagenum}`;
}

function _add_page(position) {
  const d = _page_data()
  window.location = `/read/new_page/${d.bookid}/${position}/${d.pagenum}`;
}

function add_page_before() {
  _add_page("before");
}

function add_page_after() {
  _add_page("after");
}


function _lang_is_left_to_right() {
  // read/index.js has some data rendered at the top of the page.
  const lang_is_rtl = $('#lang_is_rtl');
  if (!lang_is_rtl.length) {
    console.error("ERROR: missing lang control.");
    return true;  // fallback.
  }
  return (lang_is_rtl.val().toLowerCase() !== "true");
}


function handle_keydown (e) {
  if ($('span.word').length == 0) {
    return; // Nothing to do.
  }

  const hotkey_name = get_hotkey_name(e);
  if (hotkey_name == null)
    return;

  const next_incr = _lang_is_left_to_right() ? 1 : -1;
  const prev_incr = -1 * next_incr;

  // Map of shortcuts to lambdas:
  let map = {
    "hotkey_StartHover": () => start_hover_mode(),
    "hotkey_PrevWord": () => _move_cursor('span.word', prev_incr),
    "hotkey_NextWord": () => _move_cursor('span.word', next_incr),
    "hotkey_PrevUnknownWord": () => _move_cursor('span.word.status0', prev_incr),
    "hotkey_NextUnknownWord": () => _move_cursor('span.word.status0', next_incr),
    "hotkey_PrevSentence": () => _move_cursor('span.word.sentencestart', prev_incr),
    "hotkey_NextSentence": () => _move_cursor('span.word.sentencestart', next_incr),
    "hotkey_StatusUp": () => increment_status_for_selected_elements(+1),
    "hotkey_StatusDown": () => increment_status_for_selected_elements(-1),
    "hotkey_PageTermList": () => open_term_list_for_current_page(),
    "hotkey_PostTermsToAnki": () => send_selected_terms_to_anki(),
    "hotkey_Bookmark": () => handle_bookmark(),
    "hotkey_CopySentence": () => handle_copy('sentence-id'),
    "hotkey_CopyPara": () => handle_copy('paragraph-id'),
    "hotkey_CopyPage": () => handle_copy(null),
    "hotkey_EditPage": () => handle_edit_page(),
    "hotkey_TranslateSentence": () => handle_translate('sentence-id'),
    "hotkey_TranslatePara": () => handle_translate('paragraph-id'),
    "hotkey_TranslatePage": () => handle_translate(null),
    "hotkey_NextTheme": () => next_theme(),
    "hotkey_ToggleHighlight": () => toggle_highlight(),
    "hotkey_ToggleFocus": () => toggleFocus(),
    "hotkey_Status1": () => update_status_for_marked_elements(1),
    "hotkey_Status2": () => update_status_for_marked_elements(2),
    "hotkey_Status3": () => update_status_for_marked_elements(3),
    "hotkey_Status4": () => update_status_for_marked_elements(4),
    "hotkey_Status5": () => update_status_for_marked_elements(5),
    "hotkey_StatusIgnore": () => update_status_for_marked_elements(98),
    "hotkey_StatusWellKnown": () => update_status_for_marked_elements(99),
    "hotkey_DeleteTerm": () => update_status_for_marked_elements(0),

    // Functions defined in read/index.html, or refer to them
    // TODO javascript_hotkeys: fix javascript sprawl
    "hotkey_MarkRead": () => handle_mark_read_hotkey(),
    "hotkey_MarkReadWellKnown": () => handle_mark_read_well_known_hotkey(),
    "hotkey_PreviousPage": () => goto_relative_page(-1),
    "hotkey_NextPage": () => goto_relative_page(1),
  }

  if (hotkey_name in map) {
    // Override any existing event - e.g., if "up" arrow is in the map,
    // don't scroll screen.
    e.preventDefault();
    map[hotkey_name]();
  }
  else {
    // console.log(`hotkey "${hotkey_name}" not found in map`);
  }
}


/**
 * If the term editing form is visible when reading, and a hotkey is hit,
 * the form status should also update.
 */
function update_term_form(el, new_status) {
  const sel = 'input[name="status"][value="' + new_status + '"]';
  var radioButton = top.frames.wordframe.document.querySelector(sel);
  if (radioButton) {
    radioButton.click();
  }
  else {
    // Not found - user might just be hovering over the element,
    // or multiple elements selected.
    // console.log("Radio button with value " + new_status + " not found.");
  }
}


function update_status_for_marked_elements(new_status) {
  let elements = $('span.kwordmarked').toArray().concat($('span.wordhover').toArray());
  let updates = [ make_status_update_hash(new_status, elements) ]
  post_bulk_update(updates);
}


function make_status_update_hash(new_status, elements) {
  return {
    new_status: new_status,
    termids: elements.map(el => $(el).data('wid'))
  };
}


function post_bulk_update(updates) {
  if (updates.length == 0) {
    // console.log("No updates.");
    return;
  }
  let elements = $('span.kwordmarked').toArray().concat($('span.wordhover').toArray());
  if (elements.length == 0)
    return;
  const firstel = $(elements[0]);
  const first_status = updates[0].new_status;
  const selected_ids = $('span.kwordmarked').toArray().map(el => $(el).attr('id'));

  // Include the current book id so the server can mark its stats stale
  // (only recomputed on demand), keeping the home screen fast, and the
  // current page number so the server can return the refreshed fragment.
  let book_id = parseInt($('#book_id').val(), 10) || 0;
  let pagenum = parseInt($('#page_num').val(), 10) || 0;
  const payload = { book_id: book_id, pagenum: pagenum, updates: updates };

  // The swap replaces #thetext's paragraphs, which also wipes the
  // fit-to-screen sub-screen pagination (which paragraphs are hidden,
  // and curScreen).  Remember the paragraph holding the updated word so
  // it can be re-located after the swap and the screens re-flowed around
  // it -- otherwise the reader snaps back to sub-screen 0 on every
  // status change (same anchoring trick as the term-save reload).
  const paras_before = Array.from(document.querySelectorAll('#thetext > p'));
  const anchor_el = $(elements[0]).closest('p')[0] || null;
  const anchor_ordinal = anchor_el ? paras_before.indexOf(anchor_el) : -1;
  const anchor_id = $(elements[0]).attr('id') || null;

  // HTMX: POST the status update and swap the refreshed page fragment
  // into #thetext in a single round-trip.  Post-swap bookkeeping (re-mark
  // selected words, refresh the term form and player colors) is done in
  // the htmx:afterSwap listener below.
  _pendingStatusUpdate = {
    selected_ids: selected_ids,
    elements: elements,
    firstel: firstel,
    first_status: first_status,
    anchor_ordinal: anchor_ordinal,
    anchor_id: anchor_id,
  };
  htmx.ajax('POST', '/term/bulk_update_status', {
    target: '#thetext',
    swap: 'innerHTML',
    values: payload,
  });
}


/**
 * Re-mark selected words and refresh dependent UI after a bulk status
 * update has swapped in the new page fragment (#thetext).
 */
let _pendingStatusUpdate = null;
document.addEventListener('htmx:afterSwap', function (e) {
  if (e.target && e.target.id === 'thetext' && _pendingStatusUpdate) {
    const ps = _pendingStatusUpdate;
    _pendingStatusUpdate = null;

    // The text was re-rendered, so whatever the reader was waiting for
    // has landed.  The old spans are gone anyway; this also clears any
    // marker that survived on a span outside #thetext.
    _clear_tap_feedback();

    // The swap removed the words any open term popup was attached to;
    // clear the leftover floating cards.
    _close_term_popups();

    // Re-flow the fit-to-screen sub-screens: the swapped-in paragraphs
    // have no pagination state, so without this the reader lands back on
    // the first sub-screen after every status change.  Anchor on the
    // paragraph holding the updated word (word span ids survive a status
    // update; the pre-swap ordinal is the fallback).
    if (typeof _splitToScreens === 'function') {
      const by_id = ps.anchor_id ? $(document.getElementById(ps.anchor_id)) : $([]);
      let anchor = by_id.length ? by_id.closest('p')[0] : null;
      if (!anchor && ps.anchor_ordinal >= 0) {
        const paras = Array.from(document.querySelectorAll('#thetext > p'));
        anchor = paras[ps.anchor_ordinal] || null;
      }
      requestAnimationFrame(() => _splitToScreens(anchor));
    }

    for (let i = 0; i < ps.selected_ids.length; i++) {
      let el = $(`#${ps.selected_ids[i]}`);
      el.addClass('kwordmarked');
    }
    if (ps.selected_ids.length > 0)
      $('span.wordhover').removeClass('wordhover');

    if (ps.elements.length == 1) {
      update_term_form(ps.firstel, ps.first_status);
    }

    // Notify the YouTube / TTS player (if present) to refresh subtitle
    // word colors — the server-side subtitle cache was patched in place
    // by the bulk_update_status endpoint.  bookId lets players of other
    // books ignore the event.
    window.dispatchEvent(new CustomEvent('lute:status-updated', {
      detail: { bookId: Number($('#book_id').val()) || null, termText: null },
    }));
  }
});


/**
 * Change status using arrow keys for selected or hovered elements.
 */
function increment_status_for_selected_elements(shiftBy) {
  const elements = Array.from(document.querySelectorAll('span.kwordmarked, span.wordhover'));
  if (elements.length == 0)
    return;

  const statuses = ['status0', 'status1', 'status2', 'status3', 'status4', 'status5', 'status99'];

  // Build payloads to update for each unique status that will be changing
  let status_elements = statuses.reduce((obj, status) => {
    obj[status] = [];
    return obj;
  }, {});

  elements.forEach((element) => {
    let s = element.dataset.statusClass ?? 'missing';
    if (s in status_elements)
      status_elements[s].push(element);
  });

  // Convert map to update hashes.
  let updates = []

  Object.entries(status_elements).forEach(([status, update_elements]) => {
    if (update_elements.length == 0)
      return;

    let status_index = statuses.indexOf(status);
    let new_index = status_index + shiftBy;
    new_index = Math.max(0, Math.min((statuses.length-1), new_index));
    let new_status = Number(statuses[new_index].replace(/\D/g, ''));

    // Can't set status to 0 (that is for deleted/non-existent terms only).
    // TODO delete term from reading screen: setting to 0 could equal deleting term.
    if (new_index != status_index && new_status != 0) {
      updates.push(make_status_update_hash(new_status, update_elements));
    }
  });

  post_bulk_update(updates);
}
