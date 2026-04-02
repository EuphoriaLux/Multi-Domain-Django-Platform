"""
Google Wallet EventTicket JWT builder.

Builds signed JWTs for EventTicketObject passes that users can save to
Google Wallet. Each event ticket contains a QR code with the signed
check-in URL, event details, and Crush.lu branding.

Reuses crypto utilities from google_wallet.py.
"""

import json
import secrets
import time

from django.conf import settings
from django.core.signing import Signer

from .google_wallet import _base64url_encode, _load_private_key, _require_setting
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding


def _ensure_ticket_object_id(registration):
    """
    Ensure an EventRegistration has a Google Wallet ticket object ID.

    Format: {issuer_id}.event-{event_id}-reg-{reg_id}-{hex}
    """
    if registration.google_wallet_ticket_object_id:
        return registration.google_wallet_ticket_object_id

    issuer_id = _require_setting("WALLET_GOOGLE_ISSUER_ID")
    suffix = secrets.token_hex(8)
    object_id = f"{issuer_id}.event-{registration.event_id}-reg-{registration.id}-{suffix}"
    registration.google_wallet_ticket_object_id = object_id
    registration.save(update_fields=["google_wallet_ticket_object_id"])
    return object_id


def _build_checkin_url(registration, request=None):
    """
    Build the signed check-in URL for a registration.

    This URL is encoded in the QR code on both the web ticket page
    and the Google Wallet pass.
    """
    from crush_lu.views_ticket import _generate_checkin_token

    token = _generate_checkin_token(registration)

    base_url = "https://crush.lu"
    if request:
        base_url = f"{request.scheme}://{request.get_host()}"

    return f"{base_url}/api/events/checkin/{registration.id}/{token}/"


def _build_event_ticket_class_id(event):
    """Build the EventTicketClass ID for an event."""
    issuer_id = _require_setting("WALLET_GOOGLE_ISSUER_ID")
    return f"{issuer_id}.crush-event-{event.id}"


def build_google_event_ticket_jwt(registration, request=None):
    """
    Build a signed JWT for adding an event ticket to Google Wallet.

    Args:
        registration: EventRegistration instance (with event and user loaded)
        request: Optional HttpRequest for building absolute URLs

    Returns:
        str: Signed JWT string for Google Wallet save
    """
    issuer_id = _require_setting("WALLET_GOOGLE_ISSUER_ID")
    service_account_email = _require_setting("WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL")
    key_id = getattr(settings, "WALLET_GOOGLE_KEY_ID", "")

    event = registration.event
    object_id = _ensure_ticket_object_id(registration)
    class_id = _build_event_ticket_class_id(event)
    checkin_url = _build_checkin_url(registration, request)

    # Get display name (privacy-aware)
    try:
        profile = registration.user.crushprofile
        display_name = profile.display_name
    except Exception:
        display_name = registration.user.first_name or registration.user.username

    # Logo URL
    logo_url = getattr(settings, "WALLET_GOOGLE_LOGO_URL", None)
    if not logo_url:
        logo_url = "https://crush.lu/static/crush_lu/icons/android-launchericon-192-192.png"

    # Build event detail URL
    base_url = "https://crush.lu"
    if request:
        base_url = f"{request.scheme}://{request.get_host()}"

    # Format date/time for display
    event_date = event.date_time.strftime("%A, %B %d, %Y")
    event_time = event.date_time.strftime("%I:%M %p")

    # Build the EventTicketObject
    event_ticket_object = {
        "id": object_id,
        "classId": class_id,
        "state": "active",
        "header": {
            "defaultValue": {
                "language": "en-US",
                "value": event.title,
            }
        },
        "subheader": {
            "defaultValue": {
                "language": "en-US",
                "value": event.get_event_type_display(),
            }
        },
        "textModulesData": [
            {
                "id": "date",
                "header": "Date",
                "body": event_date,
            },
            {
                "id": "time",
                "header": "Time",
                "body": event_time,
            },
            {
                "id": "location",
                "header": "Location",
                "body": f"{event.location}\n{event.address}",
            },
            {
                "id": "attendee",
                "header": "Attendee",
                "body": display_name,
            },
        ],
        "barcode": {
            "type": "QR_CODE",
            "value": checkin_url,
            "alternateText": "Scan at entrance",
        },
        "cardTitle": {
            "defaultValue": {"language": "en-US", "value": "Crush.lu Event"}
        },
        "logo": {
            "sourceUri": {"uri": logo_url},
            "contentDescription": {
                "defaultValue": {"language": "en-US", "value": "Crush.lu Logo"}
            },
        },
        "hexBackgroundColor": "#9B59B6",
        "linksModuleData": {
            "uris": [
                {
                    "uri": f"{base_url}/events/{event.id}/",
                    "description": "View Event Details",
                },
                {
                    "uri": "https://crush.lu",
                    "description": "Crush.lu",
                },
            ]
        },
    }

    # Add hero image if event has a banner
    if event.image:
        try:
            event_ticket_object["heroImage"] = {
                "sourceUri": {"uri": event.image.url},
                "contentDescription": {
                    "defaultValue": {"language": "en-US", "value": event.title}
                },
            }
        except Exception:
            pass

    # Build the EventTicketClass (inline in JWT)
    event_ticket_class = {
        "id": class_id,
        "issuerName": "Crush.lu",
        "eventName": {
            "defaultValue": {"language": "en-US", "value": event.title}
        },
        "venue": {
            "name": {
                "defaultValue": {"language": "en-US", "value": event.location}
            },
            "address": {
                "defaultValue": {"language": "en-US", "value": event.address}
            },
        },
        "dateTime": {
            "start": event.date_time.isoformat(),
        },
        "reviewStatus": "UNDER_REVIEW",
        "hexBackgroundColor": "#9B59B6",
        "logo": {
            "sourceUri": {"uri": logo_url},
            "contentDescription": {
                "defaultValue": {"language": "en-US", "value": "Crush.lu"}
            },
        },
    }

    if event.duration_minutes:
        event_ticket_class["dateTime"]["end"] = event.end_time.isoformat()

    # Build JWT payload
    issued_at = int(time.time())
    payload = {
        "iss": service_account_email,
        "aud": "google",
        "typ": "savetowallet",
        "iat": issued_at,
        "exp": issued_at + 3600,
        "payload": {
            "eventTicketClasses": [event_ticket_class],
            "eventTicketObjects": [event_ticket_object],
        },
    }

    # Sign JWT
    header = {"alg": "RS256", "typ": "JWT"}
    if key_id:
        header["kid"] = key_id

    signing_input = b".".join([
        _base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _base64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    ])

    private_key = _load_private_key()
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    signed_jwt = b".".join([signing_input, _base64url_encode(signature)])
    return signed_jwt.decode("utf-8")
