/**
 * Service Worker Unregister (Development Only)
 * Removes any existing service workers to prevent caching issues during development.
 */
(function() {
    'use strict';

    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.getRegistrations().then(function(registrations) {
            registrations.forEach(function(registration) {
                registration.unregister();
            });
        });
    }
})();
