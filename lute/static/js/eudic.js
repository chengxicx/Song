/**
 * Eudic sync page: start a run, poll it, render the result.
 *
 * Kept in its own closure rather than the shared global scope used by
 * the reader scripts: nothing here is needed anywhere else.
 */
const EudicPage = (function () {
  'use strict';

  const POLL_MS = 1500;

  let page = null;
  let languageId = null;

  function t(key) {
    return (window.I18N && window.I18N.t) ? window.I18N.t(key) : key;
  }

  function el(id) {
    return document.getElementById(id);
  }

  function setMessage(text) {
    const node = el('eudic_message');
    if (node) node.textContent = text || '';
  }

  function setProgress(percent, message) {
    const wrap = el('eudic_progress_wrap');
    const bar = el('eudic_progress');
    const label = el('eudic_progress_message');
    if (wrap) wrap.hidden = false;
    if (bar) bar.value = percent || 0;
    if (label) label.textContent = message || '';
  }

  function hideProgress() {
    const wrap = el('eudic_progress_wrap');
    if (wrap) wrap.hidden = true;
  }

  function showWarning(text) {
    const node = el('eudic_warning');
    if (!node) return;
    if (!text) {
      node.hidden = true;
      node.textContent = '';
      return;
    }
    node.hidden = false;
    node.textContent = text;
  }

  function refreshCategories() {
    const bucket = el('eudic_bucket');
    const select = el('eudic_category');
    if (!bucket || !select) return;
    select.innerHTML = '';
    setMessage(t('Loading word lists…'));

    fetch('/eudic/categories?bucket=' + encodeURIComponent(bucket.value), {
      credentials: 'same-origin'
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) {
          setMessage(data.error || t('Could not read the word lists.'));
          return;
        }
        if (!data.categories.length) {
          setMessage(t('Eudic returned no word lists for this library.'));
          return;
        }
        data.categories.forEach(function (c) {
          const opt = document.createElement('option');
          opt.value = c.id;
          opt.textContent = c.name || c.id;
          select.appendChild(opt);
        });
        setMessage('');
      })
      .catch(function () {
        setMessage(t('Could not reach Eudic.'));
      });
  }

  function rememberCategoryName() {
    const select = el('eudic_category');
    const hidden = el('eudic_category_name');
    if (!select || !hidden) return;
    const opt = select.options[select.selectedIndex];
    hidden.value = opt ? opt.textContent.trim() : '';
  }

  function countsTable(result) {
    const plan = result.plan || {};
    const isPreview = result.mode === 'preview';
    const rows = [
      [t('New here'), plan.push_new],
      [t('Changed here'), plan.push_update],
      [t('Conflicts (Song wins)'), plan.conflicts],
      [t('New on Eudic'), plan.pull_new],
      [t('Changed on Eudic'), plan.pull_update],
      [t('Already matching'), plan.unchanged],
      [t('Locked'), plan.locked],
      [t('Skipped'), plan.skipped]
    ];

    let html = '<div class="eudic-counts">';
    rows.forEach(function (row) {
      html += '<div class="eudic-count"><b>' + (row[1] || 0) + '</b>' +
        '<span>' + row[0] + '</span></div>';
    });
    html += '</div>';

    if (!isPreview) {
      html += '<p>' +
        t('Sent') + ': ' + (result.pushed_new + result.pushed_updated + result.pushed_conflicts) +
        ' &nbsp; ' + t('Received') + ': ' + (result.pulled_new + result.pulled_updated) +
        ' &nbsp; ' + t('Took') + ': ' + result.elapsed_seconds + 's</p>';
    }
    return html;
  }

  function failuresTable(result) {
    if (!result.failures || !result.failures.length) return '';
    let html = '<h4>' + t('Failures') + ' (' + result.failure_count + ')</h4>';
    html += '<table class="eudic-table"><tr><th>' + t('Word') + '</th><th>' +
      t('Error') + '</th></tr>';
    result.failures.forEach(function (f) {
      html += '<tr><td>' + escapeHtml(f.word) + '</td><td>' +
        escapeHtml(f.error) + '</td></tr>';
    });
    html += '</table>';
    return html;
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function renderResult(result) {
    const node = el('eudic_result');
    if (!node || !result) return;
    node.innerHTML = countsTable(result) + failuresTable(result);

    if (result.remote_truncated) {
      showWarning(t('Eudic only exposes the first 5100 words of a list; anything past that was not read.'));
    } else {
      showWarning('');
    }
  }

  function poll(taskId) {
    fetch('/eudic/task/' + encodeURIComponent(taskId), {
      credentials: 'same-origin'
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) {
          hideProgress();
          setMessage(t('The sync task is gone (the server may have restarted). Run it again.'));
          return;
        }
        if (data.state === 'error') {
          hideProgress();
          setMessage(t('Sync failed') + ': ' + (data.error || ''));
          return;
        }
        if (data.state === 'finished') {
          hideProgress();
          setMessage(data.message || t('Done.'));
          renderResult(data.result);
          return;
        }
        setProgress(data.percent, data.message);
        setTimeout(function () { poll(taskId); }, POLL_MS);
      })
      .catch(function () {
        hideProgress();
        setMessage(t('Lost contact with the server.'));
      });
  }

  function startSync(mode) {
    if (!languageId) {
      setMessage(t('Pick a language first.'));
      return;
    }
    setMessage('');
    showWarning('');
    const node = el('eudic_result');
    if (node) node.innerHTML = '';
    setProgress(0, t('Starting…'));

    const body = new URLSearchParams();
    body.set('language_id', languageId);

    fetch('/eudic/' + encodeURIComponent(mode), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString()
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) {
          hideProgress();
          setMessage(data.error || t('Could not start the sync.'));
          return;
        }
        if (data.message) setMessage(data.message);
        poll(data.task_id);
      })
      .catch(function () {
        hideProgress();
        setMessage(t('Could not start the sync.'));
      });
  }

  function init() {
    page = el('eudic_page');
    if (!page) return;
    languageId = page.getAttribute('data-language-id') || null;

    const langSelect = el('eudic_language');
    if (langSelect) {
      if (!languageId) languageId = langSelect.value;
      langSelect.addEventListener('change', function () {
        // Changing the language reloads the page: the saved scope and
        // its counters belong to one language.
        window.location = '/eudic/?language_id=' + encodeURIComponent(langSelect.value);
      });
    }

    const refresh = el('eudic_refresh_categories');
    if (refresh) refresh.addEventListener('click', refreshCategories);

    const categorySelect = el('eudic_category');
    if (categorySelect) categorySelect.addEventListener('change', rememberCategoryName);

    document.querySelectorAll('[data-eudic-mode]').forEach(function (button) {
      button.addEventListener('click', function () {
        startSync(button.getAttribute('data-eudic-mode'));
      });
    });
  }

  document.addEventListener('DOMContentLoaded', init);

  return { init: init, refreshCategories: refreshCategories };
})();
