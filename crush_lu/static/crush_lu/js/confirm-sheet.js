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
 * asks only while that checkbox is ticked. data-confirm-option="<input name>"
 * plus data-confirm-option-label="..." adds an unticked checkbox to the sheet
 * and writes its state ("1" / "") into that form input on confirm (without
 * <dialog> support, a second native confirm() asks the option label). The
 * confirmed re-submit keeps the
 * clicked submit button (requestSubmit(submitter)). A form whose submit
 * buttons ask different questions puts data-confirm (and -style / -label) on
 * each <button type="submit"> instead; the clicked button's attributes win.
 */
(function () {
    "use strict";

    var dialog, msgEl, acceptBtn, cancelBtn, defaultLabel, optWrap, optInput;
    var resolveFn = null;
    // Whether the optional checkbox (data-confirm-option) was ticked on the
    // last accepted confirmation, from the sheet or the native fallback.
    var optionChecked = false;

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
        optionChecked = false;
        return new Promise(function (resolve) {
            // Old WebViews without <dialog>: fall back to the native prompt
            // rather than silently confirming. The optional checkbox becomes
            // a second native question, asked only once the first is accepted.
            if (!dialog || typeof dialog.showModal !== "function") {
                var ok = window.confirm(message);
                optionChecked = !!(ok && opts.optionLabel &&
                    window.confirm(opts.optionLabel));
                // Resolve after the submit event: requestSubmit() is a no-op
                // while the form is still firing it.
                setTimeout(function () {
                    resolve(ok);
                }, 0);
                return;
            }
            resolvePending(false); // settle any dangling promise first
            // Re-entrancy: showModal() throws InvalidStateError on an already-open
            // dialog, which would drop this confirmation. Close it first.
            if (dialog.open) dialog.close();
            msgEl.textContent = message || "";
            acceptBtn.textContent = opts.confirmLabel || defaultLabel;
            if (optWrap) {
                optWrap.hidden = !opts.optionLabel;
                optInput.checked = false;
                optWrap.querySelector("[data-confirm-option-label]").textContent =
                    opts.optionLabel || "";
            }
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
        optWrap = dialog.querySelector("[data-confirm-option]");
        optInput = optWrap && optWrap.querySelector("[data-confirm-option-input]");

        acceptBtn.addEventListener("click", function () {
            optionChecked = !!(optWrap && !optWrap.hidden && optInput.checked);
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

    // Some WebViews support <dialog> but leave SubmitEvent.submitter unset.
    // Remember the last submit button clicked in each form, so a button's own
    // data-confirm (and its name/value) survives there too. page-loading.js
    // reads the same property. Implicit submission (Enter) clicks the default
    // button, so this also covers keyboard submits.
    document.addEventListener(
        "click",
        function (evt) {
            var target = evt.target;
            if (!target || !target.closest) return;
            var btn = target.closest(
                "button, input[type=submit], input[type=image]",
            );
            if (!btn || !btn.form) return;
            var type = (btn.getAttribute("type") || "submit").toLowerCase();
            if (btn.tagName === "BUTTON" && type !== "submit") return;
            btn.form.__crushLastSubmitter = btn;
        },
        true,
    );

    // Plain (non-HTMX) forms: <form data-confirm="Question?"> asks through the
    // sheet before submitting (same data-confirm-style / -label options, read
    // from the form). The browser validates required fields first, because
    // "submit" only fires for a valid form.
    document.addEventListener("submit", function (evt) {
        var form = evt.target;
        if (!form || !form.hasAttribute) return;
        var submitter = evt.submitter || form.__crushLastSubmitter || null;
        var source =
            submitter && submitter.hasAttribute("data-confirm") ? submitter : form;
        if (!source.hasAttribute("data-confirm")) return;
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
        openConfirm(source.getAttribute("data-confirm"), {
            style: source.getAttribute("data-confirm-style") || "danger",
            confirmLabel: source.getAttribute("data-confirm-label") || undefined,
            optionLabel: source.getAttribute("data-confirm-option-label") || "",
        }).then(function (ok) {
            if (!ok) return;
            // data-confirm-option="<input name>": the sheet's checkbox fills it.
            var opt = form.elements.namedItem(
                source.getAttribute("data-confirm-option") || "",
            );
            if (opt) opt.value = optionChecked ? "1" : "";
            form.setAttribute("data-confirmed", "1");
            if (typeof form.requestSubmit === "function") {
                form.requestSubmit(submitter);
            } else {
                form.removeAttribute("data-confirmed");
                // form.submit() drops the clicked button's name/value.
                if (submitter && submitter.name) {
                    var carry = document.createElement("input");
                    carry.type = "hidden";
                    carry.name = submitter.name;
                    carry.value = submitter.value;
                    form.appendChild(carry);
                }
                form.submit();
            }
        });
    });
})();
