"""
Crush.lu PWA Device Registration API.

Tracks PWA installations across user devices for admin analytics.
"""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from .models import PWADeviceInstallation, UserActivity

logger = logging.getLogger(__name__)


@login_required
@csrf_protect
@require_http_methods(["POST"])
def register_pwa_installation(request):
    """
    Register a PWA installation with device information.

    Expected JSON body:
    {
        "deviceFingerprint": "abc123...",
        "osType": "android|ios|windows|macos|linux|chromeos|unknown",
        "formFactor": "phone|tablet|desktop|unknown",
        "browser": "Chrome|Safari|Edge|Firefox|...",
        "deviceCategory": "Android Phone|iPhone|Windows Desktop|...",
        "userAgent": "Mozilla/5.0..."
    }

    Returns:
    {
        "success": true,
        "message": "created|updated",
        "deviceCategory": "Android Phone"
    }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse(
            {"success": False, "error": "Invalid JSON"},
            status=400
        )

    fingerprint = data.get("deviceFingerprint", "")
    if not fingerprint:
        return JsonResponse(
            {"success": False, "error": "deviceFingerprint required"},
            status=400
        )

    # Validate os_type
    valid_os = ["ios", "android", "windows", "macos", "linux", "chromeos", "unknown"]
    os_type = data.get("osType", "unknown")
    if os_type not in valid_os:
        os_type = "unknown"

    # Validate form_factor
    valid_form = ["phone", "tablet", "desktop", "unknown"]
    form_factor = data.get("formFactor", "unknown")
    if form_factor not in valid_form:
        form_factor = "unknown"

    # Create or update the installation record
    installation, created = PWADeviceInstallation.objects.update_or_create(
        user=request.user,
        device_fingerprint=fingerprint,
        defaults={
            "os_type": os_type,
            "form_factor": form_factor,
            "device_category": data.get("deviceCategory", "Unknown Device")[:50],
            "browser": data.get("browser", "")[:50],
            "user_agent": data.get("userAgent", "")[:500],
            "last_used_at": timezone.now(),
        }
    )

    # Also update UserActivity.is_pwa_user for backwards compatibility
    activity, _ = UserActivity.objects.get_or_create(
        user=request.user,
        defaults={"last_seen": timezone.now()}
    )
    if not activity.is_pwa_user:
        activity.is_pwa_user = True
        activity.last_pwa_visit = timezone.now()
        activity.save(update_fields=["is_pwa_user", "last_pwa_visit"])
    else:
        activity.last_pwa_visit = timezone.now()
        activity.save(update_fields=["last_pwa_visit"])

    logger.info(
        "PWA installation %s for user %s: %s",
        "created" if created else "updated",
        request.user.username,
        installation.device_category
    )

    return JsonResponse({
        "success": True,
        "message": "created" if created else "updated",
        "deviceCategory": installation.device_category
    })
