/**
 * Crush.lu confirmation sheet (vanilla, CSP-safe).
 *
 * Replaces the browser's window.confirm with the branded <dialog> in
 * partials/confirm_sheet.html: a bottom sheet on mobile, a centered modal on
 * desktop. Wired globally into HTMX via the htmx:confirm event, so existing
 * hx-confirm attributes keep working unchanged.
 *
 * Public API:
 *   window.crushConfirm(message, {style: 'danger'|'neutral', confirmLabel})
 *     -> Promise<boolean>
 *
 * Per-trigger customization on the hx-confirm element:
 *   data-confirm-style="neutral"   brand-purple accept button (default: danger red)
 *   data-confirm-label="..."       accept button label (default: "Confirm")
 */
(function () {
    "use strict";

    var dialog, msgEl, acceptBtn, cancelBtn, defaultLabel;
    var resolveFn = null;

    function resolvePending(result) {
        if (resolveFn) {
            var r = resolveFn;
            resolveFn = null;
            r(result);
        }
    }

    function settle(result) {
        resolvePending(result);
        if (dialog && dialog.open) {
            dialog.close();
        }
    }

    function openConfirm(message, opts) {
        opts = opts || {};
        return new Promise(function (resolve) {
            // Old WebViews without <dialog>: fall back to the native prompt
            // rather than silently confirming.
            if (!dialog || typeof dialog.showModal !== "function") {
                resolve(window.confirm(message));
                return;
            }
            resolvePending(false); // settle any dangling promise first
            // Re-entrancy: showModal() throws InvalidStateError on an already-open
            // dialog, which would drop this confirmation. Close it first.
            if (dialog.open) dialog.close();
            msgEl.textContent = message || "";
            acceptBtn.textContent = opts.confirmLabel || defaultLabel;
            dialog.classList.toggle(
                "confirm-danger",
                (opts.style || "danger") !== "neutral",
            );
            resolveFn = resolve;
            dialog.showModal();
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        dialog = document.getElementById("crush-confirm-dialog");
        if (!dialog) return;
        msgEl = dialog.querySelector("[data-confirm-message]");
        acceptBtn = dialog.querySelector("[data-confirm-accept]");
        cancelBtn = dialog.querySelector("[data-confirm-cancel]");
        defaultLabel = acceptBtn.textContent.trim();

        acceptBtn.addEventListener("click", function () {
            settle(true);
        });
        cancelBtn.addEventListener("click", function () {
            settle(false);
        });
        // Fires on Esc and on any close() — resolves as "cancelled" unless
        // the accept path already resolved.
        dialog.addEventListener("close", function () {
            resolvePending(false);
        });
        // Backdrop tap: clicks on the dialog element itself (not its content).
        dialog.addEventListener("click", function (e) {
            if (e.target === dialog) settle(false);
        });
    });

    window.crushConfirm = openConfirm;

    // htmx fires htmx:confirm for EVERY request; only intercept real
    // hx-confirm questions or polling/plain requests would silently die.
    document.addEventListener("htmx:confirm", function (evt) {
        if (!evt.detail.question) return;
        if (evt.defaultPrevented) return;
        evt.preventDefault();
        var elt = evt.detail.elt;
        openConfirm(evt.detail.question, {
            style: elt.getAttribute("data-confirm-style") || "danger",
            confirmLabel: elt.getAttribute("data-confirm-label") || undefined,
        }).then(function (ok) {
            if (ok) evt.detail.issueRequest(true); // true = skip window.confirm
        });
    });
})();
