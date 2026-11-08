/* lute-plain-text.js
   --------------------------------------------------------------
   The plain text of a rendered reading node: what the reader sees,
   minus what is not text.

   A furigana <rt> reading is part of textContent but must not be read
   aloud, sent to the readings API, or matched against a transcript:
   a 普通 span reads "普通ふつう".  The per-sentence play button is
   chrome, not text.

   Two callers need the same rule, and the sentence they describe must
   be the same one -- lute-furigana.js reads a paragraph for its ruby,
   lute-shadowing.js reads the practice panel's sentence -- so the
   strip lives here rather than in each of them.

   Deliberately global (like the other read-page scripts, see
   read/index.html): the callers are plain scripts, not modules.
   -------------------------------------------------------------- */
"use strict";

window.lutePlainText = function (node) {
  if (!node) {
    return "";
  }
  if (node.nodeType === 3) {
    return node.textContent || "";
  }
  const clone = node.cloneNode(true);
  if (clone.querySelectorAll) {
    clone
      .querySelectorAll("rt, rp, .lute-sentence-play-btn")
      .forEach(function (el) {
        el.remove();
      });
  }
  return clone.textContent || "";
};
