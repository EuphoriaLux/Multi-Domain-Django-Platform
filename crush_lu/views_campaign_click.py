"""Campaign click-tracking redirect.

``/c/<token>/`` records one ``CampaignClick`` and 302s to the link's
destination (which already carries the UTM parameters). Language-neutral —
like the ``/r/<code>/`` referral redirect — because the URL lands in emails,
WhatsApp messages, and push payloads where no language prefix is known.

Recipient attribution comes from the signed ``?r=`` parameter added at send
time; a missing, tampered, or stale value silently degrades to an anonymous
click (the redirect must never break for the recipient).
"""
import logging

from django.contrib.auth.models import User
from django.core.signing import BadSignature
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404

from crush_lu.models import CampaignClick, CampaignLink
from crush_lu.newsletter_service import has_current_consent, locked_consent_holds
from crush_lu.services.campaigns import click_signer

logger = logging.getLogger(__name__)


def campaign_click_redirect(request, token):
    link = get_object_or_404(CampaignLink, token=token)

    user = None
    signed = request.GET.get('r', '')
    if signed:
        try:
            value = click_signer().unsign(signed)
            user_id, _, signed_token = value.partition(':')
            # The signature binds user AND link — a valid ?r= copied onto a
            # different campaign URL degrades to an anonymous click.
            if signed_token == token:
                user = User.objects.filter(pk=int(user_id)).first()
                # An old link must not re-attribute clicks to an erased or
                # unconsented member: degrade to an anonymous click.
                if user is not None and not has_current_consent(user):
                    user = None
            else:
                logger.info("Campaign click signature for a different link")
        except (BadSignature, ValueError):
            logger.info("Campaign click with invalid recipient signature")

    try:
        # The consent re-read (row lock) and the insert share one transaction,
        # so a deletion cannot slip in between; attribution degrades to an
        # anonymous click if consent is gone. Deletion also sweeps clicks.
        with transaction.atomic():
            if user is not None and not locked_consent_holds(user):
                user = None
            CampaignClick.objects.create(link=link, user=user)
    except Exception:  # noqa: BLE001 — tracking must never block the redirect
        logger.warning("Failed to record campaign click", exc_info=True)

    return HttpResponseRedirect(link.tracked_url)
