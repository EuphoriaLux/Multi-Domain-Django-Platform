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
 *      - "queued" copy for a sendError on a POST the service worker has
 *        confirmed it stored for background sync (sw-workbox.js posts a
 *        {type: "crush-queued", url} message to the page right after the
 *        queue write succeeds; the page waits QUEUE_ACK_WAIT_MS for it):
 *        the request is not lost, it replays once the member is back
 *        online, so the member must NOT be told to try again -- a second
 *        submit would queue an identical POST (a chat message would be
 *        sent twice). No confirmation (no worker, IndexedDB write failed,
 *        route not queueable) means the plain "network" copy;
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
    // Eligibility alone never earns the "queued" copy: the worker has to
    // confirm the write (see queuedAcks below).
    var QUEUE_EXCLUDED_PREFIXES = [
        "/api/",
        "/admin/",
        "/crush-admin/",
        "/login",
        "/logout",
        "/accounts/",
        "/signup",
    ];

    // How long a sendError waits for the worker's "crush-queued" message.
    // The worker posts it before it rejects the fetch, but message delivery
    // is asynchronous, so the ack can land after htmx:sendError.
    var QUEUE_ACK_WAIT_MS = 300;

    // Absolute request URL -> time the worker confirmed it queued that URL.
    var queuedAcks = {};
    if (navigator.serviceWorker && navigator.serviceWorker.addEventListener) {
        navigator.serviceWorker.addEventListener("message", function (evt) {
            var data = evt.data;
            if (data && data.type === "crush-queued" && data.url) {
                queuedAcks[data.url] = Date.now();
            }
        });
    }

    // The absolute URL of a failed request the worker may have queued, or
    // null when it cannot have been (no controlling worker, not a POST,
    // excluded path).
    function queueEligibleUrl(detail) {
        var sw = navigator.serviceWorker;
        if (!sw || !sw.controller) return null; // no worker: nothing queued it
        var config = detail.requestConfig || {};
        if (String(config.verb || "").toLowerCase() !== "post") return null;
        var path =
            (detail.pathInfo && detail.pathInfo.finalRequestPath) ||
            config.path ||
            "";
        var url;
        try {
            url = new URL(path, window.location.href);
        } catch (e) {
            return null;
        }
        for (var i = 0; i < QUEUE_EXCLUDED_PREFIXES.length; i++) {
            if (url.pathname.indexOf(QUEUE_EXCLUDED_PREFIXES[i]) === 0) return null;
        }
        return url.href;
    }

    function ackedRecently(url, since) {
        var at = queuedAcks[url];
        return typeof at === "number" && at >= since;
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
            // Per event: `kind` is the handler's base kind and must not be
            // reassigned, or one 429 would classify every later failure.
            if (kind === "server" && detail.xhr && detail.xhr.status === 429) {
                showToast("rate-limited");
                return;
            }
            var url =
                kind === "network" && evt.type === "htmx:sendError"
                    ? queueEligibleUrl(detail)
                    : null;
            if (!url) {
                showToast(kind);
                return;
            }
            // Wait for the worker to confirm the queue write; without the
            // confirmation the request may be lost, so say "network".
            var since = Date.now() - QUEUE_ACK_WAIT_MS;
            setTimeout(function () {
                showToast(ackedRecently(url, since) ? "queued" : "network");
            }, QUEUE_ACK_WAIT_MS);
        };
    }

    document.addEventListener("htmx:responseError", onFailure("server"));
    document.addEventListener("htmx:sendError", onFailure("network"));
    document.addEventListener("htmx:timeout", onFailure("network"));
})();
