#!/usr/bin/env python3
"""Fix transcript scroll issue by adding delay and fixing target variable."""

import re

# Read the file
with open('lute/static/js/youtube-player.js', 'r', encoding='utf-8') as f:
    content = f.read()

# Find and replace the problematic section
old_code = '''        } else {
          ytTranscript.style.display = "block";
          ytTranscriptBtn.classList.add("on");
          // Center the current line when the panel is opened.
          // Strategy:
          //   1) Use double-rAF to wait for layout after display:none -> block.
          //   2) If ytCueIndex < 0 (no playback yet), infer from currentTime.
          //   3) Retry up to 3 times over 500ms to handle slow DOM rendering.
          //   4) Log a debug message if scrolling fails so we can investigate.
          var tryScroll = function (attempt) {
            console.log("[YouTube Player] tryScroll attempt", attempt, "ytCueIndex:", ytCueIndex);
            if (!ytTranscriptList) {
              console.warn("[YouTube Player] ytTranscriptList not found on attempt", attempt);
              return;
            }
            // Force a reflow to ensure display:none -> block has completed layout.
            // Without this, clientHeight can still be 0 even after double-rAF.
            var containerHeight = ytTranscriptList.clientHeight;
            console.log("[YouTube Player] containerHeight before reflow:", containerHeight);
            void ytTranscriptList.offsetHeight; // Trigger reflow
            containerHeight = ytTranscriptList.clientHeight;
            console.log("[YouTube Player] containerHeight after reflow:", containerHeight);

            var idx = ytCueIndex;
            if (idx < 0 && ytPlayer) {
              var t = ytPlayer.getCurrentTime() || 0;
              console.log("[YouTube Player] ytCueIndex < 0, currentTime:", t, "CUES length:", CUES.length);
              for (var k = CUES.length - 1; k >= 0; k--) {
                if ((CUES[k].start || 0) <= t) {
                  idx = k;
                  break;
                }
              }
              if (idx < 0) idx = 0;
              console.log("[YouTube Player] inferred idx:", idx);
            }
            if (idx < 0) idx = 0;
            var row = ytTranscriptList.querySelector("#yt-transcript-row-" + idx);
            console.log("[YouTube Player] row:", row, "idx:", idx, "CUES length:", CUES.length);
            if (row) {
              console.log("[YouTube Player] row.offsetTop:", row.offsetTop, "containerHeight:", containerHeight, "row.offsetHeight:", row.offsetHeight, "target:", target);
              // Use scrollIntoView with block: center for more reliable scrolling.
              // This method automatically calculates the correct scroll position
              // based on the element's position relative to the viewport.
              row.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
              console.log("[YouTube Player] scrollIntoView called with block: center");
            } else if (attempt < 3) {
              // Row not found yet — retry after 100ms
              console.log("[YouTube Player] row not found, retrying...");
              setTimeout(function () {
                tryScroll(attempt + 1);
              }, 100);
            } else {
              console.warn("[YouTube Player] Failed to find row #" + idx + " after 3 attempts, CUES length:", CUES.length);
            }
          };
          window.requestAnimationFrame(function () {
            window.requestAnimationFrame(function () {
              tryScroll(1);
            });
          });
        }'''

new_code = '''        } else {
          ytTranscript.style.display = "block";
          ytTranscriptBtn.classList.add("on");
          // Center the current line when the panel is opened.
          // Strategy:
          //   1) Use double-rAF to wait for layout after display:none -> block.
          //   2) If ytCueIndex < 0 (no playback yet), infer from currentTime.
          //   3) Retry up to 3 times over 500ms to handle slow DOM rendering.
          //   4) Log a debug message if scrolling fails so we can investigate.
          //   5) Add a small delay to ensure max-height: 40vh has completed layout.
          window.requestAnimationFrame(function () {
            window.requestAnimationFrame(function () {
              setTimeout(function () {
                var tryScroll = function (attempt) {
                  console.log("[YouTube Player] tryScroll attempt", attempt, "ytCueIndex:", ytCueIndex);
                  if (!ytTranscriptList) {
                    console.warn("[YouTube Player] ytTranscriptList not found on attempt", attempt);
                    return;
                  }
                  // Force a reflow to ensure display:none -> block has completed layout.
                  // Without this, clientHeight can still be 0 even after double-rAF.
                  var containerHeight = ytTranscriptList.clientHeight;
                  console.log("[YouTube Player] containerHeight before reflow:", containerHeight);
                  void ytTranscriptList.offsetHeight; // Trigger reflow
                  containerHeight = ytTranscriptList.clientHeight;
                  console.log("[YouTube Player] containerHeight after reflow:", containerHeight);

                  var idx = ytCueIndex;
                  if (idx < 0 && ytPlayer) {
                    var t = ytPlayer.getCurrentTime() || 0;
                    console.log("[YouTube Player] ytCueIndex < 0, currentTime:", t, "CUES length:", CUES.length);
                    for (var k = CUES.length - 1; k >= 0; k--) {
                      if ((CUES[k].start || 0) <= t) {
                        idx = k;
                        break;
                      }
                    }
                    if (idx < 0) idx = 0;
                    console.log("[YouTube Player" inferred idx:", idx);
                  }
                  if (idx < 0) idx = 0;
                  var row = ytTranscriptList.querySelector("#yt-transcript-row-" + idx);
                  console.log("[YouTube Player] row:", row, "idx:", idx, "CUES length:", CUES.length);
                  if (row) {
                    var target =
                      row.offsetTop - containerHeight / 2 +
                      row.offsetHeight / 2;
                    console.log("[YouTube Player] row.offsetTop:", row.offsetTop, "containerHeight:", containerHeight, "row.offsetHeight:", row.offsetHeight, "target:", target);
                    // Use scrollIntoView with block: center for more reliable scrolling.
                    // This method automatically calculates the correct scroll position
                    // based on the element's position relative to the viewport.
                    row.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
                    console.log("[YouTube Player] scrollIntoView called with block: center");
                  } else if (attempt < 3) {
                    // Row not found yet — retry after 100ms
                    console.log("[YouTube Player] row not found, retrying...");
                    setTimeout(function () {
                      tryScroll(attempt + 1);
                    }, 100);
                  } else {
                    console.warn("[YouTube Player] Failed to find row #" + idx + " after 3 attempts, CUES length:", CUES.length);
                  }
                };
                tryScroll(1);
              }, 100);
            });
          });
        }'''

if old_code in content:
    content = content.replace(old_code, new_code)
    with open('lute/static/js/youtube-player.js', 'w', encoding='utf-8') as f:
        f.write(content)
    print("Successfully fixed youtube-player.js")
else:
    print("Could not find the code to replace")
    print("Searching for related patterns...")
