// Crush.lu Service Worker with Workbox
// Production-ready PWA implementation using local Workbox library
// Version: v24 - Force cache refresh for push notification fix in alpine-components.js

// ============================================================================
// CRITICAL: OAuth Callback Bypass - MUST BE BEFORE WORKBOX
// ============================================================================
// OAuth callbacks must COMPLETELY bypass the service worker's caching logic.
//
// IMPORTANT: Just using `return;` does NOT bypass - it only exits this handler
// but Workbox will still register its own handlers that intercept the request.
//
// Using event.respondWith(fetch(event.request)) ensures this handler "claims"
// the request, preventing any Workbox routes, offline fallbacks, or caching
// from processing it. The SW still responds, but with a direct network fetch.

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // TRUE HARD BYPASS: External CDN resources (cross-origin)
  // These cause "opaque" response errors when cached by service worker
  if (url.origin !== self.location.origin) {
    // Don't intercept cross-origin requests at all - let browser handle them
    return;
  }

  // TRUE HARD BYPASS: OAuth and auth-related URLs
  // Using event.respondWith(fetch()) ensures NO other handler can intercept
  if (
    url.pathname.startsWith('/accounts/') ||   // All OAuth/auth routes
    url.pathname.startsWith('/oauth/') ||      // OAuth landing and callbacks
    url.pathname.includes('/login/callback') ||// Explicit callback match
    url.pathname.startsWith('/api/auth/') ||   // Auth status API
    url.pathname === '/login/' ||              // Login page
    url.pathname === '/logout/'                // Logout page
  ) {
    // Direct network fetch - prevents any Workbox handler from intercepting
    event.respondWith(fetch(event.request));
    return;
  }
});

// Import Workbox from LOCAL static files (not CDN) to enable offline installation
importScripts('/static/crush_lu/workbox/workbox-sw.js');

