"""Deliver queued funnel conversions (services/funnel_events.py, audit A17).

Popped here, in a tag on full pages and on the registration success partial,
and never in a context processor: those run on every render, including HTMX
partials that would consume the queue and print nothing.
"""

import json

from django import template
from django.middleware.csp import get_nonce
from django.utils.safestring import mark_safe

from azureproject.templatetags.analytics import _json_default, _tracking_suppressed
from crush_lu.services import funnel_events as funnel

register = template.Library()


def _js(value):
    """JSON for inline script: no ``</script>`` can close the tag early."""
    return (
        json.dumps(value, default=_json_default)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


# Each event fires at most once per browser even if this page is shown again
# from the service-worker cache: the event ID is remembered in localStorage.
# Where storage is unavailable the event still fires (the server already sent
# it only once).
_GUARDED = (
    "<script{nonce}>(function(){{var k={key};"
    "try{{if(localStorage.getItem(k))return;localStorage.setItem(k,'1')}}"
    "catch(e){{}}"
    "if(typeof gtag==='function')gtag('event',{name},{params});}})();</script>"
)


@register.simple_tag(takes_context=True)
def funnel_events(context):
    """Emit every conversion queued for the current member, once.

    The same gates as the ``ga4_event`` tag: nothing on iOS, nothing without a
    GA4 property, the CSP nonce, and Consent Mode in the page's gtag setup.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return ""
    entries = funnel.pop(user.pk)
    if (
        not entries
        or _tracking_suppressed(context)
        or not context.get("GOOGLE_ANALYTICS_GTAG_PROPERTY_ID")
    ):
        return ""
    nonce = get_nonce(request) if request else None
    nonce_attr = f' nonce="{nonce}"' if nonce is not None else ""
    return mark_safe(
        "".join(
            _GUARDED.format(
                nonce=nonce_attr,
                key=_js(f"crush-fe:{entry.get('eid', '')}"),
                name=_js(entry["name"]),
                params=_js(entry.get("params", {})),
            )
            for entry in entries
        )
    )
