// Crush.lu Service Worker with Workbox
// Production-ready PWA implementation using Google's Workbox library

importScripts('https://storage.googleapis.com/workbox-cdn/releases/7.0.0/workbox-sw.js');

// Check if Workbox loaded successfully
if (workbox) {
  console.log('[Workbox] Successfully loaded! 🎉');

  // ============================================================================
  // Configuration - MUST BE SET FIRST
  // ============================================================================

  // Enable debug logging in development - SET THIS FIRST!
  if (location.hostname === 'localhost' || location.hostname === '127.0.0.1') {
    workbox.setConfig({ debug: true });
  }

  const CACHE_VERSION = 'crush-v5-auth-fix'; // Updated to fix auth redirect caching

  // Set cache name prefix - AFTER setConfig()
  workbox.core.setCacheNameDetails({
    prefix: 'crush-lu',
    suffix: CACHE_VERSION,
    precache: 'precache',
    runtime: 'runtime'
  });

  // ============================================================================
  // Precaching - Files to cache on service worker installation
  // ============================================================================

  // Precache essential assets (REMOVED '/' to allow dynamic auth redirect)
  workbox.precaching.precacheAndRoute([
    // Home page removed - needs to be dynamic based on authentication status
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

  // Set offline page as fallback
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

  // Install offline page during service worker activation
  self.addEventListener('activate', (event) => {
    event.waitUntil(
      (async () => {
        const cache = await caches.open(workbox.core.cacheNames.runtime);

        // Try to cache the Django offline page
        try {
          const response = await fetch(OFFLINE_PAGE);
          if (response.ok) {
            await cache.put(OFFLINE_PAGE, response);
            console.log('[Workbox] Cached Django offline page');
          } else {
            throw new Error('Offline page not available');
          }
        } catch (error) {
          // Fallback: cache embedded HTML
          console.log('[Workbox] Using embedded offline HTML');
          await cache.put(
            OFFLINE_PAGE,
            new Response(OFFLINE_FALLBACK_HTML, {
              headers: { 'Content-Type': 'text/html' }
            })
          );
        }
      })()
    );
  });

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
      // Notify all clients that server is unreachable
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

  // Strategy 1: Network First for HTML pages (always fresh, fallback to cache)
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
          statuses: [0, 200],
        }),
        new ServerUnreachablePlugin(), // Notify when server fails
      ],
    })
  );

  console.log('[Workbox] Page caching strategy registered');

  // Strategy 2: Cache First for static assets (CSS, JS)
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

  // Strategy 3: Cache First for images (long cache)
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

  // Strategy 4: Network Only for health checks (never cache - used for reconnection detection)
  workbox.routing.registerRoute(
    ({ url }) => url.pathname.startsWith('/healthz'),
    new workbox.strategies.NetworkOnly()
  );

  // Strategy 5: Network Only for API calls (never cache)
  workbox.routing.registerRoute(
    ({ url }) => url.pathname.startsWith('/api/'),
    new workbox.strategies.NetworkOnly()
  );

  // Strategy 6: Network Only for admin, auth, and user-specific pages
  workbox.routing.registerRoute(
    ({ url }) =>
      url.pathname.startsWith('/admin/') ||
      url.pathname.startsWith('/accounts/') ||
      url.pathname.startsWith('/coach/') ||
      url.pathname.startsWith('/dashboard/') ||
      url.pathname === '/dashboard' ||
      url.pathname.startsWith('/login/') ||
      url.pathname === '/login' ||
      url.pathname.startsWith('/logout/') ||
      url.pathname === '/logout',
    new workbox.strategies.NetworkOnly()
  );

  console.log('[Workbox] Network-only routes registered');

  // Strategy 6: Stale While Revalidate for fonts and other resources
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

  // Enable background sync for offline event registrations
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
      !url.pathname.startsWith('/api/') && // Don't queue API calls
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

    // Check notification permission before attempting to show
    if (Notification.permission !== 'granted') {
      console.log('[Workbox] Notification permission not granted, skipping notification');
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
  // Update Notification
  // ============================================================================

  // Show notification when service worker updates
  self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'SKIP_WAITING') {
      self.skipWaiting();
    }
  });

  console.log('[Workbox] Service worker configured successfully! ✨');

} else {
  console.error('[Workbox] Failed to load Workbox! 😢');
}
