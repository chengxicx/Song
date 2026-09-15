/* lute-cursor.js
   --------------------------------------------------------------
   Cursor management, status highlights, hovering, clicking,
   multiword selection.
   --------------------------------------------------------------
   Split out of lute.js.  Every script under static/js shares one
   global scope and is loaded in order (see templates/base.html),
   so splitting the file changes nothing at runtime.
*/
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
/** Tap vs. drag-selection.
 *
 * The players treat a click on the subtitle line / a transcript row as
 * "jump here and play".  A click also fires when a text selection ends,
 * so those handlers must ignore that click -- otherwise selecting a few
 * words on a paused player starts playback.
 *
 * window.getSelection() alone is not enough for that test: a drag that
 * ends inside word spans can leave the native selection collapsed (the
 * page's own word-selection UI takes over and the browser drops its
 * selection), in which case the click looks exactly like a tap.  So
 * also compare where the pointer went down with where it came up.
 */

/** Pointer travel, in CSS pixels, that still counts as a tap. */
const TAP_DRAG_SLOP = 6;

let _last_press_x = null;
let _last_press_y = null;

$(document).on('mousedown', function(e) {
  _last_press_x = e.clientX;
  _last_press_y = e.clientY;
});

/** True if the click ended a drag rather than being a tap. */
function click_was_drag(e) {
  if (_last_press_x === null)
    return false;
  return Math.abs(e.clientX - _last_press_x) > TAP_DRAG_SLOP
      || Math.abs(e.clientY - _last_press_y) > TAP_DRAG_SLOP;
}

/** True if this click should be ignored because it ended a text/word
 *  selection instead of being a tap. */
function click_ends_selection(e) {
  if (click_was_drag(e))
    return true;
  const sel = window.getSelection();
  return !!(sel && !sel.isCollapsed);
}


