/**
 * Crush.lu Page Loading Indicator
 * Shows a beautiful loading overlay during slow navigation
 */

(function () {
    "use strict";

    const loadingOverlay = document.getElementById("page-loading-overlay");
    let loadingTimeout = null;
    const LOADING_DELAY = 500; // Show loader after 500ms if page hasn't loaded

    // State tracking to prevent double-shows and debounce rapid show/hide calls
    let isShowing = false;
    let lastHideTime = 0;
    const DEBOUNCE_TIME = 100; // ms to ignore rapid show calls after hide
    // Hard ceiling on how long the overlay may stay up. Every hide path here is
    // tied to a top-level navigation (load / pageshow / DOMContentLoaded), so a
    // submit handled in JavaScript instead — a third-party widget that calls
    // preventDefault and works inside iframes — shows the overlay and then never
    // fires anything that takes it down. That is what happened on the SumUp
    // payment page: the overlay covered the live card form, 3DS included, with
    // no way past it. The opt-out below is the real fix; this is the backstop,
    // because a full-screen cover that never lifts is the worst thing this file
    // can do and it must not depend on getting the opt-out right everywhere.
    const MAX_VISIBLE_TIME = 15000;
    let watchdogTimeout = null;

    /**
     * Show loading overlay with delay to avoid flashing on fast loads
     */
    function showLoadingOverlay() {
        const now = Date.now();

        // Debounce: ignore if we just hid the overlay (prevents double-shows after redirects)
        if (now - lastHideTime < DEBOUNCE_TIME) {
            return;
        }

        // Already showing, don't show again (prevents multiple overlapping timeouts)
        if (isShowing) {
            return;
        }

        // Check if we should skip this show (set by other scripts like OAuth landing)
        if (window.__skipNextLoadingOverlay) {
            window.__skipNextLoadingOverlay = false;
            return;
        }

        isShowing = true;

        // Clear any existing timeout
        if (loadingTimeout) {
            clearTimeout(loadingTimeout);
        }

        // Delay showing loader to avoid flashing on fast loads
        loadingTimeout = setTimeout(() => {
            if (loadingOverlay) {
                loadingOverlay.classList.add("show");
            }
        }, LOADING_DELAY);

        // Arm the watchdog from the moment we decide to show, not from page
        // load — the pre-existing "hide after 10s" fallback listens on `load`,
        // which has long since fired by the time a click or submit shows the
        // overlay, so it never rescued this case.
        if (watchdogTimeout) {
            clearTimeout(watchdogTimeout);
        }
        watchdogTimeout = setTimeout(hideLoadingOverlay, MAX_VISIBLE_TIME);
    }

    /**
     * Hide loading overlay immediately
     */
    function hideLoadingOverlay() {
        lastHideTime = Date.now();
        isShowing = false;

        // Clear timeout if load completes quickly
        if (loadingTimeout) {
            clearTimeout(loadingTimeout);
            loadingTimeout = null;
        }

        if (watchdogTimeout) {
            clearTimeout(watchdogTimeout);
            watchdogTimeout = null;
        }

        // Hide overlay
        if (loadingOverlay) {
            loadingOverlay.classList.remove("show");
        }
    }

    /**
     * Check if a link is a same-origin navigation link
     */
    function isNavigationLink(link) {
        return (
            link &&
            link.href &&
            link.href.startsWith(window.location.origin) &&
            !link.href.includes("#") &&
            !link.hasAttribute("download") &&
            link.target !== "_blank" &&
            !link.closest(".no-loading")
        ); // Allow opt-out
    }

    // ============================================================================
    // Event Listeners
    // ============================================================================

    // Intercept all link clicks for same-origin navigation
    document.addEventListener(
        "click",
        function (e) {
            const link = e.target.closest("a");

            if (isNavigationLink(link)) {
                showLoadingOverlay();
            }
        },
        true,
    );

    /**
     * Check if a form is an HTMX form (handled via XHR, not navigation)
     */
    function isHtmxForm(form) {
        return (
            form &&
            (form.hasAttribute("hx-post") ||
                form.hasAttribute("hx-get") ||
                form.hasAttribute("hx-put") ||
                form.hasAttribute("hx-patch") ||
                form.hasAttribute("hx-delete") ||
                form.hasAttribute("data-hx-post") ||
                form.hasAttribute("data-hx-get"))
        );
    }

    // Intercept form submissions (all navigating forms, NOT HTMX forms)
    document.addEventListener(
        "submit",
        function (e) {
            const form = e.target;

            // Skip HTMX forms - they use XHR and don't navigate
            if (isHtmxForm(form)) {
                return;
            }

            // Allow opt-out via a no-loading class on the form OR any ancestor.
            // Ancestor matters: a third-party SDK renders its own form inside a
            // container we control, so the class can only be put on the
            // container — checking the form alone never matches.
            if (form && form.closest && form.closest(".no-loading")) {
                return;
            }

            // Show overlay for all navigating forms (GET and POST)
            if (form) {
                showLoadingOverlay();
            }
        },
        true,
    );

    // Hide loading overlay when page loads
    window.addEventListener("load", hideLoadingOverlay);

    // Hide on DOMContentLoaded as backup
    document.addEventListener("DOMContentLoaded", hideLoadingOverlay);

    // Hide on page show (back/forward navigation from bfcache)
    window.addEventListener("pageshow", function (event) {
        hideLoadingOverlay();
    });

    // Listen for service worker messages about navigation
    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.addEventListener("message", function (event) {
            if (event.data && event.data.type === "NAVIGATION_START") {
                showLoadingOverlay();
            } else if (event.data && event.data.type === "NAVIGATION_END") {
                hideLoadingOverlay();
            }
        });
    }

    // Fallback safety: hide after max 10 seconds if something goes wrong
    window.addEventListener("load", function () {
        setTimeout(hideLoadingOverlay, 10000);
    });

    // Hide loading overlay after HTMX completes swapping content
    document.body.addEventListener("htmx:afterSwap", hideLoadingOverlay);
    document.body.addEventListener("htmx:afterSettle", hideLoadingOverlay);

    // Initial check: hide overlay on script load (in case of page refresh)
    hideLoadingOverlay();
})();
