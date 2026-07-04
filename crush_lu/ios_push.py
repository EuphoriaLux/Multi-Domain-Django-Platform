import base64
import logging
import time

import httpx
import jwt
from django.conf import settings

from .models import IOSAppDevice

logger = logging.getLogger(__name__)

APNS_PRODUCTION_HOST = "https://api.push.apple.com"
APNS_SANDBOX_HOST = "https://api.sandbox.push.apple.com"


def _get_apns_config():
    private_key = getattr(settings, "IOS_APNS_PRIVATE_KEY", "")
    private_key_base64 = getattr(settings, "IOS_APNS_PRIVATE_KEY_BASE64", "")
    if not private_key and private_key_base64:
        private_key = base64.b64decode(private_key_base64).decode("utf-8")

    key_id = getattr(settings, "IOS_APNS_KEY_ID", "")
    team_id = getattr(settings, "IOS_APNS_TEAM_ID", "")
    bundle_id = getattr(settings, "IOS_APNS_BUNDLE_ID", "")
    use_sandbox = bool(getattr(settings, "IOS_APNS_USE_SANDBOX", False))

    if not all([key_id, team_id, bundle_id, private_key]):
        return None

    return {
        "key_id": key_id,
        "team_id": team_id,
        "bundle_id": bundle_id,
        "private_key": private_key,
        "host": APNS_SANDBOX_HOST if use_sandbox else APNS_PRODUCTION_HOST,
    }


def _build_apns_jwt(config):
    return jwt.encode(
        {"iss": config["team_id"], "iat": int(time.time())},
        config["private_key"],
        algorithm="ES256",
        headers={"kid": config["key_id"]},
    )


def send_ios_push_to_device(device, title, body, url="/en/dashboard/", tag="crush-ios"):
    config = _get_apns_config()
    if not config:
        logger.warning("iOS app APNS settings are not configured.")
        return False

    token = _build_apns_jwt(config)
    headers = {
        "authorization": f"bearer {token}",
        "apns-topic": config["bundle_id"],
        "apns-push-type": "alert",
        "apns-priority": "10",
    }
    payload = {
        "aps": {
            "alert": {
                "title": str(title or "Crush.lu")[:120],
                "body": str(body or "")[:240],
            },
            "sound": "default",
        },
        "url": url or "/en/dashboard/",
        "tag": tag,
    }

    request_url = f"{config['host']}/3/device/{device.device_token}"
    try:
        with httpx.Client(http2=True, timeout=10.0) as client:
            response = client.post(request_url, headers=headers, json=payload)
    except Exception as exc:
        logger.warning("APNS request failed for iOS device %s: %s", device.id, exc)
        device.mark_failure()
        return False

    if response.status_code == 200:
        device.mark_success()
        return True

    if response.status_code == 410:
        device.enabled = False
        device.failure_count += 1
        device.save(update_fields=["enabled", "failure_count"])
        logger.info("Disabled expired iOS APNS token for device %s", device.id)
    else:
        device.mark_failure()
        logger.warning(
            "APNS app push failed (%s) for iOS device %s: %s",
            response.status_code,
            device.id,
            response.text,
        )
    return False


def send_native_push_notification(
    user,
    title,
    body,
    url="/en/dashboard/",
    tag="crush-ios",
    preference_key=None,
):
    devices = IOSAppDevice.objects.filter(user=user, enabled=True)
    if preference_key:
        devices = devices.filter(**{f"notify_{preference_key}": True})

    total = devices.count()
    if not total:
        return {"success": 0, "failed": 0, "total": 0}

    if not _get_apns_config():
        logger.warning("Skipping iOS push for user ID %s: APNS settings are missing.", user.id)
        return {"success": 0, "failed": 0, "total": total}

    success_count = 0
    failed_count = 0
    for device in devices:
        if send_ios_push_to_device(device, title, body, url=url, tag=tag):
            success_count += 1
        else:
            failed_count += 1

    return {"success": success_count, "failed": failed_count, "total": total}
