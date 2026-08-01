"""
Apple Wallet EventTicket .pkpass builder.

Builds .pkpass files with eventTicket style for event registrations.
Each ticket contains a QR code with the signed check-in URL, event
details, and Crush.lu branding.

Reuses signing infrastructure from apple_pass.py.
"""

import secrets
from datetime import timedelta
from urllib.parse import urlparse

from .apple_pass import (
    _build_pkpass,
    _ensure_pass_identifiers,
    _require_setting,
    resolve_web_service_url,
)
from ..models import CrushProfile


def _ensure_event_ticket_serial(registration):
    """
    Ensure an EventRegistration has an Apple Wallet ticket serial number.

    Format: evt-{event_id}-reg-{reg_id}-{hex8}
    """
    if registration.apple_wallet_ticket_serial:
        return registration.apple_wallet_ticket_serial

    suffix = secrets.token_hex(8)
    serial = f"evt-{registration.event_id}-reg-{registration.id}-{suffix}"
    registration.apple_wallet_ticket_serial = serial
    registration.save(update_fields=["apple_wallet_ticket_serial"])
    return serial


def _origin_from_url(url):
    """Return scheme://host for a URL, or None if it can't be parsed.

    Used to carry the originating host (e.g. test.crush.lu vs crush.lu) from the
    forwarded PassKit web_service_url into the rebuilt ticket's check-in URL.
    """
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


def _ensure_ticket_auth_token(registration):
    """Per-ticket PassKit auth token, persisted on the EventRegistration.

    Open-event registration allows an attendee with no CrushProfile, and the
    web service still has to authenticate that ticket's update requests — so
    the token needs somewhere resolvable to live. Creating a placeholder
    CrushProfile is NOT an option: profile existence is application state
    elsewhere (views_events.py only requires the 18+ self-attestation when
    `profile is None`, and the account adapter redirects any user with a
    profile straight to the dashboard instead of profile creation), so merely
    downloading a ticket would silently skip both gates.
    """
    if registration.apple_wallet_auth_token:
        return registration.apple_wallet_auth_token

    token = secrets.token_hex(16)
    registration.apple_wallet_auth_token = token
    registration.save(update_fields=["apple_wallet_auth_token"])
    return token


def _resolve_checkin_base_url(
    request=None, forwarded_web_service_url=None, resolved_web_service_url=None
):
    """Pick the origin the ticket's check-in QR must point at.

    A check-in token only validates against the environment that issued it, so
    this is deliberately NOT tied to WALLET_APPLE_WEB_SERVICE_URL: a ticket
    downloaded from test.crush.lu has to keep pointing there even when the
    setting names production. Whenever a live request exists its host is the
    truth, so return None and let _build_checkin_url derive it.

    Only the request-less PassKit rebuild needs a fallback, and there the
    FORWARDED url wins over the resolved one — get_latest_pass derives the
    forwarded value from Apple's own live request, whereas the resolved value
    prefers the (host-stable, possibly cross-slot) setting.
    """
    if request is not None:
        return None
    return _origin_from_url(forwarded_web_service_url) or _origin_from_url(
        resolved_web_service_url
    )


def _build_checkin_url(registration, request=None, base_url=None):
    """
    Build the signed check-in URL for a registration.

    Reuses the same token generation as the web ticket page and Google Wallet.

    base_url, when supplied, is used as the host origin (scheme://host) —
    forwarded by the PassKit rebuild path so a ticket installed from a staging
    slot keeps its original check-in host instead of flipping to crush.lu.
    """
    from crush_lu.views_ticket import _generate_checkin_token

    token = _generate_checkin_token(registration)

    if not base_url:
        base_url = "https://crush.lu"
        if request:
            base_url = f"{request.scheme}://{request.get_host()}"

    return f"{base_url}/api/events/checkin/{registration.id}/{token}/"


