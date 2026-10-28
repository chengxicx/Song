"use strict";

let mouse_pos;
let widthDefault;
let trHeightDefault;
const borderWidth = 30; // drag click area is actually dependent on the pseudo element width. this is arbitrary high value
const wordFrame = document.getElementById("wordframeid");
const dictFramesCont = document.getElementById("dictframes");
const dictContainer = document.querySelector(".dictcontainer");

applyInitialPaneSizes();

function resizeCol(e){
  const dx = mouse_pos - e.x;
  mouse_pos = e.x;
  // getting the width with computedStyle doesn't seem to be working correctly.
  // getting width directly, but for this initial width must be set via javascript 
  // so the style is inline for this code to work
  const currentWidthLeft = readPaneLeft.style.width;
  const readPaneWidth = parseFloat(window.getComputedStyle(readPaneContainer).getPropertyValue("width"));
  // const currentWidthLeft = parseFloat(window.getComputedStyle(readPaneLeft).getPropertyValue("width"));

  let lfWidthPct = parseFloat(currentWidthLeft) - (dx / readPaneWidth * 100);
  lfWidthPct = clamp(lfWidthPct, 25, 95);
  const rtWidthPct = (100 - lfWidthPct) * getReadPaneWidthRatio();

  readPaneLeft.style.width = `${lfWidthPct}%`;
  readPaneRight.style.width = `${rtWidthPct}%`;

  localStorage.setItem("textWidth", lfWidthPct);
}

function resizeRow(e){
  const dx = mouse_pos - e.y;
  mouse_pos = e.y;
  const currentHeightWordFrame = parseFloat(window.getComputedStyle(readPaneRight).gridTemplateRows.split(" ")[0]);
  const readPaneRightHeight = parseFloat(window.getComputedStyle(readPaneRight).getPropertyValue("height"));
  // console.log(currentHeightWordFrame);
  let wordFrameHeight = (currentHeightWordFrame / readPaneRightHeight * 100) - (dx / readPaneRightHeight * 100);
  wordFrameHeight = clamp(wordFrameHeight, 5, 95);
  // const currentWidthRight = window.getComputedStyle(readPaneRight).width;
  // console.log(currentHeightWord);

  readPaneRight.style.gridTemplateRows = `${wordFrameHeight}% 1fr`;
  // console.log(`${parseInt(currentWidthRight) - dx}px`);
  // readPaneRight.style.width = `${parseInt(currentWidthRight) + dx}px`;
  localStorage.setItem("trHeight", wordFrameHeight);
}

function resizePaneRight(e) {
  const dy = mouse_pos - e.y;
  mouse_pos = e.y;

  // Hack!!!  Currently, when you open a page and long-click two terms
  // to create a multiword term, the term edit handles appear right at
  // the bottom of the screen, and dragging throws an error ("Cannot
  // read properties of undefined (reading 'split')") ... I can't
  // figure out why.  Setting the currentTy to 80 at least lets the
  // thing be draggable.
  let currentTy = 80;
  try {
    currentTy = parseFloat(readPaneRight.style.transform.split("(")[1].split(")")[0]);
  }
  catch (error) {
    console.log("Error on settingTy: " + error.message);
  }
  // console.log(`current ty = ${currentTy}`);

  let resultTy = currentTy - (dy / document.documentElement.clientHeight * 100);
  resultTy = clamp(resultTy, 5, 95);
  readPaneRight.style.transform = `translateY(${resultTy}%)`;
  e.preventDefault();
}

if (mediaTablet.matches) {
  readPaneRight.addEventListener("pointerdown", function(e){
    if (e.offsetY < borderWidth) {
      // if there's transition animation dragging is not smooth.
      // gets reverted back on document.mouseup below
      readPaneRight.style.transition = "unset";
      setIFrameStatus("none");
      mouse_pos = e.y;
      document.addEventListener("pointermove", resizePaneRight);
      e.preventDefault();
    }
  });
}

readPaneRight.addEventListener("mousedown", function(e){
  if (e.offsetX < borderWidth) {
    setIFrameStatus("none");
    mouse_pos = e.x;
    document.addEventListener("mousemove", resizeCol);
    e.preventDefault(); // prevent selection
  }
});

// double click -> widen to 95% temporarily (doesn't save state)
readPaneRight.addEventListener("dblclick", function(e){
  if (e.target != e.currentTarget) return; // fixes: clicking dict tabs resizes panes
  
  if (e.offsetX < borderWidth) {
    // if the width is 95% then return to the last width value
    if (readPaneLeft.style.width == "95%") {
      const width = getFromLocalStorage("textWidth", widthDefault);
      readPaneLeft.style.width = `${width}%`;
      readPaneRight.style.width = `${(100 - width) * getReadPaneWidthRatio()}%`;
    } else {
      readPaneLeft.style.width = "95%";
      readPaneRight.style.width = `${5 * getReadPaneWidthRatio()}%`;
    }
    }
});

