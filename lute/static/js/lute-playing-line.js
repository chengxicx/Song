/*
 * Underline the line the player is currently reading, in the reading
 * text itself (#thetext).
 *
 * The players already show the current line in their subtitle strip and
 * highlight it in the transcript panel, but the page text below is the
 * thing the reader is actually reading -- marking it there is what lets
 * them follow along without looking up at the player.
 *
 * Two callers:
 *
 * - The media players (youtube / bilibili / mp3 / video) know the
 *   absolute cue index and its text: setCueIndex(i, text).  A media
 *   book's page text is its cues' text joined by newlines (see
 *   lute/book/service.py parse_subtitle_content), so one page line is
 *   one cue line, and the server hands over the cue index of each line
 *   as window.LUTE_PAGE_CUE_MAP (read/page_content.html).  That map is
 *   positional, so it is only trusted when the lines it names actually
 *   hold the cue's text (joined, for a multi-line cue); otherwise the
 *   text is matched directly.
 *
 * - The TTS player builds its cues from the .textsentence spans
 *   themselves, so it passes the span: setElement(el).
 *
 * Everything is resolved on each call rather than cached: #thetext is
 * re-swapped by htmx on every page turn, so a cached element (or a
 * cached map) would belong to the previous page.
 */
(function () {
  "use strict";

  var CLASS = "lute-playing-line";

  // The elements currently carrying CLASS, so the previous mark can be
  // removed without scanning the whole page.
  var marked = [];

  // Compare on non-whitespace characters only: the renderer collapses
  // runs of spaces, and the empty-paragraph sentinel it appends is a
  // zero-width space, which \s does not match.
  function norm(s) {
    return (s || "").replace(/[\s\u200b]+/g, "");
  }

  // A line's text, without the UI the reading page injects into it: the
  // 🔊 sentence-play button lives inside the sentence span (tts.js), and
  // it is markup, not text -- counting it would make every line fail to
  // match its cue.
  function lineText(el) {
    var clone = el.cloneNode(true);
    var btns = clone.querySelectorAll(".lute-sentence-play-btn");
    for (var i = 0; i < btns.length; i++) {
      btns[i].parentNode.removeChild(btns[i]);
    }
    return norm(clone.textContent);
  }

  function clear() {
    for (var i = 0; i < marked.length; i++) {
      marked[i].classList.remove(CLASS);
    }
    marked = [];
  }

  function mark(els) {
    clear();
    for (var i = 0; i < els.length; i++) {
      els[i].classList.add(CLASS);
      marked.push(els[i]);
    }
  }

  function paragraphs() {
    var div = document.getElementById("thetext");
    if (!div) return [];
    return Array.prototype.slice.call(div.querySelectorAll(":scope > p"));
  }

  // Mark a specific element (the TTS player's sentence span).  A stale
  // element -- one detached by a page swap -- means there is nothing
  // left to mark, so the mark is dropped instead of leaking onto the
  // new page.
  function setElement(el) {
    if (!el || el.isConnected === false) {
      clear();
      return;
    }
    mark([el]);
  }

  // Mark the line holding cue `index`, whose text is `text`.  Nothing is
  // marked when that cue is not on the page being read.
  function setCueIndex(index, text) {
    var ps = paragraphs();
    if (!ps.length) {
      clear();
      return;
    }

    var want = norm(text);
    var map = window.LUTE_PAGE_CUE_MAP;

    if (Array.isArray(map) && map.length === ps.length) {
      var hits = [];
      for (var k = 0; k < ps.length; k++) {
        if (map[k] === index) hits.push(ps[k]);
      }
      // Verify before trusting the map: the page text can be edited out
      // of step with the cues, and underlining the wrong line is worse
      // than underlining none.  The hit lines are compared joined, since
      // one cue can be a multi-line subtitle and so own several lines.
      var ok = hits.length > 0;
      if (ok) {
        var joined = "";
        for (var h = 0; h < hits.length; h++) joined += lineText(hits[h]);
        ok = joined === want;
      }
      if (ok) {
        mark(hits);
        return;
      }
    }

    if (!want) {
      clear();
      return;
    }
    // No usable map (or it did not check out): match the cue text
    // against the page's lines.  Only the first match is marked, so a
    // repeated line (a chorus, say) does not light up everywhere.
    for (var j = 0; j < ps.length; j++) {
      if (lineText(ps[j]) === want) {
        mark([ps[j]]);
        return;
      }
    }
    clear();
  }

  window.LutePlayingLine = {
    setElement: setElement,
    setCueIndex: setCueIndex,
    clear: clear,
  };
})();
