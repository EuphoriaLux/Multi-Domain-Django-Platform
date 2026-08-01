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
    # Prefer an explicit caller-supplied URL (forwarded by the PassKit update
    # path), then the setting, then derive from the request so the ticket
    # always advertises a webServiceURL alongside its authenticationToken.
    web_service_url = resolve_web_service_url(request, web_service_url)

    event = registration.event
    serial_number = _ensure_event_ticket_serial(registration)
    # When rebuilding via the PassKit web service there is no request, so derive
    # the check-in origin from the forwarded web_service_url — otherwise the QR
    # silently flips to the hardcoded https://crush.lu and a staging ticket's
    # check-in token won't validate in production.
    checkin_base_url = _origin_from_url(web_service_url)
    checkin_url = _build_checkin_url(
        registration, request, base_url=checkin_base_url
    )

    # Get display name (privacy-aware). Open-event registration can create a
    # user with no CrushProfile; get_or_create one so the PassKit auth token is
    # PERSISTED and resolvable by the web service. The previous fallback
    # generated a token with secrets.token_hex(16) but never saved it, so the
    # resolver could never find it and every such ticket's update request 401'd.
    profile, _ = CrushProfile.objects.get_or_create(user=registration.user)
    display_name = profile.display_name or registration.user.first_name or registration.user.username
    # Reuse profile auth token for PassKit web service
    _, auth_token = _ensure_pass_identifiers(profile)

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

    if web_service_url:
        payload["webServiceURL"] = web_service_url

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
