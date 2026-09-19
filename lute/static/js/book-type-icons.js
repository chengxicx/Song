/*
 * Shared book-type metadata: one small colored icon per book type, on a
 * tinted rounded-square chip.  Brand marks (YouTube, Bilibili) are inline
 * SVG fills; everything else is a stroke-drawn Lucide-style glyph.
 *
 * Included before book-listing-shared.js on the listing pages (home book
 * table, series overview, bookmarks) and by the Create-new page, whose
 * import-type dropdown shows the same icon next to the select.
 *
 * 'webpage' and 'epub' exist only for that dropdown: those imports are
 * stored with an empty book_type, so the listings render them as 'text'.
 */
const BOOK_TYPE_META = {
  text: {
    label: 'Text',
    short: 'Text',
    color: '#3b82f6',
    paths: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>'
  },
  webpage: {
    label: 'Web page',
    short: 'Web page',
    color: '#0c8599',
    paths: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>'
  },
  youtube: {
    label: 'YouTube',
    short: 'YouTube',
    color: '#FF0000',
    filled: '<path fill="#FF0000" d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>'
  },
  bilibili: {
    label: 'Bilibili',
    short: 'Bilibili',
    color: '#00A1D6',
    filled: '<path d="M8.2 2.2 11 6M15.8 2.2 13 6" stroke="#00A1D6" stroke-width="2" stroke-linecap="round" fill="none"/><rect x="3" y="6.6" width="18" height="13.8" rx="3.2" fill="#00A1D6"/><circle cx="9" cy="12.4" r="1.7" fill="#fff"/><circle cx="15" cy="12.4" r="1.7" fill="#fff"/>'
  },
  mp3: {
    label: 'Audio (MP3)',
    short: 'MP3',
    color: '#2f9e44',
    paths: '<path d="M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a9 9 0 0 1 18 0v7a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3"/>'
  },
  netease: {
    label: 'NetEase Cloud Music',
    short: 'NetEase',
    color: '#C20C0C',
    paths: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>'
  },
  video: {
    label: 'Video',
    short: 'Video',
    color: '#845ef7',
    paths: '<path d="M10 7.75a.75.75 0 0 1 1.142-.638l3.664 2.249a.75.75 0 0 1 0 1.278l-3.664 2.25a.75.75 0 0 1-1.142-.64z"/><path d="M12 17v4"/><path d="M8 21h8"/><rect x="2" y="3" width="20" height="14" rx="2"/>'
  },
  manga: {
    label: 'Manga',
    short: 'Manga',
    color: '#f76707',
    paths: '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>'
  },
  pdf: {
    label: 'PDF',
    short: 'PDF',
    color: '#e03131',
    paths: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>'
  },
  epub: {
    label: 'EPUB',
    short: 'EPUB',
    color: '#d6336c',
    paths: '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>'
  },
  series: {
    label: 'Book Sets',
    short: 'Sets',
    color: '#4c6ef5',
    paths: '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>'
  }
};

/* rgba tint of a type's color, for chip backgrounds. */
function book_type_tint(btype, alpha) {
  const m = BOOK_TYPE_META[btype] || BOOK_TYPE_META.text;
  const r = parseInt(m.color.slice(1, 3), 16);
  const g = parseInt(m.color.slice(3, 5), 16);
  const b = parseInt(m.color.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

/* The type's icon as a standalone 16px <svg>, matching the listing chips. */
function book_type_icon_svg(btype) {
  const m = BOOK_TYPE_META[btype] || BOOK_TYPE_META.text;
  const inner = m.filled ||
    `<g fill="none" stroke="${m.color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${m.paths}</g>`;
  return `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">${inner}</svg>`;
}
