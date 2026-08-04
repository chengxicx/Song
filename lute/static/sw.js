/* Lute Service Worker - PWA offline support */
const CACHE_NAME = 'lute-v3.10.4.3';

const STATIC_ASSETS = [
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

// Last-resort document served when the network is unreachable AND no page
// is cached yet.  Guarantees the SW never calls respondWith(undefined),
// which would abort the navigation with net::ERR_FAILED.
const OFFLINE_HTML =
  '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">' +
  '<meta name="viewport" content="width=device-width, initial-scale=1">' +
  '<title>Lute offline</title></head>' +
  '<body style="font-family:system-ui,-apple-system,sans-serif;text-align:center;' +
  'padding:2rem;color:#444">' +
  '<h1>Lute is offline</h1>' +
  '<p>Not connected to your Lute server.</p>' +
  '<p><a href="/">Try again</a></p>' +
  '</body></html>';

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        // Cache every static asset independently: one failing file must not
        // wipe out the whole offline cache (Cache.addAll is all-or-nothing).
        return Promise.all(
          STATIC_ASSETS.map(url =>
            fetch(url)
              .then(resp => {
                if (resp.ok) return cache.put(url, resp);
              })
              .catch(() => {})
          )
        );
      })
      .then(() => {
        // Pre-cache the homepage for offline use, but only when it renders
        // directly.  The root path can 302 to /backup/backup when an
        // auto-backup is due; that redirected (backup) page must NOT be
        // stored under '/'.
        return fetch('/')
          .then(resp => {
            if (resp.ok && resp.type === 'basic' && !resp.redirected) {
              return caches.open(CACHE_NAME).then(cache => cache.put('/', resp));
            }
          })
          .catch(() => {});
      })
      .catch(err => {
        console.warn('SW: install caching failed', err);
      })
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
 * Strip BasicAuth credentials (user:pass@) from a URL string.
 * Browsers block sub-resource requests whose URL contains embedded
 * credentials (https://user:pass@host/...).  When the page is loaded
 * with a credentialed URL, the SW intercepts the sub-resource requests
 * which also carry credentials.  We strip them here and use the clean
 * URL for both cache lookups and network fetches.
 *
 * Note: we use URL strings (not Request objects) throughout to avoid
 * issues with `new Request(url, originalRequest)` — specifically, the
 * Fetch spec forbids constructing a Request with mode 'navigate', and
 * copying certain init properties from the original request can cause
 * unexpected "Failed to fetch" errors in some browsers.
 */
function cleanUrl(originalUrl) {
  try {
    const u = new URL(originalUrl);
    if (u.username || u.password) {
      u.username = '';
      u.password = '';
    }
    return u.toString();
  } catch (e) {
    return originalUrl;
  }
}

/**
 * Guaranteed fallback for network-first requests.  Never returns
 * undefined: serving a cached page, the cached homepage, or a minimal
 * offline document is always better than ERR_FAILED.
 */
function cacheFallback(urlStr, isNavigation) {
  return caches.match(urlStr).then(cached => {
    if (cached) return cached;
    return caches.match('/').then(home => {
      if (home) return home;
      if (isNavigation) {
        return new Response(OFFLINE_HTML, {
          status: 503,
          statusText: 'Service Unavailable',
          headers: {
            'Content-Type': 'text/html; charset=utf-8',
            'Cache-Control': 'no-store',
          },
        });
      }
      return Response.error();
    });
  });
}

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;

  // Strip credentials from the request URL.
  const urlStr = cleanUrl(event.request.url);
  const url = new URL(urlStr);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // Skip theme and custom styles (they are dynamic per-user)
  if (url.pathname.startsWith('/theme/')) return;

  // Skip never-cache JS (always fresh)
  if (url.pathname.startsWith('/static/js/never_cache/')) {
    event.respondWith(fetch(urlStr).catch(() => Response.error()));
    return;
  }

  // Cache-first for static assets
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(urlStr).then(cached => {
        if (cached) return cached;
        return fetch(urlStr).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(urlStr, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  // Network-first for pages.  The root path can 302 to /backup/backup
  // when an auto-backup is due; a redirect (or a broken redirect chain)
  // must never abort the navigation — fall back to cache instead.
  const isNavigation = event.request.mode === 'navigate';
  event.respondWith(
    fetch(urlStr)
      .then(response => {
        // 3xx / opaque / error responses cannot be handed to the browser
        // as a navigation document without risking ERR_FAILED.  Followed
        // redirects normally land on a 200 here, but if we ever see one,
        // fall back to a cached page.
        const unusable =
          response.type === 'opaqueredirect' ||
          response.type === 'error' ||
          (response.status >= 300 && response.status < 400);
        if (unusable) {
          return cacheFallback(urlStr, isNavigation);
        }
        // Cache the page for offline use, but never cache a redirect
        // target under the original URL (e.g. the backup page under '/').
        if (response.ok && response.type === 'basic' && !response.redirected) {
          const clone = response.clone();
          caches.open(CACHE_NAME)
            .then(cache => cache.put(urlStr, clone))
            .catch(() => {});
        }
        return response;
      })
      .catch(() => cacheFallback(urlStr, isNavigation))
  );
});
