/**
 * Custom SMS (Crush-Admin) — compose helpers and send-list "up next" tracking.
 *
 * Compose page (#csms-compose):
 *   - shows the audience panel matching the selected radio and lazy-loads the
 *     segment <select> over HTMX the first time "segment" is chosen;
 *   - inserts placeholder chips at the textarea cursor;
 *   - keeps a live per-language character / SMS-segment counter (GSM-7 vs
 *     UCS-2, the two encodings carriers bill by);
 *   - renders a sample preview with placeholders substituted.
 *
 * Send page (#csms-send):
 *   - after every HTMX swap (a row logged / undone, progress header replaced)
 *     re-derives the first unsent row, badges it "Up next" and wires the
 *     sticky "Next" button to scroll to it. Sent state itself is server-side —
 *     the row markup carries data-sent — so a reload after switching to the
 *     SMS app and back shows the same thing.
 */
(function () {
    "use strict";

    // ------------------------------------------------------------------
    // SMS length maths
    // ------------------------------------------------------------------
    var GSM7_BASIC =
        "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?" +
        "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà";
    var GSM7_EXTENDED = "\f^{}\\[~]|€";

    function smsStats(text) {
        var units = 0;
        var gsm = true;
        for (var i = 0; i < text.length; i++) {
            var ch = text.charAt(i);
            if (GSM7_BASIC.indexOf(ch) !== -1) {
                units += 1;
            } else if (GSM7_EXTENDED.indexOf(ch) !== -1) {
                units += 2;
            } else {
                gsm = false;
                break;
            }
        }
        var segments;
        if (gsm) {
            segments = units <= 160 ? 1 : Math.ceil(units / 153);
            return { encoding: "GSM-7", units: units, segments: segments };
        }
        // UCS-2: count UTF-16 code units (astral emoji take two).
        units = text.length;
        segments = units <= 70 ? 1 : Math.ceil(units / 67);
        return { encoding: "Unicode", units: units, segments: segments };
    }

    // ------------------------------------------------------------------
    // Compose page
    // ------------------------------------------------------------------
    function initCompose(root) {
        var coachName = root.getAttribute("data-coach-name") || "Coach";
        var SAMPLE = {
            first_name: "Marie",
            coach_name: coachName,
            event_title: "Speed Dating",
            event_date: "12/09/2026",
            event_url: "https://crush.lu/en/events/12/",
        };
        var previewEl = root.querySelector(".js-preview");
        if (previewEl && previewEl.getAttribute("data-sample-first-name")) {
            SAMPLE.first_name = previewEl.getAttribute("data-sample-first-name");
        }
        var eventSelect = root.querySelector(".js-event");
        var segmentWrap = root.querySelector("#segment-select-wrap");
        var lastFocusedTextarea = null;

        function currentAudience() {
            var checked = root.querySelector(".js-audience:checked");
            return checked ? checked.value : "";
        }

        function applyAudience() {
            var audience = currentAudience();
            root.querySelectorAll(".js-audience-panel").forEach(function (panel) {
                panel.classList.toggle(
                    "csms-hidden",
                    panel.getAttribute("data-audience") !== audience,
                );
            });
            var optionalNote = root.querySelector(".js-event-optional");
            if (optionalNote) {
                optionalNote.classList.toggle("csms-hidden", audience === "event");
            }
            if (audience === "segment" && segmentWrap) {
                // hx-trigger="segments:load once" — fires the lazy load the first time.
                segmentWrap.dispatchEvent(new CustomEvent("segments:load", { bubbles: true }));
            }
        }

        function applyEventChips() {
            var hasEvent = !!(eventSelect && eventSelect.value);
            root.querySelectorAll(".js-event-chip").forEach(function (chip) {
                chip.disabled = !hasEvent;
            });
        }

        function renderSample(text) {
            return text.replace(/\{(\w+)\}/g, function (whole, key) {
                return Object.prototype.hasOwnProperty.call(SAMPLE, key) ? SAMPLE[key] : whole;
            });
        }

        function updateCounter(textarea) {
            var lang = textarea.getAttribute("data-lang");
            var counter = root.querySelector('.js-counter[data-for="' + lang + '"]');
            if (!counter) return;
            var text = textarea.value;
            if (!text) {
                counter.textContent = "";
                return;
            }
            var stats = smsStats(renderSample(text));
            counter.innerHTML =
                "<strong>" + text.length + "</strong> chars · ~" +
                stats.units + " " + stats.encoding + " units · <strong>" +
                stats.segments + "</strong> SMS segment" + (stats.segments === 1 ? "" : "s") +
                " (sample values)";
        }

        function updatePreview() {
            if (!previewEl) return;
            var source = lastFocusedTextarea || root.querySelector('.js-message[data-lang="en"]');
            var text = source ? source.value : "";
            previewEl.textContent = text ? renderSample(text) : previewEl.getAttribute("data-empty") || "";
        }

        root.querySelectorAll(".js-audience").forEach(function (radio) {
            radio.addEventListener("change", applyAudience);
        });
        if (eventSelect) {
            eventSelect.addEventListener("change", applyEventChips);
        }
        root.querySelectorAll(".js-message").forEach(function (textarea) {
            textarea.addEventListener("input", function () {
                updateCounter(textarea);
                lastFocusedTextarea = textarea;
                updatePreview();
            });
            textarea.addEventListener("focus", function () {
                lastFocusedTextarea = textarea;
                updatePreview();
            });
            updateCounter(textarea);
        });
        root.querySelectorAll(".js-chip").forEach(function (chip) {
            chip.addEventListener("click", function () {
                if (chip.disabled) return;
                var target = lastFocusedTextarea || root.querySelector('.js-message[data-lang="en"]');
                if (!target) return;
                var token = chip.getAttribute("data-token");
                var start = target.selectionStart || 0;
                var end = target.selectionEnd || 0;
                target.value = target.value.slice(0, start) + token + target.value.slice(end);
                target.selectionStart = target.selectionEnd = start + token.length;
                target.focus();
                target.dispatchEvent(new Event("input", { bubbles: true }));
            });
        });

        if (previewEl) {
            previewEl.setAttribute("data-empty", previewEl.textContent);
        }
        applyAudience();
        applyEventChips();
        updatePreview();
    }

    // ------------------------------------------------------------------
    // Send page
    // ------------------------------------------------------------------
    function initSend(root) {
        function refreshNext() {
            var rows = root.querySelectorAll(".sms-row");
            var nextRow = null;
            rows.forEach(function (row) {
                var badge = row.querySelector(".js-next-badge");
                var isUnsent = row.getAttribute("data-sent") === "0";
                if (isUnsent && !nextRow) {
                    nextRow = row;
                    row.classList.add("is-next");
                    if (badge) badge.classList.remove("csms-hidden");
                } else {
                    row.classList.remove("is-next");
                    if (badge) badge.classList.add("csms-hidden");
                }
            });
            var nameEl = root.querySelector(".js-next-name");
            if (nameEl) {
                nameEl.textContent = nextRow ? nextRow.getAttribute("data-name") || "" : "";
            }
            return nextRow;
        }

        root.addEventListener("click", function (evt) {
            var button = evt.target.closest(".js-jump-next");
            if (!button) return;
            var nextRow = refreshNext();
            if (nextRow) {
                nextRow.scrollIntoView({ behavior: "smooth", block: "center" });
            }
        });

        // Re-derive after any HTMX swap (row logged/undone, progress header OOB).
        document.body.addEventListener("htmx:afterSettle", function () {
            var nextRow = refreshNext();
            if (nextRow && document.activeElement && document.activeElement.tagName !== "BODY") {
                // Keep the newly highlighted row in view after the previous one collapsed.
                var rect = nextRow.getBoundingClientRect();
                if (rect.top < 0 || rect.bottom > window.innerHeight) {
                    nextRow.scrollIntoView({ behavior: "smooth", block: "center" });
                }
            }
        });

        refreshNext();
    }

    document.addEventListener("DOMContentLoaded", function () {
        var compose = document.getElementById("csms-compose");
        if (compose) initCompose(compose);
        var send = document.getElementById("csms-send");
        if (send) initSend(send);
    });
})();
