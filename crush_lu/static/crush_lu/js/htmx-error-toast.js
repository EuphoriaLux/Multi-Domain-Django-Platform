/**
 * Crush.lu global HTMX failure feedback (vanilla, CSP-safe).
 *
 * htmx 2 swaps nothing for a 4xx/5xx response (default responseHandling) and
 * nothing when the request never gets an answer, so without this a failed
 * hx-post is silent: the page just sits there, and a form whose Alpine
 * component flipped `isSubmitting` on submit stays on "Processing..." with a
 * disabled button for good (event registration, UX review finding 4-04).
 *
 * On htmx:responseError, htmx:sendError and htmx:timeout this:
 *   1. resets the Alpine submit flag(s) in SUBMIT_FLAGS on the component
 *      scope around the requesting element, so every form that follows the
 *      `isSubmitting` convention gets its button and resting label back;
 *   2. shows ONE translated error toast through Alpine.store("toasts") -- the
 *      "network" copy for sendError/timeout, the "server" copy for
 *      responseError.
 *
 * htmx itself re-enables hx-disabled-elt elements and removes .htmx-request
 * from indicators on all three paths (before sendError/timeout fire, right
 * after responseError fires), so this does not touch them.
 *
 * The copy is rendered server-side in the page language by
 * crush_lu/components/htmx_error_toast.html; nothing user-facing is
 * hardcoded here.
 *
 * No toast (the state reset still runs) when:
 *   - the requesting element or an ancestor has data-htmx-error-toast="off"
 *     (pages that report the failure themselves, background polls that simply
 *     retry on the next tick);
 *   - the error response carries its own HX-Trigger showToast
 *     (view_utils.toast_response): toast-component.js already shows that more
 *     specific message;
 *   - the same message was shown moments ago (several requests failing
 *     together, e.g. while offline).
 * A response that an htmx:beforeSwap handler turns into a non-error
 * (detail.isError = false) never reaches here: htmx only raises
 * htmx:responseError while isError is still true.
 */
(function () {
    "use strict";

    // Alpine state properties that mean "a submit is in flight".
    var SUBMIT_FLAGS = ["isSubmitting"];
    var DEDUPE_MS = 3000;

    var lastMessage = null;
    var lastShownAt = 0;

    function copyFor(kind) {
        var el = document.getElementById("htmx-error-toast-messages");
        return el ? el.getAttribute("data-" + kind) : null;
    }

    function toastOptedOut(elt) {
        return !!(
            elt &&
            typeof elt.closest === "function" &&
            elt.closest('[data-htmx-error-toast="off"]')
        );
    }

    function serverSentToast(xhr) {
        var header =
            xhr && typeof xhr.getResponseHeader === "function"
                ? xhr.getResponseHeader("HX-Trigger")
                : null;
        if (!header) return false;
        try {
            var triggers = JSON.parse(header);
            return !!(triggers && triggers.showToast);
        } catch (e) {
            return false; // plain event-name list: no toast payload
        }
    }

    function resetSubmitState(elt) {
        var Alpine = window.Alpine;
        if (!elt || !Alpine || typeof Alpine.$data !== "function") return;
        var scope;
        try {
            // Merged proxy over every Alpine scope enclosing elt; `in` and
            // assignment resolve to the nearest scope that owns the flag.
            scope = Alpine.$data(elt);
        } catch (e) {
            return;
        }
        if (!scope) return;
        SUBMIT_FLAGS.forEach(function (flag) {
            // `in` first: assigning a flag no scope owns would add it to the
            // outermost one (the <body> pageTransition component).
            if (flag in scope && scope[flag] === true) {
                try {
                    scope[flag] = false;
                } catch (e) {
                    // getter-only flag: the component owns its own reset
                }
            }
        });
    }

    function showToast(kind) {
        var message = copyFor(kind);
        var Alpine = window.Alpine;
        if (!message || !Alpine || typeof Alpine.store !== "function") return;
        var now = Date.now();
        if (message === lastMessage && now - lastShownAt < DEDUPE_MS) return;
        var store = Alpine.store("toasts");
        if (!store || typeof store.add !== "function") return;
        lastMessage = message;
        lastShownAt = now;
        store.add({ type: "error", message: message });
    }

    function onFailure(kind) {
        return function (evt) {
            var detail = evt.detail || {};
            var elt = detail.elt || evt.target;
            resetSubmitState(elt);
            if (toastOptedOut(elt)) return;
            if (kind === "server" && serverSentToast(detail.xhr)) return;
            showToast(kind);
        };
    }

    document.addEventListener("htmx:responseError", onFailure("server"));
    document.addEventListener("htmx:sendError", onFailure("network"));
    document.addEventListener("htmx:timeout", onFailure("network"));
})();
