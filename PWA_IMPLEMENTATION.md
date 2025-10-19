# Progressive Web App (PWA) Implementation for Crush.lu

Transform Crush.lu into a native-like app experience that users can install on their phone or desktop!

## What is a PWA?

A Progressive Web App combines the best of web and mobile apps:
- **Installable** - Add to home screen like a native app
- **Offline Support** - Works without internet connection
- **Fast** - Cached resources load instantly
- **App-like** - Fullscreen, no browser UI
- **Push Notifications** - Re-engage users (covered in PUSH_NOTIFICATIONS_IMPLEMENTATION.md)
- **Background Sync** - Sync data when connection returns

## Implementation Steps

### 1. Create Web App Manifest

Create `static/crush_lu/manifest.json`:

```json
{
  "name": "Crush.lu - Privacy-First Dating",
  "short_name": "Crush.lu",
  "description": "Meet authentic people at real events in Luxembourg. Privacy-first, coach-curated, event-driven dating.",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "orientation": "portrait-primary",
  "theme_color": "#9B59B6",
  "background_color": "#FFFFFF",
  "icons": [
    {
      "src": "/static/crush_lu/img/icons/icon-72x72.png",
      "sizes": "72x72",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/static/crush_lu/img/icons/icon-96x96.png",
      "sizes": "96x96",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/static/crush_lu/img/icons/icon-128x128.png",
      "sizes": "128x128",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/static/crush_lu/img/icons/icon-144x144.png",
      "sizes": "144x144",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/static/crush_lu/img/icons/icon-152x152.png",
      "sizes": "152x152",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/static/crush_lu/img/icons/icon-192x192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/static/crush_lu/img/icons/icon-384x384.png",
      "sizes": "384x384",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/static/crush_lu/img/icons/icon-512x512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/static/crush_lu/img/icons/icon-maskable-192x192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "maskable"
    },
    {
      "src": "/static/crush_lu/img/icons/icon-maskable-512x512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "maskable"
    }
  ],
  "shortcuts": [
    {
      "name": "Events",
      "short_name": "Events",
      "description": "Browse upcoming events",
      "url": "/events/",
      "icons": [
        {
          "src": "/static/crush_lu/img/icons/shortcut-events.png",
          "sizes": "96x96"
        }
      ]
    },
    {
      "name": "Messages",
      "short_name": "Messages",
      "description": "View your connections",
      "url": "/connections/",
      "icons": [
        {
          "src": "/static/crush_lu/img/icons/shortcut-messages.png",
          "sizes": "96x96"
        }
      ]
    },
    {
      "name": "Journey",
      "short_name": "Journey",
      "description": "Continue your journey",
      "url": "/journey/",
      "icons": [
        {
          "src": "/static/crush_lu/img/icons/shortcut-journey.png",
          "sizes": "96x96"
        }
      ]
    }
  ],
  "categories": ["social", "lifestyle"],
  "screenshots": [
    {
      "src": "/static/crush_lu/img/screenshots/home-mobile.png",
      "sizes": "540x720",
      "type": "image/png",
      "form_factor": "narrow"
    },
    {
      "src": "/static/crush_lu/img/screenshots/events-mobile.png",
      "sizes": "540x720",
      "type": "image/png",
      "form_factor": "narrow"
    },
    {
      "src": "/static/crush_lu/img/screenshots/home-desktop.png",
      "sizes": "1280x720",
      "type": "image/png",
      "form_factor": "wide"
    }
  ],
  "share_target": {
    "action": "/share/",
    "method": "POST",
    "enctype": "multipart/form-data",
    "params": {
      "title": "title",
      "text": "text",
      "url": "url"
    }
  }
}
```

### 2. Create Advanced Service Worker

Create `static/crush_lu/js/service-worker-advanced.js`:

