# `lute3-cantonese`

A Cantonese parser for Lute (`lute3`) using the
[pycantonese](https://pycantonese.org) library.

## Installation

See the [Lute manual](https://luteorg.github.io/lute-manual/install/plugins.html).

## Usage

When this parser is installed, you can add "Cantonese Chinese" as a
language to Lute, which comes with a simple story.

## Segmentation

pycantonese segments text using longest-string matching, trained on
the HKCanCor corpus and the rime-cantonese word list.  Both
traditional and simplified characters are handled, though the
training data is traditional-oriented, so traditional texts are
segmented more accurately.

## Readings

Terms are romanized using Jyutping (e.g. 你好 → nei5 hou2), shown
in the reading box if `show_romanization` is enabled.
