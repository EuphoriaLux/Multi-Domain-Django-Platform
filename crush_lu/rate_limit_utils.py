"""
Shared helpers for rendering rate-limited ("too many attempts") responses.

UX Wave 3 · WP3 (finding 2-04): rate-limited auth actions used to return a
bare text/plain page with no branding, no navigation and, on the login and
password-reset paths, no translation. This module gives every caller the
same human-readable wait phrasing and a single branded fallback template
(``crush_lu/rate_limited.html``), while leaving the JSON/XHR contract used by
existing API clients untouched.
"""

from django.utils.translation import gettext as _


def humanize_wait_seconds(seconds):
    """Turn a rate-limit window into a short, translated phrase.

    ``seconds`` is the caller's rate-limit window (or, for throttles that
    track a rolling history, the throttle's own computed wait) - an upper
    bound on how long to wait, not a promise of the exact second the limit
    clears.
    """
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = 60
    seconds = max(seconds, 1)

    if seconds < 60:
        return _("less than a minute")

    minutes = max(1, round(seconds / 60))
    if minutes == 1:
        return _("about 1 minute")
    return _("about %(minutes)d minutes") % {"minutes": minutes}


def add_rate_limited_error(form, message):
    """Add a non-field error to a form that was never validated.

    ``Form.add_error()`` reads and writes ``self.cleaned_data``, which
    ``full_clean()`` only creates once a form actually runs. Login and
    signup add this error *instead of* running validation (the request was
    blocked before it even got there), so ``cleaned_data`` was never set -
    calling ``add_error()`` directly raises ``AttributeError``.
    """
    if not hasattr(form, "cleaned_data"):
        form.cleaned_data = {}
    form.add_error(None, message)
