import base64
import json
import secrets
import time

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..wallet_pass import build_wallet_pass_data


def _require_setting(name):
    value = getattr(settings, name, None)
    if not value:
        raise ImproperlyConfigured(f"Missing required setting: {name}")
    return value


def _base64url_encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _load_private_key():
    key_data = getattr(settings, "WALLET_GOOGLE_PRIVATE_KEY", None)
    if not key_data:
        key_path = _require_setting("WALLET_GOOGLE_PRIVATE_KEY_PATH")
        with open(key_path, "rb") as handle:
            key_data = handle.read()
    elif isinstance(key_data, str):
        key_data = key_data.replace("\\n", "\n").encode("utf-8")

    return serialization.load_pem_private_key(key_data, password=None)


def _ensure_object_id(profile):
    if profile.google_wallet_object_id:
        return profile.google_wallet_object_id

    issuer_id = _require_setting("WALLET_GOOGLE_ISSUER_ID")
    object_suffix = secrets.token_hex(8)
    object_id = f"{issuer_id}.crush-{profile.user_id}-{object_suffix}"
    profile.google_wallet_object_id = object_id
    profile.save(update_fields=["google_wallet_object_id"])
    return object_id


def build_google_wallet_jwt(profile, request=None):
    """
    Build a JWT for adding a pass to Google Wallet.

    The pass includes:
    - Member name and tier (header/subheader)
    - Next event info (text module)
    - Points balance (text module)
    - Referral QR code (barcode)

    Args:
        profile: CrushProfile instance
        request: Optional HttpRequest for building absolute URLs

    Returns:
        str: Signed JWT for Google Wallet save
    """
    issuer_id = _require_setting("WALLET_GOOGLE_ISSUER_ID")
    class_id = _require_setting("WALLET_GOOGLE_CLASS_ID")
    service_account_email = _require_setting("WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL")
    key_id = getattr(settings, "WALLET_GOOGLE_KEY_ID", "")

    object_id = _ensure_object_id(profile)

    # Get dynamic pass data
    pass_data = build_wallet_pass_data(profile, request=request)

    # Build text modules for card content
    text_modules = [
        {
            "id": "member_status",
            "header": "Status",
            "body": pass_data["tier_display"],
        },
        {
            "id": "points",
            "header": "Points",
            "body": str(pass_data["referral_points"]),
        },
    ]

    # Add next event info if available
    if pass_data["next_event"]:
        text_modules.append({
            "id": "next_event",
            "header": "Next Event",
            "body": f"{pass_data['next_event']['title']} - {pass_data['next_event']['date']}",
        })
    else:
        text_modules.append({
            "id": "next_event",
            "header": "Next Event",
            "body": "No upcoming events",
        })

    # Add member since info
    if pass_data["member_since"]:
        text_modules.append({
            "id": "member_since",
            "header": "Member Since",
            "body": pass_data["member_since"],
        })

    # Build the generic object
    generic_object = {
        "id": object_id,
        "classId": class_id,
        "state": "active",
        "header": {
            "defaultValue": {
                "language": "en-US",
                "value": pass_data["display_name"],
            }
        },
        "subheader": {
            "defaultValue": {
                "language": "en-US",
                "value": pass_data["tier_display"],
            }
        },
        "textModulesData": text_modules,
        "barcode": {
            "type": "QR_CODE",
            "value": pass_data["referral_url"],
            "alternateText": "Scan to join Crush.lu",
        },
        "cardTitle": {
            "defaultValue": {
                "language": "en-US",
                "value": "Crush.lu Member",
            }
        },
        # Crush.lu brand colors
        "hexBackgroundColor": "#9B59B6",  # crush-purple
    }

    # Add profile image if available and user has opted in
    if pass_data["photo_url"]:
        generic_object["heroImage"] = {
            "sourceUri": {
                "uri": pass_data["photo_url"],
            },
            "contentDescription": {
                "defaultValue": {
                    "language": "en-US",
                    "value": "Profile Photo",
                }
            },
        }

    # Add info module with referral details (shown on back)
    generic_object["infoModuleData"] = {
        "showLastUpdateTime": True,
        "labelValueRows": [
            {
                "columns": [
                    {
                        "label": "Referral Code",
                        "value": pass_data["referral_url"].split("/")[-2] if pass_data["referral_url"] else "",
                    }
                ]
            },
            {
                "columns": [
                    {
                        "label": "Earn Points",
                        "value": "Invite friends and earn 100 points per signup!",
                    }
                ]
            },
        ],
    }

    # Add links module
    generic_object["linksModuleData"] = {
        "uris": [
            {
                "uri": "https://crush.lu",
                "description": "Visit Crush.lu",
            },
            {
                "uri": pass_data["referral_url"],
                "description": "Share your referral link",
            },
        ]
    }

    issued_at = int(time.time())
    payload = {
        "iss": service_account_email,
        "aud": "google",
        "typ": "savetowallet",
        "iat": issued_at,
        "exp": issued_at + 3600,
        "payload": {
            "genericObjects": [generic_object]
        },
    }

    header = {"alg": "RS256", "typ": "JWT"}
    if key_id:
        header["kid"] = key_id

    signing_input = b".".join(
        [
            _base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _base64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
    )

    private_key = _load_private_key()
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    signed_jwt = b".".join([signing_input, _base64url_encode(signature)])
    return signed_jwt.decode("utf-8")
