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
import re
from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.middleware.csrf import get_token

from django.utils.translation import gettext as _
from .google_idp_verify import verify_firebase_id_token, get_phone_from_token, get_firebase_uid_from_token
from .models import CrushProfile, PhoneOTP
from .services import whatsapp
from .decorators import ratelimit
from .utils.i18n import validate_language


def _canonicalize_phone(raw: str) -> str:
    """Collapse a typed number to a single canonical ``+<digits>`` form.

    ``+352621…``, ``352621…`` and ``00352621…`` must all map to one string, or
    the string-based uniqueness constraint and the dedup query miss a match: a
    second account could verify the same real number under a different spelling
    (Meta normalizes them all to the same recipient anyway). save() does not run
    the model's ``+``-prefixed validator, so we must canonicalize here.

    Returns "" when there aren't enough digits to be a plausible E.164 number.
    """
    digits = re.sub(r"[^\d]", "", raw or "")
    digits = re.sub(r"^00", "", digits)  # international call prefix -> "+"
    if len(digits) < 8:
        return ""
    return f"+{digits}"


def _phone_taken_by_other(canonical_phone, *, exclude_user=None, exclude_pk=None):
    """True if another profile already holds this real number.

    Compares in canonical form because existing rows aren't guaranteed
    canonical: save_profile_step1 stores the raw stripped value and the DB
    uniqueness constraint is string-based, so a number saved elsewhere as
    "+352 621 123 456" would slip past an exact match on "+352621123456".
    (Repo-wide normalization is the real fix; this guards just this flow.)
    """
    qs = CrushProfile.objects.exclude(phone_number="")
    if exclude_user is not None:
        qs = qs.exclude(user=exclude_user)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    # Fast path: an exact (already-canonical) row uses the phone_number index.
    if qs.filter(phone_number=canonical_phone).exists():
        return True
    # Otherwise compare canonically to catch formatting variants.
    for stored in qs.values_list("phone_number", flat=True).iterator():
        if _canonicalize_phone(stored) == canonical_phone:
            return True
    return False


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

        # If phone is already verified, return the existing verified phone
        # The model's save() override protects verified phone data from being
        # overwritten, so attempting to save a different number would silently
        # keep the old one - leading to a misleading response.
        if profile.phone_verified:
            logger.info(
                f"Phone already verified for user ID: {request.user.id}, "
                f"returning existing verified status"
            )
            return JsonResponse({
                "success": True,
                "message": _("Your phone number is already verified."),
                "phone_verified": True,
                "phone_number": profile.phone_number,
                "csrfToken": get_token(request),
            })

        # Check if this phone number is already verified by another profile
        existing = CrushProfile.objects.filter(
            phone_number=phone_number,
            phone_verified=True,
        ).exclude(pk=profile.pk).exists()
        if existing:
            logger.warning(
                "Phone already in use by another profile (user ID: %s)",
                request.user.id,
            )
            return JsonResponse({
                "success": False,
                "error": _("This phone number is already associated with another account."),
                "error_code": "phone_already_in_use",
            }, status=409)

        # Update profile with verified phone
        profile.phone_number = phone_number
        profile.phone_verified = True
        profile.phone_verified_at = timezone.now()
        profile.phone_verification_uid = firebase_uid
        try:
            profile.save(update_fields=[
                "phone_number",
                "phone_verified",
                "phone_verified_at",
                "phone_verification_uid"
            ])
        except IntegrityError:
            # Race condition: another request verified this phone between check and save
            logger.warning(
                "IntegrityError: phone already in use (race condition, user ID: %s)",
                request.user.id,
            )
            return JsonResponse({
                "success": False,
                "error": _("This phone number is already associated with another account."),
                "error_code": "phone_already_in_use",
            }, status=409)

        # Log without phone number (avoid clear-text PII - even redacted)
        logger.info(
            f"Phone verified for user ID: {request.user.id}"
        )

        return JsonResponse({
            "success": True,
            "message": _("Phone number verified successfully"),
            "phone_verified": True,
            "phone_number": phone_number,
            "csrfToken": get_token(request),
        })

    except Exception as e:
        logger.error("Phone verification failed for user %s: %s", request.user.id, type(e).__name__)
        return JsonResponse({
            "success": False,
            "error": "Phone verification failed. Please try again."
        }, status=500)


