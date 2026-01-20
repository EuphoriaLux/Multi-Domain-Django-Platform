/**
 * HTMX CSRF Token Setup
 *
 * Sets HTMX headers from hidden input for CSRF protection.
 * Works correctly when CSRF_COOKIE_HTTPONLY=True.
 *
 * This must run synchronously before HTMX initializes, so it's
 * loaded without defer attribute.
 */
(function() {
    'use strict';

    var csrfInput = document.getElementById('csrf-token-input');
    if (csrfInput && csrfInput.value) {
        document.body.setAttribute('hx-headers', JSON.stringify({
            'X-CSRFToken': csrfInput.value
        }));
    }
})();
