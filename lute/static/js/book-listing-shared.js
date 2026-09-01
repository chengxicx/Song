/*
 * Shared renderers and row interactions for the book listing tables
 * (home book table and the /book/series/<tag> overview table).
 *
 * Both pages define a global `book_listing_table` for their DataTable,
 * which the status-modal handler below relies on.
 */

/* Tag / new-word / type filter state; the home page's setTagFilter /
   setNewWordFilter / setTypeFilter keep these updated.  Defined here so
   the shared renderers work on pages without the filter header. */
var currentTagFilter = "";
var currentNewWordFilter = "";
var currentTypeFilter = "";

/* No-op defaults; the home page overrides all of these. */
function setTagFilter(tag) {}
function setNewWordFilter(filter) {}
function setTypeFilter(btype) {}

let render_tag_list = function(data, type, row, meta) {
  if (!data) return '';
  const tags = data.split(', ');
  const filtered = currentTagFilter ? currentTagFilter.toLowerCase() : '';
  const visibleTags = tags.filter(function(t) {
    return !filtered || t.toLowerCase() !== filtered;
  });
  if (visibleTags.length === 0) return '';
  return '<span class="book-tag-list">' + visibleTags.map(function(t, i) {
    const sep = i > 0 ? '<span class="tag-sep" aria-hidden="true">·</span>' : '';
    return sep + '<span class="book-tag" data-tag="' + t + '" ' +
      'title="Click to filter by this tag">' +
      t + '</span>';
  }).join('') + '</span>';
};

/*
 * Progress column: how much of the book has been read, as a bar plus a
 * percentage.  The number comes from the server (ProgressPercent) so the
 * home table can sort on it in SQL; series aggregate rows report the
 * share of their episodes that have been read.
 *
 * Like render_last_opened_date, only the 'display' request gets HTML —
 * sort/type/filter get the bare number, otherwise DataTables would sort
 * the column as markup.
 */
let render_book_progress = function (data, type, row, meta) {
  let pct = parseInt(row['ProgressPercent'], 10);
  if (isNaN(pct)) pct = 0;
  pct = Math.max(0, Math.min(100, pct));
  if (type !== 'display') {
    return pct;
  }

  let tip;
  if (row['SeriesTag']) {
    const n = parseInt(row['SeriesBookCount']) || 0;
    const r = parseInt(row['SeriesReadCount']) || 0;
    tip = `${r} of ${n} books read`;
  } else {
    const p = parseInt(row['PageNum']) || 1;
    const n = parseInt(row['PageCount']) || 0;
    tip = n > 0 ? `page ${p} of ${n}` : 'no pages';
  }

  const fillClass = pct >= 100 ? ' book-progress-fill-complete' : '';
  return `<div class="book-progress" title="${tip}">` +
    `<div class="book-progress-track">` +
      `<div class="book-progress-fill${fillClass}" style="width:${pct}%"></div>` +
    `</div>` +
    `<span class="book-progress-pct">${pct}%</span>` +
  `</div>`;
};

/*
 * Type column: one small colored icon per book type, on a tinted
 * rounded-square chip.  Brand marks (YouTube, Bilibili) are inline SVG
 * fills; everything else is a stroke-drawn Lucide-style glyph.
 * Series aggregate rows report BookType 'series' and get the stack icon.
 */
