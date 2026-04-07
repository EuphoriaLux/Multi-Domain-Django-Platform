import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509 import load_pem_x509_certificate
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from ..wallet_pass import build_wallet_pass_data

# Placeholder 1x1 transparent PNG for icon (required by Apple)
ICON_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwAB"
    "BAEAu1fE3wAAAABJRU5ErkJggg=="
)


def _require_setting(name):
    value = getattr(settings, name, None)
    if not value:
        raise ImproperlyConfigured(f"Missing required setting: {name}")
    return value


def _load_cert_bytes():
    """
    Load certificate, key, and WWDR cert as bytes.

    Checks _BASE64 env vars first (for Azure production),
    falls back to _PATH file reads (for local development).

    Returns:
        tuple: (cert_pem_bytes, key_pem_bytes, wwdr_pem_bytes)
    """
    cert_b64 = getattr(settings, "WALLET_APPLE_CERT_BASE64", "")
    key_b64 = getattr(settings, "WALLET_APPLE_KEY_BASE64", "")
    wwdr_b64 = getattr(settings, "WALLET_APPLE_WWDR_CERT_BASE64", "")

    if cert_b64 and key_b64 and wwdr_b64:
        return (
            base64.b64decode(cert_b64),
            base64.b64decode(key_b64),
            base64.b64decode(wwdr_b64),
        )

    cert_path = _require_setting("WALLET_APPLE_CERT_PATH")
    key_path = _require_setting("WALLET_APPLE_KEY_PATH")
    wwdr_path = _require_setting("WALLET_APPLE_WWDR_CERT_PATH")

    with open(cert_path, "rb") as f:
        cert_bytes = f.read()
    with open(key_path, "rb") as f:
        key_bytes = f.read()
    with open(wwdr_path, "rb") as f:
        wwdr_bytes = f.read()

    return cert_bytes, key_bytes, wwdr_bytes


def _sign_manifest(manifest_bytes):
    """
    Sign manifest.json bytes using PKCS#7 detached signature.

    Uses the cryptography library instead of subprocess OpenSSL.
    Returns DER-encoded PKCS#7 signature bytes.
    """
    cert_pem, key_pem, wwdr_pem = _load_cert_bytes()

    key_password = getattr(settings, "WALLET_APPLE_KEY_PASSWORD", "") or None
    if key_password:
        key_password = key_password.encode("utf-8")

    cert = load_pem_x509_certificate(cert_pem)
    private_key = serialization.load_pem_private_key(key_pem, password=key_password)
    wwdr_cert = load_pem_x509_certificate(wwdr_pem)

    return (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(manifest_bytes)
        .add_signer(cert, private_key, hashes.SHA256())
        .add_certificate(wwdr_cert)
        .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature])
    )


def _ensure_pass_identifiers(profile):
    updated_fields = []
    if not profile.apple_pass_serial:
        profile.apple_pass_serial = secrets.token_hex(8)
        updated_fields.append("apple_pass_serial")
    if not profile.apple_auth_token:
        profile.apple_auth_token = secrets.token_hex(16)
        updated_fields.append("apple_auth_token")
    if updated_fields:
        profile.save(update_fields=updated_fields)
    return profile.apple_pass_serial, profile.apple_auth_token