def build_apple_event_ticket(registration, request=None, web_service_url=None):
    """
    Build a .pkpass EventTicket for an event registration.

    Args:
        registration: EventRegistration instance (with event and user loaded)
        request: Optional HttpRequest for building absolute URLs
        web_service_url: Optional explicit webServiceURL — forwarded by the
            PassKit web-service provider when rebuilding a ticket on update.

    Returns:
        bytes: .pkpass file contents
    """
    pass_type_identifier = _require_setting("WALLET_APPLE_PASS_TYPE_IDENTIFIER")
    team_identifier = _require_setting("WALLET_APPLE_TEAM_IDENTIFIER")
    organization_name = _require_setting("WALLET_APPLE_ORGANIZATION_NAME")
    # The webServiceURL embedded in the pass: the setting wins (operator-
    # controlled root), then an explicit caller-supplied URL (forwarded by the
    # PassKit update path), then the request — so the ticket always advertises
    # a webServiceURL alongside its authenticationToken.
    resolved_web_service_url = resolve_web_service_url(request, web_service_url)

    event = registration.event
    serial_number = _ensure_event_ticket_serial(registration)
    checkin_base_url = _resolve_checkin_base_url(
        request,
        forwarded_web_service_url=web_service_url,
        resolved_web_service_url=resolved_web_service_url,
    )
    checkin_url = _build_checkin_url(
        registration, request, base_url=checkin_base_url
    )

    # Get display name (privacy-aware). Open-event registration allows a user
    # with no CrushProfile, and we must NOT create one here — profile existence
    # gates the 18+ self-attestation and the onboarding redirect elsewhere (see
    # _ensure_ticket_auth_token). Such tickets get their own persisted token on
    # the registration instead.
    profile = CrushProfile.objects.filter(user=registration.user).first()
    display_name = (
        (profile.display_name if profile else "")
        or registration.user.first_name
        or registration.user.username
    )
    # Auth-token precedence mirrors _resolve_auth_token_from_profile in
    # passkit_service: a token already persisted on the registration wins,
    # because that is the token the installed pass carries — a CrushProfile
    # created later must not invalidate an already-issued ticket.
    if registration.apple_wallet_auth_token:
        auth_token = registration.apple_wallet_auth_token
    elif profile is not None:
        # Reuse profile auth token for PassKit web service
        _, auth_token = _ensure_pass_identifiers(profile)
    else:
        auth_token = _ensure_ticket_auth_token(registration)

    # Format date/time
    event_date = event.date_time.strftime("%a, %b %d, %Y")
    event_time = event.date_time.strftime("%I:%M %p")

    payload = {
        "formatVersion": 1,
        "passTypeIdentifier": pass_type_identifier,
        "serialNumber": serial_number,
        "teamIdentifier": team_identifier,
        "organizationName": organization_name,
        "description": f"Crush.lu Event: {event.title}",
        "authenticationToken": auth_token,
        "logoText": "Crush.lu",
        "eventTicket": {
            "primaryFields": [
                {
                    "key": "event_name",
                    "label": "Event",
                    "value": event.title,
                }
            ],
            "secondaryFields": [
                {
                    "key": "date",
                    "label": "Date",
                    "value": event_date,
                },
                {
                    "key": "time",
                    "label": "Time",
                    "value": event_time,
                },
            ],
            "auxiliaryFields": [
                {
                    "key": "location",
                    "label": "Location",
                    "value": event.location,
                },
                {
                    "key": "attendee",
                    "label": "Attendee",
                    "value": display_name,
                },
            ],
            "backFields": [
                {
                    "key": "address",
                    "label": "Address",
                    "value": event.address,
                },
                {
                    "key": "event_type",
                    "label": "Type",
                    "value": event.get_event_type_display(),
                },
                {
                    "key": "ticket_info",
                    "label": "Check-in",
                    "value": "Show the QR code to the coach at the event entrance.",
                },
            ],
        },
        "groupingIdentifier": pass_type_identifier,
        "sharingProhibited": True,
        "relevantDate": event.date_time.isoformat(),
        "expirationDate": (
            event.date_time + timedelta(minutes=event.duration_minutes)
        ).isoformat(),
        "backgroundColor": "rgb(155, 89, 182)",
        "foregroundColor": "rgb(255, 255, 255)",
        "labelColor": "rgb(255, 220, 230)",
        "barcode": {
            "format": "PKBarcodeFormatQR",
            "message": checkin_url,
            "messageEncoding": "iso-8859-1",
            "altText": "Scan at entrance",
        },
        "barcodes": [
            {
                "format": "PKBarcodeFormatQR",
                "message": checkin_url,
                "messageEncoding": "iso-8859-1",
                "altText": "Scan at entrance",
            }
        ],
    }

    if resolved_web_service_url:
        payload["webServiceURL"] = resolved_web_service_url

    # Add venue location for lock-screen surfacing
    if event.latitude and event.longitude:
        payload["locations"] = [
            {
                "latitude": float(event.latitude),
                "longitude": float(event.longitude),
                "relevantText": f"Check in for {event.title}",
            }
        ]

    return _build_pkpass(payload)
