import base64
import hashlib
import json
import os
import secrets
import subprocess
import tempfile
from datetime import datetime, timezone
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from ..wallet_pass import build_wallet_pass_data

# Placeholder 1x1 transparent PNG for icon (required)
ICON_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwAB"
    "BAEAu1fE3wAAAABJRU5ErkJggg=="
)


def _require_setting(name):
    value = getattr(settings, name, None)
    if not value:
        raise ImproperlyConfigured(f"Missing required setting: {name}")
    return value


def _get_pass_paths():
    cert_path = _require_setting("WALLET_APPLE_CERT_PATH")
    key_path = _require_setting("WALLET_APPLE_KEY_PATH")
    wwdr_path = _require_setting("WALLET_APPLE_WWDR_CERT_PATH")
    key_password = getattr(settings, "WALLET_APPLE_KEY_PASSWORD", "")
    return cert_path, key_path, wwdr_path, key_password


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

    # Get dynamic pass data
    pass_data = build_wallet_pass_data(profile, request=request)

    # Build primary fields (always shown)
    primary_fields = [
        {
            "key": "member",
            "label": "Member",
            "value": pass_data["display_name"],
        }
    ]

    # Build header fields (top right)
    header_fields = [
        {
            "key": "tier",
            "label": "Status",
            "value": pass_data["tier_display"],
        }
    ]

    # Build secondary fields (below primary)
    secondary_fields = []
    if pass_data["next_event"]:
        secondary_fields.append({
            "key": "next_event",
            "label": "Next Event",
            "value": pass_data["next_event"]["title"],
        })
        secondary_fields.append({
            "key": "event_date",
            "label": "Date",
            "value": pass_data["next_event"]["date"],
        })
    else:
        secondary_fields.append({
            "key": "next_event",
            "label": "Next Event",
            "value": "No upcoming events",
        })

    # Build auxiliary fields (bottom row)
    auxiliary_fields = [
        {
            "key": "member_since",
            "label": "Member since",
            "value": pass_data["member_since"] or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        },
        {
            "key": "points",
            "label": "Points",
            "value": str(pass_data["referral_points"]),
        },
    ]

    # Build back fields (shown when pass is flipped)
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
        back_fields.append({
            "key": "event_location",
            "label": "Event Location",
            "value": pass_data["next_event"]["location"],
        })

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
        # Crush.lu brand colors (purple/pink gradient approximation)
        "backgroundColor": "rgb(155, 89, 182)",  # crush-purple
        "foregroundColor": "rgb(255, 255, 255)",
        "labelColor": "rgb(255, 220, 230)",  # Light pink
        # QR code with referral URL
        "barcode": {
            "format": "PKBarcodeFormatQR",
            "message": pass_data["referral_url"],
            "messageEncoding": "iso-8859-1",
            "altText": "Scan to join Crush.lu",
        },
        # Fallback barcodes for older devices
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


def _sign_manifest(manifest_path, signature_path):
    cert_path, key_path, wwdr_path, key_password = _get_pass_paths()

    command = [
        "openssl",
        "smime",
        "-binary",
        "-sign",
        "-certfile",
        wwdr_path,
        "-signer",
        cert_path,
        "-inkey",
        key_path,
        "-outform",
        "DER",
        "-in",
        manifest_path,
        "-out",
        signature_path,
    ]

    if key_password:
        command.extend(["-passin", f"pass:{key_password}"])

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "Failed to sign Apple Wallet pass manifest: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


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
    pass_payload = _build_pass_payload(profile, serial_number, auth_token, request=request)

    with tempfile.TemporaryDirectory() as temp_dir:
        pass_json_path = os.path.join(temp_dir, "pass.json")
        manifest_path = os.path.join(temp_dir, "manifest.json")
        signature_path = os.path.join(temp_dir, "signature")
        icon_path = os.path.join(temp_dir, "icon.png")

        with open(pass_json_path, "w", encoding="utf-8") as handle:
            json.dump(pass_payload, handle, ensure_ascii=False, separators=(",", ":"))

        with open(icon_path, "wb") as handle:
            handle.write(base64.b64decode(ICON_PNG_BASE64))

        manifest = {}
        for filename in ("pass.json", "icon.png"):
            file_path = os.path.join(temp_dir, filename)
            with open(file_path, "rb") as handle:
                manifest[filename] = hashlib.sha1(handle.read()).hexdigest()

        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, separators=(",", ":"))

        _sign_manifest(manifest_path, signature_path)

        pass_buffer = tempfile.NamedTemporaryFile(suffix=".pkpass", delete=False)
        try:
            with ZipFile(pass_buffer.name, "w", ZIP_DEFLATED) as pass_zip:
                pass_zip.write(pass_json_path, "pass.json")
                pass_zip.write(manifest_path, "manifest.json")
                pass_zip.write(signature_path, "signature")
                pass_zip.write(icon_path, "icon.png")
            with open(pass_buffer.name, "rb") as handle:
                return handle.read()
        finally:
            pass_buffer.close()
            os.unlink(pass_buffer.name)