// Check if Workbox loaded successfully
if (workbox) {
  console.log('[Workbox] Successfully loaded from local bundle!');

  // ============================================================================
  // Configuration - MUST BE SET FIRST
  // ============================================================================

  // Configure Workbox to load modules from local static files
  workbox.setConfig({
    debug: location.hostname === 'localhost' || location.hostname === '127.0.0.1',
    modulePathPrefix: '/static/crush_lu/workbox/'
  });

  const CACHE_VERSION = 'crush-v24-push-fix';

  // Set cache name prefix - AFTER setConfig()
  workbox.core.setCacheNameDetails({
    prefix: 'crush-lu',
    suffix: CACHE_VERSION,
    precache: 'precache',
    runtime: 'runtime'
  });

  // ============================================================================
  // Offline Fallback Constants - MUST BE DEFINED BEFORE activate handler
  // ============================================================================
  // These constants are used in the activate handler below, so they must be
  // defined first to avoid "Cannot access before initialization" ReferenceError.

  const OFFLINE_PAGE = '/offline/';
  const OFFLINE_FALLBACK_HTML = `
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Offline - Crush.lu</title>
    <style>
        body {
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #9B59B6 0%, #FF6B9D 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .container {
            background: white;
            padding: 3rem;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            text-align: center;
            max-width: 500px;
            margin: 1rem;
        }
        .icon { font-size: 4rem; margin-bottom: 1rem; }
        h1 { color: #9B59B6; margin: 0 0 1rem 0; }
        p { color: #666; line-height: 1.6; margin-bottom: 2rem; }
        .btn {
            background: linear-gradient(135deg, #9B59B6 0%, #FF6B9D 100%);
            color: white;
            border: none;
            padding: 1rem 2rem;
            border-radius: 50px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">📡</div>
        <h1>You're Offline</h1>
        <p>Check your internet connection and try again.</p>
        <button class="btn" onclick="location.reload()">Try Again</button>
    </div>
</body>
</html>`;

  // ============================================================================
  // Cache Cleanup on Activation - Clean up old caches from previous versions
  // ============================================================================

  self.addEventListener('activate', (event) => {
    event.waitUntil(
      (async () => {
        // Clean up old caches (cache names start with 'crush-lu-')
        const cacheNames = await caches.keys();
        await Promise.all(
          cacheNames
            .filter(name => name.startsWith('crush-lu-') && !name.includes(CACHE_VERSION))
            .map(name => {
              console.log('[Workbox] Deleting old cache:', name);
              return caches.delete(name);
            })
        );

        // Cache the offline page
        const cache = await caches.open(workbox.core.cacheNames.runtime);
        try {
          const response = await fetch(OFFLINE_PAGE);
          if (response.ok) {
            await cache.put(OFFLINE_PAGE, response);
            console.log('[Workbox] Cached Django offline page');
          } else {
            throw new Error('Offline page not available');
          }
        } catch (error) {
          console.log('[Workbox] Using embedded offline HTML');
          await cache.put(
            OFFLINE_PAGE,
            new Response(OFFLINE_FALLBACK_HTML, {
              headers: { 'Content-Type': 'text/html' }
            })
          );
        }

        // NOTE: We intentionally do NOT call clients.claim() here
        // Calling clients.claim() during OAuth can cause race conditions
        // where the SW takes control mid-navigation and breaks cookie commits.
        // The SW will naturally take control on the next navigation.
        console.log('[Workbox] Service worker activated (no immediate claim to avoid OAuth race)');
      })()
    );
  });

  // ============================================================================
  // Precaching - Files to cache on service worker installation
  // ============================================================================

  // Precache essential assets (REMOVED '/' to allow dynamic auth redirect)
  // Expanded for v21 performance optimization
  workbox.precaching.precacheAndRoute([
    // Critical pages
    { url: '/offline/', revision: CACHE_VERSION },

    // CSS (critical for rendering)
    { url: '/static/crush_lu/css/tailwind.css', revision: CACHE_VERSION },
    { url: '/static/crush_lu/css/crush-modular.css', revision: CACHE_VERSION },

    // Core JavaScript
    { url: '/static/crush_lu/js/page-loading.js', revision: CACHE_VERSION },
    { url: '/static/crush_lu/js/utils.js', revision: CACHE_VERSION },
    { url: '/static/crush_lu/js/pwa-detector.js', revision: CACHE_VERSION },
    { url: '/static/crush_lu/js/sw-register.js', revision: CACHE_VERSION },

    // PWA icons (most commonly used sizes)
    { url: '/static/crush_lu/icons/icon-192x192.png', revision: CACHE_VERSION },
    { url: '/static/crush_lu/icons/android-launchericon-512-512.png', revision: CACHE_VERSION },
    { url: '/static/crush_lu/icons/ios/180.png', revision: CACHE_VERSION },

    // Favicon
    { url: '/static/crush_lu/crush_favicon.ico', revision: CACHE_VERSION },
  ]);

  console.log('[Workbox] Precaching configured');

  // ============================================================================
  // Offline Fallback
  // ============================================================================

  // Set offline page as fallback for navigation requests
  workbox.recipes.offlineFallback({
    pageFallback: OFFLINE_PAGE,
  });

  // CRITICAL: Immediately exclude auth navigations from offline fallback
  // offlineFallback() wraps navigation requests and can interfere with OAuth
  // This route MUST be registered immediately after offlineFallback()
  workbox.routing.registerRoute(
    ({ request, url }) =>
      request.mode === 'navigate' &&
      (
        url.pathname.startsWith('/accounts/') ||
        url.pathname.startsWith('/oauth/') ||
        url.pathname.startsWith('/login') ||
        url.pathname.startsWith('/logout')
      ),
    new workbox.strategies.NetworkOnly()
  );

  console.log('[Workbox] Offline fallback configured (auth excluded)');

  // ============================================================================
  // Caching Strategies
  // ============================================================================

  // Custom plugin to notify clients when server is unreachable
  class ServerUnreachablePlugin {
    async fetchDidFail({ request }) {
      const clients = await self.clients.matchAll({ type: 'window' });
      clients.forEach(client => {
        client.postMessage({
          type: 'SERVER_UNREACHABLE',
          url: request.url,
          timestamp: Date.now()
        });
      });
    }
  }

  // ============================================================================
  // OAuth/Auth Routes - NetworkOnly as backup (primary bypass is in fetch handler above)
  // ============================================================================
  // The fetch event handler above does a HARD BYPASS for /accounts/ routes.
  // This Workbox route is a backup that ensures no caching if something slips through.

  workbox.routing.registerRoute(
    ({ url }) => url.pathname.startsWith('/accounts/'),
    new workbox.strategies.NetworkOnly()
  );

  console.log('[Workbox] Auth routes registered (NetworkOnly backup)');

  // Helper function to check if path matches authenticated routes (with i18n support)
  function isAuthenticatedRoute(pathname) {
    const authPaths = [
      '/admin', '/accounts', '/coach', '/dashboard',
      '/login', '/logout', '/profile', '/connections',
      '/journey', '/create-profile', '/edit', '/signup',
      '/oauth-complete',   // PWA OAuth return handler - must never be cached
      '/oauth/popup-callback',  // Popup OAuth callback - must never be cached
      '/oauth/popup-error',     // Popup OAuth error - must never be cached
      '/api/auth/status'        // Auth status API - must never be cached
    ];

    // Check with and without language prefix (en, fr, de)
    for (const authPath of authPaths) {
      if (pathname.includes(authPath)) {
        return true;
      }
      // Check with language prefixes
      if (pathname.match(new RegExp(`^/(en|fr|de)${authPath}`))) {
        return true;
      }
    }
    return false;
  }

  // Strategy 1: Network Only for authenticated/user-specific pages (MUST BE FIRST after OAuth)
  // This prevents caching of login redirects which cause the black screen issue
  workbox.routing.registerRoute(
    ({ url }) => isAuthenticatedRoute(url.pathname),
    new workbox.strategies.NetworkOnly()
  );

  console.log('[Workbox] Authenticated routes registered (NetworkOnly)');

  // Strategy 2: Network Only for health checks (never cache - used for reconnection detection)
  workbox.routing.registerRoute(
    ({ url }) => url.pathname.startsWith('/healthz'),
    new workbox.strategies.NetworkOnly()
  );

  // Strategy 3: Network Only for API calls (never cache)
  workbox.routing.registerRoute(
    ({ url }) => url.pathname.startsWith('/api/'),
    new workbox.strategies.NetworkOnly()
  );

  // Strategy 4: Network First for HTML pages (always fresh, fallback to cache)
  // IMPORTANT: Explicitly exclude auth paths to prevent any OAuth interference
  workbox.routing.registerRoute(
    ({ request, url }) =>
      request.mode === 'navigate' &&
      !url.pathname.startsWith('/accounts/') &&
      !url.pathname.startsWith('/oauth/') &&
      !url.pathname.startsWith('/login') &&
      !url.pathname.startsWith('/logout'),
    new workbox.strategies.NetworkFirst({
      cacheName: 'crush-pages',
      plugins: [
        new workbox.expiration.ExpirationPlugin({
          maxEntries: 50,
          maxAgeSeconds: 24 * 60 * 60, // 24 hours
        }),
        new workbox.cacheableResponse.CacheableResponsePlugin({
          statuses: [200], // Only cache successful responses (not redirects!)
        }),
        new ServerUnreachablePlugin(),
      ],
    })
  );

  console.log('[Workbox] Page caching strategy registered (auth excluded)');

  // Strategy 5: StaleWhileRevalidate for static assets (CSS, JS)
  // Changed from CacheFirst to allow CSS/JS updates to propagate quickly
  workbox.routing.registerRoute(
    ({ request }) =>
      request.destination === 'style' ||
      request.destination === 'script',
    new workbox.strategies.StaleWhileRevalidate({
      cacheName: 'crush-static',
      plugins: [
        new workbox.expiration.ExpirationPlugin({
          maxEntries: 60,
          maxAgeSeconds: 7 * 24 * 60 * 60, // 7 days (reduced from 30)
        }),
      ],
    })
  );

  console.log('[Workbox] Static assets caching registered (StaleWhileRevalidate)');

  // Strategy 6a: Icons - StaleWhileRevalidate (update quickly, don't pin for 30 days)
  // MUST be registered BEFORE the general image CacheFirst route
  workbox.routing.registerRoute(
    ({ url }) => url.pathname.startsWith('/static/crush_lu/icons/'),
    new workbox.strategies.StaleWhileRevalidate({
      cacheName: 'crush-icons',
      plugins: [
        new workbox.expiration.ExpirationPlugin({
          maxEntries: 60,
          maxAgeSeconds: 24 * 60 * 60, // 1 day - allows icons to update quickly
        }),
        new workbox.cacheableResponse.CacheableResponsePlugin({
          statuses: [200],
        }),
      ],
    })
  );

  console.log('[Workbox] Icon caching strategy registered (StaleWhileRevalidate)');

  // Strategy 6b: Cache First for same-origin images only (long cache)
  // External images (Facebook profile pics, etc.) are not cached to avoid CSP connect-src issues
  workbox.routing.registerRoute(
    ({ request, url }) =>
      request.destination === 'image' &&
      url.origin === self.location.origin,
    new workbox.strategies.CacheFirst({
      cacheName: 'crush-images',
      plugins: [
        new workbox.expiration.ExpirationPlugin({
          maxEntries: 100,
          maxAgeSeconds: 30 * 24 * 60 * 60, // 30 days
        }),
        new workbox.cacheableResponse.CacheableResponsePlugin({
          statuses: [0, 200],
        }),
      ],
    })
  );

  console.log('[Workbox] Image caching strategy registered');

  // Strategy 7: Stale While Revalidate for fonts
  workbox.routing.registerRoute(
    ({ request }) => request.destination === 'font',
    new workbox.strategies.StaleWhileRevalidate({
      cacheName: 'crush-fonts',
      plugins: [
        new workbox.expiration.ExpirationPlugin({
          maxEntries: 20,
          maxAgeSeconds: 365 * 24 * 60 * 60, // 1 year
        }),
      ],
    })
  );

  // ============================================================================
  // Offline Fallback Handler
  // ============================================================================
  // Provides graceful fallbacks when requests fail (e.g., offline)

  workbox.routing.setCatchHandler(async ({ event }) => {
    // Return fallback SVG for failed image requests
    if (event.request.destination === 'image') {
      const fallbackSvg = `
        <svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200">
          <defs>
            <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" style="stop-color:#9B59B6;stop-opacity:0.1"/>
              <stop offset="100%" style="stop-color:#FF6B9D;stop-opacity:0.1"/>
            </linearGradient>
          </defs>
          <rect fill="url(#grad)" width="200" height="200" rx="8"/>
          <text x="100" y="90" text-anchor="middle" fill="#9B59B6" font-family="sans-serif" font-size="14" font-weight="500">
            Image unavailable
          </text>
          <text x="100" y="115" text-anchor="middle" fill="#999" font-family="sans-serif" font-size="12">
            You're offline
          </text>
        </svg>`;

      return new Response(fallbackSvg, {
        headers: { 'Content-Type': 'image/svg+xml' }
      });
    }

    // For navigation requests that fail, the offlineFallback() handles it
    // For other requests, return an error
    return Response.error();
  });

  console.log('[Workbox] Offline fallback handler registered');

  // ============================================================================
  // Background Sync (for future offline form submissions)
  // ============================================================================

  const bgSyncPlugin = new workbox.backgroundSync.BackgroundSyncPlugin('crush-queue', {
    maxRetentionTime: 24 * 60, // Retry for up to 24 hours (in minutes)
    onSync: async ({ queue }) => {
      let entry;
      while ((entry = await queue.shiftRequest())) {
        try {
          await fetch(entry.request);
          console.log('[Workbox] Background sync successful:', entry.request.url);
        } catch (error) {
          console.error('[Workbox] Background sync failed:', error);
          await queue.unshiftRequest(entry);
          throw error;
        }
      }
    },
  });

  // Use background sync for POST requests (event registrations, etc.)
  // IMPORTANT: Exclude auth-related POSTs - they have CSRF tokens that can't be replayed
  workbox.routing.registerRoute(
    ({ url, request }) =>
      request.method === 'POST' &&
      !url.pathname.startsWith('/api/') &&
      !url.pathname.startsWith('/admin/') &&
      !url.pathname.startsWith('/login') &&
      !url.pathname.startsWith('/logout') &&
      !url.pathname.startsWith('/accounts/') &&
      !url.pathname.startsWith('/signup'),
    new workbox.strategies.NetworkOnly({
      plugins: [bgSyncPlugin],
    }),
    'POST'
  );

  console.log('[Workbox] Background sync configured');

  // ============================================================================
  // Push Notifications
  // ============================================================================

  self.addEventListener('push', (event) => {
    console.log('[Workbox] Push notification received');

    if (Notification.permission !== 'granted') {
      console.log('[Workbox] Notification permission not granted, skipping');
      return;
    }

    let data = {};
    try {
      data = event.data ? event.data.json() : {};
    } catch (e) {
      data = {
        title: 'Crush.lu',
        body: event.data ? event.data.text() : 'New notification'
      };
    }

    const options = {
      body: data.body || 'New notification from Crush.lu',
      icon: '/static/crush_lu/icons/icon-192x192.png',
      badge: '/static/crush_lu/icons/icon-72x72.png',
      vibrate: [200, 100, 200],
      tag: data.tag || 'crush-notification',
      data: data.url || '/',
    };

    event.waitUntil(
      self.registration.showNotification(data.title || 'Crush.lu', options)
    );
  });

  self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const urlToOpen = new URL(event.notification.data || '/', self.location.origin);

    event.waitUntil(
      clients.matchAll({ type: 'window', includeUncontrolled: true })
        .then((clientList) => {
          // Find any client on same origin and focus/navigate it
          for (const client of clientList) {
            const clientUrl = new URL(client.url);
            if (clientUrl.origin === urlToOpen.origin && 'focus' in client) {
              // Navigate existing client to the target URL and focus
              client.navigate(urlToOpen.href);
              return client.focus();
            }
          }
          // No existing client found, open new window
          if (clients.openWindow) {
            return clients.openWindow(urlToOpen.href);
          }
        })
    );
  });

  // ============================================================================
  // Update Handling
  // ============================================================================

  self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'SKIP_WAITING') {
      self.skipWaiting();
    }
  });

  console.log('[Workbox] Service worker v20 (Tailwind migration) configured successfully!');

} else {
  console.error('[Workbox] Failed to load Workbox from local bundle!');

  // Fallback: Basic service worker without Workbox
  self.addEventListener('fetch', (event) => {
    // Just pass through to network if Workbox failed
    event.respondWith(fetch(event.request));
  });
}
