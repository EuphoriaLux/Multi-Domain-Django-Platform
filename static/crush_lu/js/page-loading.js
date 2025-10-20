/**
 * Crush.lu Page Loading Indicator
 * Shows a beautiful loading overlay during slow navigation
 */

(function() {
    'use strict';

    const loadingOverlay = document.getElementById('page-loading-overlay');
    let loadingTimeout = null;
    const LOADING_DELAY = 500; // Show loader after 500ms if page hasn't loaded

    /**
     * Show loading overlay with delay to avoid flashing on fast loads
     */
    function showLoadingOverlay() {
        // Clear any existing timeout
        if (loadingTimeout) {
            clearTimeout(loadingTimeout);
        }

        // Delay showing loader to avoid flashing on fast loads
        loadingTimeout = setTimeout(() => {
            if (loadingOverlay) {
                loadingOverlay.classList.add('show');
                console.log('[PWA] Loading overlay shown (slow network detected)');
            }
        }, LOADING_DELAY);
    }

    /**
     * Hide loading overlay immediately
     */
    function hideLoadingOverlay() {
        // Clear timeout if load completes quickly
        if (loadingTimeout) {
            clearTimeout(loadingTimeout);
            loadingTimeout = null;
        }

        // Hide overlay
        if (loadingOverlay) {
            loadingOverlay.classList.remove('show');
            console.log('[PWA] Loading overlay hidden');
        }
    }

    /**
     * Check if a link is a same-origin navigation link
     */
    function isNavigationLink(link) {
        return link &&
            link.href &&
            link.href.startsWith(window.location.origin) &&
            !link.href.includes('#') &&
            !link.hasAttribute('download') &&
            link.target !== '_blank' &&
            !link.classList.contains('no-loading'); // Allow opt-out
    }

    // ============================================================================
    // Event Listeners
    // ============================================================================

    // Intercept all link clicks for same-origin navigation
    document.addEventListener('click', function(e) {
        const link = e.target.closest('a');

        if (isNavigationLink(link)) {
            showLoadingOverlay();
        }
    }, true);

    // Intercept form submissions (GET forms that navigate)
    document.addEventListener('submit', function(e) {
        const form = e.target;

        // Only show for navigation (GET forms or POST that navigates)
        if (form && form.method.toLowerCase() === 'get') {
            showLoadingOverlay();
        }
    }, true);

    // Hide loading overlay when page loads
    window.addEventListener('load', hideLoadingOverlay);

    // Hide on DOMContentLoaded as backup
    document.addEventListener('DOMContentLoaded', hideLoadingOverlay);

    // Hide on page show (back/forward navigation from bfcache)
    window.addEventListener('pageshow', function(event) {
        hideLoadingOverlay();

        // If restored from bfcache, ensure service worker is active
        if (event.persisted && 'serviceWorker' in navigator && navigator.serviceWorker.controller) {
            console.log('[PWA] Page restored from bfcache');
        }
    });

    // Listen for service worker messages about navigation
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.addEventListener('message', function(event) {
            if (event.data && event.data.type === 'NAVIGATION_START') {
                showLoadingOverlay();
            } else if (event.data && event.data.type === 'NAVIGATION_END') {
                hideLoadingOverlay();
            }
        });
    }

    // Fallback safety: hide after max 10 seconds if something goes wrong
    window.addEventListener('load', function() {
        setTimeout(hideLoadingOverlay, 10000);
    });

    // Initial check: hide overlay on script load (in case of page refresh)
    hideLoadingOverlay();

})();