```javascript
// Advanced Service Worker for Crush.lu PWA
// Version: 1.0.0

const CACHE_VERSION = 'crush-v1.0.0';
const CACHE_NAMES = {
    static: `${CACHE_VERSION}-static`,
    dynamic: `${CACHE_VERSION}-dynamic`,
    images: `${CACHE_VERSION}-images`,
    api: `${CACHE_VERSION}-api`
};

// Resources to cache on install
const STATIC_ASSETS = [
    '/',
    '/static/crush_lu/css/crush.css',
    '/static/crush_lu/js/main.js',
    '/static/crush_lu/img/crush-logo.png',
    '/static/crush_lu/img/icons/icon-192x192.png',
    '/offline/',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css'
];

// API endpoints to cache
const API_CACHE_URLS = [
    '/api/events/',
    '/api/journey/progress/'
];

// Install Event - Cache static assets
self.addEventListener('install', (event) => {
    console.log('[Service Worker] Installing...');

    event.waitUntil(
        caches.open(CACHE_NAMES.static)
            .then(cache => {
                console.log('[Service Worker] Caching static assets');
                return cache.addAll(STATIC_ASSETS);
            })
            .then(() => self.skipWaiting())
    );
});

// Activate Event - Clean up old caches
self.addEventListener('activate', (event) => {
    console.log('[Service Worker] Activating...');

    event.waitUntil(
        caches.keys()
            .then(cacheNames => {
                return Promise.all(
                    cacheNames.map(cacheName => {
                        // Delete old version caches
                        if (!Object.values(CACHE_NAMES).includes(cacheName)) {
                            console.log('[Service Worker] Deleting old cache:', cacheName);
                            return caches.delete(cacheName);
                        }
                    })
                );
            })
            .then(() => self.clients.claim())
    );
});

// Fetch Event - Network-first strategy with fallback
self.addEventListener('fetch', (event) => {
    const { request } = event;
    const url = new URL(request.url);

    // API requests - Network first, cache fallback
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(networkFirstStrategy(request, CACHE_NAMES.api));
        return;
    }

    // Images - Cache first
    if (request.destination === 'image') {
        event.respondWith(cacheFirstStrategy(request, CACHE_NAMES.images));
        return;
    }

    // HTML pages - Network first
    if (request.mode === 'navigate') {
        event.respondWith(
            networkFirstStrategy(request, CACHE_NAMES.dynamic)
                .catch(() => caches.match('/offline/'))
        );
        return;
    }

    // Static assets - Cache first
    if (STATIC_ASSETS.some(asset => request.url.includes(asset))) {
        event.respondWith(cacheFirstStrategy(request, CACHE_NAMES.static));
        return;
    }

    // Default - Network first
    event.respondWith(networkFirstStrategy(request, CACHE_NAMES.dynamic));
});

// Network-first strategy
async function networkFirstStrategy(request, cacheName) {
    try {
        const response = await fetch(request);

        // Cache successful responses
        if (response.ok) {
            const cache = await caches.open(cacheName);
            cache.put(request, response.clone());
        }

        return response;
    } catch (error) {
        // Network failed, try cache
        const cached = await caches.match(request);
        if (cached) {
            return cached;
        }
        throw error;
    }
}

// Cache-first strategy
async function cacheFirstStrategy(request, cacheName) {
    const cached = await caches.match(request);
    if (cached) {
        return cached;
    }

    try {
        const response = await fetch(request);
        if (response.ok) {
            const cache = await caches.open(cacheName);
            cache.put(request, response.clone());
        }
        return response;
    } catch (error) {
        throw error;
    }
}

// Background Sync - Queue failed requests
self.addEventListener('sync', (event) => {
    console.log('[Service Worker] Background sync:', event.tag);

    if (event.tag === 'sync-messages') {
        event.waitUntil(syncMessages());
    }

    if (event.tag === 'sync-connections') {
        event.waitUntil(syncConnections());
    }
});

async function syncMessages() {
    try {
        // Get pending messages from IndexedDB
        const pendingMessages = await getPendingMessages();

        // Send each message
        for (const message of pendingMessages) {
            await fetch('/api/messages/send/', {
                method: 'POST',
                body: JSON.stringify(message),
                headers: { 'Content-Type': 'application/json' }
            });
        }

        // Clear pending messages
        await clearPendingMessages();
    } catch (error) {
        console.error('[Service Worker] Sync failed:', error);
    }
}

async function syncConnections() {
    // Similar implementation for connection requests
    console.log('[Service Worker] Syncing connections...');
}

// Push Notification Handler
self.addEventListener('push', (event) => {
    const data = event.data ? event.data.json() : {};
    const options = {
        body: data.body || 'New notification from Crush.lu',
        icon: data.icon || '/static/crush_lu/img/icons/icon-192x192.png',
        badge: '/static/crush_lu/img/icons/badge-72x72.png',
        vibrate: [200, 100, 200],
        tag: data.tag || 'crush-notification',
        data: {
            url: data.url || '/',
            timestamp: Date.now()
        },
        actions: [
            {
                action: 'open',
                title: 'Open',
                icon: '/static/crush_lu/img/icons/action-open.png'
            },
            {
                action: 'close',
                title: 'Dismiss',
                icon: '/static/crush_lu/img/icons/action-close.png'
            }
        ],
        requireInteraction: data.important || false
    };

    event.waitUntil(
        self.registration.showNotification(data.title || 'Crush.lu', options)
    );
});

// Notification Click Handler
self.addEventListener('notificationclick', (event) => {
    event.notification.close();

    if (event.action === 'close') {
        return;
    }

    const urlToOpen = event.notification.data.url;

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then((clientList) => {
                // Check if window is already open
                for (const client of clientList) {
                    if (client.url === urlToOpen && 'focus' in client) {
                        return client.focus();
                    }
                }
                // Open new window
                if (clients.openWindow) {
                    return clients.openWindow(urlToOpen);
                }
            })
    );
});

// Periodic Background Sync
self.addEventListener('periodicsync', (event) => {
    if (event.tag === 'check-new-events') {
        event.waitUntil(checkNewEvents());
    }
});

async function checkNewEvents() {
    try {
        const response = await fetch('/api/events/upcoming/');
        const events = await response.json();

        // Notify about new events
        if (events.new_count > 0) {
            self.registration.showNotification('New Events!', {
                body: `${events.new_count} new events are available`,
                icon: '/static/crush_lu/img/icons/icon-192x192.png'
            });
        }
    } catch (error) {
        console.error('[Service Worker] Event check failed:', error);
    }
}

// Helper functions for IndexedDB (for offline storage)
function getPendingMessages() {
    return new Promise((resolve, reject) => {
        // Implementation using IndexedDB
        resolve([]);
    });
}

function clearPendingMessages() {
    return new Promise((resolve, reject) => {
        // Implementation using IndexedDB
        resolve();
    });
}
```

