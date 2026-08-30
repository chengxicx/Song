/*
 * Mobile bottom mini-player (see player-styles.css, "Mobile bottom
 * mini-player").  The dock layouts themselves are pure CSS (<= 980px);
 * this file only handles the expand/collapse interaction and keeps two
 * body flags in sync with player visibility:
 *
 * - lute-player-docked: some player container is visible, so the text
 *   pane needs bottom padding to not end under the docked bar.
 * - lute-player-sheet-open: the expanded sheet is open; CSS draws the
 *   backdrop from this.
 *
 * Desktop (> 980px) is unaffected: the chevron button is display:none
 * and all dock rules live inside the mobile media query.
 */
(function () {
  "use strict";

  var mq = window.matchMedia("(max-width: 980px)");
  var DOCKED_BODY = "lute-player-docked";
  var OPEN_BODY = "lute-player-sheet-open";
  var SHEET_CLASS = "player-sheet-open";

  function containers() {
    return Array.prototype.slice.call(
      document.querySelectorAll(".youtube-player-container"));
  }

  /* getClientRects (not offsetParent): the docked containers are
     position:fixed, whose offsetParent is always null. */
  function anyVisible() {
    return containers().some(function (c) {
      return c.getClientRects().length > 0;
    });
  }

  function updateDockedFlag() {
    document.body.classList.toggle(DOCKED_BODY, mq.matches && anyVisible());
  }

  function openSheet() {
    return document.querySelector("." + SHEET_CLASS);
  }

  function setOpen(container, on) {
    containers().forEach(function (c) { c.classList.remove(SHEET_CLASS); });
    document.body.classList.remove(OPEN_BODY);
    if (container && on) {
      container.classList.add(SHEET_CLASS);
      document.body.classList.add(OPEN_BODY);
    }
  }

  function bind(container) {
    if (container.dataset.dockBound) return;
    container.dataset.dockBound = "1";
    var btn = container.querySelector(".player-expand-btn");
    if (!btn) return;
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      setOpen(container, !container.classList.contains(SHEET_CLASS));
    });
  }

  // Tap outside the open sheet (page text or the backdrop) closes it.
  document.addEventListener("click", function (e) {
    var open = openSheet();
    if (open && !open.contains(e.target)) setOpen(open, false);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      var open = openSheet();
      if (open) setOpen(open, false);
    }
  });

  function init() {
    containers().forEach(bind);
    updateDockedFlag();

    // tts.js (TTS toggle) and youtube-player.js (audio-only mode)
    // change container visibility via style/class mutations.
    var mo = new MutationObserver(updateDockedFlag);
    containers().forEach(function (c) {
      mo.observe(c, { attributes: true, attributeFilter: ["style", "class"] });
    });

    var onMq = function () {
      if (!mq.matches) {
        // Back to desktop: collapse the sheet, it is CSS-hidden there.
        var open = openSheet();
        if (open) setOpen(open, false);
      }
      updateDockedFlag();
    };
    if (mq.addEventListener) mq.addEventListener("change", onMq);
    else if (mq.addListener) mq.addListener(onMq);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
