/* Lute functions. */

/**
 * The current term index (either clicked or hovered over)
 */
let LUTE_CURR_TERM_DATA_ORDER = -1;  // initially not set.


/**
 * Reset the cursor.
 *
 * When read/page_content.html is rendered, the current
 * cursor styling etc is wiped off the screen, so it needs
 * to be restored.
 */
function reset_cursor_marker() {
  $('span.kwordmarked').removeClass('kwordmarked');

  const curr_word = $('span.word').filter(function() {
    return _get_order($(this)) == LUTE_CURR_TERM_DATA_ORDER;
  });
  if (curr_word.length == 1) {
    const w = $(curr_word[0]);
    $(w).addClass('wordhover');
    apply_status_class($(w));
  }

  // Refocus on window so keyboard events work.
  // ref https://stackoverflow.com/questions/35022716
  $(window).focus();
}


/**
 * When the reading pane is first loaded, it's in "hover mode",
 * meaning that when the user hovers over a word, that word becomes
 * the "active word" -- i.e., status update keyboard shortcuts should
 * operate on that hovered word, and as the user moves the mouse
 * around, the "active word" changes.  When a word is clicked, though,
 * there can't be any "hover changes", because the user should be
 * editing the word in the Term edit pane, and has to consciously
 * disable the "clicked word" mode by hitting ESC or RETURN.
 *
 * The full page text is often reloaded via ajax, e.g. when the user
 * saves an edited term, or the status is updated with a hotkey.
 * The template lute/templates/read/page_content.html calls
 * this method on reload to reset the cursor etc.
 */
function start_hover_mode() {
  reset_cursor_marker();
  _hide_element_message_tooltips();
  _hide_term_edit_form();
  _hide_dictionaries();
  clear_newmultiterm_elements();
}

/* ========================================= */
/** Interactions. */


/* User can explicitly set the screen type from the reading_menu. (templates/read/reading_menu) */
function set_screen_type(screen_type) {
  localStorage.setItem("screen_interactions_type", screen_type);
  window.location.reload();
}

/**
 * Find if on mobile.
 *
 * This appears to still be a big hassle.  Various posts
 * say to not use the userAgent sniffing, and use feature tests
 * instead.
 * ref: https://stackoverflow.com/questions/72502079/
 *   how-can-i-check-if-the-device-which-is-using-my-website-is-a-mobile-user-or-no
 * From the above, using answer from marc_s: https://stackoverflow.com/a/76055222/1695066
 *
 * The various answers posted are still incorrect in certain cases,
 * so Lute users can set the screen_interactions_type for the session.
 */
const _isUserUsingMobile = () => {
  const s = localStorage.getItem('screen_interactions_type');
  if (s == 'desktop')
    return false;
  if (s == 'mobile')
    return true;

  // User agent string method
  let isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);

  // Screen resolution method.
  // Using the same arbitrary width check (980) as used
  // by the various window.matchMedia checks elsewhere in the code.
  // The original method in the SO post had width, height < 768,
  // but that broke playwright tests which opens a smaller browser window.
  if (!isMobile) {
    isMobile = (window.screen < 980);
  }

  // Disabling this check - see https://stackoverflow.com/a/4819886/1695066
  // for the many cases where this fails.
  // Touch events method
  // if (!isMobile) {
  //   isMobile = (('ontouchstart' in window) || (navigator.maxTouchPoints > 0) || (navigator.msMaxTouchPoints > 0));
  //  }

  // CSS media queries method
  if (!isMobile) {
    let bodyElement = document.getElementsByTagName('body')[0];
    isMobile = window.getComputedStyle(bodyElement).getPropertyValue('content').indexOf('mobile') !== -1;
  }

  return isMobile;
}


/** 
 * Prepare the interaction events with the text.
 */
function prepareTextInteractions() {
  if (_isUserUsingMobile()) {
    console.log('Using mobile interactions');
    _add_mobile_interactions();
  }
  else {
    console.log('Using desktop interactions');
    _add_desktop_interactions();
  }

  $(document).on('keydown', handle_keydown);

  $('#thetext').tooltip({
    position: _get_tooltip_pos(),
    items: '.word',
    show: { easing: 'easeOutCirc' },
    content: function (setContent) { tooltip_textitem_hover_content($(this), setContent); }
  });
}


function _add_mobile_interactions() {
  const t = $('#thetext');
  t.on('touchstart', '.word', touch_started);
  t.on('touchend', '.word', touch_ended);
  t.on('touchcancel', '.word', touch_cancelled);
}


