// Crush.lu Service Worker with Workbox
// Production-ready PWA implementation using local Workbox library
// Version: v6 - Local Workbox bundle to fix Android black screen on cold start

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

  const CACHE_VERSION = 'crush-v6-local-workbox';

  // Set cache name prefix - AFTER setConfig()
  workbox.core.setCacheNameDetails({
    prefix: 'crush-lu',
    suffix: CACHE_VERSION,
    precache: 'precache',
    runtime: 'runtime'
  });

  // ============================================================================
  // Cache Cleanup on Activation - Clean up old caches from previous versions
  // ============================================================================

  self.addEventListener('activate', (event) => {
    event.waitUntil(
      (async () => {
        // Clean up old caches
        const cacheNames = await caches.keys();
        await Promise.all(
          cacheNames
            .filter(name => name.startsWith('crush-') && !name.includes(CACHE_VERSION))
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

        // Take control of all clients immediately
        await self.clients.claim();
        console.log('[Workbox] Service worker activated and claimed clients');
      })()
    );
  });

  // ============================================================================
  // Precaching - Files to cache on service worker installation
  // ============================================================================

  // Precache essential assets (REMOVED '/' to allow dynamic auth redirect)
  workbox.precaching.precacheAndRoute([
    { url: '/offline/', revision: CACHE_VERSION },
    { url: '/static/crush_lu/css/crush.css', revision: CACHE_VERSION },
    { url: '/static/crush_lu/js/page-loading.js', revision: CACHE_VERSION },
    { url: '/static/crush_lu/icons/icon-192x192.png', revision: CACHE_VERSION },
    { url: '/static/crush_lu/icons/icon-512x512.png', revision: CACHE_VERSION },
  ]);

  console.log('[Workbox] Precaching configured');

  // ============================================================================
  // Offline Fallback
  // ============================================================================

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

  // Set offline page as fallback for navigation requests
  workbox.recipes.offlineFallback({
    pageFallback: OFFLINE_PAGE,
  });

  console.log('[Workbox] Offline fallback configured');

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

  // Strategy 1: Network Only for authenticated/user-specific pages (MUST BE FIRST)
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
  workbox.routing.registerRoute(
    ({ request }) => request.mode === 'navigate',
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

  console.log('[Workbox] Page caching strategy registered');

  // Strategy 5: Cache First for static assets (CSS, JS)
  workbox.routing.registerRoute(
    ({ request }) =>
      request.destination === 'style' ||
      request.destination === 'script',
    new workbox.strategies.CacheFirst({
      cacheName: 'crush-static',
      plugins: [
        new workbox.expiration.ExpirationPlugin({
          maxEntries: 60,
          maxAgeSeconds: 30 * 24 * 60 * 60, // 30 days
        }),
      ],
    })
  );

  console.log('[Workbox] Static assets caching registered');

  // Strategy 6: Cache First for images (long cache)
  workbox.routing.registerRoute(
    ({ request }) => request.destination === 'image',
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
  workbox.routing.registerRoute(
    ({ url, request }) =>
      request.method === 'POST' &&
      !url.pathname.startsWith('/api/') &&
      !url.pathname.startsWith('/admin/'),
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
    const urlToOpen = event.notification.data || '/';

    event.waitUntil(
      clients.matchAll({ type: 'window', includeUncontrolled: true })
        .then((clientList) => {
          for (const client of clientList) {
            if (client.url === urlToOpen && 'focus' in client) {
              return client.focus();
            }
          }
          if (clients.openWindow) {
            return clients.openWindow(urlToOpen);
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

  console.log('[Workbox] Service worker v6 configured successfully!');

} else {
  console.error('[Workbox] Failed to load Workbox from local bundle!');

  // Fallback: Basic service worker without Workbox
  self.addEventListener('fetch', (event) => {
    // Just pass through to network if Workbox failed
    event.respondWith(fetch(event.request));
  });
}
