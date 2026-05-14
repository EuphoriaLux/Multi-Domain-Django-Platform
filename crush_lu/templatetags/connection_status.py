"""
Connection-status badge mapping.

Single source of truth for the (label, tone, icon) tuple that a given
EventConnection.status should render as. Replaces the long if/elif chains
that were duplicated across _connection_card.html, _pending_submission_card.html,
and similar partials.

Usage:
    {% load connection_status %}
    {% connection_status_badge connection %}

    {# pass the assigned-coach suffix in too: #}
    {% connection_status_badge connection show_coach=True %}
"""
from django import template
from django.utils.translation import gettext_lazy as _

register = template.Library()


# (label, tone, icon-name-without-.html)
# Tones map directly to .badge-{tone} classes in tailwind-input.css.
_STATUS_BADGE_MAP = {
    "accepted": (_("Assigned to Coach"), "info", "clock"),
    "coach_reviewing": (_("Coach Reviewing"), "primary", "user"),
    "coach_approved": (_("Pending Your Consent"), "warning", "check-circle"),
    "shared": (_("Contacts Shared!"), "success", "check-circle"),
    "declined": (_("Declined"), "danger", "x-circle"),
    "pending": (_("Awaiting Response"), "warning", "clock"),
}


@register.inclusion_tag("crush_lu/components/status_badge.html")
def connection_status_badge(connection, show_coach=False):
    """Render the status badge for an EventConnection.

    Args:
        connection: EventConnection instance with .status and optional
                    .assigned_coach
        show_coach: When True and the connection has an assigned_coach,
                    appends the coach's first name as a suffix (e.g.
                    "Assigned to Coach (Anna)").

    Returns:
        Context dict for status_badge.html.
    """
    status = getattr(connection, "status", None)
    label, tone, icon = _STATUS_BADGE_MAP.get(
        status, (_("Unknown"), "info", None)
    )
    suffix = ""
    if show_coach and getattr(connection, "assigned_coach", None):
        coach_user = getattr(connection.assigned_coach, "user", None)
        first_name = getattr(coach_user, "first_name", "") if coach_user else ""
        if first_name:
            suffix = f"({first_name})"
    return {
        "label": label,
        "tone": tone,
        "icon": icon,
        "suffix": suffix,
    }
