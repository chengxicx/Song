/* Lute Service Worker - PWA offline support */
const CACHE_NAME = 'lute-v3.10.4.0';

const STATIC_ASSETS = [
  '/',
  '/manifest.webmanifest',
  '/static/css/styles.css',
  '/static/css/player-styles.css',
  '/static/vendor/jquery/jquery.js',
  '/static/vendor/jquery/jquery-ui.min.js',
  '/static/vendor/jquery/jquery-ui.css',
  '/static/vendor/jquery/jquery.scrollTo.min.js',
  '/static/vendor/jquery/jquery.jeditable.mini.js',
  '/static/vendor/jquery/jquery.hoverIntent.js',
  '/static/vendor/tagify/tagify.min.js',
  '/static/vendor/tagify/tagify.polyfills.min.js',
  '/static/vendor/tagify/tagify.css',
  '/static/vendor/tagify/tagify_overrides.css',
  '/static/vendor/datatables/datatables.min.js',
  '/static/vendor/datatables/datatables.min.css',
  '/static/vendor/datatables/datatables.button.download.js',
  '/static/vendor/dayjs/dayjs.min.js',
  '/static/vendor/dayjs/relativeTime.js',
  '/static/vendor/chartjs/chart.umd.js',
  '/static/vendor/chartjs/chartjs-adapter-date-fns.js',
  '/static/js/resize.js',
  '/static/js/player.js',
  '/static/js/text-options.js',
  '/static/js/tts.js',
  '/static/js/lute-tagify-utils.js',
  '/static/js/lute-hotkey-utils.js',
  '/static/js/lute-popups.js',
  '/static/js/lute.js',
  '/static/js/lute-anki.js',
  '/static/img/lute.png',
  '/static/img/apple-touch-icon-57x57.png',
  '/static/img/apple-touch-icon-72x72.png',
  '/static/img/apple-touch-icon-114x114.png',
  '/static/img/icon-144.png',
  '/static/img/icon-192.png',
  '/static/img/icon-512.png',
  '/static/img/maskable-192.png',
  '/static/img/maskable-512.png',
  '/static/img/lute-screenshot-desktop.png',
  '/static/img/lute-screenshot-mobile.png',
  '/static/favicon.ico',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache =>
      cache.addAll(STATIC_ASSETS).catch(err => {
        console.warn('SW: some assets failed to cache on install', err);
      })
    )
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.map(key => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      )
    )
  );
  self.clients.claim();
});

/**
 * Strip BasicAuth credentials (user:pass@) from a request URL.
 * Browsers block sub-resource requests whose URL contains embedded
 * credentials (https://user:pass@host/...).  When the page is loaded
 * with a credentialed URL, the SW intercepts the sub-resource requests
 * which also carry credentials.  We strip them here and use the clean
 * URL for both cache lookups and network fetches, so resources load
 * correctly regardless of how the page URL was entered.
 */
function cleanRequest(originalRequest) {
  const url = new URL(originalRequest.url);
  if (url.username || url.password) {
    url.username = '';
    url.password = '';
    return new Request(url.toString(), originalRequest);
  }
  return originalRequest;
}

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;

  // Strip credentials from the request URL before doing anything.
  const request = cleanRequest(event.request);
  const url = new URL(request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // Skip theme and custom styles (they are dynamic per-user)
  if (url.pathname.startsWith('/theme/')) return;

  // Skip never-cache JS (always fresh)
  if (url.pathname.startsWith('/static/js/never_cache/')) {
    event.respondWith(fetch(request));
    return;
  }

  // Cache-first for static assets
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then(cached => {
        if (cached) return cached;
        return fetch(request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(request, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  // Network-first for pages, fallback to cache when offline
  event.respondWith(
    fetch(request)
      .then(response => {
        if (response.ok && response.type === 'basic') {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, clone));
        }
        return response;
      })
      .catch(() => {
        return caches.match(request).then(cached => {
          return cached || caches.match('/');
        });
      })
  );
});
