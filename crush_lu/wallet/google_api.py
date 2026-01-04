"""
Google Wallet REST API client for updating existing passes.

This module provides functions to update Google Wallet passes when user data changes
(points, tier, event registrations, etc.).
"""
import json
import logging
import time

import httpx
from django.conf import settings
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .google_wallet import _load_private_key, _base64url_encode
from ..wallet_pass import build_wallet_pass_data

logger = logging.getLogger(__name__)

GOOGLE_WALLET_API_BASE = "https://walletobjects.googleapis.com/walletobjects/v1"


def _get_access_token():
    """
    Generate an OAuth2 access token using service account credentials.
    Uses JWT bearer token flow for server-to-server authentication.
    """
    service_account_email = getattr(settings, "WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL", None)
    if not service_account_email:
        raise ValueError("WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL not configured")

    issued_at = int(time.time())
    expires_at = issued_at + 3600  # 1 hour

    # JWT claims for Google OAuth2
    claims = {
        "iss": service_account_email,
        "sub": service_account_email,
        "aud": "https://oauth2.googleapis.com/token",
        "iat": issued_at,
        "exp": expires_at,
        "scope": "https://www.googleapis.com/auth/wallet_object.issuer",
    }

    header = {"alg": "RS256", "typ": "JWT"}

    # Sign the JWT
    signing_input = b".".join([
        _base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _base64url_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8")),
    ])

    private_key = _load_private_key()
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    signed_jwt = b".".join([signing_input, _base64url_encode(signature)]).decode("utf-8")

    # Exchange JWT for access token
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": signed_jwt,
            },
        )

        if response.status_code != 200:
            logger.error("Failed to get access token: %s", response.text)
            raise ValueError(f"Failed to get access token: {response.status_code}")

        return response.json()["access_token"]


def _build_generic_object_payload(profile, object_id, class_id):
    """
    Build the generic object payload for updating a pass.
    Mirrors the Buffalo Grill-inspired structure in google_wallet.py.
    """
    pass_data = build_wallet_pass_data(profile)

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

    # Get hero/promo image URL
    hero_url = getattr(settings, "WALLET_GOOGLE_HERO_URL", None)
    if not hero_url:
        hero_url = "https://crush.lu/static/crush_lu/images/wallet-promo-banner.png"

    # Extract referral code
    referral_code = pass_data["referral_url"].split("/")[-2] if pass_data["referral_url"] else ""

    # Build text modules - Buffalo Grill style
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

    # Build the object
    generic_object = {
        "id": object_id,
        "classId": class_id,
        "state": "active",
        "logo": {
            "sourceUri": {"uri": logo_url},
            "contentDescription": {
                "defaultValue": {"language": "en-US", "value": "Crush.lu Logo"}
            },
        },
        "header": {
            "defaultValue": {
                "language": "en-US",
                "value": f"Meet & get rewarded! {tier_info['emoji']}",
            }
        },
        "subheader": {
            "defaultValue": {
                "language": "en-US",
                "value": f"{pass_data['display_name']} • {pass_data['tier_display']}",
            }
        },
        "textModulesData": text_modules,
        "barcode": {
            "type": "QR_CODE",
            "value": pass_data["referral_url"],
            "alternateText": "Share me to earn points! 🤳",
        },
        "cardTitle": {
            "defaultValue": {"language": "en-US", "value": "Crush.lu"}
        },
        "hexBackgroundColor": "#9B59B6",
    }

    # Hero image - profile photo or promo banner
    if pass_data["photo_url"]:
        generic_object["heroImage"] = {
            "sourceUri": {"uri": pass_data["photo_url"]},
            "contentDescription": {
                "defaultValue": {"language": "en-US", "value": "Your Profile"}
            },
        }
    else:
        generic_object["heroImage"] = {
            "sourceUri": {"uri": hero_url},
            "contentDescription": {
                "defaultValue": {"language": "en-US", "value": "Invite friends, earn rewards!"}
            },
        }

    # Info module - back of card
    generic_object["infoModuleData"] = {
        "showLastUpdateTime": True,
        "labelValueRows": [
            {"columns": [{"label": "🔗 Your Referral Code", "value": referral_code}]},
            {"columns": [{"label": "🎁 How to Earn", "value": "Invite friends → +100 pts per signup!"}]},
            {"columns": [{"label": "🏆 Tier Levels", "value": "🥉 200 | 🥈 500 | 🥇 1000 pts"}]},
            {"columns": [{"label": "💰 Redeem Points", "value": "Event discounts & exclusive perks!"}]},
            {"columns": [{"label": "🗓️ Member Since", "value": pass_data["member_since"] or "Welcome!"}]},
        ],
    }

    # Links module
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

    return generic_object


def update_google_wallet_pass(profile):
    """
    Update an existing Google Wallet pass for a user.

    Args:
        profile: CrushProfile instance with google_wallet_object_id set

    Returns:
        dict: {"success": bool, "message": str}
    """
    if not profile.google_wallet_object_id:
        return {"success": False, "message": "User has no Google Wallet pass"}

    class_id = getattr(settings, "WALLET_GOOGLE_CLASS_ID", None)
    if not class_id:
        return {"success": False, "message": "WALLET_GOOGLE_CLASS_ID not configured"}

    try:
        access_token = _get_access_token()
        object_payload = _build_generic_object_payload(
            profile,
            profile.google_wallet_object_id,
            class_id
        )

        # URL encode the object ID (it contains dots)
        encoded_object_id = profile.google_wallet_object_id.replace(".", "%2E")
        url = f"{GOOGLE_WALLET_API_BASE}/genericObject/{encoded_object_id}"

        with httpx.Client(timeout=30.0) as client:
            response = client.patch(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=object_payload,
            )

            if response.status_code == 200:
                logger.info(
                    "Updated Google Wallet pass for user %s (object: %s)",
                    profile.user_id,
                    profile.google_wallet_object_id,
                )
                return {"success": True, "message": "Pass updated successfully"}

            elif response.status_code == 404:
                # Pass doesn't exist in Google's system (user may have deleted it)
                logger.warning(
                    "Google Wallet pass not found for user %s (object: %s)",
                    profile.user_id,
                    profile.google_wallet_object_id,
                )
                return {"success": False, "message": "Pass not found (may have been deleted)"}

            else:
                logger.error(
                    "Failed to update Google Wallet pass: %s - %s",
                    response.status_code,
                    response.text,
                )
                return {"success": False, "message": f"API error: {response.status_code}"}

    except Exception as e:
        logger.exception("Error updating Google Wallet pass for user %s: %s", profile.user_id, e)
        return {"success": False, "message": str(e)}


def update_all_google_wallet_passes():
    """
    Update all existing Google Wallet passes.
    Useful for batch updates after design changes.

    Returns:
        dict: {"updated": int, "failed": int, "skipped": int}
    """
    from ..models import CrushProfile

    profiles = CrushProfile.objects.exclude(
        google_wallet_object_id__isnull=True
    ).exclude(
        google_wallet_object_id=""
    )

    results = {"updated": 0, "failed": 0, "skipped": 0}

    for profile in profiles:
        result = update_google_wallet_pass(profile)
        if result["success"]:
            results["updated"] += 1
        elif "not found" in result["message"].lower():
            results["skipped"] += 1
        else:
            results["failed"] += 1

    logger.info(
        "Batch Google Wallet update complete: %d updated, %d failed, %d skipped",
        results["updated"],
        results["failed"],
        results["skipped"],
    )

    return results