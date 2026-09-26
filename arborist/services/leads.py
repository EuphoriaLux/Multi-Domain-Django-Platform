"""Consent-aware attribution and independently retryable lead notifications."""

import logging
import re

from django.db import transaction
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from azureproject.email_utils import send_domain_email
from azureproject.templatetags.analytics import stored_cookie_choice
from arborist.models import ArboristLead

logger = logging.getLogger(__name__)
ATTRIBUTION_KEY = "arborist_attribution"


def has_analytics_consent(request):
    """True only for a current, explicit analytics opt-in.

    Reads the choice exactly as the analytics tags do (stored_cookie_choice):
    the shared banner's ``cookie_consent_analytics`` flag, which it writes as
    ``accept:<group version>`` or ``decline``, then
    the banner's JSON, then the library's own cookie. An acceptance older than
    the analytics group's current version is undecided, as it is for the
    trackers, so it keeps no attribution until the visitor accepts again.
    """
    return stored_cookie_choice(request, "analytics") is True


def capture_attribution(request):
    # Unknown/declined consent never stores attribution or reuses earlier data.
    if not has_analytics_consent(request):
        request.session.pop(ATTRIBUTION_KEY, None)
        return {}
    values = {}
    for key in ("utm_source", "utm_medium", "utm_campaign"):
        value = request.GET.get(key, "")
        if value and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value):
            values[key] = value
    current = request.session.get(ATTRIBUTION_KEY, {})
    if values:
        values["landing_path"] = request.path[:200]
        current = {"first": current.get("first", values), "last": values}
        request.session[ATTRIBUTION_KEY] = current
    return current


def notify_lead(lead_id):
    """Called after commit. Success on one leg is never resent on a retry.

    A row lock serialises staff retries. Mail is bounded to two recipients/legs,
    not a bulk campaign. A delivery accepted before a process crash remains an
    inherently uncertain SMTP/Graph outcome; operators should check mail logs.
    """
    with transaction.atomic():
        lead = ArboristLead.objects.select_for_update().get(pk=lead_id)
        with translation.override(lead.language):
            customer_body = _(
                "Thank you. Your enquiry has been saved. We will contact you to discuss the next steps. Photos are reviewed by an arborist; they do not provide a safety clearance or a binding quote."
            )
            customer_subject = _("Arborist.lu — enquiry received")
        legs = [
            (
                "staff_delivery",
                ["tom@arborist.lu"],
                f"[Arborist enquiry{' — URGENT' if lead.is_urgent else ''}] {str(lead.pk)[:8]}",
                f"A new enquiry is ready for review.\nhttps://arborist.lu/arborist-admin/arborist/arboristlead/{lead.pk}/change/",
            ),
            (
                "customer_delivery",
                [lead.email] if lead.email else [],
                customer_subject,
                customer_body,
            ),
        ]
        for field, recipients, subject, body in legs:
            if getattr(lead, field) == ArboristLead.Delivery.SENT:
                continue
            if not recipients:
                setattr(lead, field, ArboristLead.Delivery.SKIPPED)
                continue
            try:
                sent = send_domain_email(
                    subject=subject,
                    message=body,
                    recipient_list=recipients,
                    domain="arborist.lu",
                    fail_silently=False,
                )
                setattr(
                    lead,
                    field,
                    (
                        ArboristLead.Delivery.SENT
                        if sent
                        else ArboristLead.Delivery.FAILED
                    ),
                )
            except Exception:
                # Never log customer content or exception strings containing it.
                logger.warning("Arborist notification failed: %s %s", lead.pk, field)
                setattr(lead, field, ArboristLead.Delivery.FAILED)
        lead.notification_attempted_at = timezone.now()
        lead.save(
            update_fields=[
                "staff_delivery",
                "customer_delivery",
                "notification_attempted_at",
            ]
        )