dictContainer.addEventListener("pointerdown", function(e){
  //if not stopPropagation resizing dictcontainer triggers parent event which resizes
  //readPaneRight at the same time (for @media 900)
  e.stopPropagation();
  // resize only if the border is selected. fixes: clicking on tab buttons area also able to resize pane
  if (e.target != e.currentTarget) return; 

  setIFrameStatus("none");
  if (e.offsetY < borderWidth) {
    mouse_pos = e.y;
    document.addEventListener("pointermove", resizeRow);
    e.preventDefault();
  }
});

dictContainer.addEventListener("dblclick", function(e){
  if (e.target != e.currentTarget) return; 
  
  if (e.offsetY < borderWidth) {
    if (readPaneRight.style.gridTemplateRows.split(" ")[0] == "5%") {
      readPaneRight.style.gridTemplateRows = `${getFromLocalStorage("trHeight", trHeightDefault)}% 1fr`;
    } else {
      readPaneRight.style.gridTemplateRows = `${5}% 1fr`;
    }
  }
});

document.addEventListener("pointerup", function(){
  document.removeEventListener("mousemove", resizeCol);
  document.removeEventListener("pointermove", resizeRow);
  setIFrameStatus("unset");

  if (mediaTablet.matches) {
    document.removeEventListener("pointermove", resizePaneRight);
    readPaneRight.style.removeProperty("transition");
  }
});

// if the iframes are clickable mousemove doesn't work correctly
function setIFrameStatus(status) {
  wordFrame.style.pointerEvents = status;
  dictFramesCont.style.pointerEvents = status;
}

// because right side is fixed. it's width value is different. need to find ratio
// basically: when gridContainer width is 100%, this doesn't mean that it takes the whole 
// viewport width. it can be less than that. but for the right side it's an absolute percentage value
function getReadPaneWidthRatio() {
  return parseFloat(window.getComputedStyle(readPaneContainer).getPropertyValue("width")) / parseFloat(document.documentElement.clientWidth);
}

function clamp (num, min, max) {
  return Math.min(Math.max(num, min), max);
}

function getTextWidthPercentage() {
  // returns percentage value
  const elementComputedStyle = window.getComputedStyle(readPaneLeft);
  return (parseFloat(elementComputedStyle.getPropertyValue("width")) / parseFloat(window.getComputedStyle(readPaneContainer).getPropertyValue("width"))) * 100;
  // return parseFloat(elementComputedStyle.width);
}

function getWordFrameHeightPercentage() {
  // returns percentage value
  // const elementComputedStyle = window.getComputedStyle(dictContainer);
  return (parseFloat(window.getComputedStyle(readPaneRight).gridTemplateRows.split(" ")[0]) / parseFloat(window.getComputedStyle(readPaneRight).getPropertyValue("height"))) * 100;
  // return parseFloat(elementComputedStyle.width);
}

// The narrow layout (@media max-width: 980px) owns both pane widths: the
// left pane fills the viewport and the right pane is a full-width bottom
// sheet.  So getTextWidthPercentage() reads 100 there, and writing that
// back inline bakes "left 100% / right 0%" into the document.  Inline
// styles outrank the desktop rules, so the first time the viewport is wide
// again -- un-maximising the window, or the reader dropping a flash notice
// and with it the document scrollbar, which moves the 980px breakpoint --
// the left pane stays full width and the dictionary pane collapses to zero
// width at the right edge: the divider jumps to the far right and the pane
// is unusable.  Leave the widths to the media query while narrow, and
// re-apply them when the breakpoint is crossed in either direction.
function applyInitialPaneSizes() {
  trHeightDefault = getWordFrameHeightPercentage();

  if (mediaTablet.matches) {
    widthDefault = 50; // the desktop split the CSS asks for
    readPaneLeft.style.removeProperty("width");
    readPaneRight.style.removeProperty("width");
  } else {
    widthDefault = getTextWidthPercentage();
    // Same 25..95 range resizeCol stores: a bogus remembered value must not
    // be able to collapse a pane to nothing.
    const width = clamp(getFromLocalStorage("textWidth", widthDefault), 25, 95);
    const ratio = getReadPaneWidthRatio();
    readPaneLeft.style.width = `${width}%`;
    readPaneRight.style.width = `${(100 - width) * ratio}%`;
  }

  const height = getFromLocalStorage("trHeight", trHeightDefault);
  readPaneRight.style.gridTemplateRows = `${height}% 1fr`;
}

// Crossing the breakpoint changes which layout owns the widths, so start
// over: entering the narrow layout drops the desktop split, leaving it
// restores the reader's stored one.  Without this a split measured in one
// layout survives into the other.
mediaTablet.addEventListener("change", applyInitialPaneSizes);

function getFromLocalStorage(item, defaultVal) {
  // return Number(localStorage.getItem(item) ?? defaultVal);
  const storageVal = localStorage.getItem(item);
  
  if ((!storageVal) || isNaN(storageVal)) {
    return Number(defaultVal);
  } else {
    return Number(storageVal);
  }
}
