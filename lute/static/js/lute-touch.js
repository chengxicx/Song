/* lute-touch.js
   --------------------------------------------------------------
   Tap feedback (visual + haptic), touch gestures, Quick Set
   Status mode, text selection and copy helpers.
   --------------------------------------------------------------
   Split out of lute.js.  Every script under static/js shares one
   global scope and is loaded in order (see templates/base.html),
   so splitting the file changes nothing at runtime.
*/
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
