import json
import logging
import threading
import time

import requests

from django.conf import settings
from google.oauth2 import service_account
import google.auth
import google.auth.exceptions
import google.auth.transport.requests

logger = logging.getLogger(__name__)

FCM_SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]

# Suffix on every Google-managed service-account email:
#   <name>@<project-id>.iam.gserviceaccount.com
_GSA_EMAIL_SUFFIX = ".iam.gserviceaccount.com"

# One credentials object per process, so its access token (valid about an
# hour) is reused across notifications instead of being re-minted on every
# send. The lock covers every touch of it — the validity check, the refresh
# and the token read: google-auth assigns token and expiry separately, so an
# unlocked reader could pair a fresh expiry with a stale token.
_fcm_credentials_lock = threading.Lock()
_fcm_credentials = None  # (credentials, project_id) once loaded


def _derive_project_id_from_email(service_account_email):
    """Extract the GCP project id embedded in a service-account email.

    Uses an anchored suffix check (``endswith``) rather than a substring
    match so a crafted domain such as ``x.iam.gserviceaccount.com.evil.tld``
    cannot masquerade as a Google-managed account and leak the wrong id.
    """
    if not service_account_email or "@" not in service_account_email:
        return None
    domain = service_account_email.split("@", 1)[1]
    if domain.endswith(_GSA_EMAIL_SUFFIX):
        return domain[: -len(_GSA_EMAIL_SUFFIX)] or None
    return None


