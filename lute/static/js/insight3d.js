/**
 * Insight 3D page controller.
 *
 * three.js is ~670KB, so it is only fetched once the page has confirmed
 * WebGL works: an e-ink reader or an old WebView gets a text summary and
 * downloads nothing.
 *
 * One renderer and one canvas for all four charts. Switching a tab
 * disposes the previous scene instead of creating a second context,
 * because browsers cap how many WebGL contexts a page may hold and
 * leaking one per tab switch kills the page after a few clicks.
 */

const Insight3D = (function () {
  'use strict';

  const state = {
    chart: 'retention',
    languageId: null,
    ready: false,
    webgl: false,
    three: null,
    scenes: null,
    renderer: null,
    scene: null,
    camera: null,
    current: null,
    labels: [],
    // Orbit position, in spherical coordinates around `target`.
    theta: 0.7,
    phi: 1.05,
    radius: 7,
    target: [0, 0, 0],
    drag: null,
    pointers: new Map(),
    pinchStart: null,
    pending: false
  };

  const els = {};

  function el(id) {
    return document.getElementById(id);
  }

  function t(key) {
    return window.I18N && window.I18N.t ? window.I18N.t(key) : key;
  }

  function setStatus(text) {
    if (els.status) els.status.textContent = text || '';
  }

  // -- capability -----------------------------------------------------------

  function hasWebGL() {
    try {
      const probe = document.createElement('canvas');
      return !!(
        window.WebGLRenderingContext &&
        (probe.getContext('webgl2') || probe.getContext('webgl'))
      );
    } catch (e) {
      return false;
    }
  }

  function prefersReducedMotion() {
    return (
      window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    );
  }

  // -- camera / controls ----------------------------------------------------

  function applyCamera() {
    if (!state.camera || !state.three) return;
    const THREE = state.three;
    const r = state.radius;
    const sinPhi = Math.sin(state.phi);
    state.camera.position.set(
      state.target[0] + r * sinPhi * Math.sin(state.theta),
      state.target[1] + r * Math.cos(state.phi),
      state.target[2] + r * sinPhi * Math.cos(state.theta)
    );
    state.camera.lookAt(
      new THREE.Vector3(state.target[0], state.target[1], state.target[2])
    );
  }

  function requestRender() {
    if (state.pending) return;
    state.pending = true;
    window.requestAnimationFrame(function () {
      state.pending = false;
      render();
    });
  }

  function render() {
    if (!state.renderer || !state.scene || !state.camera) return;
    applyCamera();
    state.renderer.render(state.scene, state.camera);
    positionLabels();
  }

  function positionLabels() {
    if (!state.three || !state.labels.length) return;
    const THREE = state.three;
    const rect = els.canvas.getBoundingClientRect();
    state.labels.forEach(function (item) {
      const v = new THREE.Vector3(item.pos.x, item.pos.y, item.pos.z).project(
        state.camera
      );
      const visible = v.z < 1;
      item.el.style.display = visible ? '' : 'none';
      if (!visible) return;
      const sx = (v.x * 0.5 + 0.5) * rect.width;
      const sy = (-v.y * 0.5 + 0.5) * rect.height;
      item.el.style.transform =
        'translate(-50%, -50%) translate(' + sx + 'px, ' + sy + 'px)';
    });
  }

  function bindControls() {
    const canvas = els.canvas;

    canvas.addEventListener('pointerdown', function (e) {
      state.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (state.pointers.size === 1) {
        state.drag = { x: e.clientX, y: e.clientY };
        canvas.setPointerCapture(e.pointerId);
      } else if (state.pointers.size === 2) {
        state.pinchStart = {
          distance: pinchDistance(),
          radius: state.radius
        };
        state.drag = null;
      }
    });

    canvas.addEventListener('pointermove', function (e) {
      if (!state.pointers.has(e.pointerId)) return;
      state.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

      if (state.pointers.size === 2 && state.pinchStart) {
        const scale = state.pinchStart.distance / Math.max(1, pinchDistance());
        state.radius = clamp(state.pinchStart.radius * scale, 2, 26);
        requestRender();
        return;
      }

      if (!state.drag) return;
      const dx = e.clientX - state.drag.x;
      const dy = e.clientY - state.drag.y;
      state.drag = { x: e.clientX, y: e.clientY };
      state.theta -= dx * 0.008;
      state.phi = clamp(state.phi - dy * 0.006, 0.15, Math.PI - 0.15);
      requestRender();
    });

    function release(e) {
      state.pointers.delete(e.pointerId);
      if (state.pointers.size < 2) state.pinchStart = null;
      if (state.pointers.size === 0) state.drag = null;
    }
    canvas.addEventListener('pointerup', release);
    canvas.addEventListener('pointercancel', release);
    canvas.addEventListener('pointerleave', release);

    canvas.addEventListener(
      'wheel',
      function (e) {
        e.preventDefault();
        state.radius = clamp(state.radius * (1 + e.deltaY * 0.0012), 2, 26);
        requestRender();
      },
      { passive: false }
    );

    canvas.addEventListener('dblclick', function () {
      frameCurrent();
      requestRender();
    });
  }

  function pinchDistance() {
    const points = Array.from(state.pointers.values());
    if (points.length < 2) return 1;
    const dx = points[0].x - points[1].x;
    const dy = points[0].y - points[1].y;
    return Math.sqrt(dx * dx + dy * dy) || 1;
  }

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  function frameCurrent() {
    const view = (state.current && state.current.view) || {
      radius: 7,
      theta: 0.7,
      phi: 1.05,
      target: [0, 0, 0]
    };
    state.radius = view.radius;
    state.theta = view.theta;
    state.phi = view.phi;
    state.target = view.target.slice();
  }

  // -- lifecycle ------------------------------------------------------------

  function clearLabels() {
    state.labels.forEach(function (item) {
      if (item.el && item.el.parentNode) item.el.parentNode.removeChild(item.el);
    });
    state.labels = [];
  }

  function disposeCurrent() {
    if (!state.current) return;
    state.scene.remove(state.current.group);
    state.current.group.traverse(function (obj) {
      if (obj.geometry) obj.geometry.dispose();
      if (obj.material) {
        if (Array.isArray(obj.material)) {
          obj.material.forEach(function (m) {
            m.dispose();
          });
        } else {
          obj.material.dispose();
        }
      }
    });
    state.current = null;
    clearLabels();
  }

  function addLabels(items) {
    clearLabels();
    (items || []).forEach(function (item) {
      const node = document.createElement('span');
      node.className = 'i3d-label';
      node.textContent = item.text;
      els.labels.appendChild(node);
      state.labels.push({
        el: node,
        pos: { x: item.x, y: item.y, z: item.z }
      });
    });
  }

  // -- data -----------------------------------------------------------------

  function fetchChart(name) {
    const url =
      '/insight/data/' + encodeURIComponent(name) +
      '?language_id=' + encodeURIComponent(state.languageId);
    return fetch(url, { credentials: 'same-origin' }).then(function (r) {
      return r.json();
    });
  }

  function mount(name, data) {
    disposeCurrent();
    const scene = state.scenes.build(name, data);
    state.scene.add(scene.group);
    state.current = scene;
    addLabels(scene.labels);
    frameCurrent();
    requestRender();
  }

  function showFallback(name, data) {
    const node = els.fallback;
    if (!node) return;
    node.hidden = false;
    node.innerHTML = fallbackHtml(name, data);
  }

  function hideFallback() {
    if (els.fallback) {
      els.fallback.hidden = true;
      els.fallback.innerHTML = '';
    }
  }

  function escapeHtml(value) {
    return String(value === undefined || value === null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function fallbackHtml(name, data) {
    if (!data || data.empty) {
      return '<p>' + t('Not enough data for this chart yet.') + '</p>';
    }
    if (name === 'levels' && data.levels) {
      let html =
        '<table class="insight3d-table"><tr><th>' + t('Level') +
        '</th><th>' + t('Studied') + '</th><th>' + t('Vocabulary') + '</th></tr>';
      data.levels.forEach(function (level) {
        html +=
          '<tr><td>' + escapeHtml(level.level) + '</td><td>' + level.studied +
          '</td><td>' + level.total + '</td></tr>';
      });
      return html + '</table>';
    }
    if (name === 'galaxy') {
      let html =
        '<p>' + t('Words on the chart') + ': ' + data.shown + ' / ' + data.total + '</p>';
      return html;
    }
    if (name === 'terrain') {
      return (
        '<p>' + t('Words read in the period') + ': ' + data.total +
        ' (' + t('busiest day') + ': ' + data.max + ')</p>'
      );
    }
    return '<p>' + t('Review samples') + ': ' + (data.samples || 0) + '</p>';
  }

  const NOTES = {
    retention:
      'Measured recall over memory stability and days since the last review; the faint lines are the FSRS prediction.',
    galaxy:
      'One point per word: when it entered study, its status, and how many tokens it spans.',
    terrain: 'Daily words read, one column per week.',
    levels:
      'One column per level; the bands are statuses, and the wireframe shows the size of the level list.'
  };

  function setNote(name) {
    const node = el('insight3d_note');
    if (node) node.textContent = NOTES[name] ? t(NOTES[name]) : '';
  }

  function load(name) {
    state.chart = name;
    setStatus(t('Loading…'));
    setNote(name);

    fetchChart(name)
      .then(function (data) {
        if (!state.webgl) {
          setStatus('');
          showFallback(name, data);
          return;
        }
        hideFallback();
        if (!data || data.empty || data.supported === false) {
          // Nothing to draw: clear the old scene rather than leaving the
          // previous chart on screen looking current.
          disposeCurrent();
          requestRender();
          setStatus(
            data && data.supported === false
              ? t('No vocabulary level list for this language.')
              : t('Not enough data for this chart yet.')
          );
          return;
        }
        mount(name, data);
        setStatus('');
      })
      .catch(function () {
        setStatus(t('Could not load the chart data.'));
      });
  }

  // -- init -----------------------------------------------------------------

  function setupRenderer() {
    state.renderer = new state.three.WebGLRenderer({
      canvas: els.canvas,
      antialias: !prefersReducedMotion(),
      alpha: true,
      powerPreference: 'low-power'
    });
    state.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    state.scene = new state.three.Scene();
    state.camera = new state.three.PerspectiveCamera(48, 1, 0.1, 200);
    resize();
  }

  function resize() {
    if (!state.renderer || !els.canvas) return;
    const rect = els.canvas.parentNode.getBoundingClientRect();
    const width = Math.max(320, rect.width);
    const height = Math.max(240, rect.height);
    state.renderer.setSize(width, height, false);
    state.camera.aspect = width / height;
    state.camera.updateProjectionMatrix();
    requestRender();
  }

  function bindTabs() {
    document.querySelectorAll('[data-insight-chart]').forEach(function (button) {
      button.addEventListener('click', function () {
        document.querySelectorAll('[data-insight-chart]').forEach(function (b) {
          b.classList.toggle(
            'active',
            b.getAttribute('data-insight-chart') ===
              button.getAttribute('data-insight-chart')
          );
        });
        load(button.getAttribute('data-insight-chart'));
      });
    });
  }

  function init() {
    const root = el('insight3d');
    if (!root) return;
    els.canvas = el('insight3d_canvas');
    els.labels = el('insight3d_labels');
    els.status = el('insight3d_status');
    els.fallback = el('insight3d_fallback');

    state.languageId = root.getAttribute('data-language-id') || '';

    const languageSelect = el('insight3d_language');
    if (languageSelect) {
      languageSelect.addEventListener('change', function () {
        window.location =
          '/insight/?language_id=' + encodeURIComponent(languageSelect.value);
      });
    }

    bindTabs();
    state.webgl = hasWebGL();

    if (!state.webgl) {
      // No WebGL: one fetch per tab still works, it just renders as text.
      root.classList.add('i3d-no-webgl');
      load('retention');
      return;
    }

    Promise.all([import('three'), import('insight3d-scenes')])
      .then(function (mods) {
        state.three = mods[0];
        state.scenes = mods[1];
        setupRenderer();
        bindControls();
        window.addEventListener('resize', resize);
        state.ready = true;
        load('retention');
      })
      .catch(function () {
        state.webgl = false;
        root.classList.add('i3d-no-webgl');
        setStatus(t('Could not load the 3D library.'));
        load('retention');
      });
  }

  document.addEventListener('DOMContentLoaded', init);

  return { init: init, load: load };
})();