function _add_desktop_interactions() {
  const t = $('#thetext');
  // Using "t.on" here because .word elements
  // are added and removed dynamically, and "t.on"
  // ensures that events remain for each element.
  t.on('mousedown', '.word', handle_select_started);
  t.on('mouseover', '.word', handle_select_over);
  t.on('mouseup', '.word', handle_select_ended);
  t.on('mouseover', '.word', hover_over);
  t.on('mouseout', '.word', hover_out);
  if (!_show_highlights()) {
    t.on('mouseover', '.word', hover_over_add_status_class);
    t.on('mouseout', '.word', remove_status_highlights);
  }
  // Touch laptops report as "desktop" but still deserve the press
  // feedback.  Released here rather than in handle_select_ended so a
  // drag that ends outside the text (or a cancelled drag) still clears.
  $(document).on('mouseup', function () {
    $('span.tap-pressed').removeClass('tap-pressed');
  });
}

/* ========================================= */
/** Tooltip (term detail hover). */

let _get_tooltip_pos = function() {
  let ret = {my: 'left top+10', at: 'left bottom', collision: 'flipfit flip'};
  if (window.matchMedia("(max-width: 980px)").matches) {
    ret = {my: 'center bottom', at: 'center top-10', collision: 'flipfit flip'};
  }
  return ret;
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


/* ========================================= */
/** Cursor management. */

/** Called on page load (read/index.html). */
function reset_cursor() {
  LUTE_CURR_TERM_DATA_ORDER = -1;
}

let _get_order = function(el) { return parseInt(el.data('order')); };

let save_curr_data_order = function(el) {
  LUTE_CURR_TERM_DATA_ORDER = _get_order(el);
}


/* ========================================= */
/** Status highlights.
 * There is a "show_highlights" UserSetting.
 *
 * If showing highlights, then the highlights are shown when the page
 * is rendered; otherwise, they're only shown/removed on hover enter/exit.
 *
 * User setting "show_highlights" is rendered in read/index.html. */

/** True if show_highlights setting is True. */
let _show_highlights = function() {
  return ($("#show_highlights").text().toLowerCase() == "true");
}

/**
 * Terms have data-status-class attribute.  If highlights should be shown,
 * then add that value to the actual span. */
function add_status_classes() {
  if (!_show_highlights())
    return;
  $('span.word').toArray().forEach(function (w) {
    apply_status_class($(w));
  });
}

/** Add the data-status-class to the term's classes. */
let apply_status_class = function(el) {
  el.addClass(el.data("status-class"));
}

/** Remove the status from elements, if not showing highlights. */
let remove_status_highlights = function() {
  if (_show_highlights()) {
    /* Not removing anything, always showing highlights. */
    return;
  }
  $('span.word').toArray().forEach(function (m) {
    el = $(m);
    el.removeClass(el.data("status-class"));
  });
}

/** Hover and term status classes */
function hover_over_add_status_class(e) {
  remove_status_highlights();
  apply_status_class($(this));
}


/* ========================================= */
/** Hovering */

function hover_over(e) {
  $('span.wordhover').removeClass('wordhover');
  const marked_count = $('span.kwordmarked').toArray().length;
  if (marked_count == 0) {
    $(this).addClass('wordhover');
    save_curr_data_order($(this));
  }
}

function hover_out(e) {
  $('span.wordhover').removeClass('wordhover');
}


/* ========================================= */
/** Clicking */

let word_clicked = function(el, e) {
  _hide_element_message_tooltips();
  el.removeClass('wordhover');
  save_curr_data_order(el);
  el.toggleClass('kwordmarked');

  // If Shift isn't held, this is a regular click.
  if (! e.shiftKey) {
    // No other elements should be marked clicked.
    $('span.kwordmarked').not(el).removeClass('kwordmarked');
    if (el.hasClass('kwordmarked')) {
      el.removeClass('hasflash');
      show_term_edit_form(el);
    }
    else {
      _hide_dictionaries();
      _hide_term_edit_form();
    }
    return;
  }

  // Shift is held ... have 0 or more elements clicked.
  const count_marked = $('span.kwordmarked').length;
  if (count_marked == 0) {
    el.addClass('wordhover');
    start_hover_mode();
  }
  else {
    _hide_dictionaries();
    show_bulk_term_edit_form(count_marked);
  }
}


/* ========================================= */
/** Multiword selection */

let selection_start_el = null;
let selection_start_shift_held = false;

let clear_newmultiterm_elements = function() {
  $('.newmultiterm').removeClass('newmultiterm');
  selection_start_el = null;
  selection_start_shift_held = false;
}

function handle_select_started(e) {
  // Immediate "pressed" answer on mouse-down / touch-down.  Not in
  // select_started() itself: that is also called by the mobile
  // long-press (tap-hold) path, where the finger is already up.
  _tap_press($(this));
  select_started($(this), e);
}

function select_started(el, e) {
  _hide_element_message_tooltips();
  clear_newmultiterm_elements();
  el.addClass('newmultiterm');
  selection_start_el = el;
  selection_start_shift_held = e.shiftKey;
  save_curr_data_order(el);
}

let get_selected_in_range = function(start_el, end_el) {
  let tmp_start = _get_order(start_el);
  let tmp_end = _get_order(end_el);
  // Javascript sorts numbers as strings.  wtf.
  const [startord, endord] = [tmp_start, tmp_end].sort((a, b) => a - b);
  // Scope the search to the same container as the start element.
  // YouTube book pages render two independent sets of textitem spans
  // on the same page -- #thetext (the main reading text) and
  // #yt-scrolling-subtitle-inner (the scrolling subtitle) -- that share
  // the same data-order values because both are tokenized from the
  // same cue text.  Without scoping, a drag-selection in one area
  // picks up duplicate spans from the other, causing the created
  // multiword term's text to be doubled (e.g. "取って取って").
  // The TTS player's scrolling subtitle (#tts-scrolling-subtitle-inner)
  // has the same concern on text-book pages where #thetext and the
  // subtitle share identical data-order values.
  const container = start_el.closest('#thetext, #yt-scrolling-subtitle-inner, #tts-scrolling-subtitle-inner');
  const search_root = container.length ? container : $(document);
  const selected = search_root.find('span.textitem').filter(function() {
    const ord = _get_order($(this));
    return ord >= startord && ord <= endord;
  });
  return selected;
};

function handle_select_over(e) {
  select_over($(this), e);
}
  
function select_over(el, e) {
  if (selection_start_el == null)
    return;  // Not selecting
  $('.newmultiterm').removeClass('newmultiterm');
  const selected = get_selected_in_range(selection_start_el, el);
  selected.addClass('newmultiterm');
}

function handle_select_ended(e) {
  select_ended($(this), e);
}

function select_ended(el, e) {
  // Handle single word click.
  if (selection_start_el.attr('id') == el.attr('id')) {
    clear_newmultiterm_elements();
    word_clicked(el, e);
    return;
  }

  $('span.kwordmarked').removeClass('kwordmarked');

  const selected = get_selected_in_range(selection_start_el, el);
  if (selection_start_shift_held) {
    copy_text_to_clipboard(selected.toArray());
    clear_newmultiterm_elements();
    return;
  }

  show_multiword_term_edit_form(selected);
  selection_start_el = null;
  selection_start_shift_held = false;
}


/* ========================================= */
/** Tap feedback: visual + haptic.

    On a touch screen a tap used to produce no visible change until the
    server answered, so readers re-tapped "to be sure" -- and the second
    tap was then read as a double     tap.  Four CSS markers fix that (see
    styles.css, "Tap feedback on interactive text"):

      tap-pressed  finger is down right now
      tap-ack      tap received (flash + short buzz)
      tap-double   double tap received (two-beat pulse + double buzz)
      tap-pending  a request / the speech engine is in flight

    Two kinds of element carry them: words in the reading pane (wired up
    below, in touch_ended / handle_select_started) and the sentence 🔊
    buttons injected by tts.js, which reach these helpers through the
    window.luteTapFeedback bridge at the end of this section.

    The durations below must stay in sync with the matching animations
    in styles.css.
*/

// Keep in sync with the tap-ack / tap-double animations in styles.css.
const _tap_ack_ms = 420;
const _tap_double_ms = 580;

// Everything that can carry a tap marker.  The sentence 🔊 buttons are
// wired up in tts.js through the window.luteTapFeedback bridge at the
// bottom of this section, but their markers live in the same classes so
// a single "clear everything" pass covers both.
const _tap_feedback_selector = 'span.textitem, .lute-sentence-play-btn';

// Longest a word can stay "pending" if no completion event ever arrives
// (a request that failed without firing htmx:afterRequest, a term form
// that never opened, ...).  Long enough to cover a slow mobile round
// trip, short enough that a stuck marker is never mistaken for a hang.
const _tap_pending_max_ms = 2500;

let _tap_pending_timer = null;
let _tap_pending_start_timer = null;
// The element the two timers above belong to (see _clear_tap_feedback).
let _tap_pending_el = null;

/**
 * Haptic feedback.
 *
 * Only Android Chrome/Firefox implement the Vibration API -- iOS Safari
 * has no navigator.vibrate at all, so this is a silent no-op there and
 * the visual markers carry the feedback on their own.
 */
function _tap_buzz(pattern) {
  if (localStorage.getItem('tap_haptics') === 'false')
    return;
  try {
    if (typeof navigator.vibrate === 'function')
      navigator.vibrate(pattern);
  } catch (err) {
    // Throws in some browsers when called outside a user gesture.
  }
}

/** Drop every tap marker.  Pass an element to limit it to that one. */
function _clear_tap_feedback(el) {
  const target = $(el || _tap_feedback_selector);
  // The "pending" bookkeeping is global (one marker at a time), so it
  // may only be dropped when the element it belongs to is the one being
  // cleared.  Without this guard, clearing sentence 🔊 A when its speech
  // starts would also cancel the safety timeout that keeps sentence 🔊
  // B's marker from sticking on screen for ever.
  if (!el || (_tap_pending_el && _tap_pending_el[0] === target[0])) {
    if (_tap_pending_timer != null) {
      clearTimeout(_tap_pending_timer);
      _tap_pending_timer = null;
    }
    if (_tap_pending_start_timer != null) {
      clearTimeout(_tap_pending_start_timer);
      _tap_pending_start_timer = null;
    }
    _tap_pending_el = null;
  }
  target.removeClass('tap-pressed tap-ack tap-double tap-pending');
}

/** Finger / mouse button went down on el: answer immediately. */
function _tap_press(el) {
  // A new touch supersedes whatever the previous one was waiting for.
  _clear_tap_feedback();
  $(el).addClass('tap-pressed');
}

/** Finger / mouse button came up (or the touch was cancelled). */
function _tap_release(el) {
  $(el).removeClass('tap-pressed');
}

/**
 * "Got it": flash the word and buzz, so the reader knows the gesture was
 * received even if the answer takes another second.  is_double gets a
 * visibly different two-beat pulse so a double tap never feels like two
 * single taps.
 */
function _tap_acknowledge(el, is_double) {
  const $el = $(el);
  if ($el.length === 0) return;
  const cls = is_double ? 'tap-double' : 'tap-ack';
  $el.removeClass('tap-ack tap-double');
  // Force a reflow: without it, re-adding the class on the same element
  // does not restart the CSS animation.
  void $el[0].offsetWidth;
  $el.addClass(cls);
  setTimeout(function () { $el.removeClass(cls); },
             is_double ? _tap_double_ms : _tap_ack_ms);
  _tap_buzz(is_double ? [14, 45, 22] : 14);
}

/**
 * "Working": mark the word until _clear_tap_feedback() runs.  Only used
 * when the tap actually triggers a server round trip.
 *
 * Pass the ack duration as delay_ms so the two markers play in sequence
 * instead of fighting: .tap-ack/.tap-double and .tap-pending both drive
 * `outline`, and with equal CSS specificity the later rule wins outright,
 * so showing them together would hide the "tap received" flash -- the
 * very thing this is all for.  Fast responses (a cached term popup, a
 * local status swap) finish inside the delay and never show the marker.
 */
function _tap_mark_pending(el, delay_ms) {
  const $el = $(el);
  if ($el.length === 0) return;
  if (_tap_pending_start_timer != null) clearTimeout(_tap_pending_start_timer);
  if (_tap_pending_timer != null) clearTimeout(_tap_pending_timer);
  $(_tap_feedback_selector + '.tap-pending').not($el).removeClass('tap-pending');
  _tap_pending_el = $el;

  const start = function () {
    _tap_pending_start_timer = null;
    if ($el.length === 0 || !$el[0].isConnected) return;
    $el.addClass('tap-pending');
    _tap_pending_timer = setTimeout(function () { _clear_tap_feedback(); },
                                    _tap_pending_max_ms);
  };

  if (delay_ms > 0) {
    _tap_pending_start_timer = setTimeout(start, delay_ms);
  } else {
    start();
  }
}

/**
 * Bridge for the other reading-page scripts (currently tts.js, for the
 * sentence 🔊 buttons).  They live in their own file/IIFE and cannot
 * reach into this one, so the marker API is published here rather than
 * duplicated: one implementation, one set of timings, one CSS contract.
 */
window.luteTapFeedback = {
  press: _tap_press,
  release: _tap_release,
  ack: _tap_acknowledge,
  pending: _tap_mark_pending,
  clear: _clear_tap_feedback,
  buzz: _tap_buzz,
  ACK_MS: _tap_ack_ms,
  DOUBLE_MS: _tap_double_ms,
};

// Clear "pending" as soon as the thing we were waiting for shows up.
// The term form posts a message when it has rendered, and the reading
// text is re-swapped on every status update; either way the wait is over.
window.addEventListener('message', function (event) {
  const d = event.data;
  if (d && (d.event === 'LuteTermFormOpened' || d.event === 'LuteTermFormPosted')) {
    _clear_tap_feedback();
  }
});


/********************************************/
// Mobile events.
//
// 1. Regular vs long taps.
//
// Ref https://borstch.com/blog/javascript-touch-events-and-mobile-specific-considerations
//
// I had used https://github.com/benmajor/jQuery-Touch-Events, but
// during development was running into problems with chrome dev tool
// mobile emulation freezing.  I thought it was the library but the
// problem occurred with the vanilla js below.
//
// https://stackoverflow.com/questions/22722727/
// chrome-devtools-mobile-emulation-scroll-not-working suggests that
// it's a devtools problem, and I agree, as it occurred at random.
// I'm still sticking with the vanilla js below though: it's very
// simple, and there's no need to add another dependency just to
// distinguish regular and long taps.
//
// 2. Single tap vs double tap
//
// For my iphone at least, double-tap didn't seem to work, even though
// it did in chrome devtools emulation.  For my iphone, the phone
// browser seemed to add a delay after each click, so the double
// clicks were never fast enough to be distinguishable.  For that
// reason, instead of using click time differences to distinguish
// between single and double clicks, the code tracks the
// _last_touched_element: if the second tap is on the same element as
// the first AND happens within _double_tap_max_interval_ms, it's
// treated as a double tap.  The time window keeps a slow
// tap ... (pause) ... tap on the same word from being misjudged.
//
// 3. Scroll/swipe
//
// Swipes have to be tracked because each swipe starts with a touch,
// which gets confused with the other events.  If the touch start and
// end differ by a threshold amount, assume the user is scrolling.

// Tracking if long tap.
let _touch_start_time;
const _long_touch_min_duration_ms = 500;

// Tracking if double-click: same element AND re-tapped within
// _double_tap_max_interval_ms.  Without the time window, a single tap
// followed by a much later tap on the same word was misjudged as a
// double tap.
let _last_touched_element_id = null;
let _last_touched_time = 0;
const _double_tap_max_interval_ms = 350;

// Quick Set Status Mode: the single-tap popup is scheduled instead of
// shown immediately, so the second tap of a double tap cancels it and
// the popup never flashes while cycling a word's status.  The delay
// must be >= the double-tap window above.
const _quick_popup_delay_ms = _double_tap_max_interval_ms + 30;
let _quick_pending_popup_timer = null;

/** Cancel a popup that was scheduled by a previous tap. */
function _cancel_pending_popup() {
  if (_quick_pending_popup_timer != null) {
    clearTimeout(_quick_pending_popup_timer);
    _quick_pending_popup_timer = null;
  }
}

// Tracking if swipe.
let _touch_start_coords = null;
const _swipe_min_threshold_pixels = 15;

function _get_coords(touch) {
  var touchX = touch.clientX;
  var touchY = touch.clientY;
  // console.log('X: ' + touchX + ', Y: ' + touchY);
  return [ touchX, touchY ];
}

function _swipe_distance(e) {
  const curr_coords = _get_coords(e.originalEvent.changedTouches[0]);
  const dX = curr_coords[0] - _touch_start_coords[0];
  const dY = curr_coords[1] - _touch_start_coords[1];
  return Math.sqrt((dX * dX) + (dY * dY));
}

function touch_started(e) {
  _touch_start_coords = _get_coords(e.originalEvent.touches[0]);
  _touch_start_time = Date.now();
  // Answer the finger the instant it lands: everything below can wait
  // for touchend (long-press detection) or for the server.
  _tap_press($(this));
}

// The touch was stolen (incoming call, pull-down notification, browser
// gesture, ...): no tap happened, so don't leave the word marked.
function touch_cancelled(e) {
  _clear_tap_feedback($(this));
}

function touch_ended(e) {
  const el = $(this);
  _tap_release(el);

  if (_swipe_distance(e) >= _swipe_min_threshold_pixels) {
    // Do nothing else if this was a swipe.
    _cancel_pending_popup();
    _clear_tap_feedback(el);
    return;
  }

  const this_id = el.attr("id")

  $('span.kwordmarked').removeClass('kwordmarked');
  $('span.wordhover').removeClass('wordhover');

  const touch_duration = Date.now() - _touch_start_time;
  const is_long_touch = (touch_duration >= _long_touch_min_duration_ms);
  const now = Date.now();
  const is_double_click = (this_id === _last_touched_element_id &&
    (now - _last_touched_time) <= _double_tap_max_interval_ms);
  _last_touched_element_id = null;  // Already checked in is_double_click.

  if (_quick_set_status_active()) {
    // Quick Set Status Mode: reassign the gestures so marking a word's
    // status never forces the on-screen keyboard open.
    //
    //   single tap  -> term popup (same as desktop hover)
    //   double tap  -> cycle status 1 -> 3 -> well known(99) -> 1
    //   long press  -> term edit form
    _cancel_pending_popup();
    if (is_long_touch) {
      _tap_acknowledge(el, false);
      _tap_mark_pending(el, _tap_ack_ms);
      show_term_edit_form(el);
    }
    else if (is_double_click) {
      _close_term_popups();
      _tap_acknowledge(el, true);
      _tap_mark_pending(el, _tap_double_ms);
      _quick_cycle_status(el);
    }
    else if (selection_start_el != null) {
      _tap_acknowledge(el, false);
      select_over(el, e);
      select_ended(el, e);
    }
    else {
      _tap_acknowledge(el, false);
      _tap_mark_pending(el, _tap_ack_ms);
      // Delay the popup by the double-tap window: if a second tap
      // arrives in time, _cancel_pending_popup() above drops it and
      // the status cycles without the card ever appearing.
      _quick_pending_popup_timer = setTimeout(() => {
        _quick_pending_popup_timer = null;
        _quick_show_popup(el);
      }, _quick_popup_delay_ms);
      _last_touched_element_id = this_id;
      _last_touched_time = now;
    }
    return;
  }

  if (is_long_touch) {
    _tap_acknowledge(el, false);
    _tap_hold(el, e);
  }
  else if (selection_start_el != null) {
    _tap_acknowledge(el, false);
    select_over(el, e);
    select_ended(el, e);
  }
  else if (is_double_click) {
    _tap_acknowledge(el, true);
    _tap_mark_pending(el, _tap_double_ms);
    _double_tap(el);
  }
  else {
    // _single_tap only does something (and only talks to the server)
    // for status-0 words; only then is a "pending" marker honest.
    const acted = _single_tap(el, e);
    _tap_acknowledge(el, false);
    if (acted) _tap_mark_pending(el, _tap_ack_ms);
    _last_touched_element_id = this_id;
    _last_touched_time = now;
    el.addClass('kwordmarked');
  }
}


// Tap-holds define the start and end of a multi-word term.
function _tap_hold(el, e) {
  // console.log('hold tap');
  if (selection_start_el == null) {
    select_started(el, e);
    select_over(el, e);
  }
  else {
    select_over(el, e);
    select_ended(el, e);
  }
}

// Show the form.
function _double_tap(el, e) {
  // console.log('double tap');
  $(".ui-tooltip").css("display", "none");
  clear_newmultiterm_elements();
  show_term_edit_form(el);
}

/**
 * Mobile handler, single tap.
 *
 * Returns true if the tap started something that talks to the server -- a
 * status update or a term form load -- so the caller knows whether to
 * show the "pending" marker.
 **/
function _single_tap(el, e) {
  clear_newmultiterm_elements();

  const term_is_status_0 = (el.data("status-class") == "status0");
  if (!term_is_status_0) {
    return false;
  }

  const _tap_sets_status = () => {
    const s = localStorage.getItem('tap_sets_status');
    return (s === "true");
  }

  if (_tap_sets_status()) {
    el.addClass('kwordmarked');
    update_status_for_marked_elements(1);
  }
  else {
    show_term_edit_form(el);
  }
  return true;
}


/* ========================================= */
/** Quick Set Status Mode (mobile) helpers. */

// Whether the "Quick Set Status Mode" toggle is on (set from the
// reading menu; persisted in localStorage).
function _quick_set_status_active() {
  return localStorage.getItem('tap_sets_status') === 'true';
}

// Single tap in Quick Set Status Mode: show the term popup for the
// word, the same way a desktop hover does.  Reuses the jquery-ui
// tooltip widget the word's own container already has, so the popup
// content, positioning and close handlers match the hover experience
// exactly -- including for a word in a player subtitle, which carries
// its own widget instead of #thetext's.
function _quick_show_popup(el) {
  // Only one term popup may be on screen at a time.  These popups are
  // opened programmatically, and jquery-ui only wires auto-close
  // handlers (mouseleave / focusout) for real mouseover / focusin
  // opens -- see _registerCloseHandlers -- so nothing would ever close
  // one on its own.  Without this, every tap left its card behind and
  // the next tap stacked another on top: in the main text a wall of
  // popups, and over the player subtitle a pile of stray blocks.
  _close_term_popups();

  // The popup is scheduled, so the word may have been replaced by a
  // page/fragment swap while waiting.
  if (el.length === 0 || !el[0].isConnected) {
    _clear_tap_feedback();
    return;
  }
  // Words that aren't saved to the DB yet (status 0, no wid) have no
  // popup to show, so nothing is coming: drop the pending marker.
  if (isNaN(parseInt(el.data('wid'), 10))) {
    _clear_tap_feedback();
    return;
  }

  // Open through the widget of the container the word actually lives
  // in: #thetext for the reading pane, the subtitle's own widget for a
  // word in the player.
  const container = el.closest(_term_popup_containers);
  const scope = container.length ? container : $('#thetext');
  scope.tooltip("open", { target: el[0], type: "open" });
}

// Double tap in Quick Set Status Mode: cycle a single word's status
// through 1 -> 3 -> well known(99) -> 1.  Status 2/4/5 are skipped so
// the gesture is a predictable three-way toggle.
function _quick_cycle_status(el) {
  const CYCLE = [1, 3, 99];
  const cur = parseInt((el.data("status-class") || "status0").replace(/\D/g, ""), 10);
  let idx = CYCLE.indexOf(cur);
  const next = idx === -1 ? CYCLE[0] : CYCLE[(idx + 1) % CYCLE.length];
  if (next === cur) return;
  el.addClass('kwordmarked');
  update_status_for_marked_elements(next);
}


/********************************************/
// Keyboard navigation.

/** Get the textitems whose span_attribute value matches that of the
 * current active/hovered word.  If span_attribute is null, return
 * all. */
let get_textitems_spans = function(span_attribute) {
  if (span_attribute == null)
    return $('span.textitem').toArray();

  let elements = $('span.kwordmarked, span.newmultiterm, span.wordhover');
  elements.sort((a, b) => _get_order($(a)) - _get_order($(b)));
  if (elements.length == 0)
    return elements;

  const attr_value = $(elements[0]).data(span_attribute);
  const selector = `span.textitem[data-${span_attribute}="${attr_value}"]`;
  return $(selector).toArray();
};

let handle_bookmark = function() {
  // Function defined in read/index.html ... yuck, need to reorganize this js code.
  // TODO javascript: reorganize, or make modules.
  add_bookmark();
}

let handle_edit_page = function() {
  // Function defined in read/index.html ... yuck, need to reorganize this js code.
  // TODO javascript: reorganize, or make modules.
  edit_current_page();
}

let handle_mark_read_hotkey = function() {
  // Function defined in read/index.html ... yuck, need to reorganize this js code.
  // TODO javascript: reorganize
  handle_page_done(false, 1);
}

let handle_mark_read_well_known_hotkey = function() {
  // Function defined in read/index.html ... yuck, need to reorganize this js code.
  // TODO javascript: reorganize
  handle_page_done(true, 1);
}

/** Copy the text of the textitemspans to the clipboard, and add a
 * color flash. */
let handle_copy = function(span_attribute) {
  tis = get_textitems_spans(span_attribute);
  copy_text_to_clipboard(tis);
}

/** Get the text from the text items, adding "\n" between paragraphs. */
let _get_textitems_text = function(textitemspans) {
  if (textitemspans.length == 0)
    return '';

  let _partition_by_paragraph_id = function(textitemspans) {
    const partitioned = {};
    $(textitemspans).each(function() {
      const pid = $(this).attr('data-paragraph-id');
      if (!partitioned[pid])
        partitioned[pid] = [];
      partitioned[pid].push(this);
    });
    return partitioned;
  };
  const paras = _partition_by_paragraph_id(textitemspans);
  const paratexts = Object.entries(paras).map(([pid, spans]) => {
    let ptext = spans.map(s => $(s).text()).join('');
    return ptext.replace(/\u200B/g, '');
  });
  return paratexts.join('\n').trim();
}


let _show_element_message_tooltip = function(element, title, message, remove_after_timeout = 2000) {
  const el = $(element);

  let show_results = "";
  if (title != null) {
    show_results = `<b>${title}</b><br />`;
  }
  show_results += message.replaceAll("\n", "<br />");
  const tooltip = $('<span class="manual-tooltip"></span>').html(show_results);
  tooltip.insertAfter(el);

  // Positioning.  Rest of css is handled in styles.css.
  tooltip.css({
    top: el.offset().top + el.outerHeight() + 5, // below the target
    left: el.offset().left,
  });

  tooltip.hover(
    function () { tooltip.addClass('hovered'); },
    function () { tooltip.removeClass('hovered'); tooltip.remove(); }
  );

  if (remove_after_timeout > 0) {
    setTimeout(() => { tooltip.remove(); }, remove_after_timeout);
  }
};

let _hide_element_message_tooltips = function() {
  $('.manual-tooltip').remove();
  // Hide all jQuery UI tooltips (term detail popups).  Pages can have
  // multiple tooltip containers (#thetext and the two player subtitles),
  // each with its own widget instance -- one helper closes them all.
  // It has to go through the widgets (see _close_term_popups): a bare
  // $('.ui-tooltip').remove() hides the card but leaves the word marked
  // as described-by, and jquery-ui then refuses to open that word again.
  _close_term_popups();
};


let copy_text_to_clipboard = function(textitemspans) {
  const copytext = _get_textitems_text(textitemspans);
  if (copytext == '')
    return;

  var textArea = document.createElement("textarea");
  textArea.value = copytext;
  document.body.appendChild(textArea);
  textArea.select();
  document.execCommand("Copy");
  textArea.remove();

  const removeFlash = function() {
    $('span.flashtextcopy').addClass('wascopied'); // for acceptance testing.
    $('span.flashtextcopy').removeClass('flashtextcopy');
  };

  removeFlash();
  textitemspans.forEach(function (t) {
    $(t).addClass('flashtextcopy');
  });
  setTimeout(() => removeFlash(), 1000);

  const last_el = textitemspans[textitemspans.length - 1];
  _show_element_message_tooltip(last_el, null, "Copied to clipboard.", 1000);
}


/** First selected/hovered element, or null if nothing. */
let _first_selected_element = function() {
  let elements = $('span.kwordmarked, span.newmultiterm, span.wordhover');
  if (elements.length == 0)
    return null;
  elements.sort((a, b) => _get_order($(a)) - _get_order($(b)));
  return elements[0];
};


/** Update cursor, clear prior cursors. */
let _update_screen_cursor = function(target) {
  $('span.newmultiterm, span.kwordmarked, span.wordhover').removeClass('newmultiterm kwordmarked wordhover');
  remove_status_highlights();
  target.addClass('kwordmarked');
  save_curr_data_order(target);
  apply_status_class(target);
  $(window).scrollTo(target, { axis: 'y', offset: -150 });
  show_term_edit_form(target);
};


/** Move to the next/prev candidate determined by the selector.
 * direction is 1 if moving "right", -1 if moving "left" -
 * note that these switch depending on if the language is right-to-left! */
let _move_cursor = function(selector, direction = 1) {
  _hide_element_message_tooltips();
  const fe = _first_selected_element();
  const fe_order = (fe != null) ? _get_order($(fe)) : (direction > 0 ? -1 : Infinity);
  let candidates = $(selector).toArray();
  let comparator = function(a, b) { return a > b };
  if (direction < 0) {
    candidates = candidates.reverse();
    comparator = function(a, b) { return a < b };
  }

  const match = candidates.find(el => comparator(_get_order($(el)), fe_order));
  if (match) {
    _update_screen_cursor($(match));

    // Highlight the word if we're jumping around a lot.
    if (selector != 'span.word') {
      const match_order = _get_order($(match));
      const match_class = `flash_${match_order}`;
      $(match).addClass(`flashtextcopy ${match_class}`);
      setTimeout(() => $(`.${match_class}`).removeClass(`flashtextcopy ${match_class}`), 1000);
    }
  }
};


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
