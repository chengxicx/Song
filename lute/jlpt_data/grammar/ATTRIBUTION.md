# Grammar data attribution

## `n5.json` – `n1.json`

Verbatim copies of the curated JLPT grammar library from
**japanese-language-data** by Justin Kindrix and contributors:

- Source: https://github.com/jkindrix/japanese-language-data (`grammar-curated/`)
- License: **CC BY-SA 4.0** — https://creativecommons.org/licenses/by-sa/4.0/
- Counts as vendored: N5 77, N4 89, N3 130, N2 149, N1 150 (595 entries total)
- Fields used at runtime: `id`, `pattern`, `formation`, `meaning_en`,
  `examples[].japanese`.  The remaining fields (`meaning_detailed`,
  `examples[].english`, `formality`, `related`, …) are kept so the files stay
  verbatim copies.

These entries are matched by specs *derived at load time* from the
descriptive `pattern` text — see `_load_level()` in
`lute/read/render/grammar_analysis_ja.py` for the derivation and validation
rules.  Nothing in this directory is hand-tuned per entry.

## `zh.json`

Simplified-Chinese glosses for the 595 entries above, keyed by the upstream
`id`.  Written for this project (Song), so the reading-page grammar panel can
describe points in Chinese; it is a derivative of the CC BY-SA 4.0 data above
and is distributed under the same license.

## N5 hand-written rules

Not in this directory: the N5 basics that need conditional logic (particles,
て forms, politeness) live in `_N5_RULES` inside
`lute/read/render/grammar_analysis_ja.py`, with examples from
**OpenJLPT** (CC BY-SA 4.0, https://github.com/evanclan/OpenJLPT).
