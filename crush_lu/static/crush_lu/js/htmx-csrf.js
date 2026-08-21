/**
 * HTMX CSRF Token Setup
 *
 * Sets HTMX headers from hidden input for CSRF protection.
 * Works correctly when CSRF_COOKIE_HTTPONLY=True.
 *
 * Loaded with defer, which still runs this before the deferred HTMX bundle
 * (defer preserves document order) while keeping it off the critical path.
 */
(function () {
    "use strict";

    var csrfInput = document.getElementById("csrf-token-input");
    if (csrfInput && csrfInput.value) {
        document.body.setAttribute(
            "hx-headers",
            JSON.stringify({
                "X-CSRFToken": csrfInput.value,
            }),
        );
    }
})();
