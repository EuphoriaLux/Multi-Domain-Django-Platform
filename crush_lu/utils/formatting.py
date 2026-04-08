from django.utils.translation import gettext_lazy as _


def format_duration(td):
    """Format a timedelta as a human-readable string like '2d 5h' or '3h'."""
    if td is None:
        return None
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        return None
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h"
    minutes = (total_seconds % 3600) // 60
    return f"{minutes}m"


def format_review_estimate(avg_hours):
    """Convert average review hours to a user-friendly range string.

    Returns a translated string suitable for display to candidates.
    Falls back to a generic "24-48 hours" if avg_hours is None or zero.
    """
    if not avg_hours or avg_hours <= 0:
        return _("24-48 hours")
    if avg_hours < 2:
        return _("a few hours")
    if avg_hours < 12:
        return _("6-12 hours")
    if avg_hours < 24:
        return _("12-24 hours")
    if avg_hours < 48:
        return _("1-2 days")
    return _("2-3 days")