@login_required
@require_POST
@csrf_protect
@ratelimit(key='user', rate='10/m', method='POST')
def check_phone_available(request):
    """
    Check if a phone number is available (not already verified by another user).

    Call this BEFORE sending the Firebase SMS to avoid wasting SMS credits
    on phone numbers that are already taken.

    Returns:
        JsonResponse with:
        - available: bool (True if phone can be used)
        - error: str (only when available is False)
    """
    try:
        data = json.loads(request.body)
        phone_number = data.get('phone_number', '').strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"available": False, "error": "Invalid request"}, status=400)

    if not phone_number:
        return JsonResponse({"available": False, "error": "Phone number is required"}, status=400)

    # Normalize: remove spaces/dashes for comparison
    import re
    phone_clean = re.sub(r'[\s\-\(\)]', '', phone_number)

    # Check if this phone is already verified by a different user
    already_taken = CrushProfile.objects.filter(
        phone_number=phone_clean,
        phone_verified=True,
    ).exclude(user=request.user).exists()

    if already_taken:
        return JsonResponse({
            "available": False,
            "error": _("This phone number is already associated with another account."),
        })

    return JsonResponse({"available": True})


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


# ─── WhatsApp OTP (third channel, alongside Firebase SMS and LuxID) ──────────
#
# Unlike Firebase, Meta is delivery-only: we generate, store (hashed), and
# verify the code ourselves. A successful verify sets phone_verified exactly
# like mark_phone_verified() does for the Firebase path.


@login_required
@require_POST
@csrf_protect
@ratelimit(key='user', rate='3/15m', method='POST')  # cap WhatsApp sends (cost + abuse)
def send_whatsapp_otp(request):
    """Generate a code, store it hashed, and deliver it over WhatsApp.

    The code is only persisted after a successful send, so failed sends don't
    invalidate a previously delivered code. If the number isn't on WhatsApp
    (Meta error 131026) we signal the client to offer SMS instead.
    """
    # A verified phone can't be changed through this flow (the model's save()
    # protects it), so never burn a billable send for an already-verified user
    # — otherwise they could fire OTPs at arbitrary numbers up to the rate limit.
    try:
        existing_profile = request.user.crushprofile
    except CrushProfile.DoesNotExist:
        existing_profile = None
    if existing_profile is not None and existing_profile.phone_verified:
        return JsonResponse({
            "success": True,
            "already_verified": True,
            "message": _("Your phone number is already verified."),
            "phone_number": existing_profile.phone_number,
        })

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"success": False, "error": "Invalid JSON format"}, status=400)

    phone_number = _canonicalize_phone(payload.get("phone_number", ""))
    if not phone_number:
        return JsonResponse(
            {"success": False, "error": _("A valid phone number is required")},
            status=400,
        )

    # Reject any number already held by another profile (verified or not, in
    # any spelling) — matching the unique_non_empty_phone_number scope so we
    # never burn a billable send on a number verify_whatsapp_otp() could only
    # fail with an IntegrityError.
    if _phone_taken_by_other(phone_number, exclude_user=request.user):
        return JsonResponse({
            "success": False,
            "error": _("This phone number is already associated with another account."),
            "error_code": "phone_already_in_use",
        }, status=409)

    if not whatsapp.is_configured():
        return JsonResponse({
            "success": False,
            "error": _("WhatsApp verification is currently unavailable."),
        }, status=503)

    lang = validate_language(getattr(request, "LANGUAGE_CODE", "en"), default="en")
    code = PhoneOTP.generate_code()
    result = whatsapp.send_otp(phone_number, code, language=lang)

    if not result.ok:
        if result.not_on_whatsapp:
            return JsonResponse({
                "success": False,
                "error": _("This number isn't on WhatsApp. Try SMS verification instead."),
                "error_code": "not_on_whatsapp",
            }, status=422)
        logger.warning("WhatsApp OTP send failed for user %s", request.user.id)
        return JsonResponse({
            "success": False,
            "error": _("Could not send the WhatsApp code. Please try again."),
        }, status=502)

    ttl = getattr(settings, "WHATSAPP_OTP_TTL_MINUTES", 3)
    PhoneOTP.issue(
        user=request.user,
        phone_number=phone_number,
        code=code,
        channel=PhoneOTP.Channel.WHATSAPP,
        ttl_minutes=ttl,
    )
    logger.info("WhatsApp OTP sent for user ID: %s", request.user.id)
    return JsonResponse({
        "success": True,
        "message": _("Verification code sent to your WhatsApp."),
        "ttl_minutes": ttl,
    })


