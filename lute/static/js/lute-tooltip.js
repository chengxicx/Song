/* lute-tooltip.js
   --------------------------------------------------------------
   Tooltip (term detail hover) and the term edit form.
   --------------------------------------------------------------
   Split out of lute.js.  Every script under static/js shares one
   global scope and is loaded in order (see templates/base.html),
   so splitting the file changes nothing at runtime.
*/
/* ========================================= */
/** Tooltip (term detail hover). */

/** Narrow screens get the card above the word, wide ones below it. */
function _tooltip_is_above() {
  return window.matchMedia("(max-width: 980px)").matches;
}

let _get_tooltip_pos = function() {
  let ret = {my: 'left top+10', at: 'left bottom', collision: 'flipfit flip'};
  if (_tooltip_is_above()) {
    ret = {my: 'center bottom', at: 'center top-10', collision: 'flipfit flip'};
  }
  // jquery-ui fills `of` in with the word element and lays the card out
  // from that -- see _term_popup_fragment for why the element alone is
  // not good enough.  `using` runs once it has worked out its own
  // answer, and is handed the target element, so that is where we take
  // over and place the card ourselves.
  ret.using = function (css, feedback) {
    const pos = _place_term_popup($(this), feedback);
    if (pos) $(this).offset(pos);
    else $(this).css(css);
  };
  return ret;
}

// Where the pointer last entered a word, in viewport coordinates.
// Picks which of the word's line fragments the popup hangs off (see
// _term_popup_fragment) and is recorded on the document so it covers
// every reading container: #thetext here plus the player subtitles,
// which are wired up in their own files.
//
// It is read when the card is positioned, which happens after its
// content arrives -- i.e. always later than this event, so the widget's
// own mouseover handler, which runs first and starts the fetch, cannot
// make us look at a stale value.
let _term_popup_pointer = null;
$(document).on('mouseover', '.word', function (e) {
  _term_popup_pointer = { x: e.clientX, y: e.clientY };
});
// Same for a tap, so the touch modes (Quick Set Status) hang the card
// off the fragment that was actually touched.
$(document).on('touchstart', '.word', function (e) {
  const touch = e.originalEvent && e.originalEvent.touches && e.originalEvent.touches[0];
  if (touch) _term_popup_pointer = { x: touch.clientX, y: touch.clientY };
});

/**
 * The line fragment of a word that the pointer is on.
 *
 * A `.word` span that wraps at the end of a line has one client rect per
 * line.  getClientRects() returns them in reading order, but
 * getBoundingClientRect() -- and so offset(), which is what jquery-ui
 * measures the target with -- returns their UNION: for a wrapped word
 * that union starts at the column's left edge and is two lines tall.
 * Positioning the card against it threw the card hundreds of pixels away
 * from the pointer, to the far left of the column (or flipped it above
 * the word once the tall card no longer fitted below that union).
 * Words that do not wrap have a single fragment, so for them this is
 * exactly the element box and nothing changes.
 */
function _term_popup_fragment(el) {
  if (!el || !el.getClientRects) return null;
  const frags = Array.prototype.filter.call(el.getClientRects(), function (r) {
    return r.width || r.height;
  });
  if (frags.length === 0) return null;

  const pointer = _term_popup_pointer;
  if (pointer) {
    const hit = Array.prototype.find.call(frags, function (r) {
      return pointer.x >= r.left - 1 && pointer.x <= r.right + 1 &&
             pointer.y >= r.top - 1 && pointer.y <= r.bottom + 1;
    });
    if (hit) return hit;
  }
  // Opened without a pointer of its own -- a tap on a word whose content
  // was already cached, say.  The first fragment is where the reader
  // starts reading the word.
  return frags[0];
}

