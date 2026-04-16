/**
 * Newsletter admin JS:
 * - Show/hide segment_key field based on audience selection
 * - Show/hide body_html/body_text fields based on event selection
 * - Live-update estimated recipients count via AJAX
 */
(function () {
    "use strict";

    function toggleSegmentField() {
        var audienceSelect = document.getElementById("id_audience");
        var segmentRow = document.querySelector(".field-segment_key");
        if (!audienceSelect || !segmentRow) return;

        if (audienceSelect.value === "segment") {
            segmentRow.style.display = "";
        } else {
            segmentRow.style.display = "none";
        }
    }

    function toggleEventContentFields() {
        var eventSelect = document.getElementById("id_event");
        var bodyHtmlRow = document.querySelector(".field-body_html");
        var bodyTextRow = document.querySelector(".field-body_text");
        if (!eventSelect) return;

        var hasEvent = eventSelect.value && eventSelect.value !== "";

        if (bodyHtmlRow) {
            bodyHtmlRow.style.display = hasEvent ? "none" : "";
        }
        if (bodyTextRow) {
            bodyTextRow.style.display = hasEvent ? "none" : "";
        }
    }

    var fetchTimer = null;

    function updateEstimatedRecipients() {
        // Debounce: wait 300ms after last change before fetching
        if (fetchTimer) clearTimeout(fetchTimer);
        fetchTimer = setTimeout(doFetchRecipients, 300);
    }

    function doFetchRecipients() {
        var badge = document.getElementById("estimated-recipients-badge");
        if (!badge) return;

        var audience = document.getElementById("id_audience");
        var segmentKey = document.getElementById("id_segment_key");
        var language = document.getElementById("id_language");
        var event = document.getElementById("id_event");

        var params = new URLSearchParams();
        if (audience) params.set("audience", audience.value);
        if (segmentKey) params.set("segment_key", segmentKey.value || "");
        if (language) params.set("language", language.value);
        if (event) params.set("event", event.value || "");

        // Build URL relative to the current admin page
        // The endpoint is at .../crush_lu/newsletter/estimate-recipients/
        var url = window.location.pathname
            .replace(/\/add\/$/, "/estimate-recipients/")
            .replace(/\/\d+\/change\/$/, "/estimate-recipients/");
        url += "?" + params.toString();

        badge.style.opacity = "0.5";

        fetch(url, { credentials: "same-origin" })
            .then(function (resp) {
                return resp.json();
            })
            .then(function (data) {
                var count = data.count || 0;
                var suffix = count === 1 ? "" : "s";
                badge.textContent = count + " recipient" + suffix;
                badge.style.opacity = "1";
            })
            .catch(function () {
                badge.style.opacity = "1";
            });
    }

    function toggleTypeHint() {
        var typeSelect = document.getElementById("id_newsletter_type");
        if (!typeSelect) return;

        // Remove existing hint if any
        var existingHint = document.getElementById("patch-notes-hint");
        if (existingHint) existingHint.remove();

        if (typeSelect.value === "patch_notes") {
            var hint = document.createElement("div");
            hint.id = "patch-notes-hint";
            hint.style.cssText =
                "background:#eff6ff; border:1px solid #bfdbfe; padding:10px 14px; " +
                "border-radius:6px; margin-top:8px; font-size:12px; color:#1e40af;";
            hint.innerHTML =
                "<strong>Patch Notes format tip:</strong> Use &lt;h3&gt; headers " +
                "for sections like <em>New Features</em>, <em>Improvements</em>, " +
                "and <em>Bug Fixes</em>. Use &lt;ul&gt; lists for items.";
            typeSelect.parentNode.appendChild(hint);
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        var audienceSelect = document.getElementById("id_audience");
        if (audienceSelect) {
            audienceSelect.addEventListener("change", toggleSegmentField);
            audienceSelect.addEventListener("change", updateEstimatedRecipients);
            toggleSegmentField();
        }

        var eventSelect = document.getElementById("id_event");
        if (eventSelect) {
            eventSelect.addEventListener("change", toggleEventContentFields);
            eventSelect.addEventListener("change", updateEstimatedRecipients);
            toggleEventContentFields();
        }

        var segmentSelect = document.getElementById("id_segment_key");
        if (segmentSelect) {
            segmentSelect.addEventListener("change", updateEstimatedRecipients);
        }

        var languageSelect = document.getElementById("id_language");
        if (languageSelect) {
            languageSelect.addEventListener("change", updateEstimatedRecipients);
        }

        var typeSelect = document.getElementById("id_newsletter_type");
        if (typeSelect) {
            typeSelect.addEventListener("change", toggleTypeHint);
            toggleTypeHint();
        }
    });
})();
