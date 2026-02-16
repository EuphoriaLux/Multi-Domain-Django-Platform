"""
Web ticket page for event QR check-in.

Provides a dedicated ticket page at /events/{id}/ticket/ showing a QR code
that coaches scan at the door. This is the primary check-in mechanism and
works independently of Google Wallet.
"""

import logging

from django.conf import settings
from django.core.signing import Signer
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .decorators import crush_login_required
from .models import EventRegistration, MeetupEvent

logger = logging.getLogger(__name__)


def _generate_checkin_token(registration):
    """
    Generate a signed check-in token for a registration.

    The token encodes registration_id:event_id and is signed with Django's
    SECRET_KEY via Signer. Stored on the registration for reuse.
    """
    if registration.checkin_token:
        return registration.checkin_token

    signer = Signer()
    token = signer.sign(f"{registration.id}:{registration.event_id}")
    registration.checkin_token = token
    registration.save(update_fields=["checkin_token"])
    return token


@crush_login_required
def event_ticket(request, event_id):
    """
    Display event ticket page with QR code for check-in.

    The QR code contains a signed check-in URL that coaches scan at the door.
    Only accessible by the registration owner with confirmed/attended status.
    """
    event = get_object_or_404(MeetupEvent, id=event_id)

    registration = EventRegistration.objects.filter(
        event=event,
        user=request.user,
        status__in=["confirmed", "attended"],
    ).first()

    if not registration:
        from django.http import Http404

        raise Http404("No confirmed registration found for this event.")

    # Generate signed check-in token
    token = _generate_checkin_token(registration)

    # Build the check-in URL (language-neutral API endpoint)
    checkin_url = request.build_absolute_uri(
        f"/api/events/checkin/{registration.id}/{token}/"
    )

    # Check if already checked in
    already_checked_in = registration.status == "attended" or registration.checked_in_at is not None

    # Get display name (privacy-aware)
    try:
        profile = request.user.crushprofile
        display_name = profile.display_name
    except Exception:
        display_name = request.user.first_name or request.user.username

    # Check if Google Wallet event tickets are enabled
    wallet_enabled = getattr(settings, "WALLET_GOOGLE_EVENT_TICKET_ENABLED", True)

    context = {
        "event": event,
        "registration": registration,
        "checkin_url": checkin_url,
        "display_name": display_name,
        "already_checked_in": already_checked_in,
        "wallet_enabled": wallet_enabled,
    }

    return render(request, "crush_lu/event_ticket.html", context)
