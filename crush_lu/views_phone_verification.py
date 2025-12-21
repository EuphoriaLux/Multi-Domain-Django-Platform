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

from .google_idp_verify import verify_firebase_id_token, get_phone_from_token, get_firebase_uid_from_token
from .models import CrushProfile

logger = logging.getLogger(__name__)


@login_required
@require_POST
@csrf_protect
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
            profile = CrushProfile.objects.create(user=request.user)

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

        logger.info(
            f"Phone verified for user {request.user.id}: {phone_number[:7]}***"
        )

        return JsonResponse({
            "success": True,
            "message": "Phone number verified successfully",
            "phone_verified": True,
            "phone_number": phone_number,
        })

    except Exception as e:
        logger.error(f"Phone verification failed for user {request.user.id}: {e}")
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=400)


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
        profile = request.user.crushprofile
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
    """
    try:
        profile = request.user.crushprofile
    except CrushProfile.DoesNotExist:
        # No profile yet, redirect to profile creation
        return redirect('crush_lu:create_profile')

    # If already verified, redirect to dashboard with message
    if profile.phone_verified:
        messages.info(request, "Your phone number is already verified.")
        return redirect('crush_lu:dashboard')

    context = {
        'profile': profile,
        'current_phone': profile.phone_number or '',
    }

    return render(request, 'crush_lu/verify_phone.html', context)
