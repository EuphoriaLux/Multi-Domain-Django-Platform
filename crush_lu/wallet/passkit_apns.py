import logging
import time

import httpx
import jwt
from django.conf import settings
from django.utils import timezone

from ..models import PasskitDeviceRegistration

logger = logging.getLogger(__name__)


def mark_passes_updated_bulk(pass_type_identifier, serial_numbers):
    """Advance the update tag for many passes in ONE query.

    The tag is what actually makes an update land — Wallet's periodic poll
    picks it up with no push involved. Bulk event changes therefore mark
    everything here (cheap, no network) and treat the APNs fan-out as a
    best-effort "make it instant" step that can be bounded without losing the
    update. See refresh_event_tickets.
    """
    serial_numbers = list(serial_numbers)
    if not serial_numbers:
        return 0
    return PasskitDeviceRegistration.objects.filter(
        pass_type_identifier=pass_type_identifier,
        serial_number__in=serial_numbers,
    ).update(updated_at=timezone.now())


def mark_passes_updated(pass_type_identifier, serial_number):
    """Advance the update tag for a pass whose CONTENT changed.

    Wallet polls GET /devices/<id>/registrations/<type>?passesUpdatedSince=<tag>
    and we answer that from PasskitDeviceRegistration.updated_at — which
    auto_now only bumps when the device registers or refreshes its push token.
    A content change therefore left the timestamp untouched, so the poll
    filtered the pass out, returned 204, and the rebuilt package was never
    fetched: pushes fired and nothing ever updated.

    Deliberately run BEFORE (and independently of) the APNs send, because the
    content changed whether or not a push goes out. Wallet also polls on its own
    schedule, and that is the only path that works at all while PASSKIT_APNS_*
    is unconfigured — which it currently is in production.

    Uses .update() rather than .save(), so auto_now must be set explicitly.
    """
    return PasskitDeviceRegistration.objects.filter(
        pass_type_identifier=pass_type_identifier,
        serial_number=serial_number,
    ).update(updated_at=timezone.now())

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
    # Advance the update tag first: without it the subsequent poll answers 204
    # and the push is wasted. Must happen even when APNs is unconfigured, so
    # Wallet's own periodic poll still picks the change up.
    mark_passes_updated(pass_type_identifier, serial_number)

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
