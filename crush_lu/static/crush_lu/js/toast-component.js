/**
 * Toast Notification Component for Crush.lu
 * Vanilla JS rendering (no Alpine x-for) to avoid CSP eval issues.
 * The public API is still Alpine.store('toasts').add({...}).
 *
 * Usage:
 * Alpine.store('toasts').add({ type: 'success', message: 'Profile saved!' })
 * Or via event: window.dispatchEvent(new CustomEvent('show-toast', { detail: { type: 'success', message: 'Done!' }}))
 *
 * Optional action button (e.g. "Undo"): action: { label: 'Undo', form: '<form id>' }
 * submits that <form> in the page. A server-rendered page can raise a toast on
 * load with <div hidden data-toast data-toast-type="success"
 * data-toast-message="..." data-toast-action-label="Undo"
 * data-toast-action-form="<form id>" data-toast-duration="0"></div>.
 * Give action toasts duration 0 so they stay until the user acts or dismisses
 * them (WCAG 2.2.1: an Undo must not time out under a keyboard/SR user).
 */

(function () {
    "use strict";

    var TYPE_STYLES = {
        success:
            "bg-green-50 border-green-200 text-green-800 dark:bg-green-900/30 dark:border-green-700 dark:text-green-300",
        error: "bg-red-50 border-red-200 text-red-800 dark:bg-red-900/30 dark:border-red-700 dark:text-red-300",
        warning:
            "bg-yellow-50 border-yellow-200 text-yellow-800 dark:bg-yellow-900/30 dark:border-yellow-700 dark:text-yellow-300",
        info: "bg-blue-50 border-blue-200 text-blue-800 dark:bg-blue-900/30 dark:border-blue-700 dark:text-blue-300",
    };

    var TYPE_ICON_PATHS = {
        success: "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z",
        error: "M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z",
        warning:
            "M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z",
        info: "M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
    };

    var TYPE_ICON_COLORS = {
        success: "text-green-400",
        error: "text-red-400",
        warning: "text-yellow-400",
        info: "text-blue-400",
    };

    var DISMISS_ICON =
        "M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z";

    // Hidden state slides down on mobile (bottom snackbar) and right on lg+
    // (top-right stack). Keep these as full literals so the Tailwind scanner
    // emits them.
    var HIDDEN_CLASSES = [
        "opacity-0",
        "translate-y-full",
        "lg:translate-y-0",
        "lg:translate-x-full",
    ];
    var VISIBLE_CLASSES = ["opacity-100", "translate-y-0", "translate-x-0"];

    function getContainer() {
        return document.getElementById("toast-container");
    }

    function buildToastElement(toast) {
        var styles = TYPE_STYLES[toast.type] || TYPE_STYLES.info;
        var iconPath = TYPE_ICON_PATHS[toast.type] || TYPE_ICON_PATHS.info;
        var iconColor = TYPE_ICON_COLORS[toast.type] || TYPE_ICON_COLORS.info;

        // Outer wrapper
        var el = document.createElement("div");
        el.setAttribute("role", "alert");
        el.setAttribute("data-toast-id", toast.id);
        el.className =
            "pointer-events-auto w-full max-w-md lg:w-96 lg:max-w-sm rounded-lg border-2 shadow-lg p-4 transform transition ease-out duration-300 motion-reduce:transition-none " +
            HIDDEN_CLASSES.join(" ") +
            " " +
            styles;

        // Inner flex row
        var row = document.createElement("div");
        row.className = "flex items-start gap-3";

        // Icon
        var iconWrap = document.createElement("div");
        iconWrap.className = "flex-shrink-0";
        var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("class", "h-6 w-6 " + iconColor);
        svg.setAttribute("fill", "none");
        svg.setAttribute("stroke", "currentColor");
        svg.setAttribute("viewBox", "0 0 24 24");
        var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("stroke-linecap", "round");
        path.setAttribute("stroke-linejoin", "round");
        path.setAttribute("stroke-width", "2");
        path.setAttribute("d", iconPath);
        svg.appendChild(path);
        iconWrap.appendChild(svg);
        row.appendChild(iconWrap);

        // Message
        var msgWrap = document.createElement("div");
        msgWrap.className = "flex-1 min-w-0 pt-0.5";
        var msgP = document.createElement("p");
        // break-words: a message may carry a long URL (#1116 copy fallback).
        msgP.className = "text-sm font-medium break-words";
        msgP.textContent = toast.message || "";
        msgWrap.appendChild(msgP);
        row.appendChild(msgWrap);

        // Optional action (e.g. "Undo"): submits a form that lives in the page.
        if (toast.action && toast.action.label && toast.action.form) {
            var actionBtn = document.createElement("button");
            actionBtn.type = "button";
            actionBtn.className =
                "flex-shrink-0 self-center min-h-11 px-3 rounded-md text-sm font-semibold underline underline-offset-2 hover:no-underline focus:outline-none focus:ring-2 focus:ring-purple-500";
            actionBtn.textContent = toast.action.label;
            actionBtn.addEventListener("click", function () {
                var form = document.getElementById(toast.action.form);
                removeToast(toast.id);
                if (!form) return;
                if (typeof form.requestSubmit === "function") {
                    form.requestSubmit();
                } else {
                    form.submit();
                }
            });
            row.appendChild(actionBtn);
        }

        // Dismiss button
        if (toast.dismissible !== false) {
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className =
                "flex-shrink-0 inline-flex rounded-md p-1.5 hover:bg-black hover:bg-opacity-10 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 transition-colors";
            btn.setAttribute("aria-label", "Dismiss notification");
            var btnSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
            btnSvg.setAttribute("class", "h-5 w-5");
            btnSvg.setAttribute("viewBox", "0 0 20 20");
            btnSvg.setAttribute("fill", "currentColor");
            var btnPath = document.createElementNS(
                "http://www.w3.org/2000/svg",
                "path",
            );
            btnPath.setAttribute("fill-rule", "evenodd");
            btnPath.setAttribute("clip-rule", "evenodd");
            btnPath.setAttribute("d", DISMISS_ICON);
            btnSvg.appendChild(btnPath);
            btn.appendChild(btnSvg);
            btn.addEventListener("click", function () {
                removeToast(toast.id);
            });
            row.appendChild(btn);
        }

        el.appendChild(row);

        // Animate in on next frame
        requestAnimationFrame(function () {
            HIDDEN_CLASSES.forEach(function (c) {
                el.classList.remove(c);
            });
            VISIBLE_CLASSES.forEach(function (c) {
                el.classList.add(c);
            });
        });

        return el;
    }

    function removeToast(id) {
        var container = getContainer();
        if (!container) return;

        var el = container.querySelector('[data-toast-id="' + id + '"]');
        if (!el) return;

        // Leave the store now, not after the exit animation: a toast that is
        // on its way out no longer counts as showing, so a repeat of the same
        // message raised in the next 300 ms (dismiss, retry, fail again) is
        // shown rather than deduplicated away by htmx-error-toast.js.
        if (typeof Alpine !== "undefined") {
            Alpine.store("toasts").remove(id);
        }

        // Animate out
        VISIBLE_CLASSES.forEach(function (c) {
            el.classList.remove(c);
        });
        HIDDEN_CLASSES.forEach(function (c) {
            el.classList.add(c);
        });

        setTimeout(function () {
            if (el.parentNode) {
                el.parentNode.removeChild(el);
            }
        }, 300);
    }

    function addToast(toast) {
        var container = getContainer();
        if (!container) return;

        var el = buildToastElement(toast);
        container.appendChild(el);

        // Auto-dismiss
        if (toast.duration > 0) {
            setTimeout(function () {
                removeToast(toast.id);
            }, toast.duration);
        }
    }

    // Register Alpine store when Alpine initialises
    document.addEventListener("alpine:init", function () {
        Alpine.store("toasts", {
            items: [],
            maxVisible: 3,
            defaultDuration: 5000,

            add: function (toast) {
                var id = Date.now() + Math.random();
                var newToast = {
                    id: id,
                    type: toast.type || "info",
                    message: toast.message || "",
                    duration:
                        toast.duration !== undefined
                            ? toast.duration
                            : this.defaultDuration,
                    dismissible: toast.dismissible !== false,
                    action: toast.action || null,
                };

                this.items.push(newToast);

                // Limit visible toasts
                while (this.items.length > this.maxVisible) {
                    var removed = this.items.shift();
                    removeToast(removed.id);
                }

                // Render via DOM
                addToast(newToast);

                return id;
            },

            remove: function (id) {
                for (var i = 0; i < this.items.length; i++) {
                    if (this.items[i].id === id) {
                        this.items.splice(i, 1);
                        break;
                    }
                }
            },

            clear: function () {
                var container = getContainer();
                if (container) {
                    container.innerHTML = "";
                }
                this.items = [];
            },
        });

        // Server-rendered toasts: <div hidden data-toast ...> (see header).
        function raiseServerToasts() {
            var nodes = document.querySelectorAll("[data-toast]");
            Array.prototype.forEach.call(nodes, function (node) {
                var d = node.dataset;
                var duration = parseInt(d.toastDuration, 10);
                Alpine.store("toasts").add({
                    type: d.toastType || "info",
                    message: d.toastMessage || "",
                    duration: isNaN(duration) ? undefined : duration,
                    action: d.toastActionLabel
                        ? { label: d.toastActionLabel, form: d.toastActionForm }
                        : null,
                });
                node.removeAttribute("data-toast"); // raise once
            });
        }
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", raiseServerToasts);
        } else {
            raiseServerToasts();
        }

        // Listen for global toast events
        window.addEventListener("show-toast", function (event) {
            if (event.detail) {
                Alpine.store("toasts").add(event.detail);
            }
        });

        // Listen for HTMX events
        document.body.addEventListener("htmx:afterRequest", function (event) {
            var response = event.detail.xhr;
            var triggerHeader = response.getResponseHeader("HX-Trigger");
            if (triggerHeader) {
                try {
                    var triggers = JSON.parse(triggerHeader);
                    if (triggers.showToast) {
                        Alpine.store("toasts").add(triggers.showToast);
                    }
                } catch (e) {
                    // Silent fail if not JSON
                }
            }
        });
    });
})();
