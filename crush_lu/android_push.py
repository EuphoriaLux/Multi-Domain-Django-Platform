import json
import logging
import requests

from django.conf import settings
from google.oauth2 import service_account
import google.auth
import google.auth.transport.requests

logger = logging.getLogger(__name__)

FCM_SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]

# Suffix on every Google-managed service-account email:
#   <name>@<project-id>.iam.gserviceaccount.com
_GSA_EMAIL_SUFFIX = ".iam.gserviceaccount.com"


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

    credentials, project_id = get_fcm_credentials()
    if not credentials or not project_id:
        logger.warning("Skipping Android push for user ID %s: FCM settings/credentials are missing.", user.id)
        return {"success": 0, "failed": 0, "total": total}

    # Refresh credentials to obtain OAuth2 access token
    try:
        credentials.refresh(google.auth.transport.requests.Request())
        access_token = credentials.token
    except Exception as e:
        logger.error(f"Error refreshing Google OAuth2 token for FCM: {e}")
        return {"success": 0, "failed": total, "total": total}

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

    for device in devices:
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

    return {"success": success_count, "failed": failed_count, "total": total}