const BOOK_TYPE_META = {
  text: {
    label: 'Text',
    short: 'Text',
    color: '#3b82f6',
    paths: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>'
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
  series: {
    label: 'Book Sets',
    short: 'Sets',
    color: '#4c6ef5',
    paths: '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>'
  }
};

/* Type column display mode: 'icon' (tinted chips) or 'text' (colored
   pill labels).  Only the home page loads the saved preference and
   shows the toggle button in the Type header; other pages sharing this
   renderer (the series overview) always show icons. */
var book_type_display_mode = 'icon';

let render_book_type = function(data, type, row, meta) {
  const btype = row['SeriesTag'] ? 'series' : (row['BookType'] || 'text');
  if (type !== 'display') {
    return btype;
  }
  const m = BOOK_TYPE_META[btype] || BOOK_TYPE_META.text;
  const r = parseInt(m.color.slice(1, 3), 16);
  const g = parseInt(m.color.slice(3, 5), 16);
  const b = parseInt(m.color.slice(5, 7), 16);
  if (book_type_display_mode === 'text') {
    return `<span class="type-chip-text" style="color:${m.color};background:rgba(${r},${g},${b},0.12)" ` +
      `data-type="${btype}" title="Click to filter by this type (${m.label})">${m.short}</span>`;
  }
  const inner = m.filled ||
    `<g fill="none" stroke="${m.color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${m.paths}</g>`;
  return `<span class="type-chip" style="background:rgba(${r},${g},${b},0.12)" ` +
    `data-type="${btype}" title="Click to filter by this type (${m.label})">` +
    `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">${inner}</svg></span>`;
};

/* The Type header needs ~92px on one line (label + toggle button +
   sort arrows), the text pills ~96px ("YouTube" pill + padding).
   While a type filter pill is shown the toggle button is hidden but
   the pill + clear X need more room.  Enforce these as minimums only,
   so columns the user dragged wider are left alone. */
let apply_book_type_width = function() {
  if (typeof book_listing_table === 'undefined' || !book_listing_table) return;
  let need = (book_type_display_mode === 'text') ? 96 : 92;
  if (currentTypeFilter) need = 128;
  const col = book_listing_table.column('BookType:name');
  if (!col || !col.visible()) return;
  const idx = col.index();
  const $wrapper = $('#booktable').closest('.dt-container');
  if (!$wrapper.length) return;
  $wrapper.find('table').each(function() {
    const $t = $(this);
    const $cg = $t.find('colgroup col');
    if ($cg.length > idx) {
      const el = $cg.eq(idx)[0];
      // Compare the inline style ("4%", "120px", or unset) against the
      // minimum; computed styles on <col> are unreliable.
      const cur = parseFloat(el.style.width);
      if (!cur || cur < need) {
        el.style.width = need + 'px';
      }
    }
    const $th = $t.find('thead th').eq(idx);
    if ($th.length) {
      const thEl = $th[0];
      const curTh = parseFloat(thEl.style.width);
      if (!curTh || curTh < need) {
        thEl.style.width = need + 'px';
      }
    }
  });
};

/* Same async-wrapper dance as enable_column_resize: with an async
   DataTables state load the scroll structure may not exist yet.
   Re-applied on every draw as well: an ajax draw (e.g. after applying
   the type filter) resets the colgroup widths from the column's
   configured percentage width. */
let apply_book_type_width_when_ready = function() {
  let tries = 0;
  const wait = function() {
    const $wrapper = $('#booktable').closest('.dt-container');
    if ($wrapper.length === 0 || $wrapper.find('thead th').length === 0) {
      if (++tries < 50) setTimeout(wait, 100);
      return;
    }
    apply_book_type_width();
    $('#booktable').on('draw.dt', function() {
      apply_book_type_width();
    });
  };
  wait();
};

/* Flip the Type column between icon chips and text pills (the small
   button in the home table's Type header).  Rewrites only the visible
   cells in place: no ajax refetch, no skeleton flash in the stats
   columns. */
function toggle_book_type_display(e) {
  if (e) {
    e.preventDefault();
    e.stopPropagation();
  }
  book_type_display_mode = (book_type_display_mode === 'text') ? 'icon' : 'text';
  try {
    localStorage.setItem('booklisting_type_display', book_type_display_mode);
  } catch (err) { /* private mode etc. — session-only toggle still works */ }
  const btn = document.getElementById('typeDisplayToggle');
  if (btn) {
    btn.title = (book_type_display_mode === 'text') ? 'Show icons' : 'Show text labels';
  }
  if (typeof book_listing_table === 'undefined' || !book_listing_table) return;
  const col = book_listing_table.column('BookType:name');
  if (!col || !col.visible()) return;
  col.nodes().to$().each(function() {
    const $td = $(this);
    const data = book_listing_table.row($td.closest('tr')).data();
    if (data) {
      $td.html(render_book_type(data['BookType'], 'display', data));
    }
  });
  apply_book_type_width();
}

let render_book_title = function ( data, type, row, meta ) {
  // Series aggregate row: link to the series overview page.
  if (row['SeriesTag']) {
    const tag = encodeURIComponent(row['SeriesTag']);
    const n = parseInt(row['SeriesBookCount']);
    const r = parseInt(row['SeriesReadCount']);
    return `<div class="series-titlebox">` +
      `<a class="book-title series-title" href="/book/series/${tag}">${row['SeriesTag']}</a>` +
      `<div class="series-sub">${n} eps &middot; ${r}/${n} read</div>` +
      `</div>`;
  }

  const bkid = parseInt(row['BkID']);
  const pgnum = parseInt(row['PageNum']);
  const pgcount = parseInt(row['PageCount']);
  let pgfraction = '';

  return `<a class="book-title" href="/read/${bkid}">${row['BkTitle']}${pgfraction}</a>`;
};

/* Replaced by the status graph after the ajax call kicked off by createdRow. */
let render_book_stats_graph_placeholder = function(data, type, row, meta) {
  if (type === 'display' || type === 'filter') {
    // Series rows show a read-progress bar instead of a status
    // distribution (aggregated stats aren't loaded via ajax).
    if (row['SeriesTag']) {
      const n = parseInt(row['SeriesBookCount']) || 0;
      const r = parseInt(row['SeriesReadCount']) || 0;
      const pct = n > 0 ? Math.round(r * 100 / n) : 0;
      return `<div class="series-progress" title="${r} of ${n} episodes read">` +
        `<div class="series-progress-fill" style="width:${pct}%"></div></div>`;
    }
    const sd = row['StatusDistribution'];
    if (sd) {
      try {
        const result = JSON.parse(sd);
        return render_stats_graph(result);
      } catch (e) {
        // fall through to skeleton
      }
    }
  }
  return `<span class="book-stats-ajax-cell"><span class="skeleton-loading"></span></span>`;
};

let render_new_word_placeholder = function(data, type, row, meta) {
  if (type === 'display' || type === 'filter') {
    // Difficulty band + colour come from the server (data columns
    // DifficultyLabel / DifficultyColor / DifficultyDescription), so the
    // thresholds live in one place (lute/book/stats.py).
    const level = row['DifficultyLabel'];
    if (level) {
      return render_new_word(
        data,
        level,
        row['DifficultyColor'],
        row['DifficultyDescription']
      );
    }
  }
  return `<span class="new-word-ajax-cell"><span class="skeleton-loading"></span></span>`;
};

// The difficulty band, colour and tooltip are supplied by the server
// (computed from the shared thresholds in lute/book/stats.py), so the
// frontend no longer hardcodes any threshold logic.
let render_new_word = function(percent, level, colorClass, tip) {
  const pct = (percent === null || percent === undefined || percent === '') ? '' : percent + '%';
  return `<div class="new-word-badge ${colorClass}" data-level="${level}" onclick="setNewWordFilter('${level}')" title="${tip}">${level} ${pct}</div>`;
};

/* Generate stats graph <div> from statuscounts JSON. */
let render_stats_graph = function(statuscounts) {
  const countsCopy = Object.assign({}, statuscounts);
  countsCopy["99"] = (countsCopy["98"] || 0) + (countsCopy["99"] || 0);
  delete countsCopy['98'];
  const totalcount = Object.values(countsCopy).reduce((acc, val) => acc + val, 0);
  if (totalcount == 0) {
    return '<div class="status-bar-container" style="cursor:default;"><div class="status-bar-empty" style="flex:1;text-align:center;font-size:11px;color:#adb5bd;">—</div></div>';
  }
  const statuspct = {};
  Object.entries(countsCopy).forEach(([key, value]) => {
    let pct = (value * 100.0) / totalcount;
    statuspct[key] = pct.toFixed(0);
  });

  let make_bar = function(stid, title) {
    const p = statuspct[stid];
    const msg = `${p}% (${countsCopy[stid]} words)`;
    const smallestP = window.matchMedia("(max-width: 980px)").matches ? 2 : 1;
    let display = "inline-flex"
    if (p < smallestP)
      display = "none";
    return `<div
      class="status-bar${stid} status-bar"
      title="${title}: ${msg}"
      style="flex: ${p}; display: ${display}"
      ></div>`;
  };

  const bar_titles = {
    "0": "Unknown",
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
    "99": "Well Known or Ignored"
  };
  const countsJson = JSON.stringify(statuscounts).replace(/"/g, '&quot;');
  ret = `<div class="status-bar-container" data-status-counts="${countsJson}" style="cursor:pointer;">`;
  Object.entries(bar_titles).forEach(([key, title]) => {
    ret += make_bar(key, title);
  });
  ret += `</div>`;
  return ret;
};

let render_last_opened_date = function ( data, type, row, meta ) {
  const dt = row["LastOpenedDate"];
  if (dt == null) {
    return '';
  }
  // The sort/type/filter keys must be the raw timestamp, not the
  // display HTML.  Returning the rendered span for every orthogonal
  // request makes DataTables detect the column as HTML and sort by
  // the "x days ago" text, which is not chronological.  The raw value
  // is a fixed-width "YYYY-MM-DD HH:mm:ss" string, so its lexical
  // order equals chronological order.
  if (type === 'sort' || type === 'type' || type === 'filter') {
    return dt;
  }
  const djs = dayjs(dt + 'Z');
  const txt = djs.fromNow();
  const localStr = djs.format('YYYY-MM-DD HH:mm:ss');
  return `<span title="${localStr}">${txt}</span>`;
};

let render_book_actions = function ( data, type, row, meta ) {
  // Series rows: the title itself links to the series overview page,
  // so the menu only offers the listing settings and bulk delete.
  if (row['SeriesTag']) {
    const tag = encodeURIComponent(row['SeriesTag']);
    const n = parseInt(row['SeriesBookCount']) || 0;
    return `<div class="book-action-dropdown"><span>&hellip;</span>
      <div class="book-action-dropdown-content">
        <a href="/book/settings">Configure</a>
        <a href="#" data-seriestag="${tag}" data-count="${n}" onclick="confirm_delete_series(this)">Delete</a>
      </div>
    </div>`;
  }

  // TODO zzfuture fix: security - add CSRF token
  const bkid = row['BkID'];
  const bktitle = encodeURIComponent(row['BkTitle']);
  const is_active = (row['BkArchived'] == 0);

  const links = [];
  const make_link = function(label, func) {
    const s = `<a href="#" data-bkid="${bkid}" data-bktitle="${bktitle}" onclick="${func}(this)">${label}</a>`;
    links.push(s);
  };

  make_link('Edit', "edit_book");
  if (is_active) {
    make_link('Archive', "confirm_archive");
  }
  else {
    make_link('Unarchive', "confirm_unarchive");
  }
  make_link('Delete', "confirm_delete");

  return `<div class="book-action-dropdown"><span>&hellip;</span>
    <div class="book-action-dropdown-content">${links.join('')}</div>
  </div>`;
};

function do_action_post(action, bookid) {
  let f = $('#actionposter');
  f.attr('action', `/book/${action}/${bookid}`);
  f.submit();
}

function confirm_archive(el) {
  const booktitle = decodeURIComponent($(el).data('bktitle'));
  const bookid = $(el).data('bkid');
  if (!confirm(`Archiving "${booktitle}".  Click OK to proceed, or Cancel.`)) {
    return;
  }
  do_action_post('archive', bookid);
}

function confirm_unarchive(el) {
  const bookid = $(el).data('bkid');
  do_action_post('unarchive', bookid);
}

function edit_book(el) {
  const bookid = $(el).data('bkid');
  document.location = `/book/edit/${bookid}`;
}

function confirm_delete(el) {
  const booktitle = decodeURIComponent($(el).data('bktitle'));
  const bookid = $(el).data('bkid');
  if (!confirm(`Deleting "${booktitle}".  Click OK to proceed, or Cancel.`)) {
    return;
  }
  do_action_post('delete', bookid);
}

function confirm_delete_series(el) {
  const seriestag = decodeURIComponent($(el).data('seriestag'));
  const count = $(el).data('count');
  if (!confirm(`Deleting all ${count} books in series "${seriestag}".  Click OK to proceed, or Cancel.`)) {
    return;
  }
  let f = $('#actionposter');
  f.attr('action', `/book/delete_series/${encodeURIComponent(seriestag)}`);
  f.submit();
}

function showStatusModal(content) {
  document.getElementById('statusModalBody').innerHTML = content;
  const overlay = document.getElementById('statusModalOverlay');
  overlay.style.display = 'flex';
}

// Status bar click -> show detail modal.  Present on every page that
// renders a book table (the modal markup must exist in the template).
$(document).on('click', '.status-bar-container', function(e) {
  e.stopPropagation();
  const $container = $(this);
  if (typeof book_listing_table === 'undefined' || !book_listing_table) return;
  const $cell = $container.closest('td');
  const row = book_listing_table.row($cell.closest('tr'));
  const data = row.data();
  if (!data) return;
  const title = data['BkTitle'];
  const totalCount = data['WordCount'] || 0;

  // Parse status distribution from the dataset if available
  const statusCounts = $container.data('status-counts');
  if (!statusCounts) return;

  let html = '<div style="padding:10px 0;">';
  html += '<h4 style="margin:0 0 12px 0;font-size:15px;">' + title + '</h4>';
  html += '<table style="width:100%;font-size:13px;border-collapse:collapse;">';
  html += '<tr><th style="text-align:left;padding:4px 8px;border-bottom:1px solid #e9ecef;">Status</th>';
  html += '<th style="text-align:right;padding:4px 8px;border-bottom:1px solid #e9ecef;">Words</th>';
  html += '<th style="text-align:right;padding:4px 8px;border-bottom:1px solid #e9ecef;">%</th></tr>';

  const statusLabels = {
    '0': 'Unknown', '1': 'Level 1', '2': 'Level 2',
    '3': 'Level 3', '4': 'Level 4', '5': 'Level 5',
    '98': 'Ignored', '99': 'Well-known'
  };
  const statusColors = {
    '0': '#ced4da', '1': '#ff6b6b', '2': '#ffa94d',
    '3': '#ffd43b', '4': '#69db7c', '5': '#339af0',
    '98': '#adb5bd', '99': '#845ef7'
  };
  let total = 0;
  for (const k in statusCounts) { total += statusCounts[k]; }
  if (total === 0) total = 1;

  for (const key of ['0','1','2','3','4','5','98','99']) {
    const count = statusCounts[key] || 0;
    const pct = ((count / total) * 100).toFixed(1);
    html += '<tr>';
    html += '<td style="padding:4px 8px;">';
    html += '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' + (statusColors[key] || '#ccc') + ';margin-right:6px;"></span>';
    html += (statusLabels[key] || key) + '</td>';
    html += '<td style="text-align:right;padding:4px 8px;">' + count + '</td>';
    html += '<td style="text-align:right;padding:4px 8px;">' + pct + '%</td>';
    html += '</tr>';
  }
  html += '<tr style="font-weight:bold;border-top:2px solid #e9ecef;">';
  html += '<td style="padding:6px 8px;">Total</td>';
  html += '<td style="text-align:right;padding:6px 8px;">' + total + '</td>';
  html += '<td style="text-align:right;padding:6px 8px;">100%</td>';
  html += '</tr>';
  html += '</table></div>';

  showStatusModal(html);
});

// Close modal on overlay click
$(document).on('click', '#statusModalOverlay', function(e) {
  if (e.target.id === 'statusModalOverlay') {
    $(this).hide();
  }
});

// Action 下拉菜单 fixed 定位，避免被 dt-scroll-body 遮挡
// 菜单被改成 fixed 后脱离了父容器的 hover 区域，鼠标从 "…" 触发点
// 移到菜单时会经过一段空隙触发 mouseleave 导致菜单提前消失。
// 因此隐藏时加一个延时，并在鼠标进入触发器或菜单时取消，让鼠标
// 能顺利跨过空隙到达菜单。
var actionDropdownHideTimer = null;
var hideActionDropdown = function($dropdown) {
  $dropdown.css({
    position: '',
    top: '',
    left: '',
    display: ''
  });
};
$(document).on('mouseenter', '.book-action-dropdown', function() {
  clearTimeout(actionDropdownHideTimer);
  var $dropdown = $(this).find('.book-action-dropdown-content');
  var $trigger = $(this).find('span');
  if (!$dropdown.length || !$trigger.length) return;
  // Hide any other open action menu before showing this one, so
  // hovering another row's "…" doesn't leave old menus on screen.
  $('.book-action-dropdown-content').not($dropdown).each(function() {
    if ($(this).css('display') === 'block') {
      hideActionDropdown($(this));
    }
  });
  // Viewport coordinates (position:fixed is viewport-relative;
  // offset() is document-relative and drifts with page scroll).
  var rect = $trigger[0].getBoundingClientRect();
  var ddHeight = $dropdown.outerHeight();
  var ddWidth = $dropdown.outerWidth();
  var winH = $(window).height();
  var winW = $(window).width();
  // 默认显示在 trigger 下方
  var top = rect.bottom + 4;
  var left = rect.left;
  // 底部空间不够 -> 向上展开
  if (top + ddHeight > winH - 10) {
    top = rect.top - ddHeight - 4;
  }
  // 右侧空间不够 -> 向左对齐
  if (left + ddWidth > winW - 10) {
    left = winW - ddWidth - 10;
  }
  $dropdown.css({
    position: 'fixed',
    top: top + 'px',
    left: Math.max(4, left) + 'px',
    display: 'block'
  });
});
$(document).on('mouseleave', '.book-action-dropdown', function() {
  var $dropdown = $(this).find('.book-action-dropdown-content');
  clearTimeout(actionDropdownHideTimer);
  actionDropdownHideTimer = setTimeout(function() {
    hideActionDropdown($dropdown);
  }, 200);
});
// 菜单是 position:fixed 的子元素，悬停它本身不会触发父容器事件，
// 因此单独监听菜单的进入/离开来取消或触发隐藏。
$(document).on('mouseenter', '.book-action-dropdown-content', function() {
  clearTimeout(actionDropdownHideTimer);
});
$(document).on('mouseleave', '.book-action-dropdown-content', function() {
  var $dropdown = $(this);
  clearTimeout(actionDropdownHideTimer);
  actionDropdownHideTimer = setTimeout(function() {
    hideActionDropdown($dropdown);
  }, 150);
});

// Click tag pill to filter by that tag (home page header shows the pill)
$(document).on('click', '.book-tag', function(e) {
  e.stopPropagation();
  const tag = $(this).data('tag');
  setTagFilter(tag);
});

// Click a Type chip/pill to filter by that book type; clicking the
// active one again clears the filter (home page header shows the pill).
$(document).on('click', '.type-chip, .type-chip-text', function(e) {
  e.stopPropagation();
  const btype = $(this).data('type');
  setTypeFilter(currentTypeFilter === btype ? '' : btype);
});


/* Draggable column-width handles for the book table(s).  Shared by the
   home listing and the series overview. */
let enable_column_resize = function() {
  // With an async DataTables state load (first visit, empty
  // localStorage) the table is detached while the scroll structure is
  // rebuilt, so the wrapper may not be reachable yet: retry briefly.
  var tries = 0;
  var apply_when_ready = function() {
    var $wrapper = $('#booktable').closest('.dt-container');
    if ($wrapper.length === 0 || $wrapper.find('thead th').length === 0) {
      if (++tries < 50) setTimeout(apply_when_ready, 100);
      return;
    }
    apply_resizable();
    $('#booktable').on('draw.dt', function() {
      apply_resizable();
    });
  };

  function apply_resizable() {
      var $wrapper = $('#booktable').closest('.dt-container');
      if (!$wrapper.length) return;
      $wrapper.find('table').each(function() {
          var $table = $(this);
          var $ths = $table.find('thead th');
          $ths.each(function() {
              var $th = $(this);
              if ($th.find('.resize-handle').length > 0) return;
              var $handle = $('<div class="resize-handle"></div>');
              $th.append($handle);
          });
      });

      $wrapper.find('.resize-handle').each(function() {
          var $handle = $(this);
          if ($handle.data('resize-bound')) return;
          $handle.data('resize-bound', true);

          $handle.on('mousedown', function(e) {
              e.preventDefault();
              var $th = $handle.closest('th');
              var startX = e.pageX;
              var startWidth = $th.outerWidth();
              var colIndex = $th.index();

              $(document).on('mousemove.colresize', function(ev) {
                  var newWidth = Math.max(40, startWidth + (ev.pageX - startX));

                  $wrapper.find('table').each(function() {
                      var $t = $(this);
                      var $cg = $t.find('colgroup col');
                      if ($cg.length > colIndex) {
                          $cg.eq(colIndex).css('width', newWidth + 'px');
                      }
                      var $ths2 = $t.find('thead th');
                      if ($ths2.length > colIndex) {
                          $ths2.eq(colIndex).css('width', newWidth + 'px');
                      }
                  });
              });

              $(document).on('mouseup.colresize', function() {
                  $(document).off('mousemove.colresize mouseup.colresize');
              });
          });
      });
  }

  apply_when_ready();
};


/* Fetch stats for all visible rows in one batched request, instead of
   one /book/table_stats/<id> request per row. Shared by the home book
   table (tablelisting.html) and the series overview (series.html); both
   call it from their drawCallback. This reduces DB connection-pool
   pressure and load.

   Series aggregate rows (BkID null) carry SeriesStatsPending: the ids of
   member books whose stats are missing or stale.  Those books never
   appear as flat rows on the home table, so without collecting them here
   their stats would never be calculated and the series New word average
   would stay empty.

   Returns a jQuery promise/jqXHR that resolves when the stats request
   (if any) settles, so callers can chain .always() (e.g. to stop the
   refresh button spinner). Resolves immediately when there is nothing
   to fetch. */
var _last_series_pending_fetched = null;
let ajax_in_book_stats = function(settings) {
  var table = book_listing_table;
  if (!table) {
    return $.Deferred().resolve();
  }

  var newWordColIdx = table.column('NewWordPercent:name').index();
  var statsColIdx = table.column('UnknownPercent:name').index();

  var newWordColVisible = table.column(newWordColIdx).visible();
  var statsColVisible = table.column(statsColIdx).visible();

  // If both the Status and New Word columns are hidden, skip the AJAX
  // call entirely — nothing would be rendered. The DataTables colvis
  // widget lets users toggle these columns off, so this guard avoids
  // a wasted batched /book/table_stats request per page redraw.
  if (!newWordColVisible && !statsColVisible) {
    return $.Deferred().resolve();
  }

  // Collect the books currently on this page and their cell nodes.
  var book_ids = [];
  var cellNodes = {};
  var series_pending_ids = [];
  table.rows({ page: 'current' }).every(function(rowIdx, tableLoop, rowLoop) {
    var data = this.data();
    var bid = data['BkID'];
    if (bid === undefined || bid === null) {
      // Series row: queue its members that need stats calculated.
      var pending = data['SeriesStatsPending'];
      if (pending) {
        String(pending).split(',').forEach(function(id) {
          var n = parseInt(id);
          if (!isNaN(n) && book_ids.indexOf(n) === -1) {
            book_ids.push(n);
            series_pending_ids.push(n);
          }
        });
      }
      return;
    }
    if (book_ids.indexOf(bid) === -1) {
      book_ids.push(bid);
    }
    cellNodes[bid] = {
      newWord: newWordColVisible ? table.cell(rowIdx, newWordColIdx).node() : null,
      stats: statsColVisible ? table.cell(rowIdx, statsColIdx).node() : null,
    };
    // Only show a skeleton when the cell has no rendered content yet.
    // Otherwise existing values stay visible while fresh stats load, so
    // a refresh that marks many books stale (slow recalc) doesn't blank
    // out the Status / New word columns.
    var needsSkeleton = function(node) {
      if (!node) return false;
      var html = node.innerHTML.trim();
      return html === '' || html.indexOf('skeleton-loading') !== -1;
    };
    if (cellNodes[bid].newWord && needsSkeleton(cellNodes[bid].newWord)) {
      $(cellNodes[bid].newWord).html(`<span class="skeleton-loading"></span>`);
    }
    if (cellNodes[bid].stats && needsSkeleton(cellNodes[bid].stats)) {
      $(cellNodes[bid].stats).html(`<span class="skeleton-loading"></span>`);
    }
  });

  if (book_ids.length === 0) {
    return $.Deferred().resolve();
  }

  return $.ajax({
    url: '/book/table_stats',
    method: 'POST',
    contentType: 'application/json',
    data: JSON.stringify({ book_ids: book_ids }),
    success: function(response) {
      Object.keys(response).forEach(function(bid) {
        var nodes = cellNodes[bid];
        if (!nodes) {
          return;
        }
        var stats = response[bid];
        try {
          if (nodes.stats) {
            $(nodes.stats).removeClass("refreshed");
            if (stats.status_distribution) {
              const result = JSON.parse(stats.status_distribution);
              const graph = render_stats_graph(result);
              $(nodes.stats).html(graph);
            } else {
              $(nodes.stats).text('No data');
            }
          }
          if (nodes.newWord) {
            $(nodes.newWord).removeClass("refreshed");
            const newWordHtml = render_new_word(
              stats.new_word_percent,
              stats.difficulty_label,
              stats.difficulty_color,
              stats.difficulty_description
            );
            $(nodes.newWord).html(newWordHtml);
          }
        } catch (e) {
          console.error('table_stats error for book ' + bid + ':', e);
          if (nodes.stats) {
            $(nodes.stats).text('Error loading data');
            $(nodes.stats).removeClass("refreshed");
          }
          if (nodes.newWord) {
            $(nodes.newWord).text('Error');
            $(nodes.newWord).removeClass("refreshed");
          }
        }
      });

      // Series members were just calculated: reload the (home) table so
      // the series aggregates recompute.  Only fires while the pending
      // set keeps changing, so a book whose stats fail to calculate
      // can't cause a reload loop; series pages have no ajax url and no
      // series rows, so they never reload here.
      if (series_pending_ids.length > 0 &&
          table.ajax.url && table.ajax.url()) {
        var key = series_pending_ids.join(',');
        if (key !== _last_series_pending_fetched) {
          _last_series_pending_fetched = key;
          table.ajax.reload(null, false);
        }
      } else {
        _last_series_pending_fetched = null;
      }
    },
    error: function() {
      book_ids.forEach(function(bid) {
        var nodes = cellNodes[bid];
        if (!nodes) {
          return;
        }
        if (nodes.stats) {
          $(nodes.stats).html('<span class="skeleton-loading"></span>');
          $(nodes.stats).removeClass("refreshed");
        }
        if (nodes.newWord) {
          $(nodes.newWord).html('<span class="skeleton-loading"></span>');
          $(nodes.newWord).removeClass("refreshed");
        }
      });
    }
  });
};
