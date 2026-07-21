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
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404

from crush_lu.models import CampaignClick, CampaignLink
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
            else:
                logger.info("Campaign click signature for a different link")
        except (BadSignature, ValueError):
            logger.info("Campaign click with invalid recipient signature")

    try:
        CampaignClick.objects.create(link=link, user=user)
    except Exception:  # noqa: BLE001 — tracking must never block the redirect
        logger.warning("Failed to record campaign click", exc_info=True)

    return HttpResponseRedirect(link.tracked_url)
