from django.conf import settings

from .mobile_auth import SESSION_KEY as MOBILE_HANDOFF_SESSION_KEY
from .mobile_auth import peek_mobile_handoff_url


def is_ios_native_request(request):
    """Return True when a request is coming from the native iOS shell."""
    if request.GET.get("source") == "ios_app":
        return True
    if request.META.get("HTTP_X_CRUSH_CLIENT", "").lower() == "ios-app":
        return True
    if "CrushLUApp/" in request.META.get("HTTP_USER_AGENT", ""):
        return True
    if hasattr(request, "session"):
        return request.session.get("crush_ios_app") is True
    return False


def is_ios_tracking_suppressed(request):
    """Return True when no tracking tags or consent UI may be served to iOS.

    App Review (guideline 5.1.2) treats GA4, the Meta Pixel and App Insights as
    tracking that needs App Tracking Transparency; the app does not track.
    Beyond the native shell this covers the login sheet
    (ASWebAuthenticationSession): a plain browser request until login
    completes, marked only by the handoff's session flag. Kept out of
    is_ios_native_request so commerce stays visible in Safari after an
    abandoned sheet; the flag expires after 10 minutes.
    """
    if is_ios_native_request(request):
        return True
    if peek_mobile_handoff_url(request) is None:
        return False
    return request.session[MOBILE_HANDOFF_SESSION_KEY].get("platform") == "ios"


def is_android_native_request(request):
    """Return True when a request is coming from the native Android shell."""
    if request.GET.get("source") == "android_app":
        return True
    if request.META.get("HTTP_X_CRUSH_CLIENT", "").lower() == "android-app":
        return True
    if "CrushLUAndroid/" in request.META.get("HTTP_USER_AGENT", ""):
        return True
    if hasattr(request, "session"):
        return request.session.get("crush_android_app") is True
    return False


def is_native_app_request(request):
    return is_ios_native_request(request) or is_android_native_request(request)


def is_ios_device(request):
    """Return True if the user is on an iOS device (native app or browser)."""
    if is_ios_native_request(request):
        return True
    ua = request.META.get("HTTP_USER_AGENT", "").lower()
    return any(device in ua for device in ("iphone", "ipad", "ipod"))


def is_android_device(request):
    """Return True if the user is on an Android device (native app or browser)."""
    if is_android_native_request(request):
        return True
    ua = request.META.get("HTTP_USER_AGENT", "").lower()
    return "android" in ua


def ios_commerce_suppressed(request):
    return is_ios_native_request(request) and not getattr(
        settings, "IOS_NATIVE_COMMERCE_ENABLED", False
    )


def android_commerce_suppressed(request):
    return is_android_native_request(request) and not getattr(
        settings, "ANDROID_NATIVE_COMMERCE_ENABLED", False
    )


def native_commerce_suppressed(request):
    return ios_commerce_suppressed(request) or android_commerce_suppressed(request)
