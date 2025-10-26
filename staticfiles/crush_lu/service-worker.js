// Crush.lu Service Worker
// PWA implementation for privacy-first dating platform

const CACHE_VERSION = 'crush-v3';  // Incremented to clear old cache
const CACHE_NAME = `crush-lu-${CACHE_VERSION}`;

// Assets to cache on install (static assets only, not pages)
const STATIC_CACHE_URLS = [
  '/static/crush_lu/css/crush.css',
  '/static/crush_lu/icons/icon-192x192.png',
  '/static/crush_lu/icons/icon-512x512.png',
];

// Offline page - cached separately to ensure it's always available
const OFFLINE_URL = '/offline/';

// Fallback offline HTML (embedded in service worker as last resort)
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
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #9B59B6 0%, #FF6B9D 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .offline-container {
            background: white;
            padding: 3rem;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            text-align: center;
            max-width: 500px;
            margin: 1rem;
        }
        .icon {
            font-size: 4rem;
            margin-bottom: 1rem;
        }
        h1 {
            color: #9B59B6;
            margin: 0 0 1rem 0;
        }
        p {
            color: #666;
            line-height: 1.6;
            margin-bottom: 2rem;
        }
        .btn {
            background: linear-gradient(135deg, #9B59B6 0%, #FF6B9D 100%);
            color: white;
            border: none;
            padding: 1rem 2rem;
            border-radius: 50px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
        }
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(155, 89, 182, 0.3);
        }
    </style>
</head>
<body>
    <div class="offline-container">
        <div class="icon">📡</div>
        <h1>You're Offline</h1>
        <p>It looks like you've lost your internet connection. Check your connection and try again.</p>
        <button class="btn" onclick="location.reload()">Try Again</button>
    </div>
