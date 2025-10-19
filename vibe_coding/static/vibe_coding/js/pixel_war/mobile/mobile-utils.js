/**
 * Mobile UX Utility Functions
 * Shared utilities for mobile Pixel War functionality
 */

/**
 * Triggers haptic feedback on supported devices
 * @param {string} type - Type of haptic feedback: 'light', 'medium', or 'heavy'
 */
export function triggerHapticFeedback(type = 'light') {
    if ('vibrate' in navigator) {
        try {
            switch(type) {
                case 'light':
                    navigator.vibrate(10);
                    break;
                case 'medium':
                    navigator.vibrate(20);
                    break;
                case 'heavy':
                    navigator.vibrate([30, 10, 30]);
                    break;
            }
        } catch (error) {
            // Silently handle vibration errors (user interaction required, etc.)
            console.debug('Haptic feedback not available:', error.message);
        }
    }
}

/**
 * Determines the device type based on viewport width
 * @returns {string} Device type: 'mobile', 'tablet', or 'desktop'
 */
export function getDeviceType() {
    const width = window.innerWidth;
    if (width < 768) return 'mobile';
    if (width < 1024) return 'tablet';
    return 'desktop';
}

/**
 * Checks if the current device is mobile or has touch support
 * @returns {boolean} True if mobile or touch device
 */
export function isMobileDevice() {
    return window.innerWidth < 768 || 'ontouchstart' in window;
}

/**
 * Tracks analytics events
 * @param {string} eventName - Name of the event
 * @param {Object} properties - Event properties
 */
export function trackEvent(eventName, properties) {
    // Analytics tracking (implement based on your analytics system)
    console.log('📊 Mobile UX Event:', eventName, properties);
    
    // Example integration with Google Analytics
    if (typeof gtag !== 'undefined') {
        gtag('event', eventName, {
            custom_map: properties
        });
    }
}

/**
 * Debug function for mobile viewport issues
 */
export function debugMobileViewport() {
    if (!window.pixelWar) {
        console.log('❌ PixelWar not initialized');
        return;
    }
    
    const pixelWar = window.pixelWar;
    const rect = pixelWar.canvas.getBoundingClientRect();
    const { width: effectiveWidth, height: effectiveHeight, isMobile } = pixelWar.getEffectiveViewport(true);
    const minZoom = pixelWar.calculateMinZoom();
    
    console.log('🔍 MOBILE VIEWPORT DEBUG:', {
        userAgent: navigator.userAgent.substring(0, 100) + '...',
        screen: `${window.innerWidth}x${window.innerHeight}`,
        visualViewport: window.visualViewport ? `${window.visualViewport.width}x${window.visualViewport.height}` : 'N/A',
        canvasRect: `${rect.width}x${rect.height}`,
        canvasInternal: `${pixelWar.canvas.width}x${pixelWar.canvas.height}`,
        effectiveViewport: `${effectiveWidth}x${effectiveHeight}`,
        mapSize: `${pixelWar.config.width}x${pixelWar.config.height}`,
        currentZoom: pixelWar.zoom.toFixed(3),
        minZoom: minZoom.toFixed(3),
        canZoomOut: (pixelWar.zoom > minZoom).toString(),
        devicePixelRatio: window.devicePixelRatio,
        isMobile: isMobile,
        offsetX: pixelWar.offsetX.toFixed(2),
        offsetY: pixelWar.offsetY.toFixed(2)
    });
}

/**
 * Resets mobile onboarding data for testing
 */
export function resetMobileOnboarding() {
    localStorage.removeItem('pixelWarOnboardingCompleted');
    localStorage.removeItem('pixelWarTouchMode');
    localStorage.removeItem('pixelWarRecentColors');
    console.log('🔄 Mobile onboarding reset. Reload page to see onboarding.');
    return 'Onboarding reset complete. Reload the page.';
}

/**
 * Forces mobile mode for testing purposes
 */
export function forceMobileMode() {
    localStorage.setItem('forceMobileMode', 'true');
    location.reload();
}

/**
 * Tests mobile navigation by cycling through corners
 */
export function testMobileNavigation() {
    if (window.pixelWar) {
        console.log('🧪 Testing mobile navigation...');
        
        // Test each corner
        const corners = ['top-left', 'top-right', 'bottom-left', 'bottom-right', 'center'];
        let index = 0;
        
        const testNext = () => {
            if (index < corners.length) {
                const corner = corners[index];
                console.log(`Moving to ${corner}...`);
                window.pixelWar.navigateToCorner(corner);
                index++;
                setTimeout(testNext, 2000); // Wait 2 seconds between moves
            } else {
                console.log('✅ Navigation test complete!');
            }
        };
        
        testNext();
    } else {
        console.log('❌ PixelWar not initialized');
    }
}

/**
 * Utility to add standard touch feedback to an element
 * @param {HTMLElement} element - Element to add touch feedback to
 * @param {Function} callback - Optional callback function when element is activated
 */
export function addElementTouchFeedback(element, callback) {
    element.classList.add('touch-feedback');
    
    const addFeedback = (e) => {
        element.classList.add('active');
        triggerHapticFeedback('light');
        
        if (callback && typeof callback === 'function') {
            callback(e);
        }
        
        setTimeout(() => {
            element.classList.remove('active');
        }, 150);
    };
    
    // Handle both mouse and touch events
    element.addEventListener('mousedown', addFeedback);
    element.addEventListener('touchstart', addFeedback, { passive: true });
}

/**
 * Shows a temporary notification if the notification system is available
 * @param {string} message - Message to display
 * @param {string} type - Type of notification ('success', 'info', 'warning', 'error')
 */
export function showNotification(message, type = 'info') {
    if (window.pixelWar && window.pixelWar.notifications) {
        window.pixelWar.notifications.show(message, type);
    }
}

/**
 * Adds visual pulse animation to an element
 * @param {HTMLElement} element - Element to animate
 * @param {number} duration - Duration of animation in milliseconds
 */
export function addPulseAnimation(element, duration = 300) {
    element.classList.add('haptic-pulse');
    setTimeout(() => {
        element.classList.remove('haptic-pulse');
    }, duration);
}