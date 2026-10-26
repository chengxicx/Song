"use strict";

/* NOTE: this code uses some globals from resize.js */
// TODO refactor: remove js global state!

// Storage keys for the reader font picker.  They live up here, not next
// to the functions that use them, because the picker is applied during
// module init (see the end of this file): a `const` down there would
// still be in its temporal dead zone and every call would throw
// "Cannot access ... before initialization" -- which silently left the
// reading pane on the theme's font.
const READER_FONT_STORAGE_PREFIX = "readerFont:"; // + the language code
const READER_FONT_SYSTEM_PREFIX = "sys:"; // + an OS font family

const domObserver = new MutationObserver((mutationList, observer) => {
  incrementFontSize(0);
  incrementLineHeight(0);
  setColumnCount(null);
  applyReaderFont(null);
});

domObserver.observe(theText, {childList: true, subtree: true});

// Helper function to add event listeners
function addClickHandler(selector, callback, value) {
  const button = document.querySelector(selector);
  button.addEventListener("click", () => callback(value));
}

// Add button handlers.
addClickHandler(".font-plus", incrementFontSize, 1);
addClickHandler(".font-minus", incrementFontSize, -1);
addClickHandler(".lh-plus", incrementLineHeight, 0.1);
addClickHandler(".lh-minus", incrementLineHeight, -0.1);
addClickHandler(".width-plus", setTextWidth, 1.05);
addClickHandler(".width-minus", setTextWidth, 0.95);
addClickHandler(".column-one", setColumnCount, 1);
addClickHandler(".column-two", setColumnCount, 2);
addClickHandler(".manga-zoom-in", setMangaZoom, 1);
addClickHandler(".manga-zoom-out", setMangaZoom, -1);
addClickHandler(".manga-zoom-fit", resetMangaZoom);

// The font picker is a <select>, not a button: the choice is applied on
// change and re-applied from storage on load (and after every text
// swap, see the observer above).
const readerFontSelect = document.getElementById("reader-font");
if (readerFontSelect) {
  readerFontSelect.addEventListener("change", () =>
    setReaderFont(readerFontSelect.value)
  );
}
// System fonts are a Chromium-desktop-only capability (see
// loadSystemFonts); everywhere else the button in reading_menu.html
// stays hidden and the presets are the whole picker.
const readerFontSystemBtn = document.getElementById("reader-font-system");
if (readerFontSystemBtn && typeof window.queryLocalFonts === "function") {
  readerFontSystemBtn.hidden = false;
  readerFontSystemBtn.addEventListener("click", loadSystemFonts);
}
applyReaderFont(null);


function incrementFontSize(delta) {
  // When the grammar-analysis panel is open, the A+/A- buttons resize only
  // the panel (right side); the reading text keeps its own size.  Close the
  // panel to tune the reading text again.  delta === 0 is the post-reload
  // re-apply and must keep re-stamping the reading words regardless.
  const grammarPanel = document.querySelector(".grammar-analysis-panel");
  if (grammarPanel && delta !== 0) {
    const GF_KEY = "grammarFontSize";
    const gfs = parseFloat(getFromLocalStorage(GF_KEY, 14));
    const newGfs = clamp(gfs + delta, 8, 60);
    grammarPanel.style.fontSize = `${convertPixelsToRem(newGfs)}rem`;
    localStorage.setItem(GF_KEY, newGfs);
    return;
  }
  // Reader-mode EPUB chapters bring their own typography, and the
  // whitespace between tokens has no span of its own: stamping a size
  // on every span would leave the gaps at the container's size.  Scale
  // the whole chapter container instead, which also keeps the book's
  // relative sizes (headings, footnotes) intact.
  // Fixed-layout chapters are pixel page boxes fitted to the pane by
  // _fitMangaPage; their text must not be resized independently of the
  // box or it overflows, so the zoom buttons are the control for them.
  const epubBody = theText
    ? theText.querySelector(".epub-body:not([data-epub-viewport])")
    : null;
  if (epubBody) {
    const s = window.getComputedStyle(epubBody);
    const fontDefault = parseFloat(s.fontSize);
    const fontSize = getFromLocalStorage("fontSize", fontDefault);
    const newSize = clamp(fontSize + delta, 1, 50);
    epubBody.style.fontSize = `${convertPixelsToRem(newSize)}rem`;
    localStorage.setItem("fontSize", newSize);
    return;
  }

  // Manga text items size themselves with container-query units (cqw)
  // against the page image; the reading-pane font controls must not
  // override those, so exclude any item inside a manga block.  PDF page
  // words fill their % boxes, so the font controls are meaningless for
  // them too.
  // Subtitle word spans (YouTube/MP3/Bilibili and TTS players) are also
  // excluded: they must inherit the fixed subtitle size (1.5rem) rather
  // than being stamped with the reading-pane font size, which would
  // otherwise shrink them whenever #thetext is reloaded (e.g. on a term
  // status save) and never restore them.
  const textItems = Array.from(document.querySelectorAll("span.textitem"))
    .filter((item) => !item.closest(".manga-text-block"))
    .filter((item) => !item.closest(".pdf-word"))
    .filter((item) => !item.closest(".yt-scrolling-subtitle-inner"));
  if (textItems.length === 0)
    return;

  const s = window.getComputedStyle(textItems[0]);
  const fontDefault = parseFloat(s.fontSize);
  const STORAGE_KEY = "fontSize";
  const fontSize = getFromLocalStorage(STORAGE_KEY, fontDefault);

  const newSize = clamp(fontSize + delta, 1, 50);

  const sizeRem = `${convertPixelsToRem(newSize)}rem`;
  textItems.forEach((item) => {
    item.style.fontSize = sizeRem;
  });

  localStorage.setItem(STORAGE_KEY, newSize);
  // The screen groups were measured for the old font size; re-flow so
  // paragraphs don't spill into an overflowing multicol column.
  // (delta === 0 is the post-reload re-apply, which re-splits anyway.)
  if (delta !== 0 && typeof _splitToScreens === "function") {
    requestAnimationFrame(_splitToScreens);
  }
}

