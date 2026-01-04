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

    Design inspired by Buffalo Grill pass:
    - Big promotional header with emoji
    - Points counter (like "COMPTEUR")
    - Secondary fields for offers/events
    - QR code with call-to-action
    - Hero image at bottom for promotion
    - Detailed back/info section

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

    # Map tier to emoji and promotional message
    tier = pass_data["membership_tier"] or "basic"
    tier_config = {
        "basic": {"emoji": "💜", "promo": "Invite friends & earn rewards! 🎁"},
        "bronze": {"emoji": "🥉", "promo": "You're on fire! Keep inviting! 🔥"},
        "silver": {"emoji": "🥈", "promo": "Almost Gold! Just a few more! ⭐"},
        "gold": {"emoji": "🥇", "promo": "VIP Status! Exclusive perks! 👑"},
    }
    tier_info = tier_config.get(tier, tier_config["basic"])

    # Get logo URL
    logo_url = getattr(settings, "WALLET_GOOGLE_LOGO_URL", None)
    if not logo_url:
        logo_url = "https://crush.lu/static/crush_lu/icons/android-launchericon-192-192.png"

    # Get hero/promo image URL (for bottom promotional banner like Buffalo Grill)
    hero_url = getattr(settings, "WALLET_GOOGLE_HERO_URL", None)
    if not hero_url:
        hero_url = "https://crush.lu/static/crush_lu/images/wallet-promo-banner.png"

    # Extract referral code for display
    referral_code = pass_data["referral_url"].split("/")[-2] if pass_data["referral_url"] else ""

    # Build text modules - similar to Buffalo Grill's layout
    # First row: "Mes offres en cours" equivalent + Points counter
    text_modules = [
        {
            "id": "promo_message",
            "header": "🎯 Your Rewards",
            "body": tier_info["promo"],
        },
        {
            "id": "points_counter",
            "header": "POINTS",
            "body": f"{pass_data['referral_points']:,}",
        },
    ]

    # Second row: Next event info (like "Voir au verso")
    if pass_data["next_event"]:
        text_modules.append({
            "id": "next_event",
            "header": "📅 Next Event",
            "body": f"{pass_data['next_event']['title']}\n{pass_data['next_event']['date']}",
        })
    else:
        text_modules.append({
            "id": "next_event",
            "header": "📅 Upcoming Events",
            "body": "Browse events on crush.lu 💜",
        })

    # Build the generic object
    generic_object = {
        "id": object_id,
        "classId": class_id,
        "state": "active",
        # Logo in top-left corner (like Buffalo Grill's red circle with "B")
        "logo": {
            "sourceUri": {"uri": logo_url},
            "contentDescription": {
                "defaultValue": {"language": "en-US", "value": "Crush.lu Logo"}
            },
        },
        # Main header - Big promotional text like "Manger et être récompensé!"
        "header": {
            "defaultValue": {
                "language": "en-US",
                "value": f"Meet & get rewarded! {tier_info['emoji']}",
            }
        },
        # Subheader - User name and tier
        "subheader": {
            "defaultValue": {
                "language": "en-US",
                "value": f"{pass_data['display_name']} • {pass_data['tier_display']}",
            }
        },
        "textModulesData": text_modules,
        # QR code with call-to-action text (like "Scannez-moi à chaque visite")
        "barcode": {
            "type": "QR_CODE",
            "value": pass_data["referral_url"],
            "alternateText": "Share me to earn points! 🤳",
        },
        "cardTitle": {
            "defaultValue": {"language": "en-US", "value": "Crush.lu"}
        },
        # Crush.lu brand color (purple like Buffalo Grill's red)
        "hexBackgroundColor": "#9B59B6",
    }

    # Hero image at bottom - promotional banner (like Buffalo Grill's "-10% toutes les 3 visites")
    # Use profile photo if available, otherwise use promo banner
    if pass_data["photo_url"]:
        generic_object["heroImage"] = {
            "sourceUri": {"uri": pass_data["photo_url"]},
            "contentDescription": {
                "defaultValue": {"language": "en-US", "value": "Your Profile"}
            },
        }
    else:
        # Use a promotional banner image
        generic_object["heroImage"] = {
            "sourceUri": {"uri": hero_url},
            "contentDescription": {
                "defaultValue": {"language": "en-US", "value": "Invite friends, earn rewards!"}
            },
        }

    # Info module - detailed back of card (like Buffalo Grill's expandable sections)
    generic_object["infoModuleData"] = {
        "showLastUpdateTime": True,
        "labelValueRows": [
            {
                "columns": [
                    {"label": "🔗 Your Referral Code", "value": referral_code}
                ]
            },
            {
                "columns": [
                    {"label": "🎁 How to Earn", "value": "Invite friends → +100 pts per signup!"}
                ]
            },
            {
                "columns": [
                    {"label": "🏆 Tier Levels", "value": "🥉 200 | 🥈 500 | 🥇 1000 pts"}
                ]
            },
            {
                "columns": [
                    {"label": "💰 Redeem Points", "value": "Event discounts & exclusive perks!"}
                ]
            },
            {
                "columns": [
                    {"label": "🗓️ Member Since", "value": pass_data["member_since"] or "Welcome!"}
                ]
            },
        ],
    }

    # Links module (like Buffalo Grill's MON COMPTE, FACEBOOK, etc.)
    generic_object["linksModuleData"] = {
        "uris": [
            {"uri": "https://crush.lu/dashboard/", "description": "👤 My Account"},
            {"uri": "https://crush.lu/events/", "description": "📅 Browse Events"},
            {"uri": pass_data["referral_url"], "description": "📋 Share Referral Link"},
            {"uri": "https://crush.lu", "description": "💜 Visit Crush.lu"},
            {"uri": "https://instagram.com/crush.lu", "description": "📸 Instagram"},
        ]
    }

    # Share functionality
    generic_object["appLinkData"] = {
        "androidAppLinkInfo": {
            "appTarget": {
                "targetUri": {
                    "uri": pass_data["referral_url"],
                    "description": "Open in Crush.lu",
                }
            }
        }
    }

    generic_object["shareData"] = {
        "displayName": f"Join me on Crush.lu! 💜 Use my code: {referral_code}",
        "url": pass_data["referral_url"],
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
