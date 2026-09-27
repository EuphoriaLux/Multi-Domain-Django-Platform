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
 *
 * Plain POST forms (no HTMX): put data-confirm="Question?" (plus the two
 * options above) on the <form> itself. data-confirm-when="<checkbox name>"
 * asks only while that checkbox is ticked. The confirmed re-submit keeps the
 * clicked submit button (requestSubmit(submitter)).
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

    // Plain (non-HTMX) forms: <form data-confirm="Question?"> asks through the
    // sheet before submitting (same data-confirm-style / -label options, read
    // from the form). The browser validates required fields first, because
    // "submit" only fires for a valid form.
    document.addEventListener("submit", function (evt) {
        var form = evt.target;
        if (!form || !form.hasAttribute || !form.hasAttribute("data-confirm")) {
            return;
        }
        if (form.getAttribute("data-confirmed") === "1") {
            form.removeAttribute("data-confirmed"); // one pass per confirmation
            return;
        }
        // data-confirm-when="<checkbox name>" asks only while that box is
        // ticked; page-loading.js mirrors this rule to keep its overlay off.
        var when = form.getAttribute("data-confirm-when");
        if (when) {
            var box = form.elements.namedItem(when);
            if (!box || !box.checked) return;
        }
        evt.preventDefault();
        var submitter = evt.submitter || null;
        openConfirm(form.getAttribute("data-confirm"), {
            style: form.getAttribute("data-confirm-style") || "danger",
            confirmLabel: form.getAttribute("data-confirm-label") || undefined,
        }).then(function (ok) {
            if (!ok) return;
            form.setAttribute("data-confirmed", "1");
            if (typeof form.requestSubmit === "function") {
                form.requestSubmit(submitter);
            } else {
                form.removeAttribute("data-confirmed");
                form.submit();
            }
        });
    });
})();