### 3. Create Offline Page

Create `crush_lu/templates/crush_lu/offline.html`:

```html
{% extends "crush_lu/base.html" %}

{% block title %}You're Offline - Crush.lu{% endblock %}

{% block content %}
<div class="container text-center mt-5">
    <div class="row justify-content-center">
        <div class="col-md-6">
            <div class="offline-icon mb-4">
                <i class="bi bi-wifi-off" style="font-size: 6rem; color: #9B59B6;"></i>
            </div>

            <h1 class="mb-3">You're Offline</h1>

            <p class="lead text-muted mb-4">
                No internet connection detected. Some features may not be available.
            </p>

            <div class="card mb-4">
                <div class="card-body">
                    <h5 class="card-title">What you can still do:</h5>
                    <ul class="list-unstyled text-start">
                        <li><i class="bi bi-check-circle text-success"></i> View cached events</li>
                        <li><i class="bi bi-check-circle text-success"></i> Continue your journey</li>
                        <li><i class="bi bi-check-circle text-success"></i> Read your messages</li>
                        <li><i class="bi bi-check-circle text-success"></i> Draft new messages (sent when online)</li>
                    </ul>
                </div>
            </div>

            <button onclick="location.reload()" class="btn btn-crush-primary">
                <i class="bi bi-arrow-clockwise"></i> Try Again
            </button>

            <div class="mt-4">
                <small class="text-muted">
                    <i class="bi bi-info-circle"></i>
                    Your messages and actions will sync automatically when you're back online
                </small>
            </div>
        </div>
    </div>
</div>

<style>
.offline-icon {
    animation: pulse 2s infinite;
}

@keyframes pulse {
    0%, 100% {
        opacity: 1;
    }
    50% {
        opacity: 0.5;
    }
}
</style>
{% endblock %}
```

