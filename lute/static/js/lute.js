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
    // Close immediately, with no fade-out.  jQuery UI's default hide
    // animation keeps the card in the DOM -- visible and clickable -- for
    // ~450ms after it has been closed, and the card is 400px wide and sits
    // just below the word, over the next line.  Any word in that strip was
    // then swallowed by the fading card instead of being selected, which
    // is exactly the "some words intermittently can't be clicked" report.
    // It also left a stale card on screen at the previous word's position
    // while the next word's card opened, which reads as "the popup is not
    // next to the mouse".  See also div.ui-tooltip in styles.css.
    hide: false,
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

