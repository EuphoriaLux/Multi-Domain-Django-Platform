"""
Template tags for the peer-safety (block/report) UI.

``{% block_report_menu member %}`` renders the reusable disclosure that lets the
viewer report or block another member, on any card that shows one member to
another. The tag supplies the report-reason choices itself so callers don't have
to thread them through view context.

Usage:
    {% load moderation_tags %}
    {% block_report_menu spark.sender source="spark" source_id=spark.pk %}
"""
from django import template

from crush_lu.models import UserReport

register = template.Library()


@register.inclusion_tag("crush_lu/moderation/_block_report_menu.html")
def block_report_menu(member, source="", source_id=None):
    return {
        "member": member,
        "source": source,
        "source_id": source_id,
        "report_reasons": UserReport.REASON_CHOICES,
    }