### 4. Add PWA View

Add to `crush_lu/views.py`:

```python
from django.shortcuts import render
from django.views.decorators.cache import cache_page

@cache_page(60 * 60 * 24)  # Cache for 24 hours
def offline(request):
    """Offline fallback page"""
    return render(request, 'crush_lu/offline.html')

def manifest(request):
    """Serve manifest.json"""
    return JsonResponse({
        "name": "Crush.lu - Privacy-First Dating",
        "short_name": "Crush.lu",
        "description": "Meet authentic people at real events in Luxembourg",
        "start_url": "/",
        "display": "standalone",
        "theme_color": "#9B59B6",
        "background_color": "#FFFFFF",
        # ... rest of manifest
    })
```

### 5. Add URL Patterns

Add to `crush_lu/urls.py`:

```python
urlpatterns = [
    # ... existing patterns

    # PWA endpoints
    path('manifest.json', views.manifest, name='manifest'),
    path('offline/', views.offline, name='offline'),
    path('sw.js', TemplateView.as_view(
        template_name='crush_lu/service-worker-advanced.js',
        content_type='application/javascript'
    ), name='service-worker'),
]
```

### 6. Update Base Template

Add to `crush_lu/templates/crush_lu/base.html` in the `<head>` section:

```html
{% load static %}

<!-- PWA Meta Tags -->
<meta name="theme-color" content="#9B59B6">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Crush.lu">
<meta name="mobile-web-app-capable" content="yes">

<!-- PWA Manifest -->
<link rel="manifest" href="{% static 'crush_lu/manifest.json' %}">

<!-- iOS Icons -->
<link rel="apple-touch-icon" sizes="180x180" href="{% static 'crush_lu/img/icons/apple-touch-icon.png' %}">
<link rel="apple-touch-icon" sizes="152x152" href="{% static 'crush_lu/img/icons/icon-152x152.png' %}">
<link rel="apple-touch-icon" sizes="144x144" href="{% static 'crush_lu/img/icons/icon-144x144.png' %}">

<!-- Favicon -->
<link rel="icon" type="image/png" sizes="32x32" href="{% static 'crush_lu/img/icons/favicon-32x32.png' %}">
<link rel="icon" type="image/png" sizes="16x16" href="{% static 'crush_lu/img/icons/favicon-16x16.png' %}">

<!-- iOS Splash Screens -->
<link rel="apple-touch-startup-image" href="{% static 'crush_lu/img/splash/splash-2048x2732.png' %}"
      media="(device-width: 1024px) and (device-height: 1366px) and (-webkit-device-pixel-ratio: 2)">
<link rel="apple-touch-startup-image" href="{% static 'crush_lu/img/splash/splash-1668x2388.png' %}"
      media="(device-width: 834px) and (device-height: 1194px) and (-webkit-device-pixel-ratio: 2)">
<link rel="apple-touch-startup-image" href="{% static 'crush_lu/img/splash/splash-1242x2688.png' %}"
      media="(device-width: 414px) and (device-height: 896px) and (-webkit-device-pixel-ratio: 3)">
<link rel="apple-touch-startup-image" href="{% static 'crush_lu/img/splash/splash-1125x2436.png' %}"
      media="(device-width: 375px) and (device-height: 812px) and (-webkit-device-pixel-ratio: 3)">
<link rel="apple-touch-startup-image" href="{% static 'crush_lu/img/splash/splash-828x1792.png' %}"
      media="(device-width: 414px) and (device-height: 896px) and (-webkit-device-pixel-ratio: 2)">
<link rel="apple-touch-startup-image" href="{% static 'crush_lu/img/splash/splash-750x1334.png' %}"
      media="(device-width: 375px) and (device-height: 667px) and (-webkit-device-pixel-ratio: 2)">
```

At the bottom before `</body>`:

