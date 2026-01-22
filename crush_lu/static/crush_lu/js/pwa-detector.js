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

    // ========================================================================
    // Device Detection Functions
    // ========================================================================

    /**
     * Detect operating system from userAgent and userAgentData
     */
    function detectOS() {
        // Modern API (Chrome 90+)
        if (navigator.userAgentData && navigator.userAgentData.platform) {
            var platform = navigator.userAgentData.platform.toLowerCase();
            if (platform.includes('android')) return 'android';
            if (platform.includes('ios') || platform === 'iphone' || platform === 'ipad') return 'ios';
            if (platform.includes('windows')) return 'windows';
            if (platform.includes('mac') || platform.includes('macos')) return 'macos';
            if (platform.includes('linux')) return 'linux';
            if (platform.includes('chrome os') || platform.includes('chromeos')) return 'chromeos';
        }

        // Fallback to userAgent parsing
        var ua = navigator.userAgent.toLowerCase();
        if (/iphone|ipad|ipod/.test(ua)) return 'ios';
        if (/android/.test(ua)) return 'android';
        if (/windows/.test(ua)) return 'windows';
        if (/macintosh|mac os/.test(ua)) return 'macos';
        if (/cros/.test(ua)) return 'chromeos';
        if (/linux/.test(ua)) return 'linux';

        return 'unknown';
    }

    /**
     * Detect form factor (phone, tablet, desktop)
     */
    function detectFormFactor() {
        var ua = navigator.userAgent.toLowerCase();
        var minDimension = Math.min(window.screen.width, window.screen.height);

        var isMobileUA = /mobile|android.*mobile|iphone|ipod|blackberry|opera mini|iemobile/i.test(ua);
        var isTabletUA = /ipad|android(?!.*mobile)|tablet/i.test(ua);

        if (isMobileUA) return 'phone';
        if (isTabletUA) return 'tablet';
        if (minDimension <= 480) return 'phone';
        if (minDimension <= 1024 && 'ontouchstart' in window) return 'tablet';

        return 'desktop';
    }

    /**
     * Detect browser name
     */
    function detectBrowser() {
        var ua = navigator.userAgent;
        if (/Edg\//.test(ua)) return 'Edge';
        if (/OPR\/|Opera/.test(ua)) return 'Opera';
        if (/Firefox\//.test(ua)) return 'Firefox';
        if (/SamsungBrowser\//.test(ua)) return 'Samsung Internet';
        if (/Chrome\//.test(ua) && !/Edg\//.test(ua)) return 'Chrome';
        if (/Safari\//.test(ua) && !/Chrome\//.test(ua)) return 'Safari';
        return 'Unknown';
    }

    /**
     * Build device category string (e.g., "Android Phone", "Windows Desktop")
     */
    function getDeviceCategory(os, formFactor) {
        var osNames = {
            'ios': 'iOS', 'android': 'Android', 'windows': 'Windows',
            'macos': 'macOS', 'linux': 'Linux', 'chromeos': 'ChromeOS', 'unknown': 'Unknown'
        };
        var formNames = {
            'phone': 'Phone', 'tablet': 'Tablet', 'desktop': 'Desktop', 'unknown': 'Device'
        };

        // Special cases for better readability
        if (os === 'ios' && formFactor === 'phone') return 'iPhone';
        if (os === 'ios' && formFactor === 'tablet') return 'iPad';

        return osNames[os] + ' ' + formNames[formFactor];
    }

    // ========================================================================
    // Device Fingerprint Generation (matches push-notifications.js pattern)
    // ========================================================================

    /**
     * Generate a stable device fingerprint
     */
    function generateDeviceFingerprint() {
        var components = [
            screen.width,
            screen.height,
            window.devicePixelRatio || 1,
            new Date().getTimezoneOffset(),
            navigator.language || '',
            navigator.platform || '',
            navigator.hardwareConcurrency || 0,
            navigator.deviceMemory || 0,
            ('ontouchstart' in window) ? 1 : 0,
            screen.colorDepth || 0,
            getCanvasFingerprint()
        ];
        return simpleHash(components.join('|'));
    }

    /**
     * Get canvas-based fingerprint
     */
    function getCanvasFingerprint() {
        try {
            var canvas = document.createElement('canvas');
            canvas.width = 200;
            canvas.height = 50;
            var ctx = canvas.getContext('2d');
            ctx.textBaseline = 'top';
            ctx.font = '14px Arial';
            ctx.fillStyle = '#f60';
            ctx.fillRect(125, 1, 62, 20);
            ctx.fillStyle = '#069';
            ctx.fillText('Crush.lu PWA', 2, 15);
            ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
            ctx.fillText('Crush.lu PWA', 4, 17);
            return canvas.toDataURL().slice(-50);
        } catch (e) {
            return 'no-canvas';
        }
    }

    /**
     * Simple hash function (djb2)
     */
    function simpleHash(str) {
        var hash = 5381;
        for (var i = 0; i < str.length; i++) {
            hash = ((hash << 5) + hash) + str.charCodeAt(i);
            hash = hash & hash; // Convert to 32-bit integer
        }
        return Math.abs(hash).toString(16).padStart(8, '0');
    }

    // ========================================================================
    // PWA Installation Registration
    // ========================================================================

    /**
     * Register PWA installation with device info to backend
     */
    async function registerPWAInstallation() {
        // Only register if running as PWA
        if (!isPWAMode()) {
            return;
        }

        // Only register once per session
        if (sessionStorage.getItem('pwaInstallationRegistered') === 'true') {
            console.log('[PWA] Installation already registered this session');
            return;
        }

        // Get CSRF token
        var csrfToken = getCookie('csrftoken');
        if (!csrfToken) {
            console.warn('[PWA] No CSRF token available, skipping installation registration');
            return;
        }

        var os = detectOS();
        var formFactor = detectFormFactor();
        var fingerprint = generateDeviceFingerprint();

        try {
            var response = await fetch('/api/pwa/register-installation/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken,
                    'X-Requested-With': 'Crush-PWA'
                },
                credentials: 'same-origin',
                body: JSON.stringify({
                    deviceFingerprint: fingerprint,
                    osType: os,
                    formFactor: formFactor,
                    browser: detectBrowser(),
                    deviceCategory: getDeviceCategory(os, formFactor),
                    userAgent: navigator.userAgent
                })
            });

            if (response.ok) {
                var data = await response.json();
                if (data.success) {
                    console.log('[PWA] Installation registered:', data.deviceCategory, '(' + data.message + ')');
                    sessionStorage.setItem('pwaInstallationRegistered', 'true');
                }
            } else if (response.status === 403) {
                // User not logged in, that's OK
                console.log('[PWA] User not logged in, cannot register installation');
            } else {
                console.warn('[PWA] Failed to register installation:', response.status);
            }
        } catch (error) {
            console.error('[PWA] Error registering installation:', error);
        }
    }

    // Register installation after marking PWA user (if running as PWA)
    if (isRunningAsPWA) {
        // Small delay to ensure markPWAUser runs first
        setTimeout(registerPWAInstallation, 500);
    }

    // ========================================================================
    // Global API Exposure
    // ========================================================================

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
        },
        // Expose device detection for external use
        detectOS: detectOS,
        detectFormFactor: detectFormFactor,
        detectBrowser: detectBrowser,
        getDeviceCategory: function() {
            return getDeviceCategory(detectOS(), detectFormFactor());
        },
        getDeviceFingerprint: generateDeviceFingerprint
    };

    console.log('[PWA] Display mode:', window.CrushPWA.getDisplayMode());
    if (isRunningAsPWA) {
        console.log('[PWA] Device:', getDeviceCategory(detectOS(), detectFormFactor()));
    }

})();
