import json
import logging
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from .decorators import ratelimit
from .models import IOSAppDevice, IOSNativeAuthCode

logger = logging.getLogger(__name__)


class IOSAppRedirect(HttpResponseRedirect):
    allowed_schemes = ["http", "https", "ftp", "crushlu"]


PREFERENCE_MAP = {
    "newMessages": "notify_new_messages",
    "eventReminders": "notify_event_reminders",
    "newConnections": "notify_new_connections",
    "profileUpdates": "notify_profile_updates",
}


def _json_body(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _allowed_redirect_uri(uri):
    return uri in set(getattr(settings, "IOS_AUTH_REDIRECT_URIS", []))


def _append_query(uri, params):
    parsed = urlparse(uri)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _apns_configured():
    return bool(
        getattr(settings, "IOS_APNS_KEY_ID", "")
        and getattr(settings, "IOS_APNS_TEAM_ID", "")
        and getattr(settings, "IOS_APNS_BUNDLE_ID", "")
        and (
            getattr(settings, "IOS_APNS_PRIVATE_KEY", "")
            or getattr(settings, "IOS_APNS_PRIVATE_KEY_BASE64", "")
        )
    )


@require_http_methods(["GET"])
def ios_app_config(request):
    """Public native app bootstrap config."""
    app = {
        "name": getattr(settings, "IOS_APP_NAME", "Crush.lu"),
        "bundleId": getattr(settings, "IOS_APP_BUNDLE_ID", "lu.crush.app"),
        "teamId": getattr(settings, "IOS_APP_TEAM_ID", "C5XDPB2G33"),
        "version": getattr(settings, "IOS_APP_VERSION", "1.0.0"),
        "build": getattr(settings, "IOS_APP_BUILD", "1"),
        "minSupportedVersion": getattr(settings, "IOS_APP_MIN_SUPPORTED_VERSION", "1.0.0"),
        "appStoreUrl": getattr(settings, "IOS_APP_STORE_URL", ""),
    }
    return JsonResponse(
        {
            "success": True,
            "app": app,
            "features": {
                "nativePushEnabled": _apns_configured(),
                "nativeCommerceEnabled": getattr(
                    settings, "IOS_NATIVE_COMMERCE_ENABLED", False
                ),
                "universalLinksEnabled": True,
            },
            "urls": {
                "start": "/en/dashboard/?source=ios_app",
                "login": "/en/login/?source=ios_app",
                "privacy": "/en/privacy-policy/?source=ios_app",
                "terms": "/en/terms-of-service/?source=ios_app",
                "accountDeletion": "/en/account/gdpr/?source=ios_app",
                "notificationSettings": "/en/profile/edit/?section=account&sub=notifications&source=ios_app",
                "support": "/en/support/?source=ios_app",
            },
        }
    )


@login_required
@require_http_methods(["GET"])
def ios_auth_handoff(request):
    """Issue a one-time code that the native shell can redeem in WKWebView."""
    redirect_uri = request.GET.get("redirect_uri", "")
    if not _allowed_redirect_uri(redirect_uri):
        return JsonResponse(
            {"success": False, "error": "Unsupported redirect_uri"},
            status=400,
        )

    code = IOSNativeAuthCode.issue(request.user, redirect_uri, request=request)
    complete_url = request.build_absolute_uri(
        f"/api/mobile/ios/auth/complete/{code}/"
    )
    return IOSAppRedirect(
        _append_query(
            redirect_uri,
            {
                "code": code,
                "complete_url": complete_url,
            },
        )
    )


@require_http_methods(["GET"])
def ios_auth_complete(request, code):
    """Consume a one-time auth code and create the WKWebView session."""
    user = IOSNativeAuthCode.consume(code)
    if not user:
        return JsonResponse(
            {"success": False, "error": "Invalid or expired authentication code"},
            status=400,
        )

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session["crush_ios_app"] = True
    return redirect("/en/dashboard/?source=ios_app")


@login_required
@require_http_methods(["GET"])
def list_ios_devices(request):
    devices = IOSAppDevice.objects.filter(user=request.user).order_by("-last_seen_at")
    return JsonResponse(
        {
            "success": True,
            "devices": [
                {
                    "id": device.id,
                    "deviceId": device.device_id,
                    "environment": device.environment,
                    "appVersion": device.app_version,
                    "appBuild": device.app_build,
                    "deviceName": device.device_name,
                    "systemVersion": device.system_version,
                    "enabled": device.enabled,
                    "preferences": {
                        "newMessages": device.notify_new_messages,
                        "eventReminders": device.notify_event_reminders,
                        "newConnections": device.notify_new_connections,
                        "profileUpdates": device.notify_profile_updates,
                    },
                    "lastSeenAt": device.last_seen_at.isoformat(),
                }
                for device in devices
            ],
        }
    )


@login_required
@ratelimit(key="user", rate="30/m", method="POST")
@require_http_methods(["POST"])
def register_ios_device(request):
    data = _json_body(request)
    if data is None:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    device_token = str(data.get("deviceToken", "")).strip()
    if not device_token or len(device_token) > 255 or any(c.isspace() for c in device_token):
        return JsonResponse(
            {"success": False, "error": "Invalid deviceToken"},
            status=400,
        )

    environment = data.get("environment") or (
        "sandbox" if getattr(settings, "IOS_APNS_USE_SANDBOX", False) else "production"
    )
    if environment not in dict(IOSAppDevice.ENVIRONMENT_CHOICES):
        return JsonResponse({"success": False, "error": "Invalid environment"}, status=400)

    device, created = IOSAppDevice.objects.update_or_create(
        device_token=device_token,
        defaults={
            "user": request.user,
            "device_id": str(data.get("deviceId", ""))[:128],
            "environment": environment,
            "bundle_id": str(
                data.get("bundleId", getattr(settings, "IOS_APP_BUNDLE_ID", "lu.crush.app"))
            )[:128],
            "app_version": str(data.get("appVersion", ""))[:32],
            "app_build": str(data.get("appBuild", ""))[:32],
            "device_name": str(data.get("deviceName", ""))[:100],
            "system_version": str(data.get("systemVersion", ""))[:50],
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:1000],
            "enabled": True,
            "failure_count": 0,
        },
    )

    return JsonResponse(
        {
            "success": True,
            "message": "iOS device registration created" if created else "iOS device registration updated",
            "deviceId": device.id,
        }
    )