def _build_pass_payload(profile, serial_number, auth_token, request=None):
    """
    Build the pass.json payload for Apple Wallet.

    The pass includes:
    - Member name and tier (primary fields)
    - Next event info if registered (secondary fields)
    - Member since and points (auxiliary fields)
    - Referral QR code (barcode)
    """
    pass_type_identifier = _require_setting("WALLET_APPLE_PASS_TYPE_IDENTIFIER")
    team_identifier = _require_setting("WALLET_APPLE_TEAM_IDENTIFIER")
    organization_name = _require_setting("WALLET_APPLE_ORGANIZATION_NAME")
    web_service_url = getattr(settings, "WALLET_APPLE_WEB_SERVICE_URL", "")

    pass_data = build_wallet_pass_data(profile, request=request)

    primary_fields = [
        {
            "key": "member",
            "label": "Member",
            "value": pass_data["display_name"],
        }
    ]

    header_fields = [
        {
            "key": "tier",
            "label": "Status",
            "value": pass_data["tier_display"],
        }
    ]

    secondary_fields = []
    if pass_data["next_event"]:
        secondary_fields.append(
            {
                "key": "next_event",
                "label": "Next Event",
                "value": pass_data["next_event"]["title"],
            }
        )
        secondary_fields.append(
            {
                "key": "event_date",
                "label": "Date",
                "value": pass_data["next_event"]["date"],
            }
        )
    else:
        secondary_fields.append(
            {
                "key": "next_event",
                "label": "Next Event",
                "value": "No upcoming events",
            }
        )

    auxiliary_fields = [
        {
            "key": "member_since",
            "label": "Member since",
            "value": pass_data["member_since"]
            or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        },
        {
            "key": "points",
            "label": "Points",
            "value": str(pass_data["referral_points"]),
        },
    ]

    back_fields = [
        {
            "key": "referral_info",
            "label": "Share & Earn",
            "value": "Invite friends using the QR code on this card. Earn 100 points for each signup!",
        },
        {
            "key": "tier_info",
            "label": "Membership Tiers",
            "value": "Basic (0pts) > Bronze (200pts) > Silver (500pts) > Gold (1000pts)",
        },
    ]

    if pass_data["next_event"] and pass_data["next_event"].get("location"):
        back_fields.append(
            {
                "key": "event_location",
                "label": "Event Location",
                "value": pass_data["next_event"]["location"],
            }
        )

    payload = {
        "formatVersion": 1,
        "passTypeIdentifier": pass_type_identifier,
        "serialNumber": serial_number,
        "teamIdentifier": team_identifier,
        "organizationName": organization_name,
        "description": "Crush.lu member pass",
        "authenticationToken": auth_token,
        "logoText": "Crush.lu",
        "generic": {
            "primaryFields": primary_fields,
            "headerFields": header_fields,
            "secondaryFields": secondary_fields,
            "auxiliaryFields": auxiliary_fields,
            "backFields": back_fields,
        },
        "backgroundColor": "rgb(155, 89, 182)",
        "foregroundColor": "rgb(255, 255, 255)",
        "labelColor": "rgb(255, 220, 230)",
        "barcode": {
            "format": "PKBarcodeFormatQR",
            "message": pass_data["referral_url"],
            "messageEncoding": "iso-8859-1",
            "altText": "Scan to join Crush.lu",
        },
        "barcodes": [
            {
                "format": "PKBarcodeFormatQR",
                "message": pass_data["referral_url"],
                "messageEncoding": "iso-8859-1",
                "altText": "Scan to join Crush.lu",
            }
        ],
    }

    if web_service_url:
        payload["webServiceURL"] = web_service_url

    return payload


def _build_pkpass(pass_payload, files=None):
    """
    Build a .pkpass ZIP file from a pass payload and optional extra files.

    Args:
        pass_payload: dict -- the pass.json content
        files: dict of {filename: bytes} -- extra files to include (icon.png, etc.)

    Returns:
        bytes: The .pkpass file contents
    """
    if files is None:
        files = {}

    # Add default icon if not provided
    if "icon.png" not in files:
        files["icon.png"] = base64.b64decode(ICON_PNG_BASE64)

    # Serialize pass.json
    pass_json_bytes = json.dumps(
        pass_payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")

    # Build manifest (SHA1 hashes of all files)
    manifest = {}
    manifest["pass.json"] = hashlib.sha1(
        pass_json_bytes, usedforsecurity=False
    ).hexdigest()
    for filename, content in files.items():
        manifest[filename] = hashlib.sha1(
            content, usedforsecurity=False
        ).hexdigest()

    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")

    # Sign manifest
    signature_bytes = _sign_manifest(manifest_bytes)

    # Build ZIP
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as zf:
        zf.writestr("pass.json", pass_json_bytes)
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("signature", signature_bytes)
        for filename, content in files.items():
            zf.writestr(filename, content)

    return buffer.getvalue()


def build_apple_pass(profile, request=None):
    """
    Build a complete Apple Wallet .pkpass file for the given profile.

    Args:
        profile: CrushProfile instance
        request: Optional HttpRequest for building absolute URLs

    Returns:
        bytes: The .pkpass file contents
    """
    serial_number, auth_token = _ensure_pass_identifiers(profile)
    pass_payload = _build_pass_payload(
        profile, serial_number, auth_token, request=request
    )
    return _build_pkpass(pass_payload)


def provide_pass_for_serial(
    pass_type_identifier,
    serial_number,
    web_service_url=None,
    authentication_token=None,
):
    """
    PassKit web service provider -- rebuilds a member pass for a given serial.

    Called by passkit_service.get_latest_pass() when Apple Wallet requests
    an updated pass after an APNS push notification.
    """
    from ..models import CrushProfile

    profile = CrushProfile.objects.filter(apple_pass_serial=serial_number).first()
    if not profile:
        return None
    return build_apple_pass(profile)
