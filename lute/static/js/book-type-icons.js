/*
 * Book-type icons for the listing pages (home book table, series
 * overview, bookmarks) and the Create-new page's import-type dropdown.
 *
 * The metadata (labels, colors, icon SVGs) comes from the single
 * book-type registry -- lute/book/types.py on the Python side, injected
 * by base.html as window.LUTE_BOOK_TYPES -- so the frontend can never
 * drift from the server's type list.  This file only holds the
 * rendering helpers.
 *
 * 'webpage' and 'epub' exist only for the import dropdown: those
 * imports are stored with an empty book_type, so the listings render
 * them as 'text'.
 */
const BOOK_TYPE_META = window.LUTE_BOOK_TYPES || {};

/* rgba tint of a type's color, for chip backgrounds. */
function book_type_tint(btype, alpha) {
  const m = BOOK_TYPE_META[btype] || BOOK_TYPE_META.text || { color: '#888888' };
  const r = parseInt(m.color.slice(1, 3), 16);
  const g = parseInt(m.color.slice(3, 5), 16);
  const b = parseInt(m.color.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

/* The type's icon as a standalone 16px <svg>, matching the listing chips. */
function book_type_icon_svg(btype) {
  const m = BOOK_TYPE_META[btype] || BOOK_TYPE_META.text || {};
  const inner = m.filled ||
    `<g fill="none" stroke="${m.color || '#888888'}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${m.paths || ''}</g>`;
  return `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">${inner}</svg>`;
}
