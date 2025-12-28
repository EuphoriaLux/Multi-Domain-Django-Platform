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

        // Mark user as PWA user in the backend (enables push notification UI)
        markPWAUser();

    } else {
        console.log('[PWA] Running in browser mode');
        sessionStorage.setItem('isPWA', 'false');
    }

    /**
     * Mark the current user as a PWA user in the backend
     * This enables push notification prompts in the UI
     */
    async function markPWAUser() {
        // Only mark if not already marked in this session
        if (sessionStorage.getItem('pwaUserMarked') === 'true') {
            console.log('[PWA] User already marked as PWA user this session');
            return;
        }

        try {
            // Get CSRF token from cookie
            const csrfToken = getCookie('csrftoken');
            if (!csrfToken) {
                console.warn('[PWA] No CSRF token available, skipping PWA user marking');
                return;
            }

            const response = await fetch('/api/push/mark-pwa-user/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken,
                    'X-Requested-With': 'Crush-PWA'
                },
                credentials: 'same-origin'
            });

            if (response.ok) {
                const data = await response.json();
                if (data.success) {
                    console.log('[PWA] User marked as PWA user successfully');
                    sessionStorage.setItem('pwaUserMarked', 'true');
                }
            } else if (response.status === 404) {
                // User doesn't have a profile yet, that's OK
                console.log('[PWA] User has no profile, cannot mark as PWA user');
            } else {
                console.warn('[PWA] Failed to mark PWA user:', response.status);
            }
        } catch (error) {
            console.error('[PWA] Error marking PWA user:', error);
        }
    }

    /**
     * Get cookie value by name
     */
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
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
