import json
import logging
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .decorators import ratelimit
from .mobile_auth import clear_mobile_handoff, stash_mobile_handoff
from .models import IOSNativeAuthCode, AndroidAppDevice

logger = logging.getLogger(__name__)

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


class AndroidAppRedirect(HttpResponseRedirect):
    allowed_schemes = ["http", "https", "ftp", "crushlu"]


def _allowed_redirect_uri(uri):
    return uri in set(getattr(settings, "ANDROID_AUTH_REDIRECT_URIS", []))


def _append_query(uri, params):
    parsed = urlparse(uri)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urlunparse(parsed._replace(query=urlencode(query)))


@require_http_methods(["GET"])
def android_app_config(request):
    """Public native Android app bootstrap config."""
    return JsonResponse(
        {
            "success": True,
            "app": {
                "name": getattr(settings, "ANDROID_APP_NAME", "Crush.lu"),
                "packageName": getattr(settings, "ANDROID_APP_PACKAGE", "lu.crush.app"),
                "version": getattr(settings, "ANDROID_APP_VERSION", "1.0.0"),
                "build": getattr(settings, "ANDROID_APP_BUILD", "1"),
                "minSupportedVersion": getattr(
                    settings, "ANDROID_APP_MIN_SUPPORTED_VERSION", "1.0.0"
                ),
                "playStoreUrl": getattr(settings, "ANDROID_PLAY_STORE_URL", ""),
            },
            "features": {
                "nativeCommerceEnabled": getattr(
                    settings, "ANDROID_NATIVE_COMMERCE_ENABLED", False
                ),
                "appLinksEnabled": True,
            },
            "urls": {
                "start": "/en/dashboard/?source=android_app",
                "login": "/en/login/?source=android_app",
                "privacy": "/en/privacy-policy/?source=android_app",
                "terms": "/en/terms-of-service/?source=android_app",
                "accountDeletion": "/en/account/gdpr/?source=android_app",
                "notificationSettings": "/en/profile/edit/?section=account&sub=notifications&source=android_app",
                "support": "/en/support/?source=android_app",
                "childSafetyStandards": "/en/child-safety-standards/?source=android_app",
            },
        }
    )


@require_http_methods(["GET"])
def android_auth_handoff(request):
    """Issue a one-time code that the Android shell can redeem in WebView."""
    redirect_uri = request.GET.get("redirect_uri", "")
    if not _allowed_redirect_uri(redirect_uri):
        return JsonResponse(
            {"success": False, "error": "Unsupported redirect_uri"},
            status=400,
        )

    if not request.user.is_authenticated:
        # Stash a session flag so any login completed inside the auth sheet
        # routes back here even if the ?next= chain gets lost on the way.
        stash_mobile_handoff(request, "android", redirect_uri)
        return redirect_to_login(request.get_full_path())

    clear_mobile_handoff(request)
    code = IOSNativeAuthCode.issue(request.user, redirect_uri, request=request)
    complete_url = request.build_absolute_uri(
        f"/api/mobile/android/auth/complete/{code}/"
    )
    return AndroidAppRedirect(
        _append_query(
            redirect_uri,
            {
                "code": code,
                "complete_url": complete_url,
            },
        )
    )


@require_http_methods(["GET"])
def android_auth_complete(request, code):
    """Consume a one-time auth code and create the Android WebView session."""
    user = IOSNativeAuthCode.consume(code)
    if not user:
        return JsonResponse(
            {"success": False, "error": "Invalid or expired authentication code"},
            status=400,
        )

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session["crush_android_app"] = True
    return redirect("/en/dashboard/?source=android_app")


@login_required
@require_http_methods(["GET"])
def list_android_devices(request):
    devices = AndroidAppDevice.objects.filter(user=request.user).order_by(
        "-last_seen_at"
    )
    return JsonResponse(
        {
            "success": True,
            "devices": [
                {
                    "id": device.id,
                    "deviceId": device.device_id,
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
@csrf_exempt
@require_http_methods(["POST"])
def register_android_device(request):
    data = _json_body(request)
    if data is None:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    registration_token = str(data.get("registrationToken", "")).strip()
    if (
        not registration_token
        or len(registration_token) > 255
        or any(c.isspace() for c in registration_token)
    ):
        return JsonResponse(
            {"success": False, "error": "Invalid registrationToken"},
            status=400,
        )

    device, created = AndroidAppDevice.objects.update_or_create(
        registration_token=registration_token,
        defaults={
            "user": request.user,
            "device_id": str(data.get("deviceId", ""))[:128],
            "package_name": str(
                data.get(
                    "packageName",
                    getattr(settings, "ANDROID_APP_PACKAGE", "lu.crush.app"),
                )
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
            "message": (
                "Android device registration created"
                if created
                else "Android device registration updated"
            ),
            "deviceId": device.id,
        }
    )


@login_required
@ratelimit(key="user", rate="30/m", method="POST")
@csrf_exempt
@require_http_methods(["POST", "DELETE"])
def unregister_android_device(request):
    data = _json_body(request) if request.body else {}
    if data is None:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    devices = AndroidAppDevice.objects.filter(user=request.user)
    if data.get("registrationToken"):
        devices = devices.filter(
            registration_token=str(data["registrationToken"]).strip()
        )
    elif data.get("deviceId"):
        devices = devices.filter(device_id=str(data["deviceId"])[:128])
    else:
        return JsonResponse(
            {"success": False, "error": "registrationToken or deviceId required"},
            status=400,
        )

    count = devices.update(enabled=False)
    return JsonResponse({"success": True, "disabled": count})


@login_required
@ratelimit(key="user", rate="30/m", method="POST")
@csrf_exempt
@require_http_methods(["POST"])
def update_android_device_preferences(request):
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
        return JsonResponse(
            {"success": False, "error": "No preferences provided"}, status=400
        )

    devices = AndroidAppDevice.objects.filter(user=request.user)
    if data.get("registrationToken"):
        devices = devices.filter(
            registration_token=str(data["registrationToken"]).strip()
        )
    elif data.get("deviceId"):
        devices = devices.filter(device_id=str(data["deviceId"])[:128])

    count = devices.update(**updates)
    return JsonResponse({"success": True, "updated": count})
