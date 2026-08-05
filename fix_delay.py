#!/usr/bin/env python3
"""Fix transcript scroll by adding delay."""

import re

with open('lute/static/js/youtube-player.js', 'r') as f:
    lines = f.readlines()

# Find and fix the lines
new_lines = []
i = 0
while i < len(lines):
    line = lines[i]

    # Fix target variable definition
    if 'console.log("[YouTube Player] row.offsetTop:' in line:
        new_lines.append('                  var target =\n')
        new_lines.append('                    row.offsetTop - containerHeight / 2 +\n')
        new_lines.append('                    row.offsetHeight / 2;\n')
        new_lines.append('                  console.log("[YouTube Player] row.offsetTop:", row.offsetTop, "containerHeight:", containerHeight, "row.offsetHeight:", row.offsetHeight, "target:", target);\n')

    # Remove the old line that has target: (buggy)
    elif 'console.log("[YouTube Player] row.offsetTop:' in line and 'target:' in line:
        pass  # Skip this line

    # Remove scrollIntoView line (we'll add it back with delay)
    elif 'row.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });' in line:
        pass  # Skip this line for now

    # Add delay wrapper
    elif 'window.requestAnimationFrame(function () {' in line and 'tryScroll(1);' in ''.join(lines[i:]):
        # Found the end of tryScroll function, add delay wrapper before it
        new_lines.append(line)
        # Add the delay wrapper
        new_lines.append('          window.requestAnimationFrame(function () {\n')
        new_lines.append('            window.requestAnimationFrame(function () {\n')
        new_lines.append('              setTimeout(function () {\n')
        new_lines.append('                tryScroll(1);\n')
        new_lines.append('              }, 100);\n')
        new_lines.append('            });\n')
        new_lines.append('          });\n')

    else:
        new_lines.append(line)
    i += 1

with open('lute/static/js/youtube-player.js', 'w') as f:
    f.writelines(new_lines)

print("Fixed youtube-player.js")