/**
 * Where the term popup goes: just below the word's line fragment on wide
 * screens, centered above it on narrow ones, kept on screen.
 *
 * Called through jquery-ui's `position.using` hook, which runs after it
 * has worked out its own answer and hands us the target element.  We
 * ignore that answer and place the card from the fragment instead.
 *
 * Doing the placement here rather than handing jquery-ui a point to
 * anchor on keeps the flip decision honest: whether the card fits below
 * the word depends on the word's own height, which a point anchor does
 * not carry, and without it a word near the top of a narrow screen got
 * its card on top of itself instead of flipping to below.
 *
 * Returns null when the word has no fragments to speak of, leaving
 * jquery-ui's own placement in place.
 */
function _place_term_popup(card, feedback) {
  const target = feedback && feedback.target && feedback.target.element;
  const frag = _term_popup_fragment(target && target[0]);
  if (!frag) return null;

  const cardW = card.outerWidth();
  const cardH = card.outerHeight();
  const gap = 10;
  const pageX = window.pageXOffset;
  const pageY = window.pageYOffset;
  const viewW = window.innerWidth;
  const viewH = window.innerHeight;

  let left, top;
  if (_tooltip_is_above()) {
    left = pageX + frag.left + frag.width / 2 - cardW / 2;
    top = pageY + frag.top - gap - cardH;
    if (top < pageY) {
      // No room above: flip below the word, or pin to the top.
      const below = pageY + frag.bottom + gap;
      top = below + cardH <= pageY + viewH ? below : pageY;
    }
  } else {
    left = pageX + frag.left;
    top = pageY + frag.bottom + gap;
    if (top + cardH > pageY + viewH) {
      // No room below: flip above the word, or pin to the bottom.
      const above = pageY + frag.top - gap - cardH;
      top = above >= pageY ? above : pageY + viewH - cardH;
    }
  }
  // Keep it inside the window horizontally.  A card wider than the
  // window has nowhere left to go, so it is left where it is.
  if (cardW + 8 <= viewW) {
    left = Math.min(Math.max(left, pageX + 4), pageX + viewW - cardW - 4);
  }
  return {left: left, top: top};
}

/**
 * The containers that own a term-popup tooltip widget.
 *
 * Every reading container binds its own jquery-ui tooltip instance:
 * #thetext here in lute.js, and each player subtitle in
 * youtube-player.js / bilibili-player.js / tts.js.  A word's popup is
 * therefore owned by the widget of the container that word sits in, so
 * "close the term popup" always has to mean "close it in all of them".
 */
const _term_popup_containers =
  '#thetext, #yt-scrolling-subtitle-inner, #tts-scrolling-subtitle-inner';

/**
 * Close every term popup that is currently open, wherever its word
 * lives (main text or a player subtitle).
 *
 * tooltip("close") reads the element to close from event.currentTarget:
 * called with no argument it falls back to the widget element itself,
 * and a word is never that element, so for word popups it silently does
 * nothing.  Walk the widget's own registry instead and hand each entry
 * back to close() -- that is also the only path that clears the word's
 * ui-tooltip-id and drops the registry entry, which is what makes the
 * word openable again later: jquery-ui's open() refuses a word that
 * still carries a ui-tooltip-id, so a popup that was taken off screen
 * with a plain $('.ui-tooltip').remove() would never come back.
 *
 * Detached words are closed on purpose: they are the leftovers this
 * exists for.  A fragment swap or a subtitle rebuild replaced the word,
 * so nothing else will ever close the card it left behind.
 */
function _close_term_popups() {
  $(_term_popup_containers).each(function () {
    const widget = $(this).data('ui-tooltip');
    if (!widget || !widget.tooltips) return;
    Object.keys(widget.tooltips).forEach(function (id) {
      const entry = widget.tooltips[id];
      const el = entry && entry.element && entry.element[0];
      if (!el) return;
      const ev = $.Event('close');
      ev.currentTarget = el;
      try {
        widget.close(ev);
      } catch (err) {
        // The widget was torn down under us: nothing left to close.
      }
    });
  });
}

