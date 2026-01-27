/**
 * Crush.lu Service Worker Registration
 * Handles PWA installation, updates, and network status detection
 *
 * This module registers the Workbox-based service worker and provides:
 * - Service worker registration and update handling
 * - PWA install prompt management
 * - Network status detection (online/offline)
 * - Server unreachable banner with auto-reconnect
 */

(function() {
    'use strict';

    // ========================================================================
    // Service Worker Registration
    // ========================================================================

    if ('serviceWorker' in navigator) {
        window.addEventListener('load', function() {
            // Register from root path to control entire site
            navigator.serviceWorker.register('/sw-workbox.js', { scope: '/' })
                .then(function(registration) {
                    // Check for updates (handled by pwa-update.js which shows banner)
                    registration.addEventListener('updatefound', function() {
                        // Update will be handled by pwa-update.js
                    });
                })
                .catch(function(error) {
                    // Silently fail
                });

            // Handle service worker updates - reload when new SW takes control
            var refreshing = false;
            navigator.serviceWorker.addEventListener('controllerchange', function() {
                if (!refreshing) {
                    refreshing = true;
                    window.location.reload();
                }
            });
        });
    }

    // ========================================================================
    // PWA Install Prompt Handler
    // ========================================================================

    var deferredPrompt;
    window.addEventListener('beforeinstallprompt', function(e) {
        // Prevent the mini-infobar from appearing on mobile
        e.preventDefault();
        // Stash the event so it can be triggered later
        deferredPrompt = e;
    });

    window.addEventListener('appinstalled', function() {
        deferredPrompt = null;
    });

    // ========================================================================
    // Network Status Detection - Auto-reload when back online
    // ========================================================================

    var wasOffline = false;
    var serverCheckInterval = null;

    // Function to check if server is reachable
    function checkServerConnection() {
        return new Promise(function(resolve) {
            // BYPASS service worker by adding timestamp - forces network request
            var timestamp = new Date().getTime();
            fetch('/healthz/?_=' + timestamp, {
                method: 'GET',
                cache: 'no-store',
                headers: {
                    'Cache-Control': 'no-cache, no-store, must-revalidate',
                    'Pragma': 'no-cache'
                }
            })
            .then(function(response) {
                resolve(response.ok);
            })
            .catch(function(e) {
                resolve(false);
            });
        });
    }

    // Detect when going offline (network interface down)
    window.addEventListener('offline', function() {
        wasOffline = true;
    });

    // Detect when coming back online (network interface up)
    window.addEventListener('online', function() {
        if (wasOffline) {
            // Only reload if we were previously offline
            setTimeout(function() {
                window.location.reload();
            }, 500); // Small delay to ensure connection is stable
        }

        wasOffline = false;
        hideServerUnreachableBanner();
    });

    // ========================================================================
    // Server Unreachable Banner
    // ========================================================================

    function showServerUnreachableBanner() {
        if (document.querySelector('.server-unreachable-banner')) return;

        var banner = document.createElement('div');
        banner.className = 'server-unreachable-banner';
        banner.innerHTML =
            '<span class="server-unreachable-icon">&#x1F4E1;</span>' +
            '<span class="server-unreachable-text">Server unreachable - retrying...</span>' +
            '<span class="server-unreachable-spinner"></span>';
        document.body.prepend(banner);

        // Inject styles if not present
        if (!document.getElementById('server-unreachable-styles')) {
            var styles = document.createElement('style');
            styles.id = 'server-unreachable-styles';
            styles.textContent =
                '.server-unreachable-banner {' +
                    'position: fixed;' +
                    'bottom: 0;' +
                    'left: 0;' +
                    'right: 0;' +
                    'z-index: 9998;' +
                    'background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);' +
                    'color: white;' +
                    'padding: 10px 16px;' +
                    'display: flex;' +
                    'align-items: center;' +
                    'justify-content: center;' +
                    'gap: 10px;' +
                    'font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;' +
                    'font-size: 14px;' +
                    'font-weight: 500;' +
                    'box-shadow: 0 -2px 10px rgba(0,0,0,0.2);' +
                '}' +
                '.server-unreachable-icon {' +
                    'font-size: 1.2em;' +
                '}' +
                '.server-unreachable-spinner {' +
                    'width: 16px;' +
                    'height: 16px;' +
                    'border: 2px solid rgba(255,255,255,0.3);' +
                    'border-top-color: white;' +
                    'border-radius: 50%;' +
                    'animation: sw-spin 1s linear infinite;' +
                '}' +
                '@keyframes sw-spin {' +
                    'to { transform: rotate(360deg); }' +
                '}';
            document.head.appendChild(styles);
        }
    }

    function hideServerUnreachableBanner() {
        var banner = document.querySelector('.server-unreachable-banner');
        if (banner) banner.remove();
    }

    // ========================================================================
    // Service Worker Message Handler (for offline detection)
    // ========================================================================

    if (navigator.serviceWorker) {
        navigator.serviceWorker.addEventListener('message', function(event) {
            if (event.data && event.data.type === 'SERVER_UNREACHABLE') {
                if (!wasOffline) {
                    wasOffline = true;
                    showServerUnreachableBanner();

                    // Start polling to detect when server comes back
                    if (serverCheckInterval) clearInterval(serverCheckInterval);
                    serverCheckInterval = setInterval(function() {
                        checkServerConnection().then(function(isOnline) {
                            if (isOnline) {
                                clearInterval(serverCheckInterval);
                                hideServerUnreachableBanner();
                                wasOffline = false;
                                window.location.reload();
                            }
                        });
                    }, 5000); // Check every 5 seconds
                }
            }
        });
    }

    // ========================================================================
    // Initial Network Status Check
    // ========================================================================

    if (!navigator.onLine) {
        wasOffline = true;
    }

})();