@login_required
@ratelimit(key="user", rate="30/m", method="POST")
@require_http_methods(["POST", "DELETE"])
def unregister_ios_device(request):
    data = _json_body(request) if request.body else {}
    if data is None:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    devices = IOSAppDevice.objects.filter(user=request.user)
    if data.get("deviceToken"):
        devices = devices.filter(device_token=str(data["deviceToken"]).strip())
    elif data.get("deviceId"):
        devices = devices.filter(device_id=str(data["deviceId"])[:128])
    else:
        return JsonResponse(
            {"success": False, "error": "deviceToken or deviceId required"},
            status=400,
        )

    count = devices.update(enabled=False)
    return JsonResponse({"success": True, "disabled": count})


@login_required
@ratelimit(key="user", rate="30/m", method="POST")
@require_http_methods(["POST"])
def update_ios_device_preferences(request):
    data = _json_body(request)
    if data is None:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    preferences = data.get("preferences", data)
    updates = {
        model_field: bool(preferences[key])
        for key, model_field in PREFERENCE_MAP.items()
        if key in preferences
    }
    if not updates:
        return JsonResponse({"success": False, "error": "No preferences provided"}, status=400)

    devices = IOSAppDevice.objects.filter(user=request.user)
    if data.get("deviceToken"):
        devices = devices.filter(device_token=str(data["deviceToken"]).strip())
    elif data.get("deviceId"):
        devices = devices.filter(device_id=str(data["deviceId"])[:128])

    count = devices.update(**updates)
    return JsonResponse({"success": True, "updated": count})