```html
<!-- PWA Installation & Service Worker -->
<script>
// Register Service Worker
if ('serviceWorker' in navigator) {
    window.addEventListener('load', async () => {
        try {
            const registration = await navigator.serviceWorker.register('/static/crush_lu/js/service-worker-advanced.js');
            console.log('Service Worker registered:', registration);

            // Check for updates
            registration.addEventListener('updatefound', () => {
                const newWorker = registration.installing;
                newWorker.addEventListener('statechange', () => {
                    if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                        // New version available
                        showUpdateNotification();
                    }
                });
            });
        } catch (error) {
            console.error('Service Worker registration failed:', error);
        }
    });
}

// Show update notification
function showUpdateNotification() {
    const notification = document.createElement('div');
    notification.className = 'alert alert-info position-fixed bottom-0 end-0 m-3';
    notification.style.zIndex = '9999';
    notification.innerHTML = `
        <strong>Update Available!</strong>
        <button onclick="window.location.reload()" class="btn btn-sm btn-primary ms-2">
            Update Now
        </button>
        <button onclick="this.parentElement.remove()" class="btn-close ms-2"></button>
    `;
    document.body.appendChild(notification);
}

// PWA Install Prompt
let deferredPrompt;

window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;

    // Show install button
    showInstallButton();
});

function showInstallButton() {
    const installBtn = document.getElementById('pwa-install-btn');
    if (installBtn) {
        installBtn.style.display = 'block';
    }
}

async function installPWA() {
    if (!deferredPrompt) return;

    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;

    console.log(`User ${outcome === 'accepted' ? 'accepted' : 'dismissed'} the install prompt`);
    deferredPrompt = null;
}

// Network Status Detection
window.addEventListener('online', () => {
    document.getElementById('offline-banner')?.remove();
});

window.addEventListener('offline', () => {
    const banner = document.createElement('div');
    banner.id = 'offline-banner';
    banner.className = 'alert alert-warning position-fixed top-0 start-0 w-100 text-center m-0';
    banner.style.zIndex = '9999';
    banner.innerHTML = '<i class="bi bi-wifi-off"></i> You are offline. Some features may not work.';
    document.body.prepend(banner);
});
</script>
```

### 7. Create Install Button Component

Add to your navigation or homepage (`crush_lu/templates/crush_lu/home.html`):

```html
<!-- PWA Install Button -->
<button id="pwa-install-btn" class="btn btn-crush-primary"
        style="display: none;" onclick="installPWA()">
    <i class="bi bi-download"></i> Install App
</button>
```

### 8. Create App Icons

You'll need to generate icons in various sizes. Use a tool or script:

**Icon Sizes Needed**:
- 72x72
- 96x96
- 128x128
- 144x144
- 152x152 (iOS)
- 180x180 (iOS)
- 192x192
- 384x384
- 512x512
- Maskable versions (safe area for Android adaptive icons)

**Generate with ImageMagick** (if you have a 512x512 source):

```bash
# Create icons directory
mkdir -p static/crush_lu/img/icons

# Generate all sizes
for size in 72 96 128 144 152 180 192 384 512; do
    convert source-icon-512x512.png \
        -resize ${size}x${size} \
        static/crush_lu/img/icons/icon-${size}x${size}.png
done

# Generate maskable icons (with padding)
for size in 192 512; do
    convert source-icon-512x512.png \
        -background transparent \
        -gravity center \
        -extent ${size}x${size} \
        static/crush_lu/img/icons/icon-maskable-${size}x${size}.png
done
```

Or use online tools:
- https://realfavicongenerator.net/
- https://www.pwabuilder.com/imageGenerator

### 9. Create Splash Screens (iOS)

Generate splash screens for iOS devices:

```bash
# iPhone XS Max, 11 Pro Max
convert splash-source.png -resize 1242x2688 splash-1242x2688.png

# iPhone XR, 11
convert splash-source.png -resize 828x1792 splash-828x1792.png

# iPhone X, XS, 11 Pro
convert splash-source.png -resize 1125x2436 splash-1125x2436.png

# iPhone 8 Plus
convert splash-source.png -resize 1242x2208 splash-1242x2208.png

# iPhone 8
convert splash-source.png -resize 750x1334 splash-750x1334.png

# iPad Pro 12.9"
convert splash-source.png -resize 2048x2732 splash-2048x2732.png
```

