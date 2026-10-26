Simple themes for users.

Each file here is loaded into the settings "theme" dropdown.  The filename is used as the drop down display value, with underscore and .css removed.

## Theming architecture (token layer, work in progress)

`lute/static/css/styles.css` defines semantic custom properties on
`body` (the light, no-theme defaults): `--background-color`,
`--font-color`, `--panel-bg`, `--link-color`, `--home-link-color`,
`--menu-link-color`, `--status-N-color`, `--status-N-text`,
`--btn-accent`, ... Components read those tokens, so a theme can
re-skin by **setting token values** instead of re-painting whole
selectors.

- `Default.css` / `Default-Light.css` are the reference token themes:
  their `body` block is mostly token values.
- `styles.css` uses a *guard-var* pattern where themes used to fight it
  with `!important` (e.g. `a.home-link`,
  `.menu-item > span`): the declaration keeps `!important` — legacy
  themes like Boox_Leaf5C still set `a { color: ... !important }`
  globally — but the value comes from `var(--token, fallback)`, so
  token-based themes override it through the cascade of custom
  properties, which ignores specificity entirely.

To migrate a theme off `!important`:

1. Move its palette into the `body` token block (match the names in
   styles.css; add a token + fallback in styles.css if one is missing).
2. Delete the theme's per-selector color rules that the token now
   covers.
3. Only keep `!important` in a theme where it must beat a vendor
   stylesheet (DataTables/Tagify) or a legacy theme-level global rule;
   leave a comment saying what the `!important` is fighting.

## The reader-mode EPUB paper

The "original" look paints a sheet for the book's own palette, and the
sheet follows the theme:

- A **light** theme sets nothing: the sheet is white and the chapter
  renders as authored (`--paper-bg` falls back to `#fff`,
  `--paper-filter` to `none`).
- A **dark** theme sets `--paper-bg: #16161d` and
  `--paper-filter: invert(0.88) hue-rotate(180deg)`, so the sheet is
  dark and the chapter is inverted onto it exactly like the night look
  (images and videos are inverted back).  The ink inside the sheet stays
  dark in the CSS — the filter is what makes it light — because a theme
  that forced its own light ink onto the reading column would invert to
  near-black on near-black.

The night look is always dark, whatever the theme is.


Note: Default.css still carries many non-color rules (geometry,
backdrop filters, DataTables control layout).  Those stay in the theme
for now — moving them into styles.css would change the look of the
other (color-only) themes, which is a product decision to make
separately.
