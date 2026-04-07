"""
Apple Wallet EventTicket .pkpass builder.

Builds .pkpass files with eventTicket style for event registrations.
Each ticket contains a QR code with the signed check-in URL, event
details, and Crush.lu branding.

Reuses signing infrastructure from apple_pass.py.
"""

import secrets
from datetime import timedelta

from django.conf import settings

from .apple_pass import _build_pkpass, _ensure_pass_identifiers, _require_setting


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


def _build_checkin_url(registration, request=None):
    """
    Build the signed check-in URL for a registration.

    Reuses the same token generation as the web ticket page and Google Wallet.
    """
    from crush_lu.views_ticket import _generate_checkin_token

    token = _generate_checkin_token(registration)

    base_url = "https://crush.lu"
    if request:
        base_url = f"{request.scheme}://{request.get_host()}"

    return f"{base_url}/api/events/checkin/{registration.id}/{token}/"


def build_apple_event_ticket(registration, request=None):
    """
    Build a .pkpass EventTicket for an event registration.

    Args:
        registration: EventRegistration instance (with event and user loaded)
        request: Optional HttpRequest for building absolute URLs

    Returns:
        bytes: .pkpass file contents
    """
    pass_type_identifier = _require_setting("WALLET_APPLE_PASS_TYPE_IDENTIFIER")
    team_identifier = _require_setting("WALLET_APPLE_TEAM_IDENTIFIER")
    organization_name = _require_setting("WALLET_APPLE_ORGANIZATION_NAME")
    web_service_url = getattr(settings, "WALLET_APPLE_WEB_SERVICE_URL", "")

    event = registration.event
    serial_number = _ensure_event_ticket_serial(registration)
    checkin_url = _build_checkin_url(registration, request)

    # Get display name (privacy-aware)
    try:
        profile = registration.user.crushprofile
        display_name = profile.display_name
        # Reuse profile auth token for PassKit web service
        _, auth_token = _ensure_pass_identifiers(profile)
    except Exception:
        display_name = registration.user.first_name or registration.user.username
        auth_token = secrets.token_hex(16)

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
