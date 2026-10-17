"""Deliver queued funnel conversions (services/funnel_events.py, audit A17).

Popped here, in a tag on full pages and on the registration success partial,
and never in a context processor: those run on every render, including HTMX
partials that would consume the queue and print nothing.
"""

from django import template
from django.utils.safestring import mark_safe

from azureproject.templatetags.analytics import ga4_event
from crush_lu.services import funnel_events as funnel

register = template.Library()


@register.simple_tag(takes_context=True)
def funnel_events(context):
    """Emit every conversion queued for the current member, once.

    Each goes through ``ga4_event``, so Consent Mode, the CSP nonce and iOS
    suppression apply exactly as for any other GA4 event.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return ""
    return mark_safe(
        "".join(
            ga4_event(context, entry["name"], **entry.get("params", {}))
            for entry in funnel.pop(user.pk)
        )
    )
