/**
 * Crush.lu Push Notification Manager
 * Handles push notification subscription for PWA
 */

(function() {
    'use strict';

    // Check if push notifications are supported
    const isPushSupported = 'serviceWorker' in navigator && 'PushManager' in window;

    if (!isPushSupported) {
        console.log('[Push] Push notifications not supported in this browser');
        return;
    }

    // CSRF token helper
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

    // Convert base64 VAPID key to Uint8Array
    function urlBase64ToUint8Array(base64String) {
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding)
            .replace(/\-/g, '+')
            .replace(/_/g, '/');

        const rawData = window.atob(base64);
        const outputArray = new Uint8Array(rawData.length);

        for (let i = 0; i < rawData.length; ++i) {
            outputArray[i] = rawData.charCodeAt(i);
        }
        return outputArray;
    }

    // Detect device name from user agent
    function getDeviceName() {
        const ua = navigator.userAgent;
        if (/Android/i.test(ua)) {
            return 'Android Chrome';
        } else if (/iPhone|iPad|iPod/i.test(ua)) {
            return 'iPhone Safari';
        } else if (/Windows/i.test(ua)) {
            return 'Windows Desktop';
        } else if (/Macintosh/i.test(ua)) {
            return 'Mac Desktop';
        } else if (/Linux/i.test(ua)) {
            return 'Linux Desktop';
        }
        return 'Unknown Device';
    }

    /**
     * Generate a stable browser fingerprint from hardware/software characteristics.
     * This fingerprint persists across sessions and is used to identify the same
     * physical device even when the push endpoint changes (e.g., after logout/login).
     */
    function generateDeviceFingerprint() {
        // Collect stable browser characteristics
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
            // Canvas fingerprint for additional uniqueness
            getCanvasFingerprint()
        ];

        // Create hash from components
        var str = components.join('|');
        return simpleHash(str);
    }

    /**
     * Get a canvas-based fingerprint component.
     * Different GPUs/drivers render text slightly differently.
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
     * Simple string hash function (djb2 algorithm).
     * Returns a hex string for the fingerprint.
     */
    function simpleHash(str) {
        var hash = 5381;
        for (var i = 0; i < str.length; i++) {
            hash = ((hash << 5) + hash) + str.charCodeAt(i);
            hash = hash & hash;  // Convert to 32-bit integer
        }
        // Convert to hex and ensure positive, pad to 8 chars
        return Math.abs(hash).toString(16).padStart(8, '0');
    }

    /**
     * Request notification permission and subscribe to push
     */
    async function subscribeToPush() {
        try {
            // Step 1: Request notification permission
            const permission = await Notification.requestPermission();

            if (permission !== 'granted') {
                console.log('[Push] Notification permission denied');
                return {
                    success: false,
                    error: gettext('Permission denied')
                };
            }

            console.log('[Push] Notification permission granted');

            // Step 2: Get service worker registration
            const registration = await navigator.serviceWorker.ready;

            // Step 3: Get VAPID public key from server
            const vapidResponse = await fetch('/api/push/vapid-public-key/');
            const vapidData = await vapidResponse.json();

            if (!vapidData.success) {
                console.error('[Push] Failed to get VAPID key:', vapidData.error);
                return {
                    success: false,
                    error: vapidData.error
                };
            }

            const vapidPublicKey = vapidData.publicKey;
            const convertedKey = urlBase64ToUint8Array(vapidPublicKey);

            // Step 4: Subscribe to push manager
            const subscription = await registration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: convertedKey
            });

            console.log('[Push] Push subscription created:', subscription);

            // Step 5: Send subscription to server
            const response = await fetch('/api/push/subscribe/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify({
                    endpoint: subscription.endpoint,
                    keys: {
                        p256dh: btoa(String.fromCharCode.apply(null, new Uint8Array(subscription.getKey('p256dh')))),
                        auth: btoa(String.fromCharCode.apply(null, new Uint8Array(subscription.getKey('auth'))))
                    },
                    userAgent: navigator.userAgent,
                    deviceName: getDeviceName(),
                    deviceFingerprint: generateDeviceFingerprint()
                })
            });

            const data = await response.json();

            if (data.success) {
                console.log('[Push] Subscription saved to server');
                return {
                    success: true,
                    subscription: subscription
                };
            } else {
                console.error('[Push] Failed to save subscription:', data.error);
                return {
                    success: false,
                    error: data.error
                };
            }

        } catch (error) {
            console.error('[Push] Error subscribing to push:', error);

            // Provide more helpful error messages for common issues
            var errorMessage = error.message;
            if (error.name === 'AbortError') {
                // This typically means the VAPID key is invalid or push service is unavailable
                errorMessage = gettext('Push service error. This may be due to server misconfiguration or network issues. Please try again later.');
                console.error('[Push] AbortError details: VAPID key may be invalid or push service unavailable');
            } else if (error.name === 'NotAllowedError') {
                errorMessage = gettext('Push notifications are blocked. Please enable them in your browser settings.');
            } else if (error.name === 'InvalidStateError') {
                errorMessage = gettext('Push subscription already exists or is in an invalid state.');
            }

            return {
                success: false,
                error: errorMessage
            };
        }
    }

    /**
     * Unsubscribe from push notifications
     * Checks if coach push subscription exists before removing browser subscription
     */
    async function unsubscribeFromPush() {
        try {
            const registration = await navigator.serviceWorker.ready;
            const subscription = await registration.pushManager.getSubscription();

            if (!subscription) {
                console.log('[Push] No subscription found');
                return { success: true };
            }

            // Remove from server first to check if browser subscription should be kept
            // Include fingerprint so server can check for coach subscriptions with different endpoint
            const response = await fetch('/api/push/unsubscribe/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify({
                    endpoint: subscription.endpoint,
                    deviceFingerprint: generateDeviceFingerprint()
                })
            });

            const data = await response.json();

            if (data.success) {
                // Only unsubscribe browser if no other system needs it
                if (!data.keep_browser_subscription) {
                    await subscription.unsubscribe();
                    console.log('[Push] Unsubscribed from browser');
                } else {
                    console.log('[Push] Keeping browser subscription for coach push');
                }
                console.log('[Push] Unsubscribed:', data);
                return { success: true };
            }

            return {
                success: false,
                error: data.error || gettext('Failed to unsubscribe')
            };

        } catch (error) {
            console.error('[Push] Error unsubscribing:', error);
            return {
                success: false,
                error: error.message
            };
        }
    }

    /**
     * Check if user is currently subscribed
     */
    async function isSubscribed() {
        try {
            const registration = await navigator.serviceWorker.ready;
            const subscription = await registration.pushManager.getSubscription();
            return subscription !== null;
        } catch (error) {
            console.error('[Push] Error checking subscription:', error);
            return false;
        }
    }

    /**
     * Send test notification
     */
    async function sendTestNotification() {
        try {
            const response = await fetch('/api/push/test/', {
                method: 'POST',
                headers: {
                    'X-CSRFToken': getCookie('csrftoken')
                }
            });

            const data = await response.json();
            return data;

        } catch (error) {
            console.error('[Push] Error sending test notification:', error);
            return {
                success: false,
                error: error.message
            };
        }
    }

    // Expose public API
    window.CrushPush = {
        subscribe: subscribeToPush,
        unsubscribe: unsubscribeFromPush,
        isSubscribed: isSubscribed,
        sendTest: sendTestNotification,
        isSupported: isPushSupported,
        getFingerprint: generateDeviceFingerprint
    };

    console.log('[Push] Push notification manager initialized');

})();