def get_fcm_credentials():
    """
    Load Google Service Account Credentials for FCM v1 dispatch.
    Tries settings configuration, falls back to workspace project JSON key,
    and falls back to Application Default Credentials.
    """
    # 1. Check settings for explicit FCM credentials
    service_account_email = getattr(settings, "FCM_SERVICE_ACCOUNT_EMAIL", None)
    private_key = getattr(settings, "FCM_PRIVATE_KEY", None)
    private_key_path = getattr(settings, "FCM_PRIVATE_KEY_PATH", None)

    # Fallback to wallet credentials if configured
    if not service_account_email:
        service_account_email = getattr(settings, "WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL", None)
        private_key = getattr(settings, "WALLET_GOOGLE_PRIVATE_KEY", None)
        private_key_path = getattr(settings, "WALLET_GOOGLE_PRIVATE_KEY_PATH", None)

    if service_account_email:
        credentials_info = {
            "type": "service_account",
            "client_email": service_account_email,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        if private_key:
            if isinstance(private_key, str):
                private_key = private_key.replace("\\n", "\n")
            credentials_info["private_key"] = private_key
            project_id = getattr(settings, "FIREBASE_PROJECT_ID", None)
            if not project_id:
                project_id = _derive_project_id_from_email(service_account_email)
            try:
                credentials = service_account.Credentials.from_service_account_info(
                    credentials_info, scopes=FCM_SCOPES
                )
                return credentials, project_id
            except Exception as e:
                logger.error(f"Error building FCM service account credentials from key: {e}")

        elif private_key_path:
            try:
                with open(private_key_path, "r") as f:
                    content = f.read()
                    if content.strip().startswith("{"):
                        full_credentials = json.loads(content)
                        return service_account.Credentials.from_service_account_info(
                            full_credentials, scopes=FCM_SCOPES
                        ), full_credentials.get("project_id")
                    
                    credentials_info["private_key"] = content
                    project_id = getattr(settings, "FIREBASE_PROJECT_ID", None)
                    if not project_id:
                        project_id = _derive_project_id_from_email(service_account_email)
                    credentials = service_account.Credentials.from_service_account_info(
                        credentials_info, scopes=FCM_SCOPES
                    )
                    return credentials, project_id
            except FileNotFoundError:
                logger.error(f"FCM private key path not found: {private_key_path}")

    # 2. Check for default local workspace credentials file
    import os
    json_filename = "project-2dcadfa2-93e4-4d72-8a8-bb6bb44150d2.json"
    local_json_path = os.path.join(settings.BASE_DIR, json_filename)
    if os.path.exists(local_json_path):
        try:
            with open(local_json_path, "r") as f:
                full_credentials = json.load(f)
                credentials = service_account.Credentials.from_service_account_info(
                    full_credentials, scopes=FCM_SCOPES
                )
                return credentials, full_credentials.get("project_id")
        except Exception as e:
            logger.error(f"Error loading local workspace GCP credentials JSON: {e}")

    # 3. Fallback to Google Application Default Credentials
    try:
        credentials, project_id = google.auth.default(scopes=FCM_SCOPES)
        return credentials, project_id
    except Exception as e:
        logger.error(f"Error loading Google Application Default Credentials: {e}")

    return None, None


class _DeadlineRequest:
    """google-auth transport that makes one bounded token-endpoint attempt.

    ``credentials.refresh()`` takes no timeout, and google-auth's requests
    transport defaults to 120s — the whole gunicorn window. So the call gets
    what is left until ``deadline`` as its timeout, and refuses to start once
    the deadline has passed.

    A per-call timeout alone does not bound the refresh: google-auth retries a
    failed token response (any 5xx, or a ``server_error``-style body) up to 3
    times, sleeping 1s then 2s in its own loop, where this wrapper cannot see
    the clock. A non-200 response therefore raises here instead of going back
    to google-auth, so there is exactly one attempt and no backoff sleep. The
    next notification tries again.
    """

    def __init__(self, deadline):
        self._deadline = deadline
        self._transport = google.auth.transport.requests.Request()

    def __call__(self, *args, **kwargs):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise google.auth.exceptions.TransportError(
                "FCM token refresh ran out of time"
            )
        kwargs["timeout"] = max(0.1, remaining)
        response = self._transport(*args, **kwargs)
        if response.status != 200:
            raise google.auth.exceptions.TransportError(
                f"FCM token endpoint returned HTTP {response.status}: "
                f"{response.data[:200]!r}"
            )
        return response


def _get_fcm_access_token(deadline):
    """Return ``(access_token, project_id)``, refreshing only when needed.

    Returns ``(None, None)`` when no FCM credentials are configured. Raises if
    the token cannot be refreshed before ``deadline`` — including while waiting
    for another thread's refresh to finish.
    """
    global _fcm_credentials

    if not _fcm_credentials_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise TimeoutError("timed out waiting for a concurrent FCM token refresh")
    try:
        if _fcm_credentials is None:
            credentials, project_id = get_fcm_credentials()
            if not credentials or not project_id:
                return None, None
            _fcm_credentials = (credentials, project_id)
        credentials, project_id = _fcm_credentials

        if not credentials.valid:
            refresh_timeout = getattr(
                settings, "CRUSH_PUSH_TOKEN_REFRESH_TIMEOUT_SECONDS", 5.0
            )
            refresh_deadline = min(deadline, time.monotonic() + refresh_timeout)
            credentials.refresh(_DeadlineRequest(refresh_deadline))
        return credentials.token, project_id
    finally:
        _fcm_credentials_lock.release()


def send_native_android_push_notification(
    user,
    title,
    body,
    url="/en/dashboard/",
    tag="crush-android",
    preference_key=None,
):
    """
    Send a native FCM notification to all active Android devices of a user.
    """
    from .models import AndroidAppDevice

    devices = AndroidAppDevice.objects.filter(user=user, enabled=True)
    if preference_key:
        devices = devices.filter(**{f"notify_{preference_key}": True})

    total = devices.count()
    if not total:
        return {"success": 0, "failed": 0, "total": 0}

    # Bounded like the web fan-out in push_notifications.send_push_notification:
    # production has no task worker, so this runs inside the request that
    # triggered the notification. Each FCM post carries a 10s timeout, so the
    # deadline — not the count — is what keeps a member with a pile of stale
    # devices from eating the gunicorn window. It starts before the OAuth
    # token refresh, so a slow token endpoint is charged to the same budget.
    limit = getattr(settings, "CRUSH_PUSH_FANOUT_LIMIT", 10)
    budget = getattr(settings, "CRUSH_PUSH_FANOUT_BUDGET_SECONDS", 10.0)
    deadline = time.monotonic() + budget

    try:
        access_token, project_id = _get_fcm_access_token(deadline)
    except Exception as e:
        logger.error(f"Error refreshing Google OAuth2 token for FCM: {e}")
        return {"success": 0, "failed": total, "total": total}
    if not project_id:
        logger.warning("Skipping Android push for user ID %s: FCM settings/credentials are missing.", user.id)
        return {"success": 0, "failed": 0, "total": total}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    fcm_url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

    # FCM payload 'data' values must strictly be strings
    data_payload = {}
    if url:
        data_payload["url"] = str(url)

    success_count = 0
    failed_count = 0
    attempted = 0

    for device in devices[:limit]:
        if time.monotonic() >= deadline:
            break
        attempted += 1
        payload = {
            "message": {
                "token": device.registration_token,
                "notification": {
                    "title": title,
                    "body": body,
                }
            }
        }
        if data_payload:
            payload["message"]["data"] = data_payload

        try:
            response = requests.post(fcm_url, json=payload, headers=headers, timeout=10)
            if response.status_code == 200:
                device.mark_success()
                success_count += 1
            else:
                logger.warning(
                    f"FCM send failed for device {device.id}: {response.status_code} - {response.text}"
                )
                # If token is invalid or unregistered, mark as failed
                if response.status_code in [404, 410] or "UNREGISTERED" in response.text:
                    device.mark_failure()
                failed_count += 1
        except Exception as e:
            logger.error(f"Exception during FCM send to device {device.id}: {e}")
            failed_count += 1

    skipped = total - attempted
    if skipped > 0:
        logger.warning(
            "Android push fan-out for user ID %s bounded: sent to %s of %s "
            "device(s), %s skipped (limit=%s, budget=%ss)",
            user.id,
            attempted,
            total,
            skipped,
            limit,
            budget,
        )

    return {
        "success": success_count,
        "failed": failed_count,
        "total": total,
        "skipped": skipped,
    }



