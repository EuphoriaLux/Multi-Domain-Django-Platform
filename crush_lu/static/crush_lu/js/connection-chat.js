/**
 * Connection chat behavior (vanilla, CSP-safe).
 *
 * - Opens the thread scrolled to the newest message.
 * - After sending (HTMX beforeend append), scrolls to the new message.
 * - #messages-container polls its endpoint every 20s (hx-trigger in the
 *   template); this script pauses the poll while the tab is hidden — HTMX
 *   trigger filters like [document.visibilityState=='visible'] compile via
 *   Function() and would violate the site CSP.
 * - On poll refreshes, only re-pins to the bottom when the reader was
 *   already at (or near) the bottom, so scrolling back through history is
 *   never hijacked.
 * - Stops the visibility catch-up trigger once the server answers 286
 *   (connection declined/blocked), and reloads the page if a poll gets
 *   bounced to the login form (session expiry) instead of swapping a whole
 *   login document into the chat box.
 */
(function () {
    "use strict";

    var NEAR_BOTTOM_PX = 80;
    var SEND_GUARD_MS = 2500; // ignore stale poll swaps briefly after a send
    var stopped = false; // server said 286 (dead connection) — quiesce
    var wasAtBottom = true; // measured just before each swap
    var lastSendAt = -Infinity; // performance.now() of the last own send

    function box() {
        return document.getElementById("messages-container");
    }

    function atBottom(el) {
        return el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
    }

    function scrollBottom(el) {
        el.scrollTop = el.scrollHeight;
    }

    function looksLikeLogin(xhr) {
        var url = (xhr && xhr.responseURL) || "";
        return /\/login\b|\/accounts\/login/.test(url);
    }

    document.addEventListener("DOMContentLoaded", function () {
        var el = box();
        if (el) scrollBottom(el);
    });

    document.body.addEventListener("htmx:beforeRequest", function (evt) {
        var el = box();
        if (!el || evt.detail.elt !== el) return; // only the poll, not the send form
        if (stopped || document.hidden) {
            evt.preventDefault(); // skip this tick; the every-20s timer stays alive
        }
    });

    // Measure scroll position with the OLD content still in place.
    document.body.addEventListener("htmx:beforeSwap", function (evt) {
        var el = box();
        if (!el || evt.detail.target !== el) return;
        var xhr = evt.detail.xhr;
        // Session expired mid-poll: the request was redirected to login and the
        // response body is a full login page. Don't nest it in the chat box.
        if (looksLikeLogin(xhr)) {
            evt.detail.shouldSwap = false;
            window.location.reload();
            return;
        }
        if (xhr && xhr.status === 286) stopped = true;
        var cfg = evt.detail.requestConfig;
        var isPoll = !cfg || cfg.verb === "get";
        // A poll (full innerHTML re-render) that resolves right after a send can
        // land with DB state from before the INSERT and wipe the just-appended
        // bubble. Skip poll swaps briefly after a send; the next tick reconciles.
        if (isPoll && performance.now() - lastSendAt < SEND_GUARD_MS) {
            evt.detail.shouldSwap = false;
            return;
        }
        wasAtBottom = atBottom(el);
    });

    document.body.addEventListener("htmx:afterSwap", function (evt) {
        var el = box();
        if (!el || evt.detail.target !== el) return;
        var cfg = evt.detail.requestConfig;
        var sentOwn = cfg && cfg.verb === "post";
        if (sentOwn) lastSendAt = performance.now();
        if (sentOwn || wasAtBottom) scrollBottom(el);
    });

    // Catch up immediately when the user returns to the tab (unless stopped).
    document.addEventListener("visibilitychange", function () {
        var el = box();
        if (el && !stopped && !document.hidden && window.htmx) {
            window.htmx.trigger(el, "chat:refresh");
        }
    });
})();
