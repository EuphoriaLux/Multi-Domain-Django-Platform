"""
Phone Verification Views for Crush.lu

Handles Firebase/Google Identity Platform phone verification.
Uses secure server-side token verification - phone numbers are extracted
from verified tokens, NOT from request body.

Security:
- Token verified using Google's public JWKS keys (no service account needed)
- Phone number extracted from verified token claims only
- Firebase UID stored for audit and anti-replay protection
"""
import json
import logging
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.middleware.csrf import get_token
from django.conf import settings

from django.utils.translation import gettext as _
from django.utils.http import url_has_allowed_host_and_scheme
from .google_idp_verify import verify_firebase_id_token, get_phone_from_token, get_firebase_uid_from_token
from .models import CrushProfile
from .decorators import ratelimit
from .utils.i18n import validate_language

logger = logging.getLogger(__name__)


@login_required
@require_POST
@csrf_protect
@ratelimit(key='user', rate='5/15m', method='POST')  # Rate limit phone verification
def mark_phone_verified(request):
    """
    Mark phone as verified after verifying Firebase ID token.

    Expects JSON body: { "idToken": "..." }

    SECURITY: Phone number is extracted from the verified token claims,
    NOT from the request body. This prevents users from claiming
    arbitrary phone numbers.

    Returns:
        JsonResponse with:
        - success: bool
        - message: str
        - phone_verified: bool (if successful)
        - phone_number: str (if successful, the verified phone)
    """
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"Invalid JSON in phone verification request: {e}")
        return JsonResponse({
            "success": False,
            "error": "Invalid JSON format"
        }, status=400)

    id_token = (payload.get("idToken") or "").strip()
    if not id_token:
        return JsonResponse({
            "success": False,
            "error": "idToken is required"
        }, status=400)

    try:
        # Verify token using Google's public keys (no service account needed)
        decoded = verify_firebase_id_token(id_token)

        # Extract phone number from verified token - this is the secure way
        phone_number = get_phone_from_token(decoded)
        if not phone_number:
            logger.warning(
                f"Token for user {request.user.id} has no phone_number claim"
            )
            return JsonResponse({
                "success": False,
                "error": "Token does not contain a verified phone number"
            }, status=400)

        # Get Firebase UID for audit trail
        firebase_uid = get_firebase_uid_from_token(decoded)

        # Get or create profile
        try:
            profile = request.user.crushprofile
        except CrushProfile.DoesNotExist:
            # Create profile if it doesn't exist (shouldn't happen normally)
            # Set preferred language from current request
            preferred_lang = validate_language(
                getattr(request, 'LANGUAGE_CODE', 'en'), default='en'
            )
            profile = CrushProfile.objects.create(
                user=request.user,
                preferred_language=preferred_lang,
            )

        # Update profile with verified phone
        profile.phone_number = phone_number
        profile.phone_verified = True
        profile.phone_verified_at = timezone.now()
        profile.phone_verification_uid = firebase_uid
        profile.save(update_fields=[
            "phone_number",
            "phone_verified",
            "phone_verified_at",
            "phone_verification_uid"
        ])

        # Log with properly redacted phone number
        phone_redacted = phone_number[:4] + "***" if len(phone_number) > 4 else "***"
        logger.info(
            f"Phone verified for user {request.user.id}: {phone_redacted}"
        )

        return JsonResponse({
            "success": True,
            "message": "Phone number verified successfully",
            "phone_verified": True,
            "phone_number": phone_number,
            "csrfToken": get_token(request),
        })

    except Exception as e:
        logger.error(f"Phone verification failed for user {request.user.id}: {e}", exc_info=True)
        return JsonResponse({
            "success": False,
            "error": "Phone verification failed. Please try again."
        }, status=500)


@login_required
@require_GET
def phone_verification_status(request):
    """
    Get current phone verification status for the logged-in user.

    Returns:
        JsonResponse with:
        - phone_verified: bool
        - phone_verified_at: ISO datetime string or null
        - phone_number: str (only if verified, partial masked)
    """
    try:
        profile = CrushProfile.objects.get(user=request.user)
        phone_number = None
        if profile.phone_verified and profile.phone_number:
            # Show partial phone for confirmation
            phone_number = profile.phone_number

        return JsonResponse({
            "phone_verified": bool(profile.phone_verified),
            "phone_verified_at": (
                profile.phone_verified_at.isoformat()
                if profile.phone_verified_at else None
            ),
            "phone_number": phone_number
        })
    except CrushProfile.DoesNotExist:
        return JsonResponse({
            "phone_verified": False,
            "phone_verified_at": None,
            "phone_number": None
        })


@login_required
def verify_phone_page(request):
    """
    Standalone phone verification page for existing users.

    Users who need to verify their phone (e.g., existing users before
    phone verification was mandatory) can use this page.

    Supports `next` query parameter for redirecting after successful verification.
    """
    try:
        profile = request.user.crushprofile
    except CrushProfile.DoesNotExist:
        # Create profile if it doesn't exist (needed for new signups
        # who are redirected here before completing profile creation)
        # Set preferred language from current request
        preferred_lang = validate_language(
            getattr(request, 'LANGUAGE_CODE', 'en'), default='en'
        )
        profile = CrushProfile.objects.create(
            user=request.user,
            completion_status='not_started',
            preferred_language=preferred_lang,
        )
        logger.info(f"Created CrushProfile for user {request.user.id} during phone verification")

    # Get the redirect URL from query params (for returning to create_profile, etc.)
    next_url = request.GET.get('next', '')

    # Validate next_url to prevent open redirect vulnerabilities
    # Use Django's built-in URL validation
    allowed_hosts = [request.get_host()] if request else settings.ALLOWED_HOSTS
    if next_url and not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts=allowed_hosts,
        require_https=request.is_secure() if request else False
    ):
        # Invalid redirect target - ignore it
        next_url = ''

    # If already verified, redirect to next or dashboard with message
    if profile.phone_verified:
        messages.info(request, _("Your phone number is already verified."))
        if next_url:
            return redirect(next_url)
        return redirect('crush_lu:dashboard')

    context = {
        'profile': profile,
        'current_phone': profile.phone_number or '',
        'next_url': next_url,  # Pass to template for redirect after verification
        # Firebase config from environment variables
        'firebase_api_key': settings.FIREBASE_API_KEY,
        'firebase_auth_domain': settings.FIREBASE_AUTH_DOMAIN,
        'firebase_project_id': settings.FIREBASE_PROJECT_ID,
        # User's preferred language for Firebase SMS localization
        'firebase_language': profile.preferred_language or 'en',
    }

    return render(request, 'crush_lu/verify_phone.html', context)