</body>
</html>
`;

// Dynamic cache for user content
const DYNAMIC_CACHE_NAME = `crush-dynamic-${CACHE_VERSION}`;

// API endpoints and dynamic pages that should always fetch fresh (no cache)
const NO_CACHE_URLS = [
  '/api/',
  '/accounts/',
  '/admin/',
  '/coach/',
  '/dashboard/',
  '/login/',
  '/logout/',
];

// Install event - cache static assets
self.addEventListener('install', (event) => {
  console.log('[Service Worker] Installing Crush.lu service worker...');

  event.waitUntil(
    Promise.all([
      // Cache static assets
      caches.open(CACHE_NAME).then((cache) => {
        console.log('[Service Worker] Caching static assets');
        return cache.addAll(STATIC_CACHE_URLS);
      }),
      // Cache offline page separately with error handling
      caches.open(CACHE_NAME).then((cache) => {
        console.log('[Service Worker] Caching offline page');
        return fetch(OFFLINE_URL)
          .then((response) => {
            if (response.ok) {
              return cache.put(OFFLINE_URL, response);
            } else {
              console.warn('[Service Worker] Offline page returned status:', response.status);
            }
          })
          .catch((error) => {
            console.warn('[Service Worker] Could not cache offline page:', error);
          });
      })
    ])
    .then(() => {
      console.log('[Service Worker] Installed successfully');
      return self.skipWaiting(); // Activate immediately
    })
    .catch((error) => {
      console.error('[Service Worker] Installation failed:', error);
    })
  );
});

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
  console.log('[Service Worker] Activating Crush.lu service worker...');

  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((cacheName) => {
              // Remove old caches that don't match current version
              return cacheName.startsWith('crush-') && cacheName !== CACHE_NAME && cacheName !== DYNAMIC_CACHE_NAME;
            })
            .map((cacheName) => {
              console.log('[Service Worker] Deleting old cache:', cacheName);
              return caches.delete(cacheName);
            })
        );
      })
      .then(() => {
        console.log('[Service Worker] Activated successfully');
        return self.clients.claim(); // Take control immediately
      })
  );
});

// Fetch event - serve from cache or network
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip cross-origin requests
  if (url.origin !== location.origin) {
    return;
  }

  // Skip non-GET requests
  if (request.method !== 'GET') {
    return;
  }

  // Check if URL should bypass cache
  const shouldBypassCache = NO_CACHE_URLS.some(path => url.pathname.startsWith(path));

  if (shouldBypassCache) {
    // Always fetch fresh for API and admin endpoints
    event.respondWith(fetch(request));
    return;
  }

  // Cache-first strategy for static assets
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request)
        .then((cachedResponse) => {
          if (cachedResponse) {
            return cachedResponse;
          }

          // Fetch and cache if not found
          return fetch(request)
            .then((networkResponse) => {
              return caches.open(CACHE_NAME)
                .then((cache) => {
                  cache.put(request, networkResponse.clone());
                  return networkResponse;
                });
            });
        })
    );
    return;
  }

  // Network-first strategy for dynamic content (pages, events, profiles)
  event.respondWith(
    fetch(request)
      .then((networkResponse) => {
        // Cache successful responses for offline access
        if (networkResponse.ok) {
          return caches.open(DYNAMIC_CACHE_NAME)
            .then((cache) => {
              cache.put(request, networkResponse.clone());
              return networkResponse;
            });
        }
        return networkResponse;
      })
      .catch((error) => {
        console.log('[Service Worker] Network request failed:', error);

        // If network fails, try cache first
        return caches.match(request)
          .then((cachedResponse) => {
            if (cachedResponse) {
              console.log('[Service Worker] Serving from cache:', request.url);
              return cachedResponse;
            }

            // If no cache and it's a page navigation, show offline page
            if (request.mode === 'navigate' || request.headers.get('accept')?.includes('text/html')) {
              console.log('[Service Worker] No cache, showing offline page');
              return caches.match(OFFLINE_URL).then((offlineResponse) => {
                if (offlineResponse) {
                  return offlineResponse;
                }
                // Fallback if offline page isn't cached
                return new Response(
                  '<html><body><h1>You are offline</h1><p>Please check your internet connection.</p></body></html>',
                  {
                    status: 200,
                    statusText: 'OK',
                    headers: new Headers({
                      'Content-Type': 'text/html'
                    })
                  }
                );
              });
            }

            // For other requests (images, etc.), return a basic offline response
            return new Response('Offline', {
              status: 503,
              statusText: 'Service Unavailable',
              headers: new Headers({
                'Content-Type': 'text/plain'
              })
            });
          });
      })
  );
});

// Listen for messages from the client
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }

  if (event.data && event.data.type === 'CACHE_URLS') {
    // Allow client to request specific URLs to be cached
    const urls = event.data.urls;
    event.waitUntil(
      caches.open(DYNAMIC_CACHE_NAME)
        .then((cache) => cache.addAll(urls))
    );
  }
});

// Background sync for offline actions (future enhancement)
self.addEventListener('sync', (event) => {
  if (event.tag === 'sync-event-registrations') {
    event.waitUntil(syncEventRegistrations());
  }
});

// Push notifications (future enhancement)
self.addEventListener('push', (event) => {
  // Handle both JSON and text push messages
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    // If not JSON, treat as plain text
    data = { title: 'Crush.lu', body: event.data ? event.data.text() : 'New notification' };
  }

  const title = data.title || 'Crush.lu';
  const options = {
    body: data.body || 'New notification from Crush.lu',
    icon: '/static/crush_lu/icons/icon-192x192.png',
    badge: '/static/crush_lu/icons/icon-72x72.png',
    tag: data.tag || 'crush-notification',
    data: data.url,
  };

  event.waitUntil(
    self.registration.showNotification(title, options)
  );
});

// Notification click handler
self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const urlToOpen = event.notification.data || '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then((clientList) => {
        // Check if app is already open
        for (const client of clientList) {
          if (client.url === urlToOpen && 'focus' in client) {
            return client.focus();
          }
        }
        // Open new window if not found
        if (clients.openWindow) {
          return clients.openWindow(urlToOpen);
        }
      })
  );
});

// Helper function for background sync
async function syncEventRegistrations() {
  // This would sync any offline event registrations
  // Implementation depends on IndexedDB storage of pending actions
  console.log('[Service Worker] Syncing event registrations...');
}

console.log('[Service Worker] Crush.lu service worker loaded');
