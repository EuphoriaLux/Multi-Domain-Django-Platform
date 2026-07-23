"""
Buffer REST API integration service for hub.crush.lu.

Handles profile queries and dispatching updates to Buffer.
Buffer API Endpoint: https://api.bufferapp.com/1/updates/create.json
Note: Buffer API requires media[photo] to be a public HTTP/HTTPS URL.
"""
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

BUFFER_API_BASE = "https://api.bufferapp.com/1"


def get_buffer_access_token() -> str:
    return getattr(settings, "BUFFER_ACCESS_TOKEN", "")


def list_buffer_profiles() -> list:
    """Fetch connected Buffer social channels."""
    token = get_buffer_access_token()
    if not token:
        logger.info("[Buffer] BUFFER_ACCESS_TOKEN not set; returning default fallback profiles")
        return [
            {
                "id": "buf_ig_default",
                "service": "instagram",
                "service_username": "crush.lu",
                "formatted_username": "Crush Luxembourg (Instagram)",
            },
            {
                "id": "buf_fb_default",
                "service": "facebook",
                "service_username": "crushlu",
                "formatted_username": "Crush Luxembourg (Facebook)",
            },
            {
                "id": "buf_li_default",
                "service": "linkedin",
                "service_username": "crush-lu",
                "formatted_username": "Crush Luxembourg (LinkedIn)",
            },
        ]

    try:
        resp = requests.get(
            f"{BUFFER_API_BASE}/profiles.json",
            params={"access_token": token},
            timeout=10,
        )
        if resp.status_code == 200:
            profiles = resp.json()
            return [
                {
                    "id": p.get("id"),
                    "service": p.get("service"),
                    "service_username": p.get("service_username"),
                    "avatar_url": p.get("avatar_https"),
                    "formatted_username": f"Crush ({p.get('service', '').capitalize()}: @{p.get('service_username')})",
                }
                for p in profiles
            ]
        else:
            logger.warning("[Buffer] Failed to fetch profiles (%d): %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.exception("[Buffer] Error connecting to Buffer API: %s", exc)

    return []


def create_buffer_update(
    text: str,
    profile_ids: list,
    scheduled_at: str = None,
    media_url: str = None,
) -> dict:
    """Create a scheduled update in Buffer.
    
    Buffer REST API expects POST to /1/updates/create.json:
    - access_token
    - profile_ids[]
    - text
    - media[photo] (public HTTP URL)
    - scheduled_at (ISO timestamp string or Unix timestamp)
    """
    token = get_buffer_access_token()
    if not token:
        logger.info("[Buffer Mock] Dispatching simulated post to Buffer: %s", text[:50])
        return {
            "success": True,
            "buffer_id": f"mock_buf_{requests.utils.quote(text[:10])}",
            "message": "Simulated dispatch (BUFFER_ACCESS_TOKEN not configured)",
        }

    payload = {
        "access_token": token,
        "text": text,
        "now": False if scheduled_at else True,
    }

    if profile_ids:
        for idx, pid in enumerate(profile_ids):
            payload[f"profile_ids[{idx}]"] = pid

    if media_url:
        payload["media[photo]"] = media_url

    if scheduled_at:
        payload["scheduled_at"] = scheduled_at

    try:
        resp = requests.post(
            f"{BUFFER_API_BASE}/updates/create.json",
            data=payload,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            updates = data.get("updates", [])
            update_id = updates[0].get("id") if updates else "buf_created"
            return {"success": True, "buffer_id": update_id, "data": data}
        else:
            logger.error("[Buffer] Failed to create update (%d): %s", resp.status_code, resp.text)
            return {"success": False, "error": resp.text}
    except Exception as exc:
        logger.exception("[Buffer] Exception creating update: %s", exc)
        return {"success": False, "error": str(exc)}
