/** Shared focus lifecycle for the lobby's irreversible confirmation sheets. */
(function () {
    "use strict";

    function usable(element) {
        return element && element.isConnected && !element.disabled &&
            !element.closest("[inert]") && element.getClientRects().length > 0 &&
            window.getComputedStyle(element).visibility !== "hidden";
    }

    window.makeEventConfirmDialog = function (dialog, onClose, restoreTarget) {
        var active = false;
        var destroyed = false;
        var opener = null;
        var overflow = "";
        var background = [];
        var generation = 0;

        function controls() {
            return Array.from(dialog.querySelectorAll(
                'button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
            )).filter(usable);
        }

        function initialFocus() {
            var target = dialog.querySelector("[data-confirm-cancel]");
            if (usable(target)) target.focus();
        }

        function keydown(event) {
            if (!active) return;
            if (event.key === "Escape") {
                event.preventDefault();
                onClose();
                return;
            }
            if (event.key !== "Tab") return;
            var buttons = controls();
            var first = buttons[0];
            var last = buttons[buttons.length - 1];
            if (!first) {
                event.preventDefault();
                dialog.focus();
            } else if (event.shiftKey &&
                (document.activeElement === first || !dialog.contains(document.activeElement))) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey &&
                (document.activeElement === last || !dialog.contains(document.activeElement))) {
                event.preventDefault();
                first.focus();
            }
        }

        function containFocus(event) {
            if (active && !dialog.contains(event.target)) initialFocus();
        }

        function isolateBackground() {
            var child = dialog;
            while (child.parentElement && child.parentElement !== document.documentElement) {
                Array.from(child.parentElement.children).forEach(function (sibling) {
                    if (sibling === child || ["SCRIPT", "STYLE", "TEMPLATE"].indexOf(sibling.tagName) !== -1) return;
                    background.push({
                        element: sibling,
                        inert: sibling.getAttribute("inert"),
                        hidden: sibling.getAttribute("aria-hidden"),
                    });
                    sibling.setAttribute("inert", "");
                    sibling.setAttribute("aria-hidden", "true");
                });
                child = child.parentElement;
            }
        }

        function restoreAttribute(element, name, value) {
            if (value === null) element.removeAttribute(name);
            else element.setAttribute(name, value);
        }

        function nativeClose() {
            // close() dispatches asynchronously: an older close must not
            // cancel a sheet which has already reopened.
            if (active && !dialog.open) onClose();
        }

        function cancel(event) {
            event.preventDefault();
            onClose();
        }

        dialog.addEventListener("close", nativeClose);
        dialog.addEventListener("cancel", cancel);
        return {
            open: function (trigger) {
                if (destroyed || !dialog.isConnected) return;
                generation += 1;
                if (!active) {
                    opener = trigger || document.activeElement;
                    overflow = document.body.style.overflow;
                    active = true;
                    document.body.style.overflow = "hidden";
                    document.addEventListener("keydown", keydown, true);
                    document.addEventListener("focusin", containFocus);
                    if (typeof dialog.showModal === "function") dialog.showModal();
                    else {
                        dialog.setAttribute("open", "");
                        isolateBackground();
                    }
                }
                initialFocus();
            },
            close: function (restore) {
                if (!active) return;
                active = false;
                var closedGeneration = ++generation;
                document.removeEventListener("keydown", keydown, true);
                document.removeEventListener("focusin", containFocus);
                if (dialog.open && typeof dialog.close === "function") dialog.close();
                else dialog.removeAttribute("open");
                background.forEach(function (saved) {
                    restoreAttribute(saved.element, "inert", saved.inert);
                    restoreAttribute(saved.element, "aria-hidden", saved.hidden);
                });
                background = [];
                document.body.style.overflow = overflow;
                var previousOpener = opener;
                opener = null;
                function returnFocus(attempts) {
                    if (active || generation !== closedGeneration) return;
                    var target = restoreTarget(previousOpener, usable);
                    if (usable(target)) target.focus();
                    // Alpine's phase x-show may reveal the next step on the
                    // following frame. Never leave focus on a hidden sheet.
                    else if (attempts > 0) {
                        requestAnimationFrame(function () { returnFocus(attempts - 1); });
                    }
                }
                if (restore !== false) requestAnimationFrame(function () { returnFocus(2); });
            },
            destroy: function () {
                destroyed = true;
                this.close(false);
                dialog.removeEventListener("close", nativeClose);
                dialog.removeEventListener("cancel", cancel);
            },
        };
    };
})();