@login_required
@require_POST
@csrf_protect
@ratelimit(key='user', rate='10/15m', method='POST')  # cap verify attempts
def verify_whatsapp_otp(request):
    """Check a WhatsApp OTP and, on success, mark the phone verified.

    Mirrors the verified-phone write and "already in use" race guards from
    mark_phone_verified() so both channels behave identically.
    """
    # Idempotency: a retry or double-submit after a successful first verify
    # finds the OTP already consumed. Return success when the profile is already
    # verified instead of a misleading otp_expired (a lost first response must
    # not look like a failure).
    try:
        verified_profile = request.user.crushprofile
    except CrushProfile.DoesNotExist:
        verified_profile = None
    if verified_profile is not None and verified_profile.phone_verified:
        return JsonResponse({
            "success": True,
            "message": _("Your phone number is already verified."),
            "phone_verified": True,
            "phone_number": verified_profile.phone_number,
            "csrfToken": get_token(request),
        })

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"success": False, "error": "Invalid JSON format"}, status=400)

    code = (payload.get("code") or "").strip()
    if not code:
        return JsonResponse(
            {"success": False, "error": _("Code is required")}, status=400
        )

    # Lock the OTP row while we check and increment attempts so concurrent
    # verifies can't each pass the cap on the same pre-increment count and
    # exceed MAX_ATTEMPTS in a race (no-op on SQLite; enforced on Postgres).
    with transaction.atomic():
        otp = (
            PhoneOTP.objects
            .select_for_update()
            .filter(user=request.user, channel=PhoneOTP.Channel.WHATSAPP, consumed=False)
            .order_by("-created_at")
            .first()
        )
        if otp is None or otp.is_expired:
            return JsonResponse({
                "success": False,
                "error": _("Your code has expired. Please request a new one."),
                "error_code": "otp_expired",
            }, status=400)
        verified_ok = otp.verify(code)

    if not verified_ok:
        remaining = max(0, PhoneOTP.MAX_ATTEMPTS - otp.attempts)
        return JsonResponse({
            "success": False,
            "error": _("Incorrect code. Please try again."),
            "error_code": "otp_invalid",
            "attempts_remaining": remaining,
        }, status=400)

    # Code valid — promote to a verified phone (get-or-create profile first).
    try:
        profile = request.user.crushprofile
    except CrushProfile.DoesNotExist:
        preferred_lang = validate_language(
            getattr(request, "LANGUAGE_CODE", "en"), default="en"
        )
        profile = CrushProfile.objects.create(
            user=request.user, preferred_language=preferred_lang
        )

    if profile.phone_verified:
        return JsonResponse({
            "success": True,
            "message": _("Your phone number is already verified."),
            "phone_verified": True,
            "phone_number": profile.phone_number,
            "csrfToken": get_token(request),
        })

    # Same canonical, non-empty uniqueness scope as the send check.
    if _phone_taken_by_other(otp.phone_number, exclude_pk=profile.pk):
        return JsonResponse({
            "success": False,
            "error": _("This phone number is already associated with another account."),
            "error_code": "phone_already_in_use",
        }, status=409)

    profile.phone_number = otp.phone_number
    profile.phone_verified = True
    profile.phone_verified_at = timezone.now()
    profile.phone_verification_uid = f"whatsapp:{otp.pk}"
    try:
        profile.save(update_fields=[
            "phone_number",
            "phone_verified",
            "phone_verified_at",
            "phone_verification_uid",
        ])
    except IntegrityError:
        return JsonResponse({
            "success": False,
            "error": _("This phone number is already associated with another account."),
            "error_code": "phone_already_in_use",
        }, status=409)

    logger.info("Phone verified via WhatsApp for user ID: %s", request.user.id)
    return JsonResponse({
        "success": True,
        "message": _("Phone number verified successfully"),
        "phone_verified": True,
        "phone_number": otp.phone_number,
        "csrfToken": get_token(request),
    })


