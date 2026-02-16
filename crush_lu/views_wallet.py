import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

from .models import CrushProfile, EventRegistration

logger = logging.getLogger(__name__)


def _is_apple_wallet_configured():
    """Check if Apple Wallet settings are configured."""
    required_settings = [
        "WALLET_APPLE_PASS_TYPE_IDENTIFIER",
        "WALLET_APPLE_TEAM_IDENTIFIER",
        "WALLET_APPLE_ORGANIZATION_NAME",
        "WALLET_APPLE_CERT_PATH",
        "WALLET_APPLE_KEY_PATH",
        "WALLET_APPLE_WWDR_CERT_PATH",
    ]
    return all(getattr(settings, s, None) for s in required_settings)


def _is_google_wallet_configured():
    """Check if Google Wallet settings are configured."""
    required_settings = [
        "WALLET_GOOGLE_ISSUER_ID",
        "WALLET_GOOGLE_CLASS_ID",
        "WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL",
    ]
    # Also need either WALLET_GOOGLE_PRIVATE_KEY or WALLET_GOOGLE_PRIVATE_KEY_PATH
    has_key = bool(
        getattr(settings, "WALLET_GOOGLE_PRIVATE_KEY", None)
        or getattr(settings, "WALLET_GOOGLE_PRIVATE_KEY_PATH", None)
    )
    return all(getattr(settings, s, None) for s in required_settings) and has_key


@login_required
@require_GET
def apple_wallet_pass(request):
    """
    Generate and return an Apple Wallet .pkpass file for the current user.

    The pass includes:
    - Member name and tier
    - Next upcoming event (if registered)
    - Referral QR code
    - Points balance

    NOTE: Apple Wallet is currently not implemented - returns "Coming Soon" message.
    """
    # Feature not yet implemented - return Coming Soon message
    from django.utils.translation import gettext as _

    logger.info("Apple Wallet pass requested - feature coming soon")
    return JsonResponse(
        {
            "error": _("Apple Wallet support is coming soon! Please use Google Wallet in the meantime."),
            "status": "coming_soon",
        },
        status=501,  # Not Implemented
    )

    # TODO: Uncomment below when Apple Wallet is implemented
    # # Check if Apple Wallet is configured
    # if not _is_apple_wallet_configured():
    #     logger.warning("Apple Wallet pass requested but not configured")
    #     return JsonResponse(
    #         {"error": "Apple Wallet is not configured on this server."},
    #         status=503,
    #     )
    #
    # try:
    #     from .wallet import build_apple_pass
    #
    #     profile, _ = CrushProfile.objects.get_or_create(user=request.user)
    #     pkpass_data = build_apple_pass(profile, request=request)
    #     response = HttpResponse(pkpass_data, content_type="application/vnd.apple.pkpass")
    #     response["Content-Disposition"] = "attachment; filename=crushlu.pkpass"
    #     return response
    # except ImproperlyConfigured as e:
    #     logger.error("Apple Wallet configuration error: %s", e)
    #     return JsonResponse(
    #         {"error": "Apple Wallet is not properly configured."},
    #         status=503,
    #     )
    # except Exception as e:
    #     logger.exception("Error generating Apple Wallet pass: %s", e)
    #     return JsonResponse(
    #         {"error": "Failed to generate Apple Wallet pass."},
    #         status=500,
    #     )


@login_required
@require_GET
def google_wallet_jwt(request):
    """
    Generate and return a JWT for adding to Google Wallet.

    The pass includes:
    - Member name and tier
    - Next upcoming event (if registered)
    - Referral QR code
    - Points balance
    """
    # Check if Google Wallet is configured
    if not _is_google_wallet_configured():
        logger.warning("Google Wallet JWT requested but not configured")
        return JsonResponse(
            {"error": "Google Wallet is not configured on this server."},
            status=503,
        )

    try:
        from .wallet import build_google_wallet_jwt

        profile, _ = CrushProfile.objects.get_or_create(user=request.user)
        jwt_token = build_google_wallet_jwt(profile, request=request)
        return JsonResponse({"jwt": jwt_token})
    except ImproperlyConfigured as e:
        logger.error("Google Wallet configuration error: %s", e)
        return JsonResponse(
            {"error": "Google Wallet is not properly configured."},
            status=503,
        )
    except Exception as e:
        logger.exception("Error generating Google Wallet JWT: %s", e)
        return JsonResponse(
            {"error": "Failed to generate Google Wallet JWT."},
            status=500,
        )


@login_required
@require_GET
def google_event_ticket_jwt(request, registration_id):
    """
    Generate and return a JWT for adding an event ticket to Google Wallet.

    Validates that the current user owns the registration and that the
    registration status is confirmed or attended.
    """
    if not _is_google_wallet_configured():
        return JsonResponse(
            {"error": "Google Wallet is not configured on this server."},
            status=503,
        )

    if not getattr(settings, "WALLET_GOOGLE_EVENT_TICKET_ENABLED", True):
        return JsonResponse(
            {"error": "Event tickets are not enabled."},
            status=503,
        )

    try:
        registration = EventRegistration.objects.select_related(
            "event", "user", "user__crushprofile"
        ).get(id=registration_id, user=request.user)
    except EventRegistration.DoesNotExist:
        return JsonResponse({"error": "Registration not found."}, status=404)

    if registration.status not in ("confirmed", "attended"):
        return JsonResponse(
            {"error": "Only confirmed registrations can be added to wallet."},
            status=400,
        )

    try:
        from .wallet.google_event_ticket import build_google_event_ticket_jwt

        jwt_token = build_google_event_ticket_jwt(registration, request=request)
        return JsonResponse({"jwt": jwt_token})
    except ImproperlyConfigured as e:
        logger.error("Google Wallet event ticket config error: %s", e)
        return JsonResponse(
            {"error": "Google Wallet is not properly configured."},
            status=503,
        )
    except Exception as e:
        logger.exception("Error generating event ticket JWT: %s", e)
        return JsonResponse(
            {"error": "Failed to generate event ticket JWT."},
            status=500,
        )