"""
Template tags for the peer-safety (block/report) UI.

``{% block_report_menu member %}`` renders the reusable disclosure that lets the
viewer report or block another member, on any card that shows one member to
another. The tag supplies the report-reason choices itself so callers don't have
to thread them through view context.

Usage:
    {% load moderation_tags %}
    {% block_report_menu spark.sender source="spark" source_id=spark.pk %}
    {% block_report_menu partner source="connect_chat" source_id=chat.pk variant="icon" %}
"""

from django import template

from crush_lu.models import UserReport

register = template.Library()


@register.inclusion_tag("crush_lu/moderation/_block_report_menu.html")
def block_report_menu(member, source="", source_id=None, variant="", block_url=""):
    """``variant="icon"`` renders the trigger as a 44px shield button with a
    floating panel (for page headers); ``block_url`` points the plain block
    form at a surface-specific endpoint (e.g. the Connect chat block, which
    also closes the chat)."""
    return {
        "member": member,
        "source": source,
        "source_id": source_id,
        "variant": variant,
        "block_url": block_url,
        "report_reasons": UserReport.REASON_CHOICES,
    }
