from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from .models import IOSNativeAuthCode


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
            },
        }
    )


@login_required
@require_http_methods(["GET"])
def android_auth_handoff(request):
    """Issue a one-time code that the Android shell can redeem in WebView."""
    redirect_uri = request.GET.get("redirect_uri", "")
    if not _allowed_redirect_uri(redirect_uri):
        return JsonResponse(
            {"success": False, "error": "Unsupported redirect_uri"},
            status=400,
        )

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
