/**
 * Crush.lu PWA Mode Detector
 * Detects if user is running the app in standalone mode (PWA installed)
 * and sends a header with requests to track PWA usage
 */

(function() {
    'use strict';

    /**
     * Detect if app is running in standalone mode (PWA)
     */
    function isPWAMode() {
        // Method 1: Check display-mode media query (most reliable)
        if (window.matchMedia('(display-mode: standalone)').matches) {
            return true;
        }

        // Method 2: Check navigator.standalone (iOS Safari)
        if (window.navigator.standalone === true) {
            return true;
        }

        // Method 3: Check if opened from home screen (Android)
        if (document.referrer.includes('android-app://')) {
            return true;
        }

        return false;
    }

    // Detect PWA mode on load
    const isRunningAsPWA = isPWAMode();

    if (isRunningAsPWA) {
        console.log('[PWA] Running in standalone mode (installed PWA)');

        // Store PWA status in sessionStorage
        sessionStorage.setItem('isPWA', 'true');

        // Add custom header to all fetch requests to track PWA usage
        const originalFetch = window.fetch;
        window.fetch = function(...args) {
            // Add custom header
            if (args[1]) {
                args[1].headers = args[1].headers || {};
                if (args[1].headers instanceof Headers) {
                    args[1].headers.append('X-Requested-With', 'Crush-PWA');
                } else {
                    args[1].headers['X-Requested-With'] = 'Crush-PWA';
                }
            } else {
                args[1] = {
                    headers: {
                        'X-Requested-With': 'Crush-PWA'
                    }
                };
            }

            return originalFetch.apply(this, args);
        };

        // Log PWA usage (analytics)
        if (typeof gtag !== 'undefined') {
            gtag('event', 'pwa_usage', {
                'event_category': 'PWA',
                'event_label': 'Standalone Mode Detected'
            });
        }

    } else {
        console.log('[PWA] Running in browser mode');
        sessionStorage.setItem('isPWA', 'false');
    }

    // Expose detection function globally
    window.CrushPWA = {
        isStandalone: isRunningAsPWA,
        isPWAInstalled: function() {
            return isRunningAsPWA;
        },
        getDisplayMode: function() {
            if (isPWAMode()) return 'standalone';
            if (window.matchMedia('(display-mode: fullscreen)').matches) return 'fullscreen';
            if (window.matchMedia('(display-mode: minimal-ui)').matches) return 'minimal-ui';
            return 'browser';
        }
    };

    console.log('[PWA] Display mode:', window.CrushPWA.getDisplayMode());

})();
