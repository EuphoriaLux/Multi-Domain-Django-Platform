from django import template

from crush_lu.services.connect_summary import get_connect_summary

register = template.Library()


@register.simple_tag(takes_context=True)
def connect_navigation(context):
    request = context["request"]
    if not hasattr(request, "_connect_summary"):
        request._connect_summary = get_connect_summary(request.user)
    return request._connect_summary
