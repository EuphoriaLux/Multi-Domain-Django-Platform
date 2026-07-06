from django.conf import settings


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


def android_commerce_suppressed(request):
    return is_android_native_request(request) and not getattr(
        settings, "ANDROID_NATIVE_COMMERCE_ENABLED", False
    )