### 10. Add Django PWA Package (Optional)

For easier management, use `django-pwa`:

```bash
pip install django-pwa==1.1.0
```

Add to `settings.py`:

```python
INSTALLED_APPS = [
    # ... other apps
    'pwa',
]

PWA_APP_NAME = 'Crush.lu'
PWA_APP_DESCRIPTION = 'Privacy-first dating in Luxembourg'
PWA_APP_THEME_COLOR = '#9B59B6'
PWA_APP_BACKGROUND_COLOR = '#FFFFFF'
PWA_APP_DISPLAY = 'standalone'
PWA_APP_SCOPE = '/'
PWA_APP_ORIENTATION = 'portrait-primary'
PWA_APP_START_URL = '/'
PWA_APP_ICONS = [
    {
        'src': '/static/crush_lu/img/icons/icon-192x192.png',
        'sizes': '192x192'
    },
    {
        'src': '/static/crush_lu/img/icons/icon-512x512.png',
        'sizes': '512x512'
    }
]
PWA_SERVICE_WORKER_PATH = '/static/crush_lu/js/service-worker-advanced.js'
```

### 11. IndexedDB for Offline Storage

Create `static/crush_lu/js/offline-storage.js`:

```javascript
// IndexedDB wrapper for offline data storage

class OfflineStorage {
    constructor(dbName = 'crush-offline-db', version = 1) {
        this.dbName = dbName;
        this.version = version;
        this.db = null;
    }

    async init() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(this.dbName, this.version);

            request.onerror = () => reject(request.error);
            request.onsuccess = () => {
                this.db = request.result;
                resolve(this.db);
            };

            request.onupgradeneeded = (event) => {
                const db = event.target.result;

                // Create object stores
                if (!db.objectStoreNames.contains('messages')) {
                    const messageStore = db.createObjectStore('messages', {
                        keyPath: 'id',
                        autoIncrement: true
                    });
                    messageStore.createIndex('timestamp', 'timestamp', { unique: false });
                    messageStore.createIndex('synced', 'synced', { unique: false });
                }

                if (!db.objectStoreNames.contains('events')) {
                    const eventStore = db.createObjectStore('events', {
                        keyPath: 'id'
                    });
                    eventStore.createIndex('date', 'date_time', { unique: false });
                }

                if (!db.objectStoreNames.contains('connections')) {
                    db.createObjectStore('connections', { keyPath: 'id' });
                }

                if (!db.objectStoreNames.contains('journey')) {
                    db.createObjectStore('journey', { keyPath: 'id' });
                }
            };
        });
    }

    async saveMessage(message) {
        const transaction = this.db.transaction(['messages'], 'readwrite');
        const store = transaction.objectStore('messages');

        const data = {
            ...message,
            timestamp: Date.now(),
            synced: false
        };

        return store.add(data);
    }

    async getPendingMessages() {
        const transaction = this.db.transaction(['messages'], 'readonly');
        const store = transaction.objectStore('messages');
        const index = store.index('synced');

        return new Promise((resolve, reject) => {
            const request = index.getAll(false);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    async markMessagesSynced(messageIds) {
        const transaction = this.db.transaction(['messages'], 'readwrite');
        const store = transaction.objectStore('messages');

        for (const id of messageIds) {
            const request = store.get(id);
            request.onsuccess = () => {
                const message = request.result;
                if (message) {
                    message.synced = true;
                    store.put(message);
                }
            };
        }
    }

    async cacheEvents(events) {
        const transaction = this.db.transaction(['events'], 'readwrite');
        const store = transaction.objectStore('events');

        for (const event of events) {
            store.put(event);
        }
    }

    async getCachedEvents() {
        const transaction = this.db.transaction(['events'], 'readonly');
        const store = transaction.objectStore('events');

        return new Promise((resolve, reject) => {
            const request = store.getAll();
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }
}

// Initialize and export
const offlineStorage = new OfflineStorage();
offlineStorage.init().catch(console.error);
```

### 12. Background Sync Registration

Add to your message sending code:

