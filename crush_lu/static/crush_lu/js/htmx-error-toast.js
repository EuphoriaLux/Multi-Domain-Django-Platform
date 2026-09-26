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
 * On every htmx POST this also disables the submit control(s) inside the
 * requesting element from htmx:beforeRequest until the request succeeds
 * (htmx:afterRequest) or its failure is classified below, whether or not
 * the form follows the isSubmitting convention or sets hx-disabled-elt: a
 * second click during the request or during the queue-ack wait would store
 * a second non-idempotent POST for replay (chat message, registration).
 * And it asks the worker to drain the background-sync queue
 * ({type: "crush-drain-queue"}) when the page comes back online AND, after
 * every queue acknowledgement, on a short retry schedule while the page
 * lives (DRAIN_RETRY_MS): a POST can fail while the browser still reports
 * itself online (server or DNS outage), so an "online" event alone would
 * leave the request waiting for the next worker start where the
 * Background Sync API is missing or its registration failed.
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
 *      - "queued" copy (event registrations and messages will sync once
 *        online) for a sendError on the event registration or connection
 *        message POST the service worker has confirmed it stored for
 *        background sync; every other confirmed POST (sparks, connection
 *        requests, cache answers, coach actions...) gets the action-neutral
 *        "interrupted" copy instead, since the specific wording would not
 *        name what the member just did (sw-workbox.js posts a
 *        {type: "crush-queued", requestId, url} message to the ONE client
 *        that issued the fetch right after the queue write succeeds; the
 *        page waits QUEUE_ACK_WAIT_MS for it, and keeps the form's
 *        isSubmitting flag up until then so a resubmit cannot slip in and
 *        queue a duplicate). Every htmx request carries an
 *        X-Crush-Request-Id header (set in htmx:configRequest) so the
 *        acknowledgement is matched to that request, not to a URL two
 *        tabs or two quick submits may share:
 *        the request is not lost, it replays once the member is back
 *        online, so the member must NOT be told to try again -- a second
 *        submit would queue an identical POST (a chat message would be
 *        sent twice). No confirmation from a worker that speaks this
 *        protocol (it answered {type: "crush-capabilities?"} with
 *        queuedAck: true) means the IndexedDB write failed, so the plain
 *        "network" copy is right. No confirmation from a worker that never
 *        answered (a v31 worker keeps controlling the page until the member
 *        taps "Update Now" in pwa-update.js, and queues the same POSTs
 *        without acknowledging; or the answer has not arrived yet) proves
 *        nothing either way, so the "unconfirmed" copy is shown: it says
 *        the send could not be confirmed and asks the member to check
 *        before sending again -- no retry promise, no resend prompt;
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
        "/cookies/",
    ];

    // How long a sendError waits for the worker's "crush-queued" message.
    // The worker posts it before it rejects the fetch, but message delivery
    // is asynchronous, so the ack can land after htmx:sendError.
    var QUEUE_ACK_WAIT_MS = 300;

    // Every htmx request gets its own id, echoed back in the worker's
    // acknowledgement, so an ack is matched to the request it belongs to.
    var REQUEST_ID_HEADER = "X-Crush-Request-Id";
    var requestSeq = 0;
    // Per request id: which worker generation controlled the page when the
    // request was sent, and whether THAT worker acknowledges queue writes.
    // A worker update between the request and its failure (controllerchange)
    // means the answer on record may describe the wrong worker, so such a
    // request is classified as unconfirmed.
    var requestMeta = {};
    document.addEventListener("htmx:configRequest", function (evt) {
        var headers = evt.detail && evt.detail.headers;
        if (!headers) return;
        requestSeq += 1;
        var id = "r" + requestSeq + "-" + Date.now();
        headers[REQUEST_ID_HEADER] = id;
        requestMeta[id] = {
            generation: controllerGeneration,
            queuedAck: workerQueuedAck,
        };
    });

    // Request id (or, for a worker that sends none, absolute URL) -> time
    // the worker confirmed it queued that request.
    var queuedAcks = {};
    // Whether the CONTROLLING worker acknowledges queue writes. null until a
    // worker answers the capability question; a worker from before v32
    // never answers, so null also means "old worker, no ack will ever come".
    var workerQueuedAck = null;
    // Bumped on every controllerchange, so a request can tell whether the
    // worker that handled it is still the one whose capability is on record.
    var controllerGeneration = 0;

    // Submit controls disabled by the generic hold (never ones that were
    // already disabled, e.g. by an isSubmitting binding or hx-disabled-elt).
    var HELD_ATTR = "data-crush-held";

    function submitControls(elt) {
        var controls = [];
        if (!elt || typeof elt.querySelectorAll !== "function") return controls;
        if (elt.matches && elt.matches('button, input[type="submit"]')) {
            controls.push(elt);
        }
        var found = elt.querySelectorAll(
            'button:not([type]), button[type="submit"], input[type="submit"]',
        );
        for (var i = 0; i < found.length; i++) controls.push(found[i]);
        return controls;
    }

    function holdSubmitters(elt) {
        submitControls(elt).forEach(function (control) {
            if (control.disabled) return;
            control.disabled = true;
            control.setAttribute(HELD_ATTR, "1");
        });
    }

    function releaseHeld(elt) {
        submitControls(elt).forEach(function (control) {
            if (!control.hasAttribute(HELD_ATTR)) return;
            control.removeAttribute(HELD_ATTR);
            control.disabled = false;
        });
    }

    document.addEventListener("htmx:beforeRequest", function (evt) {
        var detail = evt.detail || {};
        var config = detail.requestConfig || {};
        if (String(config.verb || "").toLowerCase() !== "post") return;
        holdSubmitters(detail.elt || evt.target);
    });
    document.addEventListener("htmx:afterRequest", function (evt) {
        var detail = evt.detail || {};
        // A failure is released by finish() once its copy is known.
        if (detail.successful) {
            releaseHeld(detail.elt || evt.target);
            var id = requestIdOf(detail);
            if (id) delete requestMeta[id];
        }
    });

    function askWorkerCapabilities() {
        var sw = navigator.serviceWorker;
        controllerGeneration += 1;
        workerQueuedAck = null;
        if (sw && sw.controller && typeof sw.controller.postMessage === "function") {
            try {
                sw.controller.postMessage({ type: "crush-capabilities?" });
            } catch (e) {
                // no handshake: treated as an old worker
            }
        }
    }

    if (navigator.serviceWorker && navigator.serviceWorker.addEventListener) {
        navigator.serviceWorker.addEventListener("message", function (evt) {
            var data = evt.data;
            if (!data) return;
            if (data.type === "crush-queued" && (data.requestId || data.url)) {
                queuedAcks[data.requestId || data.url] = Date.now();
                scheduleDrains();
            } else if (data.type === "crush-capabilities") {
                workerQueuedAck = data.queuedAck === true;
                // Entries an earlier page left behind still need a drain.
                drainReported(data.queued);
            } else if (data.type === "crush-drained") {
                drainReported(data.remaining);
            }
        });
        // A new worker taking over mid-page (pwa-update.js "Update Now")
        // answers for itself.
        navigator.serviceWorker.addEventListener(
            "controllerchange",
            askWorkerCapabilities,
        );
        askWorkerCapabilities();
        // Background Sync is not everywhere (and its registration can fail);
        // the worker's own fallback only drains on worker start. Ask for a
        // drain whenever this page regains connectivity (the retry schedule
        // above covers failures the browser never reported as offline).
        window.addEventListener("online", scheduleDrains);
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

    // Confirmed-queued POSTs whose replay the "queued" copy describes by
    // name (event registrations and messages); anything else queued gets
    // the action-neutral "interrupted" copy.
    var QUEUED_COPY_ROUTES = [/\/events\/\d+\/register\/$/, /\/connections\/\d+\/$/];

    function queuedCopyFor(url) {
        var path;
        try {
            path = new URL(url).pathname;
        } catch (e) {
            return "interrupted";
        }
        for (var i = 0; i < QUEUED_COPY_ROUTES.length; i++) {
            if (QUEUED_COPY_ROUTES[i].test(path)) return "queued";
        }
        return "interrupted";
    }

    // Drain requests while the worker reports queued work: right away (the
    // browser may still say it is online during a server outage, so no
    // "online" event is coming), then with growing gaps, capped at the last
    // value and repeated until the worker answers {type: "crush-drained",
    // remaining: 0}. One chain per page, restarted by each acknowledgement,
    // by an "online" event, and by a capability answer that reports leftover
    // entries from an earlier page.
    var DRAIN_RETRY_MS = [3000, 15000, 60000, 300000];
    var drainTimer = null;
    var drainPending = false;

    function requestDrain() {
        var sw = navigator.serviceWorker;
        if (!sw || !sw.controller) return;
        try {
            sw.controller.postMessage({ type: "crush-drain-queue" });
        } catch (e) {
            // no worker to ask
        }
    }

    function scheduleDrains() {
        if (drainTimer) clearTimeout(drainTimer);
        drainPending = true;
        var step = 0;
        var tick = function () {
            drainTimer = null;
            if (!drainPending) return; // the worker reported an empty queue
            if (navigator.onLine !== false) requestDrain();
            var delay = DRAIN_RETRY_MS[Math.min(step, DRAIN_RETRY_MS.length - 1)];
            step += 1;
            drainTimer = setTimeout(tick, delay);
        };
        drainTimer = setTimeout(tick, 0);
    }

    function drainReported(remaining) {
        if (remaining === 0) {
            drainPending = false;
            if (drainTimer) clearTimeout(drainTimer);
            drainTimer = null;
        } else if (typeof remaining === "number" && remaining > 0 && !drainPending) {
            scheduleDrains();
        }
    }

    function requestIdOf(detail) {
        var headers = detail.requestConfig && detail.requestConfig.headers;
        return (headers && headers[REQUEST_ID_HEADER]) || null;
    }

    // An ack keyed by the request's own id belongs to this request whatever
    // its age (a backgrounded tab can deliver the error task long after the
    // ack); the time bound applies only to the URL fallback, which two
    // requests may share. A consumed request-id ack is dropped.
    function acked(key, isRequestId, since) {
        var at = queuedAcks[key];
        if (typeof at !== "number") return false;
        if (isRequestId) {
            delete queuedAcks[key];
            return true;
        }
        return at >= since;
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
        // A queued or still-retrying request is not an error: the member
        // has nothing to do. An unconfirmed one needs their attention.
        var info = kind === "queued" || kind === "interrupted";
        store.add({ type: info ? "info" : "error", message: message });
    }

    function onFailure(kind) {
        return function (evt) {
            var detail = evt.detail || {};
            var elt = detail.elt || evt.target;
            // The toast, then the form: isSubmitting stays up until the copy
            // is known, so a member cannot resubmit into the ack wait and
            // queue a duplicate of a request the worker already holds.
            var finish = function (copy) {
                if (copy) showToast(copy);
                resetSubmitState(elt);
                releaseHeld(elt);
                restoreFocus(elt);
            };
            if (toastOptedOut(elt)) {
                finish(null);
                return;
            }
            if (kind === "server" && serverSentToast(detail.xhr)) {
                finish(null);
                return;
            }
            // Per event: `kind` is the handler's base kind and must not be
            // reassigned, or one 429 would classify every later failure.
            if (kind === "server" && detail.xhr && detail.xhr.status === 429) {
                finish("rate-limited");
                return;
            }
            var url =
                kind === "network" && evt.type === "htmx:sendError"
                    ? queueEligibleUrl(detail)
                    : null;
            if (!url) {
                finish(kind);
                return;
            }
            // Wait for the worker to confirm the queue write. Without it:
            // a worker that acknowledges writes (v32+) failed to store the
            // request, so "network" (retry) is right; an older worker has
            // most likely stored it silently, so "interrupted" (no retry
            // prompt, no replay promise).
            var requestId = requestIdOf(detail);
            var key = requestId || url;
            var meta = requestId ? requestMeta[requestId] : null;
            if (requestId) delete requestMeta[requestId];
            var since = Date.now() - QUEUE_ACK_WAIT_MS;
            setTimeout(function () {
                var copy = "network";
                // The capability that counts is the one of the worker that
                // handled THIS request. Unknown, or a controller change since
                // (a new worker may answer for a request the old one queued
                // silently), means the store is unconfirmed.
                var sameWorker = !!meta && meta.generation === controllerGeneration;
                if (acked(key, !!requestId, since)) {
                    copy = queuedCopyFor(url);
                } else if (!sameWorker || meta.queuedAck !== true) {
                    copy = "unconfirmed";
                }
                finish(copy);
            }, QUEUE_ACK_WAIT_MS);
        };
    }

    document.addEventListener("htmx:responseError", onFailure("server"));
    document.addEventListener("htmx:sendError", onFailure("network"));
    document.addEventListener("htmx:timeout", onFailure("network"));
})();