function convertPixelsToRem(sizePx) {
  const bodyFontSize =  window.getComputedStyle(document.querySelector("body")).fontSize;
  const sizeRem = sizePx / parseFloat(bodyFontSize);
  return sizeRem;
}

function incrementLineHeight(delta) {
  const paras = document.querySelectorAll("#thetext p");
  if (paras.length === 0)
    return; // e.g. manga pages have no paragraphs.
  const s = window.getComputedStyle(paras[0]);
  // getComputedStyle returns the line-height in pixels; convert it to a
  // unitless multiplier (line-height / font-size) so the stored value and
  // the +/- 0.1 steps are consistent multipliers, not px.
  const lhPx = parseFloat(s.getPropertyValue('line-height'));
  const fsPx = parseFloat(s.fontSize);
  const lhDefault = fsPx > 0 ? lhPx / fsPx : 1.3;

  // Key bumped (paraLineHeight -> paraLineHeight2) because the old key
  // may hold a px-derived value (e.g. 5) persisted by the pre-fix code
  // that read the computed line-height in px; applying that as a unitless
  // multiplier would render the text at an absurd 5x spacing.
  const STORAGE_KEY = "paraLineHeight2";
  let current_h = getFromLocalStorage(STORAGE_KEY, lhDefault);
  current_h = Number(current_h.toPrecision(2));
  let new_h = clamp(current_h + delta, 1.3, 5);

  paras.forEach((p) => { p.style.lineHeight = new_h; });
  localStorage.setItem(STORAGE_KEY, new_h);
  if (delta !== 0 && typeof _splitToScreens === "function") {
    requestAnimationFrame(_splitToScreens);
  }
}

function setTextWidth(factor) {
  const STORAGE_KEY = "textWidth";
  const currentWidth = getFromLocalStorage(STORAGE_KEY, widthDefault);
  const newWidth = clamp(currentWidth * factor, 25, 95);

  readPaneLeft.style.width = `${newWidth}%`;
  readPaneRight.style.width = `${(100 - newWidth) * getReadPaneWidthRatio()}%`;

  localStorage.setItem(STORAGE_KEY, newWidth);
}

function setColumnCount(num) {
  // A manga or PDF page is one fixed page with %-positioned word
  // overlays.  CSS multicol -- even column-count: 1 -- makes #thetext a
  // fragmentation/fragmentainer context: once the zoomed page grows taller
  // than the pane, every overlay box past the first ~pane-height boundary
  // is painted ~one pane-height too high (layout correct, paint/hit off),
  // so the lower boxes stop lining up with / become unclickable after
  // zooming and scrolling.  Fixed-page readers must never run in a
  // multicol container.
  if (theText && (theText.classList.contains("manga-text-container")
               || theText.classList.contains("pdf-text-container"))) {
    theText.style.columnCount = "auto";
    return;
  }
  // Reader-mode EPUB chapters scroll continuously: multicol would
  // fragment the chapter into columns inside a scrolling pane and the
  // book's own layout already decides how the text flows.
  if (theText && theText.classList.contains("epub-text-container")) {
    theText.style.columnCount = "auto";
    return;
  }
  let columnCount = num;
  if (columnCount == null) {
    const s = window.getComputedStyle(theText);
    columnCount = getFromLocalStorage("columnCount", s.columnCount);
  }
  theText.style.columnCount = columnCount;
  localStorage.setItem("columnCount", columnCount);
  // Re-flow the page into fresh screens for the new column count;
  // otherwise the previously-computed screen groups are kept and the
  // reading pane stays sparse after switching single <-> double column.
  if (typeof _splitToScreens === "function") {
    requestAnimationFrame(_splitToScreens);
  }
}

/**
 * The reading pane's font picker (#reader-font, see reading_menu.html).
 *
 * The theme stylesheets pin the reading text's font-family themselves,
 * so the pick is applied as a CSS override on #thetext -- the variable
 * below plus the data attribute that switches the rule on (see "Reader
 * font choice" in styles.css) -- rather than by inheritance.
 *
 * One stored key per language: the choices are per-language lists
 * (read/reader_fonts.py), so a "mincho" picked in a Japanese book must
 * not be looked for -- and silently miss -- in a Spanish one.  A key
 * that is not in the current book's list (switched language, or a font
 * removed from the table) falls back to the theme default.
 */