```javascript
// When sending a message offline
async function sendMessage(messageData) {
    if (!navigator.onLine) {
        // Save to IndexedDB
        await offlineStorage.saveMessage(messageData);

        // Register background sync
        if ('sync' in serviceWorkerRegistration) {
            await serviceWorkerRegistration.sync.register('sync-messages');
        }

        showNotification('Message will be sent when you\'re back online');
        return;
    }

    // Send immediately if online
    const response = await fetch('/api/messages/send/', {
        method: 'POST',
        body: JSON.stringify(messageData)
    });

    return response.json();
}
```

### 13. Testing PWA

#### Test Locally:

1. **Chrome DevTools**:
   - Open DevTools (F12)
   - Go to "Application" tab
   - Check "Service Workers", "Manifest", "Cache Storage"

2. **Lighthouse Audit**:
   - DevTools > Lighthouse
   - Generate report for "Progressive Web App"
   - Aim for score > 90

3. **Test Offline**:
   - DevTools > Network tab
   - Check "Offline"
   - Navigate app and verify functionality

#### Test on Mobile:

1. **Android Chrome**:
   - Visit https://crush.lu
   - Chrome will show "Add to Home Screen" banner
   - Install and test

2. **iOS Safari**:
   - Visit https://crush.lu
   - Tap Share button
   - Tap "Add to Home Screen"
   - Install and test

### 14. PWA Analytics

Add to Google Analytics or your analytics platform:

```javascript
// Track PWA installations
window.addEventListener('appinstalled', (event) => {
    console.log('PWA installed');

    // Track in analytics
    gtag('event', 'pwa_install', {
        'event_category': 'PWA',
        'event_label': 'User installed PWA'
    });
});

// Track PWA usage
if (window.matchMedia('(display-mode: standalone)').matches) {
    gtag('event', 'pwa_launch', {
        'event_category': 'PWA',
        'event_label': 'User launched PWA from home screen'
    });
}
```

### 15. App Store Submission (Optional)

Package your PWA for app stores using:

**Google Play Store**:
- Use TWA (Trusted Web Activities)
- Tool: https://www.pwabuilder.com/

**Apple App Store**:
- Requires native wrapper
- Consider using PWA to iOS converter

## PWA Features Checklist

✅ **Core Requirements**:
- [ ] HTTPS enabled
- [ ] Web App Manifest
- [ ] Service Worker
- [ ] Installable
- [ ] Works offline

✅ **Enhanced Features**:
- [ ] Push Notifications
- [ ] Background Sync
- [ ] Periodic Background Sync
- [ ] Share Target API
- [ ] Shortcuts
- [ ] Screenshots

✅ **Performance**:
- [ ] Fast load time (< 3s)
- [ ] Smooth animations (60fps)
- [ ] Responsive design
- [ ] Optimized images

✅ **UX**:
- [ ] App-like navigation
- [ ] Offline page
- [ ] Loading states
- [ ] Update prompts
- [ ] Install prompts

## Browser Support

| Feature | Chrome | Firefox | Safari | Edge |
|---------|--------|---------|--------|------|
| Service Worker | ✅ | ✅ | ✅ | ✅ |
| Manifest | ✅ | ✅ | ✅ | ✅ |
| Install Prompt | ✅ | ❌ | ✅ (iOS 16.4+) | ✅ |
| Background Sync | ✅ | ❌ | ❌ | ✅ |
| Periodic Sync | ✅ | ❌ | ❌ | ❌ |
| Push Notifications | ✅ | ✅ | ✅ (iOS 16.4+) | ✅ |

## Next Steps

1. Create manifest.json
2. Generate all required icons and splash screens
3. Implement service worker with caching strategies
4. Create offline page
5. Add installation prompt
6. Test thoroughly on mobile devices
7. Run Lighthouse audit
8. Deploy to production
9. Monitor PWA analytics

## Resources

- [PWA Builder](https://www.pwabuilder.com/)
- [Google PWA Guide](https://web.dev/progressive-web-apps/)
- [MDN PWA Documentation](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps)
- [Workbox (Google's PWA library)](https://developers.google.com/web/tools/workbox)