/**
 * Tap-away closing for term popups on touch screens.
 *
 * A popup opened programmatically (_quick_show_popup -- the single tap
 * of Quick Set Status mode) never gets auto-close handlers, because
 * jquery-ui only wires mouseleave / focusout for opens that came from
 * real mouseover / focusin events.  So close it whenever a touch ends
 * somewhere that is neither the popup card itself (its links still
 * need their taps) nor another word (a word tap manages its own
 * popups: it replaces this one or opens the edit form).  A tap that
 * went elsewhere also drops a popup still waiting out its double-tap
 * delay -- it should never appear at all.
 *
 * Bound on touchend, not click: the reading-page touch flow can keep
 * the synthesized click from firing, and on desktop this handler is
 * irrelevant anyway -- the popup closes on mouseleave long before any
 * click could land.
 */
$(document).on('touchend', function (e) {
  const $t = $(e.target);
  if ($t.closest('.ui-tooltip').length) return;
  if ($t.closest('.word').length) return;
  _cancel_pending_popup();
  _close_term_popups();
});

/**
 * Term-popup content for the jquery-ui tooltip.
 *
 * Content is fetched with HTMX and cached: the first hover on a word
 * issues one HTMX GET that is swapped into the hidden #termpopup-cache
 * container; the response is stored and handed to the tooltip via its
 * setContent callback.  Hovering the same word again is served instantly
 * from the cache with no network request.
 */
let tooltip_textitem_hover_content = function (el, setContent) {
  const elid = parseInt(el.data('wid'), 10);
  if (isNaN(elid)) {
    // Not saved to the DB yet (e.g. status 0 with no word id).
    setContent('');
    return;
  }
  _termpopup_get(elid, el, setContent);
}

// Cached term-popup HTML, keyed by word id.
const _termpopup_cache = {};
// setContent callbacks waiting for a fetch: word id -> { el, cb }.
const _termpopup_pending = {};
// Queue of word ids whose popup has not been fetched yet.  Fetches are
// serialized (one HTMX request at a time) so the htmx:afterSwap handler
// below knows which cache slot to fill.
const _termpopup_queue = [];
let _termpopup_fetching = false;
let _termpopup_fetching_wid = null;

// Deliver cached HTML to a waiting tooltip, guarding against the word
// having been re-rendered/replaced while the request was in flight (e.g.
// the TTS subtitle is rebuilt on each loop iteration, or the page
// navigated).  Calling setContent on a detached element makes jquery-ui
// resurrect a stray tooltip pinned at the document top-left corner.
function _termpopup_deliver(elid) {
  const p = _termpopup_pending[elid];
  if (!p) return;
  delete _termpopup_pending[elid];
  const node = p.el && p.el[0];
  if (!node || !node.isConnected) return;
  p.cb(_termpopup_cache[elid]);
  // The popup the reader was waiting for is on screen.  Only reachable
  // with a real setContent callback, so desktop hover prefetches (which
  // pass none) don't clear anything.
  _clear_tap_feedback();
}

// Start the next queued fetch, if any.
function _termpopup_pump() {
  if (_termpopup_fetching || _termpopup_queue.length === 0) return;
  const elid = _termpopup_queue.shift();
  if (_termpopup_cache[elid] !== undefined) {
    _termpopup_deliver(elid);
    _termpopup_pump();
    return;
  }
  _termpopup_fetching = true;
  _termpopup_fetching_wid = elid;
  htmx.ajax('GET', `/read/termpopup/${elid}`, {
    target: '#termpopup-cache',
    swap: 'innerHTML',
  });
}

// Fetch (or reuse) the popup HTML for a word id, then hand it to the
// tooltip's setContent callback if one was provided.
function _termpopup_get(elid, el, setContent) {
  if (setContent) _termpopup_pending[elid] = { el: el, cb: setContent };
  if (_termpopup_cache[elid] !== undefined) {
    _termpopup_deliver(elid);
    return;
  }
  if (_termpopup_queue.indexOf(elid) === -1) {
    _termpopup_queue.push(elid);
  }
  _termpopup_pump();
}

