/*
 * Shared renderers and row interactions for the book listing tables
 * (home book table and the /book/series/<tag> overview table).
 *
 * Both pages define a global `book_listing_table` for their DataTable,
 * which the status-modal handler below relies on.
 */

/* Tag / new-word filter state; the home page's setTagFilter /
   setNewWordFilter keep these updated.  Defined here so the shared
   renderers work on pages without the filter header. */
var currentTagFilter = "";
var currentNewWordFilter = "";

/* No-op defaults; the home page overrides both. */
function setTagFilter(tag) {}
function setNewWordFilter(filter) {}

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

let render_book_title = function ( data, type, row, meta ) {
  // Series aggregate row: link to the series overview page.
  if (row['SeriesTag']) {
    const tag = encodeURIComponent(row['SeriesTag']);
    const n = parseInt(row['SeriesBookCount']);
    const r = parseInt(row['SeriesReadCount']);
    return `<div class="series-titlebox">` +
      `<div class="series-titlerow">` +
      `<span class="series-caret">&#9656;</span>` +
      `<a class="book-title series-title" href="/book/series/${tag}">${row['SeriesTag']}</a>` +
      `<a class="series-settings-gear" href="/book/settings" title="Configure book series tags">` +
      `<img src="/static/icn/settings-gear-icon.svg" alt="configure series"></a>` +
      `</div>` +
      `<div class="series-sub">${n} eps &middot; ${r}/${n} read</div>` +
      `</div>`;
  }

  const bkid = parseInt(row['BkID']);
  const pgnum = parseInt(row['PageNum']);
  const pgcount = parseInt(row['PageCount']);
  let pgfraction = '';

  const completed = (parseInt(row['IsCompleted']) == 1);
  let book_title_classes = ['book-title'];
  if (completed) {
    book_title_classes.push('completed_book');
  }
  if (pgnum > 1) {
    pgfraction = ` (${pgnum}/${pgcount})`;
  }

  return `<a class="${book_title_classes.join(' ')}" href="/read/${bkid}">${row['BkTitle']}${pgfraction}</a>`;
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
  const djs = dayjs(dt + 'Z');
  const txt = djs.fromNow();
  const localStr = djs.format('YYYY-MM-DD HH:mm:ss');
  return `<span title="${localStr}">${txt}</span>`;
};

let render_book_actions = function ( data, type, row, meta ) {
  // Series rows link to the series overview page.
  if (row['SeriesTag']) {
    const tag = encodeURIComponent(row['SeriesTag']);
    return `<div class="book-action-dropdown"><span>&hellip;</span>
      <div class="book-action-dropdown-content">
        <a href="/book/series/${tag}">Open series page</a>
        <a href="/book/settings">Configure series</a>
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
  var offset = $trigger.offset();
  var ddHeight = $dropdown.outerHeight();
  var ddWidth = $dropdown.outerWidth();
  var winH = $(window).height();
  var winW = $(window).width();
  // 默认显示在 trigger 下方
  var top = offset.top + $trigger.outerHeight() + 4;
  var left = offset.left;
  // 底部空间不够 -> 向上展开
  if (top + ddHeight > winH - 10) {
    top = offset.top - ddHeight - 4;
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
