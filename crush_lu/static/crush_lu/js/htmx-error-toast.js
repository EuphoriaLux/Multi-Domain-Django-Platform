/**
 * Crush.lu global HTMX failure feedback (vanilla, CSP-safe).
 *
 * htmx 2 swaps nothing for a 4xx/5xx response (default responseHandling) and
 * nothing when the request never gets an answer, so without this a failed
 * hx-post is silent: the page just sits there, and a form whose Alpine
 * component flipped `isSubmitting` on submit stays on "Processing..." with a
 * disabled button for good (the event registration form was the reported
 * case).
 *
 * On htmx:responseError, htmx:sendError and htmx:timeout this:
 *   1. resets the Alpine submit flag(s) in SUBMIT_FLAGS on the component
 *      scope around the requesting element, so every form that follows the
 *      `isSubmitting` convention gets its button and resting label back;
 *   2. gives focus back to the button that had it once it is enabled again,
 *      when disabling it for the request dropped focus to <body> (a keyboard
 *      user would otherwise have to find the way back to retry). Only for a
 *      control inside the requesting element, and only if nothing has taken
 *      focus since: focus the member moved elsewhere stays there, and a
 *      button that never had focus (a tap in iOS Safari) is not focused;
 *   3. shows ONE translated toast through Alpine.store("toasts"):
 *      - "server" copy for responseError;
 *      - "rate-limited" copy for a 429 responseError: the @ratelimit
 *        decorators answer a bare 429 and queue "Too many attempts" as a
 *        Django message that only a full page load would show, so the toast
 *        says it now instead of inviting retries that stay blocked;
 *      - "queued" copy for a sendError on a POST the service worker holds
 *        for background sync (see queuedByServiceWorker below): the request
 *        is not lost, it replays once the member is back online, so the
 *        member must NOT be told to try again -- a second submit would queue
 *        an identical POST (a chat message would be sent twice);
 *      - "network" copy for every other sendError/timeout.
 *
 * htmx itself re-enables hx-disabled-elt elements and removes .htmx-request
 * from indicators on all three paths (before sendError/timeout fire, right
 * after responseError fires), so this does not touch them.
 *
 * The copy is rendered server-side in the page language by
 * crush_lu/components/htmx_error_toast.html; nothing user-facing is
 * hardcoded here.
 *
 * No toast (the state and focus reset still run) when:
 *   - the requesting element or an ancestor has data-htmx-error-toast="off"
 *     (pages that report the failure themselves, background polls that simply
 *     retry on the next tick);
 *   - the error response carries its own HX-Trigger showToast
 *     (view_utils.toast_response): toast-component.js shows that message
 *     instead. Known issue: toast-component.js currently shows such a
 *     message twice (htmx 2 also re-dispatches it as `show-toast`), so fix
 *     that before a view adopts toast_response;
 *   - the same message is still on screen (several requests failing
 *     together, e.g. while offline). Once the member dismisses it, or it
 *     expires or is pushed out by newer toasts, the next failure shows it
 *     again.
 * A response that an htmx:beforeSwap handler turns into a non-error
 * (detail.isError = false) never reaches here: htmx only raises
 * htmx:responseError while isError is still true.
 */
(function () {
    "use strict";

    // Alpine state properties that mean "a submit is in flight".
    var SUBMIT_FLAGS = ["isSubmitting"];

    // The control whose focus fell to nothing because it was disabled (the
    // :disabled / hx-disabled-elt of an in-flight request); cleared as soon
    // as anything takes focus.
    var droppedFocus = null;
    document.addEventListener(
        "focusout",
        function (evt) {
            if (!evt.relatedTarget && evt.target && evt.target.disabled === true) {
                droppedFocus = evt.target;
            }
        },
        true,
    );
    document.addEventListener(
        "focusin",
        function () {
            droppedFocus = null;
        },
        true,
    );

    function copyFor(kind) {
        var el = document.getElementById("htmx-error-toast-messages");
        return el ? el.getAttribute("data-" + kind) : null;
    }

    // Mirror of isQueueablePost() in crush_lu/static/crush_lu/sw-workbox.js:
    // a POST whose path starts with none of these is held in the "crush-queue"
    // background-sync queue when the network fails, and replayed for up to
    // 24 h. test_htmx_error_toast.py checks that the two lists stay equal.
    var QUEUE_EXCLUDED_PREFIXES = [
        "/api/",
        "/admin/",
        "/crush-admin/",
        "/login",
        "/logout",
        "/accounts/",
        "/signup",
    ];

    function queuedByServiceWorker(detail) {
        var sw = navigator.serviceWorker;
        if (!sw || !sw.controller) return false; // no worker: nothing queued it
        var config = detail.requestConfig || {};
        if (String(config.verb || "").toLowerCase() !== "post") return false;
        var path =
            (detail.pathInfo && detail.pathInfo.finalRequestPath) ||
            config.path ||
            "";
        try {
            path = new URL(path, window.location.href).pathname;
        } catch (e) {
            return false;
        }
        for (var i = 0; i < QUEUE_EXCLUDED_PREFIXES.length; i++) {
            if (path.indexOf(QUEUE_EXCLUDED_PREFIXES[i]) === 0) return false;
        }
        return true;
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

    function restoreFocus(elt) {
        var control = droppedFocus;
        if (!control || !elt || typeof elt.contains !== "function") return;
        if (!elt.contains(control)) return; // not this request's control
        // Next task: by then Alpine has re-rendered the reset flag (a
        // microtask) and htmx has re-enabled hx-disabled-elt.
        setTimeout(function () {
            if (droppedFocus !== control) return; // something took focus
            var active = document.activeElement;
            if (active && active !== document.body) return;
            if (!control.isConnected || control.disabled) return;
            try {
                control.focus({ preventScroll: true });
            } catch (e) {
                // unfocusable: nothing to restore
            }
        }, 0);
    }

    function isShowing(store, message) {
        var items = store.items || [];
        for (var i = 0; i < items.length; i++) {
            if (items[i] && items[i].message === message) return true;
        }
        return false;
    }

    function showToast(kind) {
        var message = copyFor(kind);
        var Alpine = window.Alpine;
        if (!message || !Alpine || typeof Alpine.store !== "function") return;
        var store = Alpine.store("toasts");
        if (!store || typeof store.add !== "function") return;
        // The store drops a toast the moment it is dismissed, expires or is
        // pushed out (before its exit animation ends), so only a toast that
        // is still up suppresses a repeat.
        if (isShowing(store, message)) return;
        // A queued request is not an error: the member has nothing to do.
        store.add({ type: kind === "queued" ? "info" : "error", message: message });
    }

    function onFailure(kind) {
        return function (evt) {
            var detail = evt.detail || {};
            var elt = detail.elt || evt.target;
            resetSubmitState(elt);
            restoreFocus(elt);
            if (toastOptedOut(elt)) return;
            if (kind === "server" && serverSentToast(detail.xhr)) return;
            if (kind === "server" && detail.xhr && detail.xhr.status === 429) {
                kind = "rate-limited";
            } else if (
                kind === "network" &&
                evt.type === "htmx:sendError" &&
                queuedByServiceWorker(detail)
            ) {
                kind = "queued";
            }
            showToast(kind);
        };
    }

    document.addEventListener("htmx:responseError", onFailure("server"));
    document.addEventListener("htmx:sendError", onFailure("network"));
    document.addEventListener("htmx:timeout", onFailure("network"));
})();