// Invalidate the cached term-popup HTML (and pending/queue/fetch state).
// Term saves/edits/deletes change a term's popup content server-side;
// without this, hovering a just-saved word keeps serving the stale
// (often empty) popup fetched before the save, until a full reload.
function clear_termpopup_cache() {
  for (const k of Object.keys(_termpopup_cache)) {
    delete _termpopup_cache[k];
  }
  for (const k of Object.keys(_termpopup_pending)) {
    delete _termpopup_pending[k];
  }
  _termpopup_queue.length = 0;
  _termpopup_fetching = false;
  _termpopup_fetching_wid = null;
}

// Cache the HTMX term-popup response and hand it to any waiting tooltip.
// htmx:afterRequest fires on success AND error (afterSwap only on success),
// so a failed fetch cannot stall the queue.  The path check keeps this
// handler from reacting to the page-navigation / status-update requests
// that also run through HTMX on the reading page.
document.addEventListener('htmx:afterRequest', function (e) {
  const detail = e.detail || {};
  const path = (detail.pathInfo && detail.pathInfo.requestPath) || '';
  if (path.indexOf('/read/termpopup/') === -1) return;
  if (!_termpopup_fetching) return;
  const elid = _termpopup_fetching_wid;
  _termpopup_fetching = false;
  _termpopup_fetching_wid = null;
  if (elid === null) return;
  _termpopup_cache[elid] = detail.successful && detail.target ? detail.target.innerHTML : '';
  _termpopup_deliver(elid);
  _termpopup_pump();
  // Whether it succeeded or failed, the wait is over.
  _clear_tap_feedback();
});

// Prefetch the popup as soon as the pointer lands on a word, so the
// tooltip (which opens a moment later) usually finds its content cached.
// While the term form is loaded in the wordframe, also prefetch that
// word's form data, so clicking it can populate the form with no
// network wait.
$(document).on('mouseenter', '.word', function () {
  if (_isUserUsingMobile()) return;
  const $el = $(this);
  const elid = parseInt($el.data('wid'), 10);
  if (!isNaN(elid)) {
    _termpopup_get(elid, $el, null);
    if (_wordframe_can_populate())
      _prefetch_term_form_data(`/read/edit_term/${elid}`);
  }
});


/* ========================================= */
/** Showing the edit form. */

// Prefetched term form data (hover → click optimization), keyed by
// the form URL.  Only used while the term form is already loaded in
// the wordframe; entries expire after a few seconds so a term changed
// by other actions (hotkey status updates, saves) is re-fetched.
const _termFormDataCache = new Map();
const _TERM_FORM_PREFETCH_TTL = 4000;  // ms

function luteClearTermFormPrefetchCache() {
  _termFormDataCache.clear();
}

function _prefetch_term_form_data(url) {
  if (_termFormDataCache.has(url)) return;
  _termFormDataCache.set(url, {
    ts: Date.now(),
    promise: fetch(url + "?format=json", { cache: "no-store" }).then((r) => {
      if (!r.ok) throw new Error("bad response " + r.status);
      return r.json();
    }),
  });
}

function _get_term_form_data(url) {
  const entry = _termFormDataCache.get(url);
  if (!entry) return null;
  _termFormDataCache.delete(url);
  if (Date.now() - entry.ts > _TERM_FORM_PREFETCH_TTL) return null;
  return entry.promise;
}

function _wordframe_can_populate() {
  const f = top.frames.wordframe;
  try {
    return !!(
      f &&
      f.document &&
      typeof f.LuteTermForm_populate === "function" &&
      f.document.getElementById("term-form-container")
    );
  } catch (e) {
    return false;  // frame not ready or not same-origin
  }
}

let _termFormPopulateSeq = 0;

