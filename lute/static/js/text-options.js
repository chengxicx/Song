"use strict";

/* NOTE: this code uses some globals from resize.js */
// TODO refactor: remove js global state!

const domObserver = new MutationObserver((mutationList, observer) => {
  incrementFontSize(0);
  incrementLineHeight(0);
  setColumnCount(null);
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


function incrementFontSize(delta) {
  // Manga text items size themselves with container-query units (cqw)
  // against the page image; the reading-pane font controls must not
  // override those, so exclude any item inside a manga block.
  // Subtitle word spans (YouTube/MP3/Bilibili and TTS players) are also
  // excluded: they must inherit the fixed subtitle size (1.5rem) rather
  // than being stamped with the reading-pane font size, which would
  // otherwise shrink them whenever #thetext is reloaded (e.g. on a term
  // status save) and never restore them.
  const textItems = Array.from(document.querySelectorAll("span.textitem"))
    .filter((item) => !item.closest(".manga-text-block"))
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
  const lhDefault = parseFloat(s.getPropertyValue('line-height'));

  const STORAGE_KEY = "paraLineHeight";
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
  // A manga page is a single full-page image with %-positioned text
  // overlays.  CSS multicol -- even column-count: 1 -- makes #thetext a
  // fragmentation/fragmentainer context: once the zoomed page grows taller
  // than the pane, every OCR box past the first ~pane-height boundary is
  // painted ~one pane-height too high (layout correct, paint/hit off),
  // so the lower boxes stop lining up with / become unclickable after
  // zooming and scrolling.  Manga must never run in a multicol container.
  if (theText && theText.classList.contains("manga-text-container")) {
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
