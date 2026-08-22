/* Lute Service Worker - PWA offline support */
const CACHE_NAME = 'lute-v3.10.5.0';

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
  '/static/vendor/htmx/htmx.min.js',
  '/static/vendor/alpine/alpine.min.js',
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
  '/static/img/lute.webp',
  '/static/img/icon-192.png',
  '/static/img/icon-512.png',
  '/static/img/lute-screenshot-desktop.webp',
  '/static/img/lute-screenshot-mobile.webp',
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
 *
 * If a fallbackResponse is supplied (e.g. a server page we deliberately
 * don't hand to the visitor but that came from a healthy server), use it
 * when the cache is completely empty so a first-time visitor still sees a
 * real document instead of the offline stub.
 */
function isRedirectedResponse(resp) {
  return resp.redirected || resp.type === 'opaqueredirect';
}

/**
 * Reconstruct a response as a fresh non-redirected response.
 * The browser rejects redirected navigation responses served via
 * event.respondWith() with ERR_FAILED.  This is needed when the
 * SW's fetch() follows a server redirect (e.g. / -> /backup/backup)
 * and we want to hand the final body to the browser anyway.
 */
function cloneResponse(resp) {
  return new Response(resp.body, {
    status: resp.status,
    statusText: resp.statusText || 'OK',
    headers: resp.headers,
  });
}

function cacheFallback(urlStr, isNavigation, fallbackResponse) {
  // NOTE: we deliberately do NOT fall back to a cached home page here.
  // The home page (and all pages) embed dynamic per-user state such as
  // current_language_id; serving a stale cached copy makes user settings
  // appear to reset.  Static assets are served cache-first above, so
  // this fallback only matters for page navigations.
  return caches.match(urlStr).then(cached => {
    if (cached) return cached;
    if (fallbackResponse) {
      // De-redirect the response if needed — see isRedirectedResponse.
      return isRedirectedResponse(fallbackResponse)
        ? cloneResponse(fallbackResponse)
        : fallbackResponse;
    }
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

  // Skip dynamic pages that must never be cached (term forms,
  // reading pages, term API routes).  These change on every request
  // and caching them causes stale data after term status updates.
  if (url.pathname.startsWith('/read/') || url.pathname.startsWith('/term/')) {
    event.respondWith(
      fetch(urlStr)
        .then(response => {
          // Some GET endpoints 302 to a reading page (e.g.
          // /read/delete_page/<id>/<n> -> /read/<id>).  fetch() follows the
          // redirect, and handing the resulting "redirected" response to a
          // navigation via respondWith() makes Chrome fail the load with
          // net::ERR_FAILED.  Reconstruct it as a fresh non-redirected
          // response so the final body is served to the browser.
          if (isRedirectedResponse(response)) {
            return cloneResponse(response);
          }
          return response;
        })
        .catch(() => Response.error())
    );
    return;
  }

  // Skip redirect-only utility endpoints — let the browser handle
  // the 302 redirect natively instead of returning a redirected
  // response via the SW, which can cause ERR_FAILED in the browser.
  // This includes the dev_api endpoints used by acceptance tests,
  // which 302 to "/" to report a flash message.
  if (
    url.pathname === '/refresh_all_stats' ||
    url.pathname.startsWith('/dev_api/')
  ) {
    return;
  }

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
  // when an auto-backup is due.  fetch() follows that redirect, so the
  // "Backing up..." page arrives here as a "redirected" 200 — never hand
  // it to the visitor as the homepage; serve a cached homepage instead.
  const isNavigation = event.request.mode === 'navigate';
  event.respondWith(
    fetch(urlStr)
      .then(response => {
        // 3xx / opaque / error responses cannot be handed to the browser
        // as a navigation document without risking ERR_FAILED.  Followed
        // redirects normally land on a 200 here, but if we ever see one,
        // fall back to a cached page.  Opaque responses happen when the
        // server uses Cloudflare Access (Zero Trust) and the session has
        // expired — the fetch follows the cross-origin redirect to the
        // Cloudflare Access login page, returning an opaque response that
        // the browser cannot render for navigation.
        const unusable =
          response.type === 'opaqueredirect' ||
          response.type === 'opaque' ||
          response.type === 'error' ||
          (response.status >= 300 && response.status < 400);
        // Auto-backup due: the root path is 302'd to the backup page.
        // Block that forced redirect — show the cached homepage instead
        // (or the fetched page itself when nothing is cached yet).
        const forcedToBackup =
          url.pathname === '/' &&
          response.redirected &&
          response.url.includes('/backup/backup');
        if (unusable || forcedToBackup) {
          // Don't pass opaque responses as fallback — the browser cannot
          // render them for navigation (e.g. Cloudflare Access cross-origin
          // redirects).  Fall through to cached/offline page instead.
          const fallback = (response.type === 'opaque') ? undefined : response;
          return cacheFallback(urlStr, isNavigation, fallback);
        }
        // Do NOT cache page responses.  Pages (including the home page)
        // are dynamic: they embed user-specific state such as
        // current_language_id.  Caching them and later serving the stale
        // copy as a network fallback makes settings appear to reset (e.g.
        // the language dropdown reverting after clicking Home).  If the
        // network request fails or returns an opaque/redirect response we
        // fall through to the offline placeholder below instead.
        return response;
      })
      .catch(() => cacheFallback(urlStr, isNavigation))
  );
});
