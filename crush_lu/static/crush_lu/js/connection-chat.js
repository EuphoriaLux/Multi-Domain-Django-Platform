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
 */
(function () {
    "use strict";

    var NEAR_BOTTOM_PX = 80;

    function box() {
        return document.getElementById("messages-container");
    }

    function atBottom(el) {
        return el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
    }

    function scrollBottom(el) {
        el.scrollTop = el.scrollHeight;
    }

    document.addEventListener("DOMContentLoaded", function () {
        var el = box();
        if (el) scrollBottom(el);
    });

    document.body.addEventListener("htmx:beforeRequest", function (evt) {
        var el = box();
        if (!el || evt.detail.elt !== el) return; // only the poll, not the send form
        if (document.hidden) {
            evt.preventDefault(); // skip this tick; the every-20s timer stays alive
            return;
        }
        el.dataset.wasAtBottom = atBottom(el) ? "1" : "";
    });

    document.body.addEventListener("htmx:afterSwap", function (evt) {
        var el = box();
        if (!el || evt.detail.target !== el) return;
        var cfg = evt.detail.requestConfig;
        var sentOwn = cfg && cfg.verb === "post";
        if (sentOwn || el.dataset.wasAtBottom === "1") scrollBottom(el);
    });

    // Catch up immediately when the user returns to the tab.
    document.addEventListener("visibilitychange", function () {
        var el = box();
        if (el && !document.hidden && window.htmx) {
            window.htmx.trigger(el, "chat:refresh");
        }
    });
})();