function _show_wordframe_url(url) {
  if (_wordframe_can_populate()) {
    // Fast path: the term form is already loaded in the frame, so
    // fetch just this term's data and update the form in place
    // instead of reloading the whole frame document.
    const seq = ++_termFormPopulateSeq;
    const cached = _get_term_form_data(url);
    const fetchFresh = () =>
      fetch(url + "?format=json", { cache: "no-store" }).then((r) => {
        if (!r.ok) throw new Error("bad response " + r.status);
        return r.json();
      });
    (cached || fetchFresh())
      .then((data) => {
        if (seq !== _termFormPopulateSeq) return;  // newer click won
        top.frames.wordframe.LuteTermForm_populate(data);
      })
      .catch(() => {
        if (seq !== _termFormPopulateSeq) return;
        if (_wordframe_can_populate())
          top.frames.wordframe.location.href = url;  // full-load fallback
      });
  } else {
    top.frames.wordframe.location.href = url;
  }
  applyInitialPaneSizes();  // in resize.js
}

function show_term_edit_form(el) {
  const wid = parseInt(el.data('wid'));
  if (isNaN(wid)) {
    // The term hasn't been saved to the DB yet (status 0 with no ID).
    // Fall back to the "create new term" form using the element's text.
    const text = el.data('text') || el.text();
    const lid = parseInt(el.data('lang-id'));
    if (isNaN(lid)) return;
    const sendtext = text.replace(/\//g, "LUTESLASH");
    _show_wordframe_url(`/read/termform/${lid}/${sendtext}`);
    return;
  }
  _show_wordframe_url(`/read/edit_term/${wid}`);
}

function show_bulk_term_edit_form(count_of_terms) {
  const url = '/read/term_bulk_edit_form';

  const wordFrame = top.frames.wordframe;

  function updateSpanContent() {
    const frameDocument = wordFrame.document;
    const spanElement = $(frameDocument).find('#bulkUpdateCount');
    if (spanElement.length) {
      spanElement.text(`Updating ${count_of_terms} term(s)`);
    }
  }

  if (!wordFrame.location.href.endsWith(url))
    wordFrame.location.href = url;
  else
    updateSpanContent();
}

function _hide_dictionaries() {
  $('.dictcontainer').hide();
}

/* Hide word editing form. */
function _hide_term_edit_form() {
  $('#wordframeid').attr('src', '/read/empty');
  // NOTE: checking for specific URLs or fragments in the location.href
  // causes security errors in some cases.
  /*
  const hide_me = ['read/edit_term', 'read/term_bulk_edit_form', 'read/termform'];
  const c = top.frames.wordframe.location.href;
  if (hide_me.some(path => c.includes(path))) {
    $('#wordframeid').attr('src', '/read/empty');
  }
  */
}

function show_multiword_term_edit_form(selected) {
  if (selected.length == 0)
    return;
  // Zero-width space: matches the separator Lute uses internally
  // (see lute.models.term.Term) to mark token boundaries in
  // multiword terms.
  const ZWS = '\u200B';
  // Use data('text') instead of text() to preserve the internal ZWS
  // boundaries of multiword token spans. Each span's displayed text
  // has ZWS stripped (see TextItem.html_display_text), but data-text
  // holds the raw text with ZWS intact. Using text() would lose those
  // internal boundaries, causing the created term's token_count to
  // not match the actual parsed tokens on the page.
  // Example: "すれば" displayed on screen may actually be stored as
  // "すれ\u200Bば" (2 tokens). Using text() gives "すれば" (1 token),
  // but data('text') gives "すれ\u200Bば" (2 tokens, correct).
  const textparts = selected.toArray().map((el) => $(el).data('text') || $(el).text());
  // Join with ZWS, not '', so the exact tokens the parser already
  // produced for this sentence (i.e., the ones currently rendered
  // on screen) are preserved and sent to the backend as-is, instead
  // of being collapsed into a flat string that then has to be
  // re-parsed out of context. Context-sensitive parsers (e.g. MeCab
  // for Japanese) can tokenize an isolated substring differently
  // than they tokenize it within its original sentence, which was
  // causing newly-created multiword terms to not match when reading.
  const text = textparts.join(ZWS).trim();
  if (text == "")
    return;
  const lid = parseInt(selected.eq(0).data('lang-id'));
  // "/" in the term cause problems with routing, so hack a fix.
  const sendtext = text.replace(/\//g, "LUTESLASH");
  _show_wordframe_url(`/read/termform/${lid}/${sendtext}`);
}


