import logging
import time

import httpx
import jwt
from django.conf import settings

from ..models import PasskitDeviceRegistration

logger = logging.getLogger(__name__)

APNS_PRODUCTION_HOST = "https://api.push.apple.com"
APNS_SANDBOX_HOST = "https://api.sandbox.push.apple.com"


def _get_apns_config():
    key_id = getattr(settings, "PASSKIT_APNS_KEY_ID", None)
    team_id = getattr(settings, "PASSKIT_APNS_TEAM_ID", None)
    private_key = getattr(settings, "PASSKIT_APNS_PRIVATE_KEY", None)
    use_sandbox = bool(getattr(settings, "PASSKIT_APNS_USE_SANDBOX", False))

    if not all([key_id, team_id, private_key]):
        return None

    return {
        "key_id": key_id,
        "team_id": team_id,
        "private_key": private_key,
        "host": APNS_SANDBOX_HOST if use_sandbox else APNS_PRODUCTION_HOST,
    }


def _build_apns_jwt(config):
    issued_at = int(time.time())
    return jwt.encode(
        {"iss": config["team_id"], "iat": issued_at},
        config["private_key"],
        algorithm="ES256",
        headers={"kid": config["key_id"]},
    )


def send_passkit_push_notifications(pass_type_identifier, serial_number):
    config = _get_apns_config()
    if not config:
        logger.warning("PassKit APNS settings are not configured.")
        return {"success": 0, "failed": 0, "total": 0}

    registrations = PasskitDeviceRegistration.objects.filter(
        pass_type_identifier=pass_type_identifier,
        serial_number=serial_number,
    )
    total = registrations.count()

    if not registrations.exists():
        return {"success": 0, "failed": 0, "total": 0}

    token = _build_apns_jwt(config)
    headers = {
        "authorization": f"bearer {token}",
        "apns-topic": pass_type_identifier,
    }
    payload = {"aps": {"content-available": 1}}

    success_count = 0
    failed_count = 0

    with httpx.Client(http2=True, timeout=10.0) as client:
        for registration in registrations:
            url = f"{config['host']}/3/device/{registration.push_token}"
            response = client.post(url, headers=headers, json=payload)

            if response.status_code == 200:
                success_count += 1
                continue

            failed_count += 1
            if response.status_code == 410:
                registration.delete()
                logger.info(
                    "Removed expired PassKit token for %s",
                    registration.device_library_identifier,
                )
            else:
                logger.warning(
                    "APNS PassKit push failed (%s) for %s: %s",
                    response.status_code,
                    registration.device_library_identifier,
                    response.text,
                )

    return {"success": success_count, "failed": failed_count, "total": total}
