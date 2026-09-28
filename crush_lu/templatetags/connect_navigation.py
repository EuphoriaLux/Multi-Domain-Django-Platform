from django import template

from crush_lu.services.connect_summary import get_connect_summary

register = template.Library()

# Connect subnav tab per url name; anything else in the Connect shell is Home.
CONNECT_TABS = {
    "connect_week_home": "today",
    "connect_week_review": "today",
    "crush_connect_catalogue_status": "today",
    "connect_week_inbox": "requests",
    "connect_week_chats": "chats",
    "connect_week_chat_detail": "chats",
}


@register.filter
def connect_tab(url_name):
    return CONNECT_TABS.get(url_name, "home")


@register.simple_tag(takes_context=True)
def connect_navigation(context):
    request = context["request"]
    if not hasattr(request, "_connect_summary"):
        request._connect_summary = get_connect_summary(request.user)
    return request._connect_summary
