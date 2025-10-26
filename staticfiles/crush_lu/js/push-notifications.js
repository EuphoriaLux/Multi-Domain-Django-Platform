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
                    error: 'Permission denied'
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
                    deviceName: getDeviceName()
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
            return {
                success: false,
                error: error.message
            };
        }
    }

    /**
     * Unsubscribe from push notifications
     */
    async function unsubscribeFromPush() {
        try {
            const registration = await navigator.serviceWorker.ready;
            const subscription = await registration.pushManager.getSubscription();

            if (!subscription) {
                console.log('[Push] No subscription found');
                return { success: true };
            }

            // Unsubscribe from browser
            const unsubscribed = await subscription.unsubscribe();

            if (unsubscribed) {
                // Remove from server
                const response = await fetch('/api/push/unsubscribe/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCookie('csrftoken')
                    },
                    body: JSON.stringify({
                        endpoint: subscription.endpoint
                    })
                });

                const data = await response.json();
                console.log('[Push] Unsubscribed:', data);
                return { success: true };
            }

            return {
                success: false,
                error: 'Failed to unsubscribe'
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
        isSupported: isPushSupported
    };

    console.log('[Push] Push notification manager initialized');

})();
