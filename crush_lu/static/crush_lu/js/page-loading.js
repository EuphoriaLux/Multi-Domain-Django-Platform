/**
 * Crush.lu Page Loading Indicator
 * Shows a slim progress bar at the top of the page during slow navigation.
 *
 * The bar never covers the page, so it cannot block anything. What stops a
 * second click on a form that is already on its way is the submit guard at
 * the bottom of this file, not the indicator.
 */

(function () {
    "use strict";

    const loadingOverlay = document.getElementById("page-loading-overlay");
    let loadingTimeout = null;
    const LOADING_DELAY = 300; // Show the bar after 300ms if the page hasn't loaded

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

        // Hide the bar
        if (loadingOverlay) {
            loadingOverlay.classList.remove("show");
        }

        releaseSubmittingForms();
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

    /**
     * Run `callback` once the event has finished dispatching, unless a handler
     * cancelled it. These listeners run in the capture phase, before any
     * handler has had the chance to call preventDefault, so the check has to
     * wait: a cancelled click or submit never navigates and nothing would take
     * the indicator down again.
     */
    function afterDispatchUnlessCancelled(e, callback) {
        setTimeout(function () {
            if (!e.defaultPrevented) {
                callback();
            }
        }, 0);
    }

    // Intercept all link clicks for same-origin navigation
    document.addEventListener(
        "click",
        function (e) {
            // Ctrl/Cmd/Shift/Alt-click and middle-click open the link somewhere
            // else (new tab, new window, download): this page stays put.
            if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
                return;
            }

            const link = e.target.closest("a");

            // Read the opt-outs now: other scripts toggle `.no-loading` from
            // their own handlers during this same dispatch.
            if (isNavigationLink(link)) {
                afterDispatchUnlessCancelled(e, showLoadingOverlay);
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

            // data-confirm forms ask first through the confirm sheet
            // (confirm-sheet.js cancels this submit); this capture listener
            // runs before it, so a cancel would strand the overlay. Only the
            // confirmed re-submit, marked data-confirmed="1", navigates.
            // data-confirm-when="<checkbox name>" asks only while that box
            // is ticked (same rule as confirm-sheet.js). A clicked submit
            // button carrying its own data-confirm asks the same way.
            // confirm-sheet.js records the clicked button for WebViews
            // that leave SubmitEvent.submitter unset.
            const submitter = e.submitter || (form && form.__crushLastSubmitter);
            if (
                form &&
                form.hasAttribute &&
                (form.hasAttribute("data-confirm") ||
                    (submitter && submitter.hasAttribute("data-confirm"))) &&
                form.getAttribute("data-confirmed") !== "1"
            ) {
                const when = form.getAttribute("data-confirm-when");
                const box = when && form.elements.namedItem(when);
                if (!when || (box && box.checked)) {
                    return;
                }
            }

            // Opens somewhere else (new tab, a dialog): this page stays put.
            if (!form || isElsewhere(form, submitter)) {
                return;
            }

            // A second submit while the first is still on its way: drop it.
            // The full-screen overlay used to make this impossible by covering
            // the page; the progress bar does not, so the guard is explicit.
            if (form.__crushSubmitting) {
                e.preventDefault();
                e.stopImmediatePropagation();
                return;
            }

            // Show the bar and arm the guard for all navigating forms (GET and
            // POST), once we know no handler cancelled this submit.
            afterDispatchUnlessCancelled(e, function () {
                markSubmitting(form, submitter);
                showLoadingOverlay();
            });
        },
        true,
    );

    /**
     * A submit that does not navigate this page: target="_blank" and
     * method="dialog", on the form or overridden by the clicked button.
     */
    function isElsewhere(form, submitter) {
        const target =
            (submitter && submitter.getAttribute("formtarget")) ||
            form.getAttribute("target");
        const method =
            (submitter && submitter.getAttribute("formmethod")) ||
            form.getAttribute("method");
        return (
            (target && target !== "_self") ||
            (method && method.toLowerCase() === "dialog")
        );
    }

    // Forms whose submit is on its way. The submit button is not disabled: a
    // disabled button drops its name/value from the POST, and several forms
    // branch on which button was pressed. aria-busy says the same thing to
    // assistive tech and gives CSS a hook.
    const submittingForms = new Set();

    function markSubmitting(form, submitter) {
        form.__crushSubmitting = true;
        form.setAttribute("aria-busy", "true");
        if (submitter && submitter.setAttribute) {
            submitter.setAttribute("aria-busy", "true");
            form.__crushBusySubmitter = submitter;
        }
        submittingForms.add(form);
    }

    /**
     * Let every guarded form submit again. Called whenever the indicator goes
     * down: the page was restored from the back/forward cache, the response
     * was a file download, or the watchdog gave up waiting.
     */
    function releaseSubmittingForms() {
        submittingForms.forEach(function (form) {
            form.__crushSubmitting = false;
            form.removeAttribute("aria-busy");
            if (form.__crushBusySubmitter) {
                form.__crushBusySubmitter.removeAttribute("aria-busy");
                form.__crushBusySubmitter = null;
            }
        });
        submittingForms.clear();
    }

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

    // No HTMX hook here. A click or submit that HTMX handles is cancelled by
    // HTMX, so the bar is never raised for it (afterDispatchUnlessCancelled).
    // Hiding on every swap would instead take the bar down, and release the
    // submit guard, whenever an unrelated background swap landed while a real
    // page change was still on its way.

    // Initial check: hide overlay on script load (in case of page refresh)
    hideLoadingOverlay();
})();
