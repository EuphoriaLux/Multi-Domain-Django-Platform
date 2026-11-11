from django import template
from crush_lu.services.verification_funnel import funnel_visible, member_funnel

register = template.Library()


@register.simple_tag(takes_context=True)
def verification_funnel(context):
    request = context["request"]
    if not request.user.is_authenticated or not funnel_visible(request.user):
        return None
    if not hasattr(request, "_verification_funnel"):
        request._verification_funnel = member_funnel(request.user)
    if (
        request.resolver_match
        and request.resolver_match.url_name == "crush_connect_hub"
        and request._verification_funnel["state"] in ("ready", "paused")
    ):
        return None
    return request._verification_funnel