function _readerFontStorageKey() {
  const sel = document.getElementById("reader-font");
  return READER_FONT_STORAGE_PREFIX + (sel ? sel.dataset.lang || "" : "");
}

// choice === null means "re-apply whatever is stored" (page load, and
// after every text swap).
function applyReaderFont(choice) {
  const sel = document.getElementById("reader-font");
  if (!sel || !theText || sel.options.length === 0) return null;

  let stored =
    choice === null ? localStorage.getItem(_readerFontStorageKey()) || "" : choice;
  // A system font picked earlier is not in the list on a fresh page --
  // enumerating needs a permission prompt -- but the name is known, so
  // the one option it needs can be rebuilt without asking again.
  if (stored.startsWith(READER_FONT_SYSTEM_PREFIX)) {
    stored = addSystemFontChoice(sel, stored.slice(READER_FONT_SYSTEM_PREFIX.length));
  }
  const opt = Array.from(sel.options).find((o) => o.value === stored);
  const chosen = opt || sel.options[0];
  sel.value = chosen.value;

  const stack = chosen.value ? chosen.dataset.stack : "";
  if (stack) {
    theText.setAttribute("data-reader-font", "1");
    theText.style.setProperty("--reader-font-stack", stack);
  } else {
    theText.removeAttribute("data-reader-font");
    theText.style.removeProperty("--reader-font-stack");
  }
  return chosen.value;
}

function setReaderFont(choice) {
  const applied = applyReaderFont(choice);
  if (applied === null) return;
  localStorage.setItem(_readerFontStorageKey(), applied);
  // A new face re-flows the text: the screen groups were measured with
  // the old metrics, and in two-column mode the stale groups leave the
  // pane sparse or overflowing.
  if (typeof _splitToScreens === "function") {
    requestAnimationFrame(_splitToScreens);
  }
}

// ---- System fonts (Chromium only) -----------------------------------
// window.queryLocalFonts() is the only way a page can see the families
// installed on the machine, and only Chromium desktop has it: reading
// the font list is a fingerprinting vector, so Safari, Firefox and
// every mobile browser simply refuse.  The button is revealed by the
// init block below when the API exists; elsewhere the presets are the
// whole picker.
//
// A picked family is stored as "sys:<family>": that survives a reload
// without asking for the font list again, because applyReaderFont can
// rebuild the single option from the stored name (see the top of this
// file for the prefix).
function _readerFontSystemStack(family) {
  // A quote or backslash in the family name would break the CSS value,
  // so drop them.  The serif tail only applies if the font has been
  // uninstalled since it was picked.
  const safe = String(family).replace(/["\\]/g, "");
  return `"${safe}", serif`;
}

function _readerFontSystemGroup(sel) {
  let group = sel.querySelector('optgroup[data-reader-system-fonts="1"]');
  if (!group) {
    group = document.createElement("optgroup");
    group.dataset.readerSystemFonts = "1";
    group.label = I18N.t("System fonts");
    sel.appendChild(group);
  }
  return group;
}

function addSystemFontChoice(sel, family) {
  const value = READER_FONT_SYSTEM_PREFIX + family;
  if (Array.from(sel.options).some((o) => o.value === value)) return value;
  const group = _readerFontSystemGroup(sel);
  const stack = _readerFontSystemStack(family);
  const opt = document.createElement("option");
  opt.value = value;
  opt.textContent = family;
  opt.dataset.stack = stack;
  // Preview each family in its own face, like the presets do.
  opt.style.fontFamily = stack;
  // Keep the group in family order: it is what makes a few hundred
  // entries from queryLocalFonts browsable.
  const after = Array.from(group.children).find(
    (o) => o.textContent.localeCompare(family) > 0
  );
  group.insertBefore(opt, after || null);
  return value;
}

async function loadSystemFonts() {
  const sel = document.getElementById("reader-font");
  const btn = document.getElementById("reader-font-system");
  if (!sel || !btn) return;
  btn.disabled = true;
  // The permission prompt is answered within a click or two, but a
  // prompt the browser never shows leaves the promise pending forever.
  // Re-enable the button after a while so a blocked prompt cannot leave
  // the reader with a dead control.
  const watchdog = setTimeout(() => {
    btn.disabled = false;
  }, 15000);
  try {
    const fonts = await window.queryLocalFonts();
    const families = [...new Set(fonts.map((f) => f.family))]
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b));
    families.forEach((family) => addSystemFontChoice(sel, family));
    // The list is loaded; nothing left for the button to do.
    btn.hidden = true;
  } catch (e) {
    // Denied, or the permission is blocked by policy.  Keep the presets
    // working and leave the button clickable -- the prompt only appears
    // on a user gesture, so a second click can still succeed.
    btn.disabled = false;
    btn.title = I18N.t("Could not read the system fonts");
  } finally {
    clearTimeout(watchdog);
  }
}
