from django.shortcuts import render, redirect
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from urllib.parse import urlencode
from django.utils.http import url_has_allowed_host_and_scheme
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponsePermanentRedirect, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.debug import sensitive_post_parameters
from django.conf import settings
from django.core.cache import cache
import logging
import uuid
import json
import base64
import hashlib
import hmac

logger = logging.getLogger(__name__)

from .models import (
    CrushProfile,
    ProfileSubmission,
    EventRegistration,
    EventConnection,
    ConnectionMessage,
    CoachSession,
)
from .forms import CrushSignupForm
from .decorators import crush_login_required, ratelimit
from .rate_limit_utils import add_rate_limited_error, humanize_wait_seconds
from .referrals import (
    capture_referral,
    capture_referral_from_request,
)


def oauth_complete(request):
    """
    PWA OAuth completion handler.

    When OAuth (Facebook, etc.) completes on Android, it typically opens in the
    system browser instead of returning to the PWA. This view provides:

    1. A landing page that confirms login success
    2. An automatic redirect attempt back to the PWA
    3. A manual "Open in Crush.lu App" button as fallback

    The page uses multiple strategies to return to the PWA:
    - Android Intent URL scheme
    - window.open with target _self
    - Meta refresh as fallback
    """
    if not request.user.is_authenticated:
        # Not logged in - redirect to login
        return redirect("crush_lu:login")

    # Get the intended destination from session, or default to dashboard
    final_destination = request.session.pop("oauth_final_destination", "/dashboard/")

    # Route users who haven't finished onboarding through the smart-resume
    # entry, which lands them on the correct step (welcome / phone / coach
    # intro / build profile / …) based on CrushProfile state.
    try:
        profile = request.user.crushprofile
        from . import onboarding

        # Steps 1-4 (welcome → build profile) resume in the onboarding flow;
        # once the profile is submitted (step 5, pending/verified) land on the
        # dashboard, where the get-verified card guides them.
        if onboarding.get_current_step(profile) < 5:
            final_destination = "/onboarding/"
    except CrushProfile.DoesNotExist:
        final_destination = "/onboarding/"

    context = {
        "final_destination": final_destination,
        "user": request.user,
    }
    return render(request, "crush_lu/oauth_complete.html", context)


# SEC-03: @csrf_exempt is intentional here. Authentication is provided by
# Facebook's signed_request (HMAC-SHA256 with app_secret, verified below via
# parse_facebook_signed_request). Facebook cannot fetch and return our CSRF
# token, so standard Django CSRF protection is not applicable — the signed
# request IS the CSRF-equivalent authenticity proof. Do not remove.
@csrf_exempt
@require_http_methods(["POST"])
def facebook_data_deletion_callback(request):
    """
    Facebook Data Deletion Callback URL.

    Facebook sends a signed request when a user requests to delete their data.
    This endpoint:
    1. Verifies the signed request using app secret
    2. Finds and deletes/anonymizes the user's data
    3. Returns a JSON response with confirmation URL and code

    Facebook docs: https://developers.facebook.com/docs/development/create-an-app/app-dashboard/data-deletion-callback
    """
    try:
        signed_request = request.POST.get("signed_request")
        if not signed_request:
            logger.error("Facebook data deletion: No signed_request provided")
            return JsonResponse({"error": "No signed_request"}, status=400)

        # Parse and verify the signed request
        data = parse_facebook_signed_request(signed_request)
        if not data:
            logger.error("Facebook data deletion: Invalid signed_request")
            return JsonResponse({"error": "Invalid signed_request"}, status=400)

        facebook_user_id = data.get("user_id")
        if not facebook_user_id:
            logger.error("Facebook data deletion: No user_id in signed_request")
            return JsonResponse({"error": "No user_id"}, status=400)

        # Find the user by their Facebook social account
        from allauth.socialaccount.models import SocialAccount

        try:
            social_account = SocialAccount.objects.get(
                provider="facebook", uid=facebook_user_id
            )
            user = social_account.user

            # Generate a unique confirmation code
            confirmation_code = str(uuid.uuid4())

            # Log the deletion request
            logger.info(
                f"Facebook data deletion request for user {user.id} (FB ID: {facebook_user_id})"
            )

            # Delete/anonymize user data
            delete_full_account(user)

            # Build the status URL where user can check deletion status
            status_url = request.build_absolute_uri(
                f"/data-deletion/status/?code={confirmation_code}"
            )

            # Return the required JSON response
            return JsonResponse(
                {"url": status_url, "confirmation_code": confirmation_code}
            )

        except SocialAccount.DoesNotExist:
            # User not found - still return success (data already doesn't exist)
            logger.warning(
                f"Facebook data deletion: No user found for FB ID {facebook_user_id}"
            )
            confirmation_code = str(uuid.uuid4())
            status_url = request.build_absolute_uri(
                f"/data-deletion/status/?code={confirmation_code}"
            )
            return JsonResponse(
                {"url": status_url, "confirmation_code": confirmation_code}
            )

    except Exception as e:
        logger.exception(f"Facebook data deletion error: {str(e)}")
        return JsonResponse({"error": "Server error"}, status=500)


def parse_facebook_signed_request(signed_request):
    """
    Parse and verify a Facebook signed request.

    Args:
        signed_request: The signed_request string from Facebook

    Returns:
        dict: The decoded payload if valid, None otherwise
    """
    try:
        # Get app secret from settings
        from allauth.socialaccount.models import SocialApp

        try:
            facebook_app = SocialApp.objects.get(provider="facebook")
            app_secret = facebook_app.secret
        except SocialApp.DoesNotExist:
            logger.error("Facebook app not configured in SocialApp")
            return None

        # Split the signed request
        parts = signed_request.split(".")
        if len(parts) != 2:
            return None

        encoded_sig, payload = parts

        # Decode the signature
        # Facebook uses URL-safe base64, add padding if needed
        encoded_sig += "=" * (4 - len(encoded_sig) % 4)
        sig = base64.urlsafe_b64decode(encoded_sig)

        # Decode the payload
        payload += "=" * (4 - len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))

        # Verify the signature
        expected_sig = hmac.new(
            app_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).digest()

        if not hmac.compare_digest(sig, expected_sig):
            logger.error("Facebook signed request signature mismatch")
            return None

        return data

    except Exception as e:
        logger.exception(f"Error parsing Facebook signed request: {str(e)}")
        return None


def _retire_event_checkouts_before_profile_deletion(user):
    """Close every event checkout before its registration can be erased.

    The deletion-in-progress ban is committed first. New member- or
    staff-opened event checkouts then fail their locked eligibility check,
    while an already-running creator is serialized through its event row and
    leaves either a durable claim or PaymentTransaction for this sweep to see.
    Provider I/O runs between short database phases; ambiguous deactivation
    aborts deletion rather than orphaning a possible late card capture.
    """

    from crush_lu.models.payments import (
        EventCheckoutCreationClaim,
        PaymentTransaction,
    )
    from crush_lu.models import MeetupEvent
    from crush_lu.models.profiles import UserDataConsent
    from crush_lu.services.sumup import SumUpClient

    with transaction.atomic():
        consent, _created = UserDataConsent.objects.select_for_update().get_or_create(
            user=user
        )
        consent.crushlu_consent_given = False
        consent.crushlu_consent_date = None
        consent.crushlu_banned = True
        consent.crushlu_ban_date = timezone.now()
        consent.crushlu_ban_reason = "deletion_in_progress"
        consent.save(
            update_fields=[
                "crushlu_consent_given",
                "crushlu_consent_date",
                "crushlu_banned",
                "crushlu_ban_date",
                "crushlu_ban_reason",
            ]
        )

    registration_rows = list(
        EventRegistration.objects.filter(user=user)
        .order_by("pk")
        .values_list("pk", "event_id")
    )
    registration_ids = [
        registration_id for registration_id, _event_id in registration_rows
    ]
    event_ids = sorted({event_id for _registration_id, event_id in registration_rows})

    # Pre-PR production rows may already have lost their registration through
    # an ordinary cascade. PENDING event checkouts intentionally have no event
    # attribution yet, so ownership is the only remaining way to find them.
    # Include those legacy orphans in the same provider-retirement proof rather
    # than letting profile erasure strand a still-payable widget.
    payment_scope = Q(event_registration_id__in=registration_ids) | Q(
        user_id=user.pk,
        status=PaymentTransaction.Status.PENDING,
    )

    with transaction.atomic():
        payments = list(
            PaymentTransaction.objects.select_for_update(of=("self",))
            .filter(
                payment_scope,
                purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            )
            .order_by("pk")
        )
        event_ids = sorted(
            {
                *event_ids,
                *(payment.event_id for payment in payments if payment.event_id),
            }
        )
        list(
            MeetupEvent.objects.select_for_update()
            .filter(pk__in=event_ids)
            .order_by("pk")
        )
        list(
            EventRegistration.objects.select_for_update()
            .filter(event_id__in=event_ids)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        claims = list(
            EventCheckoutCreationClaim.objects.select_for_update()
            .filter(
                Q(registration_id__in=registration_ids)
                | Q(registration_id_snapshot__in=registration_ids)
            )
            .order_by("pk")
        )
        payment_snapshot = {
            payment.pk: (
                payment.status,
                payment.provider,
                payment.sumup_checkout_id,
                payment.event_registration_id,
                payment.user_id,
                payment.event_id,
                str(payment.transaction_reference),
            )
            for payment in payments
        }
        claim_snapshot = {
            claim.pk: (
                claim.token,
                claim.provider_checkout_id,
                claim.state,
                claim.transaction_reference,
                claim.registration_id,
                claim.registration_id_snapshot,
                claim.event_id_snapshot,
            )
            for claim in claims
        }
        if any(
            not claim.provider_checkout_id
            and claim.state != EventCheckoutCreationClaim.State.RETIRED
            for claim in claims
        ):
            raise RuntimeError(
                "Account deletion paused because an event checkout is still "
                "being created and its provider outcome is not yet known. "
                "Retry after payment reconciliation."
            )
        if any(
            claim.state == EventCheckoutCreationClaim.State.RETIRING for claim in claims
        ):
            raise RuntimeError(
                "Account deletion paused because an event checkout is already "
                "being retired. Retry after payment reconciliation."
            )
        checkout_ids = {
            checkout_id
            for checkout_id in (
                *(
                    payment.sumup_checkout_id
                    for payment in payments
                    if payment.status == PaymentTransaction.Status.PENDING
                    if payment.provider == PaymentTransaction.Provider.SUMUP
                ),
                *(
                    claim.provider_checkout_id
                    for claim in claims
                    if claim.state == EventCheckoutCreationClaim.State.ACTIVE
                ),
            )
            if checkout_id
        }

    client = SumUpClient()
    deactivated = {
        checkout_id: client.ensure_checkout_not_payable(checkout_id)
        for checkout_id in sorted(checkout_ids)
    }
    failed_ids = [
        checkout_id for checkout_id, accepted in deactivated.items() if not accepted
    ]
    if failed_ids:
        raise RuntimeError(
            "Account deletion paused because an event card checkout could not "
            "be safely deactivated. Retry after payment reconciliation."
        )

    with transaction.atomic():
        current_payments = list(
            PaymentTransaction.objects.select_for_update(of=("self",))
            .filter(
                payment_scope,
                purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            )
            .order_by("pk")
        )
        list(
            MeetupEvent.objects.select_for_update()
            .filter(pk__in=event_ids)
            .order_by("pk")
        )
        list(
            EventRegistration.objects.select_for_update()
            .filter(event_id__in=event_ids)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        current_claims = list(
            EventCheckoutCreationClaim.objects.select_for_update()
            .filter(
                Q(registration_id__in=registration_ids)
                | Q(registration_id_snapshot__in=registration_ids)
            )
            .order_by("pk")
        )

        current_payment_snapshot = {
            payment.pk: (
                payment.status,
                payment.provider,
                payment.sumup_checkout_id,
                payment.event_registration_id,
                payment.user_id,
                payment.event_id,
                str(payment.transaction_reference),
            )
            for payment in current_payments
        }
        current_claim_snapshot = {
            claim.pk: (
                claim.token,
                claim.provider_checkout_id,
                claim.state,
                claim.transaction_reference,
                claim.registration_id,
                claim.registration_id_snapshot,
                claim.event_id_snapshot,
            )
            for claim in current_claims
        }
        if (
            current_payment_snapshot != payment_snapshot
            or current_claim_snapshot != claim_snapshot
        ):
            raise RuntimeError(
                "Account deletion paused because event payment state changed "
                "during checkout retirement. Retry after reconciliation."
            )

        for payment in current_payments:
            if payment.status != PaymentTransaction.Status.PENDING:
                continue
            payment.status = PaymentTransaction.Status.CANCELLED
            payment.failure_reason = (
                "Checkout retired before Crush.lu profile deletion; the card "
                "checkout was deactivated before registration erasure."
            )
            payment.save(update_fields=["status", "failure_reason", "updated_at"])
        for claim in current_claims:
            # Keep a durable tombstone for any creator that was already past
            # provider I/O and waiting on our event lock.  It must learn that
            # this exact remote checkout was proven closed, rather than
            # publishing a new PENDING row after the retirement sweep.
            claim.state = EventCheckoutCreationClaim.State.RETIRED
            claim.claimed_at = timezone.now()
            claim.save(update_fields=["state", "claimed_at"])


def delete_crushlu_profile_only(user):
    """
    Delete ONLY Crush.lu profile data, keeping PowerUp account intact.

    Deletes:
    - CrushProfile
    - Profile photos (Azure Blob)
    - Event registrations
    - Connections and messages
    - Journey progress
    - Crush.lu consent flag

    KEEPS:
    - Django User record
    - EmailAddress records
    - SocialAccount/SocialToken records
    - PowerUp consent flag

    Sets permanent ban preventing future Crush.lu profile creation.
    """
    from crush_lu.storage import delete_user_storage

    logger.info(f"Starting Crush.lu profile deletion for user {user.id}")

    # First make every event checkout non-payable and retire provider state.
    # Nothing personal is erased until this succeeds: a late SumUp capture
    # must always retain its registration long enough to receive a seat or the
    # automatic full-value group remedy.
    _retire_event_checkouts_before_profile_deletion(user)

    # Fetch from the database instead of the reverse OneToOne cache.  A retry
    # can reuse the same in-memory User after an earlier attempt already
    # deleted the profile, leaving ``user.crushprofile`` cached and stale.
    profile = CrushProfile.objects.filter(user=user).first()

    # Delete profile photos from Azure Blob
    if profile is not None:

        # Delete profile photos from storage
        for photo_field in ["photo_1", "photo_2", "photo_3"]:
            photo = getattr(profile, photo_field, None)
            if photo:
                try:
                    photo.delete(save=False)
                except Exception as e:
                    logger.warning(f"Could not delete {photo_field}: {e}")

        # Delete the profile (cascades to related data via Django's on_delete)
        profile.delete()
        logger.info(f"Deleted CrushProfile for user {user.id}")

    # Clean up blob storage folder (users/{user_id}/)
    success, deleted_count = delete_user_storage(user.id)
    if success and deleted_count > 0:
        logger.info(f"Deleted {deleted_count} blob(s) from storage for user {user.id}")

    # Delete ProfileSubmissions (in case profile was deleted manually)
    ProfileSubmission.objects.filter(profile__user=user).delete()

    # Close out spendable-only Crush Credit BEFORE the registrations go.
    #
    # Credit hangs off User, not CrushProfile, so nothing here cascades it: a
    # member with a balance would leave it sitting `active` in the ledger on an
    # account that is now permanently banned from creating a Crush profile and
    # can never reach an event checkout to spend it. That is worse than either
    # honest outcome — the ledger would keep reporting a live liability that
    # cannot be discharged, and any "outstanding credit" figure read off it
    # would be wrong.
    #
    # Voided rather than deleted, with the reason on the row: this is an
    # append-only ledger, and "the holder deleted their account" is exactly the
    # kind of thing it exists to still be able to answer a year later.
    #
    # One class must survive: an unexpired, wholly unspent organiser-
    # cancellation award still represents the member's unclaimed cash-refund
    # right. Profile deletion is not a waiver of that cash remedy. Keep those
    # rows active in the staff refund queue; the retained/anonymised User and
    # immutable payment record preserve the financial liability without
    # keeping the deleted Crush profile.
    #
    # Ordered before the registration delete only for tidiness of the log line;
    # CreditRedemption is SET_NULL on registration, so the spend history
    # survives either way.
    from crush_lu.models.credits import CrushCredit
    from crush_lu.services.credits import void_credit

    # A replacement can still carry a contingent 50% obligation to this user.
    # Account deletion is an explicit withdrawal from future Crush.lu value:
    # clear those claims before inspecting active credits so a concurrent
    # replacement capture either settles first (and is voided below) or sees
    # no beneficiary after this short row lock commits.
    with transaction.atomic():
        pending_replacement_ids = list(
            EventRegistration.objects.select_for_update()
            .filter(resale_beneficiary=user)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        if pending_replacement_ids:
            EventRegistration.objects.filter(pk__in=pending_replacement_ids).update(
                resale_source_registration=None,
                resale_source_payment=None,
                resale_beneficiary=None,
            )
    if pending_replacement_ids:
        logger.info(
            "Withdrew %s pending resale claim(s) for deleted user %s",
            len(pending_replacement_ids),
            user.id,
        )

    active_credits = CrushCredit.objects.filter(
        user=user, status=CrushCredit.Status.ACTIVE
    )
    preserved_refund_ids = list(
        active_credits.filter(
            cash_refund_eligible=True,
            expires_at__gt=timezone.now(),
            redemptions__isnull=True,
        ).values_list("pk", flat=True)
    )
    forfeited = active_credits.exclude(pk__in=preserved_refund_ids)
    forfeited_count = forfeited.count()
    if forfeited_count:
        for credit in forfeited:
            void_credit(
                credit.pk,
                note=(
                    "— voided on Crush.lu account deletion "
                    f"({timezone.now():%Y-%m-%d})."
                ),
                require_unspent=False,
            )
        logger.info(
            "Voided %s active Crush Credit row(s) for deleted user %s",
            forfeited_count,
            user.id,
        )
    if preserved_refund_ids:
        logger.info(
            "Preserved %s unclaimed cash-refund right(s) for deleted user %s",
            len(preserved_refund_ids),
            user.id,
        )

    # Delete EventRegistrations
    EventRegistration.objects.filter(user=user).delete()

    # Delete ConnectionMessages
    ConnectionMessage.objects.filter(
        Q(sender=user) | Q(connection__requester=user) | Q(connection__recipient=user)
    ).delete()

    # Delete EventConnections (both as requester and recipient)
    EventConnection.objects.filter(Q(requester=user) | Q(recipient=user)).delete()

    # Delete CoachSessions
    CoachSession.objects.filter(user=user).delete()

    # Clear Crush.lu consent and set permanent ban
    if hasattr(user, "data_consent"):
        consent = user.data_consent
        consent.crushlu_consent_given = False
        consent.crushlu_consent_date = None
        consent.crushlu_consent_ip = None
        consent.crushlu_banned = True
        consent.crushlu_ban_date = timezone.now()
        consent.crushlu_ban_reason = "user_deletion"
        consent.save()
        logger.info(f"Set permanent Crush.lu ban for user {user.id}")

    logger.info(f"Crush.lu profile deleted for user {user.id} (PowerUp account kept)")


def delete_full_account(user):
    """
    Delete ENTIRE PowerUp account including User model and all platform data.

    Deletes:
    - ALL Crush.lu data (via delete_crushlu_profile_only)
    - Django User record (anonymized, not deleted)
    - EmailAddress records
    - SocialAccount/SocialToken records
    - All photos across all platforms

    Anonymizes:
    - Email → deleted_{user_id}@deleted.crush.lu
    - Username → deleted_user_{user_id}
    - First/last names cleared
    - Password set to unusable
    - is_active = False
    """
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount, SocialToken

    logger.info(f"Starting full account deletion for user {user.id}")

    # First delete Crush.lu profile
    delete_crushlu_profile_only(user)

    # Anonymize User record (instead of deleting to preserve referential integrity)
    user.email = f"deleted_{user.id}@deleted.crush.lu"
    user.username = f"deleted_user_{user.id}"
    user.first_name = ""
    user.last_name = ""
    user.set_unusable_password()
    user.is_active = False
    user.save()

    # Delete email addresses (allauth)
    EmailAddress.objects.filter(user=user).delete()

    # Delete social accounts and tokens (allauth)
    SocialToken.objects.filter(account__user=user).delete()
    SocialAccount.objects.filter(user=user).delete()

    logger.info(f"Full account deleted for user {user.id} (all platforms)")


def data_deletion_status(request):
    """
    Page where users can check the status of their data deletion request.
    """
    confirmation_code = request.GET.get("code", "")
    return render(
        request,
        "crush_lu/data_deletion_status.html",
        {"confirmation_code": confirmation_code},
    )


# LuxID connect-URL resolution lives in crush_lu.luxid so the Crush Connect
# teaser can share it. Re-exported here under the original private name for the
# account drill-down (views.py).
from crush_lu.luxid import luxid_connect_url as _luxid_connect_url  # noqa: F401


@login_required
def dev_simulate_luxid_connect(request):
    """Dev-only: simulate LuxID OAuth callback and verify a pending profile.

    No ProfileSubmission required — mirrors the new free-path flow where
    LuxId verification is direct and independent of coach review.
    Only reachable when DEBUG=True.
    """
    from django.http import Http404

    if not settings.DEBUG:
        raise Http404

    from allauth.socialaccount.models import SocialAccount
    from crush_lu.models import CrushProfile
    from crush_lu.models.profiles import ProfileSubmission
    from crush_lu.signals import _execute_luxid_direct_verify

    user = request.user

    try:
        profile = CrushProfile.objects.get(user=user)
    except CrushProfile.DoesNotExist:
        messages.warning(request, "No CrushProfile found for your account.")
        return redirect("crush_lu:profile_submitted")

    if profile.verification_status == "verified":
        messages.info(request, "[Dev] Profile is already verified.")
        return redirect("crush_lu:dashboard")

    if profile.verification_status == "incomplete":
        messages.warning(
            request, "[Dev] Profile is incomplete — finish the wizard first."
        )
        return redirect("crush_lu:create_profile")

    # Create a stub SocialAccount so the user appears LuxId-linked
    SocialAccount.objects.get_or_create(
        user=user,
        provider="openid_connect",
        defaults={"uid": f"dev-luxid-{user.pk}"},
    )

    # Use any pending submission opportunistically (revision / paid-coach path)
    submission = (
        ProfileSubmission.objects.filter(profile=profile, status="pending")
        .order_by("-submitted_at")
        .first()
    )

    _execute_luxid_direct_verify(user, profile, submission, request)

    return redirect("crush_lu:dashboard")


def account_settings_url(sub="", anchor=""):
    """Path of the account drill-down that replaced /account/settings/ (8-08).

    ``sub`` picks a sub-section (settings, notifications, danger); ``anchor``
    is appended as a #fragment.
    """
    url = reverse("crush_lu:edit_profile") + "?section=account"
    if sub:
        url += f"&sub={sub}"
    if anchor:
        url += f"#{anchor}"
    return url


@crush_login_required
def legacy_account_settings(request):
    """Retired /account/settings/ monolith: 301 to the account drill-down.

    Old links, bookmarks and already-sent emails keep working. A #fragment
    never reaches the server; the browser re-applies it after the redirect and
    the drill-down's ``legacySettingsAnchor`` component maps it to ``sub=``.
    ``?apple_link=1`` (Apple relay banner) carries over to the settings card.
    """
    if request.GET.get("apple_link") == "1":
        return HttpResponsePermanentRedirect(
            account_settings_url("settings") + "&apple_link=1"
        )
    return HttpResponsePermanentRedirect(account_settings_url())


def _whatsapp_preference_redirect(request):
    """Send the member back to the WhatsApp card in the account drill-down."""
    return redirect(account_settings_url("notifications", "whatsapp-notifications"))


@login_required
@require_http_methods(["POST"])
def update_whatsapp_preference(request):
    """Update only the WhatsApp opt-in preference without touching email settings."""
    from .models import EmailPreference

    wants_opt_in = "whatsapp_opt_in" in request.POST

    # Server-side guard: opt-in requires a verified phone number regardless of
    # what the UI shows — the form toggle is hidden for unverified users but
    # a crafted POST must not create inconsistent state.
    if wants_opt_in:
        try:
            profile = request.user.crushprofile
            has_verified_phone = bool(profile.phone_number and profile.phone_verified)
        except Exception:
            has_verified_phone = False

        if not has_verified_phone:
            messages.error(
                request,
                _(
                    "A verified phone number is required to enable WhatsApp notifications."
                ),
            )
            return _whatsapp_preference_redirect(request)

    email_prefs = EmailPreference.get_or_create_for_user(request.user)
    email_prefs.whatsapp_opt_in = wants_opt_in
    email_prefs.save(update_fields=["whatsapp_opt_in"])

    messages.success(request, _("WhatsApp notification preference updated."))
    return _whatsapp_preference_redirect(request)


@login_required
@require_http_methods(["POST"])
def api_update_email_preference(request):
    """
    JSON API endpoint for updating a single email preference toggle.
    Called from Alpine.js emailPreferences component via fetch(), which sends
    the X-CSRFToken header — standard Django CSRF protection applies (SEC-10).
    """
    from .models import EmailPreference

    VALID_KEYS = {
        "unsubscribed_all",
        "email_profile_updates",
        "email_event_reminders",
        "email_new_connections",
        "email_new_messages",
        "email_marketing",
        "whatsapp_opt_in",
    }

    try:
        data = json.loads(request.body)
        key = data.get("key", "")
        value = bool(data.get("value", False))
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    if key not in VALID_KEYS:
        return JsonResponse(
            {"success": False, "error": "Invalid preference key"}, status=400
        )

    if key == "whatsapp_opt_in" and value:
        try:
            profile = request.user.crushprofile
            has_verified_phone = bool(profile.phone_number and profile.phone_verified)
        except Exception:
            has_verified_phone = False
        if not has_verified_phone:
            return JsonResponse(
                {"success": False, "error": "Verified phone number required"},
                status=400,
            )

    email_prefs = EmailPreference.get_or_create_for_user(request.user)
    setattr(email_prefs, key, value)
    email_prefs.save(update_fields=[key])

    return JsonResponse({"success": True})


def email_unsubscribe(request, token):
    """
    One-click unsubscribe view.
    Accessible without login - uses secure token for authentication.

    GET: Show unsubscribe confirmation page
    POST: Process unsubscribe action
    """
    from .models import EmailPreference

    try:
        email_prefs = EmailPreference.objects.get(unsubscribe_token=token)
    except EmailPreference.DoesNotExist:
        messages.error(
            request,
            _("Invalid unsubscribe link. Please check your email or contact support."),
        )
        return render(
            request,
            "crush_lu/email_unsubscribe.html",
            {
                "error": True,
                "token": token,
            },
        )

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "unsubscribe_all":
            # Unsubscribe from ALL emails
            email_prefs.unsubscribed_all = True
            email_prefs.save()
            messages.success(
                request, _("You have been unsubscribed from all Crush.lu emails.")
            )

        elif action == "unsubscribe_marketing":
            # Only unsubscribe from marketing emails
            email_prefs.email_marketing = False
            email_prefs.save()
            messages.success(
                request, _("You have been unsubscribed from marketing emails.")
            )

        elif action == "resubscribe":
            # Re-enable all emails
            email_prefs.unsubscribed_all = False
            email_prefs.email_profile_updates = True
            email_prefs.email_event_reminders = True
            email_prefs.email_new_connections = True
            email_prefs.email_new_messages = True
            email_prefs.save()
            messages.success(
                request, _("You have been re-subscribed to Crush.lu emails.")
            )

        return render(
            request,
            "crush_lu/email_unsubscribe.html",
            {
                "success": True,
                "email_prefs": email_prefs,
                "token": token,
            },
        )

    # GET request - show unsubscribe form
    return render(
        request,
        "crush_lu/email_unsubscribe.html",
        {
            "email_prefs": email_prefs,
            "token": token,
            "user": email_prefs.user,
        },
    )


@crush_login_required
@require_http_methods(["GET", "POST"])
def set_password(request):
    """
    Allow Facebook-registered users to set a password for email/password login.

    Only available to users who:
    1. Are logged in via social account (Facebook)
    2. Don't have a usable password set

    This enables dual login (Facebook OR email+password).
    """
    from django.contrib.auth import update_session_auth_hash
    from .forms import CrushSetPasswordForm

    # Check if user has social account
    has_social = request.user.socialaccount_set.exists()
    has_password = request.user.has_usable_password()

    # Only allow if user has social account but no password
    if not has_social:
        messages.info(
            request, _("This feature is only for users who signed up with Facebook.")
        )
        return redirect(account_settings_url("settings"))

    if has_password:
        messages.info(
            request,
            _('You already have a password set. Use "Change Password" to update it.'),
        )
        return redirect(account_settings_url("settings"))

    if request.method == "POST":
        form = CrushSetPasswordForm(request.user, request.POST)
        if form.is_valid():
            form.save()

            # Keep user logged in after password change
            update_session_auth_hash(request, request.user)

            messages.success(
                request,
                "Password set successfully! You can now log in with your email and password.",
            )
            return redirect(account_settings_url("settings"))
    else:
        form = CrushSetPasswordForm(request.user)

    return render(
        request,
        "crush_lu/set_password.html",
        {
            "form": form,
            "social_accounts": request.user.socialaccount_set.all(),
        },
    )


@crush_login_required
@require_http_methods(["POST"])
def disconnect_social_account(request, social_account_id):
    """
    Disconnect a linked social account.

    Security checks:
    - User must have at least one other login method (password OR another social account)
    - Only the account owner can disconnect their social accounts
    """
    from allauth.socialaccount.models import SocialAccount

    try:
        social_account = SocialAccount.objects.get(
            id=social_account_id, user=request.user
        )
    except SocialAccount.DoesNotExist:
        messages.error(request, _("Social account not found."))
        return redirect(account_settings_url("settings"))

    # Security check: ensure user has another login method
    other_social_accounts = request.user.socialaccount_set.exclude(
        id=social_account_id
    ).count()
    has_password = request.user.has_usable_password()

    if not has_password and other_social_accounts == 0:
        messages.error(
            request,
            f"Cannot disconnect {social_account.provider.title()} - you need at least one login method. "
            "Set a password first or connect another social account.",
        )
        return redirect(account_settings_url("settings"))

    # Log the disconnection
    provider_name = social_account.provider.title()
    logger.info(f"User {request.user.id} disconnected {provider_name} account")

    # Delete the social account
    social_account.delete()

    messages.success(
        request,
        _("%(provider_name)s account has been disconnected.")
        % {"provider_name": provider_name},
    )
    return redirect(account_settings_url("settings"))


@crush_login_required
def apple_relay_link_prompt(request):
    """
    One-time prompt for Apple "Hide My Email" users to link an existing account.

    Shown after a new signup with an Apple relay email. Offers two options:
    1. Link existing account (log out and log in with other method)
    2. Continue as new user
    """
    # Clear the session flag so it doesn't show again
    request.session.pop("apple_relay_needs_linking", None)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "link_existing":
            # Log out and redirect to login with ?next pointing to account settings
            logout(request)
            login_url = reverse("crush_lu:login")
            settings_url = account_settings_url("settings") + "&apple_link=1"
            return redirect(f"{login_url}?{urlencode({'next': settings_url})}")
        else:
            # Continue as new user
            has_profile = hasattr(request.user, "crushprofile")
            if has_profile:
                return redirect("crush_lu:dashboard")
            return redirect("crush_lu:create_profile")

    return render(request, "crush_lu/apple_link_prompt.html")


@crush_login_required
@require_http_methods(["GET", "POST"])
def delete_crushlu_profile_view(request):
    """
    Simplified view for deleting Crush.lu profile only (default action).
    Permanent deletion - user cannot rejoin Crush.lu with this account.
    """
    from crush_lu.forms_account import DeletionEmailConfirmForm
    from crush_lu.models.profiles import UserDataConsent

    profile = CrushProfile.objects.filter(user=request.user).first()
    deletion_retry_in_progress = (
        profile is None
        and UserDataConsent.objects.filter(
            user=request.user,
            crushlu_banned=True,
            crushlu_ban_reason="deletion_in_progress",
        ).exists()
    )

    # A partially completed deletion may already have removed CrushProfile.
    # Only the transient, payment-blocking deletion state may resume without
    # one; ordinary profile-less accounts keep the existing safe redirect.
    if profile is None and not deletion_retry_in_progress:
        messages.info(request, _("You do not have a Crush.lu profile to delete."))
        return redirect(account_settings_url("danger"))

    if request.method == "POST":
        confirm_email = request.POST.get("confirm_email", "").strip()

        if confirm_email != request.user.email:
            messages.error(request, _("Email confirmation does not match"))
            return redirect("crush_lu:delete_crushlu_profile")

        # Delete Crush.lu profile (sets permanent ban)
        try:
            delete_crushlu_profile_only(request.user)
            messages.success(
                request,
                _(
                    "Your Crush.lu profile has been permanently deleted. You cannot create a new profile with this account."
                ),
            )
            return redirect(account_settings_url())
        except Exception as e:
            logger.exception(
                f"Error deleting Crush.lu profile for user {request.user.id}: {e}"
            )
            messages.error(
                request,
                _("An error occurred while deleting your profile. Please try again."),
            )
            return redirect("crush_lu:delete_crushlu_profile")

    context = {
        "profile": profile,
        "confirm_form": DeletionEmailConfirmForm(),
    }
    return render(request, "crush_lu/delete_crushlu_profile_confirm.html", context)


@crush_login_required
@require_http_methods(["GET", "POST"])
def take_a_break_view(request):
    """Self-service, reversible pause (UX Wave 3 · WP13).

    GET shows a confirm step (Danger Zone entry point); POST applies it via
    CrushProfile.take_a_break(), which also mirrors the pause onto Crush
    Connect matching. Existing event registrations are left untouched.
    """
    profile = CrushProfile.objects.filter(user=request.user).first()
    if profile is None:
        messages.info(request, _("You do not have a Crush.lu profile to pause."))
        return redirect(account_settings_url("danger"))

    if profile.is_on_break:
        messages.info(request, _("You're already taking a break."))
        return redirect(account_settings_url("danger"))

    if request.method == "POST":
        profile.take_a_break()
        messages.success(
            request,
            _(
                "You're taking a break. You're hidden from events, Connect "
                "matching and marketing emails until you resume."
            ),
        )
        return redirect("crush_lu:dashboard")

    return render(request, "crush_lu/take_a_break_confirm.html", {"profile": profile})


@crush_login_required
@require_http_methods(["POST"])
def resume_from_break_view(request):
    """Undo `take_a_break()` from the dashboard banner button."""
    profile = CrushProfile.objects.filter(user=request.user).first()
    if profile is not None and profile.is_on_break:
        profile.resume_from_break()
        messages.success(request, _("Welcome back! Your profile is visible again."))
    return redirect("crush_lu:dashboard")


@crush_login_required
@require_http_methods(["GET", "POST"])
def gdpr_data_management(request):
    """
    GDPR data management dashboard.
    Shows two deletion options:
    1. Delete Crush.lu profile only (keeps the shared login account)
    2. Delete the entire login account (erases everything)
    """
    from crush_lu.forms_account import DeletionEmailConfirmForm
    from crush_lu.models.profiles import UserDataConsent

    consent, created = UserDataConsent.objects.get_or_create(
        user=request.user,
        defaults={
            "powerup_consent_given": True,
            "powerup_consent_date": timezone.now(),
        },
    )

    if request.method == "POST":
        deletion_type = request.POST.get("deletion_type")
        confirm_email = request.POST.get("confirm_email", "").strip()

        if confirm_email != request.user.email:
            messages.error(request, _("Email confirmation does not match"))
            return redirect("crush_lu:gdpr_data_management")

        if deletion_type == "crushlu_only":
            # Delete Crush.lu profile only
            try:
                delete_crushlu_profile_only(request.user)
                messages.success(
                    request,
                    _(
                        "Your Crush.lu profile has been deleted. Your login account remains active."
                    ),
                )
                return redirect(account_settings_url())
            except Exception as e:
                logger.exception(
                    f"Error deleting Crush.lu profile for user {request.user.id}: {e}"
                )
                messages.error(request, _("An error occurred. Please try again."))
                return redirect("crush_lu:gdpr_data_management")

        elif deletion_type == "full_account":
            # Delete entire PowerUp account
            try:
                delete_full_account(request.user)
                logout(request)
                messages.success(
                    request,
                    _("Your account and all your data have been permanently deleted."),
                )
                return redirect("crush_lu:home")
            except Exception as e:
                logger.exception(
                    f"Error deleting full account for user {request.user.id}: {e}"
                )
                messages.error(request, _("An error occurred. Please try again."))
                return redirect("crush_lu:gdpr_data_management")

    context = {
        "consent": consent,
        "has_crushlu_profile": hasattr(request.user, "crushprofile"),
        "profile_confirm_form": DeletionEmailConfirmForm(auto_id="id_profile_%s"),
        "account_confirm_form": DeletionEmailConfirmForm(auto_id="id_account_%s"),
    }
    return render(request, "crush_lu/gdpr_data_management.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def consent_confirm(request):
    """
    Consent confirmation page for users who signed up before consent system.

    This page is shown to authenticated users who don't have Crush.lu consent.
    Typically this only applies to users who signed up before the consent tracking
    was implemented.
    """
    from crush_lu.models.profiles import UserDataConsent
    from crush_lu.oauth_statekit import get_client_ip

    # Block banned users from re-consenting
    if (
        hasattr(request.user, "data_consent")
        and request.user.data_consent.crushlu_banned
    ):
        return redirect("crush_lu:account_banned")

    # Check if user already has consent (shouldn't happen, but be safe)
    if (
        hasattr(request.user, "data_consent")
        and request.user.data_consent.crushlu_consent_given
    ):
        messages.info(request, _("You have already given consent."))
        return redirect("crush_lu:dashboard")

    if request.method == "POST":
        # Get consent checkboxes
        crushlu_consent = request.POST.get("crushlu_consent") == "on"
        marketing_consent = request.POST.get("marketing_consent") == "on"

        if not crushlu_consent:
            messages.error(request, _("You must consent to continue using Crush.lu."))
            return redirect("crush_lu:consent_confirm")

        # Update or create consent record
        consent, created = UserDataConsent.objects.get_or_create(user=request.user)
        consent.crushlu_consent_given = True
        consent.crushlu_consent_date = timezone.now()
        consent.crushlu_consent_ip = get_client_ip(request)
        consent.marketing_consent = marketing_consent
        consent.marketing_consent_date = timezone.now() if marketing_consent else None
        consent.save()

        logger.info(f"User {request.user.id} retroactively gave Crush.lu consent")
        messages.success(request, _("Thank you for confirming your consent!"))
        return redirect("crush_lu:dashboard")

    # GET request - show consent form
    context = {}
    return render(request, "crush_lu/consent_confirm.html", context)


@login_required
def account_banned(request):
    """
    Info page shown to banned users explaining their account status.
    """
    reason = None
    ban_date = None

    if hasattr(request.user, "data_consent"):
        consent = request.user.data_consent
        if not consent.crushlu_banned:
            return redirect("crush_lu:dashboard")
        reason = consent.crushlu_ban_reason
        ban_date = consent.crushlu_ban_date
    else:
        # No consent record means not banned - redirect
        return redirect("crush_lu:dashboard")

    reason_display = {
        "user_deletion": _("You deleted your Crush.lu profile."),
        "admin_action": _("Your account was suspended by an administrator."),
        "terms_violation": _(
            "Your account was suspended due to a terms of service violation."
        ),
    }

    context = {
        "ban_reason": reason_display.get(reason, _("Your account has been suspended.")),
        "ban_date": ban_date,
    }
    return render(request, "crush_lu/account_banned.html", context)


# Onboarding
@require_GET
def referral_redirect(request, code):
    """
    Referral landing route. GET-only.

    Stores referral attribution and redirects to signup with code preserved.
    Restricted to GET so that messenger link-unfurlers and scanners sending POST
    requests get an immediate 405 instead of tripping the CSRF middleware and
    producing ERROR-level log noise.
    """
    referral = capture_referral(request, code, source="link")
    # A shared event link (build_referral_url(next_url=...), 4-18) lands on
    # the event; the captured code still credits a later signup.
    # Only a valid code earns the event landing; an unknown/inactive one keeps
    # the plain signup redirect.
    next_url = request.GET.get("next")
    if (
        referral
        and next_url
        and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return redirect(next_url)
    signup_url = reverse("crush_lu:signup")
    if referral:
        return redirect(f"{signup_url}?ref={referral.code}")
    return redirect(signup_url)


# Outermost, so the passwords are masked in error reports even on the
# throttled branch below, which renders a full template.
@sensitive_post_parameters("password1", "password2")
@ensure_csrf_cookie
# UX Wave 3 · WP3 (finding 2-04): block=False so a rate-limited POST still
# reaches this view instead of the decorator's bare 429 text page - it's
# re-rendered inline below, on the same auth.html the user was already on.
@ratelimit(key="ip", rate="5/h", method="POST", block=False)
def signup(request):
    """
    User registration with Allauth integration
    Supports both manual signup and social login (LinkedIn, Google, etc.)
    Uses unified auth template with login/signup tabs
    """
    from allauth.account.forms import LoginForm

    signup_form = CrushSignupForm()
    login_form = LoginForm()
    status_code = 200
    limited = request.method == "POST" and getattr(request, "limited", False)

    # UX Wave 3 · WP3 review (P2): a throttled POST must not reach
    # capture_referral_from_request()'s get_or_create() - block=False lets
    # every request past the limit still enter this view, so a client
    # cycling fresh sessions with a valid `?ref=` could keep writing
    # ReferralAttribution rows after its IP was throttled, defeating the
    # point of the rate limit for this write path. GET requests (the
    # initial landing with `?ref=`) are never rate-limited, so they still
    # capture normally.
    if not limited:
        capture_referral_from_request(request)

    if limited:
        signup_form = CrushSignupForm(request.POST)
        add_rate_limited_error(
            signup_form,
            _("Too many signup attempts. Please try again in %(wait)s.")
            % {
                "wait": humanize_wait_seconds(
                    getattr(request, "limited_retry_after", 3600)
                )
            },
        )
        status_code = 429
    elif request.method == "POST":
        signup_form = CrushSignupForm(request.POST)
        if signup_form.is_valid():
            try:
                # Allauth's save() method handles EmailAddress creation automatically
                # This will raise IntegrityError if email/username already exists
                user = signup_form.save(request)

                messages.success(
                    request,
                    _("Account created! Check your email and complete your profile."),
                )

                # Hand off to allauth's complete_signup so ACCOUNT_EMAIL_VERIFICATION
                # is honored: in "mandatory" mode allauth renders the verification-sent
                # page and does NOT log the user in until they confirm their email.
                from allauth.account import app_settings as allauth_account_settings
                from allauth.account.utils import complete_signup

                pending_gift_code = request.session.get("pending_gift_code")
                if pending_gift_code:
                    success_url = reverse(
                        "crush_lu:gift_claim", kwargs={"gift_code": pending_gift_code}
                    )
                else:
                    success_url = reverse("crush_lu:onboarding_entry")

                return complete_signup(
                    request,
                    user,
                    allauth_account_settings.EMAIL_VERIFICATION,
                    success_url,
                )

            except Exception as e:
                # Handle duplicate email/username errors
                logger.error(f"❌ Signup failed for email: {e}", exc_info=True)

                # Check if it's a duplicate email error
                error_msg = str(e).lower()
                if (
                    "unique" in error_msg
                    or "duplicate" in error_msg
                    or "already exists" in error_msg
                ):
                    messages.error(
                        request,
                        _(
                            "An account with this email already exists. "
                            "Please login or use a different email."
                        ),
                    )
                else:
                    messages.error(
                        request,
                        _(
                            "An error occurred while creating your account. "
                            "Please try again."
                        ),
                    )

    context = {
        "signup_form": signup_form,
        "login_form": login_form,
        "mode": "signup",
    }
    response = render(request, "crush_lu/auth.html", context, status=status_code)
    if status_code == 429:
        response["Retry-After"] = str(getattr(request, "limited_retry_after", 3600))
    return response


# Match allauth's per-address confirm_email limiter (the send goes through
# it), so the button never re-enables while a retry would silently not send.
RESEND_VERIFICATION_COOLDOWN_SECONDS = getattr(
    settings, "ACCOUNT_EMAIL_CONFIRMATION_COOLDOWN", 3 * 60
)


def _email_digest(email):
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def claim_resend_cooldown(email, force=False):
    """Claim the resend cooldown for ``email`` (#1059).

    ``cache.add`` is atomic, so of two overlapping POSTs (a double-click
    during the synchronous send) only one gets to send. Keyed on the
    normalised address, not the session: a corrected address is not held
    back by the typo's cooldown, and rotating cookies does not reset it.
    ``force`` records a send that already happened (the social pre_login
    hold). ``None`` means the cache backend swallowed an error: fail open
    like ``_may_ask_sumup``; the 3/h IP limit and allauth's per-address
    limiter still bound the sends. The claim is taken before the address
    is looked up (so the response cannot reveal whether it exists): anyone
    can therefore hold back a resend to an address for one cooldown by
    posting it first, a small cost bounded by the 3/h IP limit.
    """
    key = f"crush:resend-verification:{_email_digest(email)}"
    if force:
        cache.set(key, 1, RESEND_VERIFICATION_COOLDOWN_SECONDS)
        return True
    return cache.add(key, 1, RESEND_VERIFICATION_COOLDOWN_SECONDS) is not False


def start_resend_cooldown_display(request, email):
    """Session copy of the cooldown, only for the page's countdown."""
    request.session["resend_verification_cooldown_until"] = (
        int(timezone.now().timestamp()) + RESEND_VERIFICATION_COOLDOWN_SECONDS
    )
    request.session["resend_verification_cooldown_hash"] = _email_digest(email)


# How long the social pre_login hold may rewrite its account's address. The
# session itself lives for weeks; a shared device must not inherit this.
SOCIAL_ADDRESS_REWRITE_WINDOW_SECONDS = getattr(
    settings, "CRUSH_SOCIAL_ADDRESS_REWRITE_WINDOW_SECONDS", 30 * 60
)


def _drop_social_hold(request):
    request.session.pop("pending_verification_user_id", None)
    request.session.pop("pending_verification_user_id_at", None)


def _held_social_user_id(request):
    """The social pre_login hold's user id, only while it still owns the
    session's pending address (an unverified row of that user). A stale id
    (another signup or login in the same browser since) is dropped, so it
    can never rewrite a different account than the one being verified.
    So is one older than SOCIAL_ADDRESS_REWRITE_WINDOW_SECONDS, or without
    an issued-at (a hold that was already used is cleared)."""
    from allauth.account.models import EmailAddress

    user_id = request.session.get("pending_verification_user_id")
    if not user_id:
        return None
    issued_at = request.session.get("pending_verification_user_id_at")
    age = timezone.now().timestamp() - (issued_at or 0)
    if not issued_at or not 0 <= age <= SOCIAL_ADDRESS_REWRITE_WINDOW_SECONDS:
        _drop_social_hold(request)
        return None
    email = request.session.get("pending_verification_email") or ""
    if EmailAddress.objects.filter(
        user_id=user_id, email__iexact=email, verified=False
    ).exists():
        return user_id
    _drop_social_hold(request)
    return None


def _replace_pending_social_address(request, typed_email):
    """Swap a held social account's unverified address for ``typed_email``.

    Only for the account the social pre_login hold stashed in this session
    (the visitor just proved the provider login), and only while it has no
    verified address. The old row is deleted rather than edited: HMAC
    confirmation keys sign the row's pk, so an edited row would let the
    link already mailed to the typo confirm the corrected address. An
    address another account uses is skipped silently, so the caller's
    response stays identical (ACCOUNT_UNIQUE_EMAIL, no enumeration).
    """
    from allauth.account.models import EmailAddress
    from django.contrib.auth.models import User
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    user_id = _held_social_user_id(request)
    if not user_id:
        return None
    # Stored lower-case like the signup form (forms.py): allauth lowercases
    # login and reset input and then matches exactly.
    typed_email = typed_email.strip().lower()
    try:
        validate_email(typed_email)
    except ValidationError:
        return None
    # validate_email allows 320 characters; the columns are varchar(254), so
    # a longer address would raise DataError (a 500) on Postgres at the save.
    if len(typed_email) > min(
        User._meta.get_field("email").max_length,
        EmailAddress._meta.get_field("email").max_length,
    ):
        return None
    with transaction.atomic():
        user = User.objects.select_for_update().filter(pk=user_id).first()
        if (
            user is None
            or EmailAddress.objects.filter(user=user, verified=True).exists()
        ):
            return None
        pending = EmailAddress.objects.filter(user=user, verified=False)
        if pending.filter(email__iexact=typed_email).exists():
            return None
        taken = (
            EmailAddress.objects.filter(email__iexact=typed_email)
            .exclude(user=user)
            .exists()
            or User.objects.filter(email__iexact=typed_email)
            .exclude(pk=user.pk)
            .exists()
        )
        if taken:
            return None
        pending.delete()
        user.email = typed_email
        user.save(update_fields=["email"])
        address = EmailAddress.objects.create(
            user=user, email=typed_email, primary=True, verified=False
        )
    # One rewrite per provider login: the authorization is spent.
    _drop_social_hold(request)
    return address


@require_http_methods(["POST"])
@ratelimit(
    key="ip",
    rate="3/h",
    method="POST",
    rate_limited_template="crush_lu/rate_limited.html",
)
def resend_verification_email(request):
    """Re-send the email-verification link for a pending signup.

    The user is not yet logged in (mandatory mode blocks login until the email
    is verified), so we cannot use allauth's built-in email-management page
    which requires authentication. Always returns the same generic message so
    we don't leak whether an account exists for a given address.

    The target email normally comes from the session key set when the user
    signed up (or last tried to log in unverified). That session can be
    empty -- a different device, or cookies cleared -- so this also accepts
    an ``email`` POST field from the visible form the template shows in that
    case. A per-address cooldown (``claim_resend_cooldown``, separate from
    the hourly IP rate limit above, which guards against abuse) stops a
    re-send on every reload/back-button; the response stays identical
    either way. For a social account held by pre_login, a typed address
    replaces the account's unverified one (#1059).
    """
    from allauth.account.models import EmailAddress

    # A typed address wins over the session one, so a member who mistyped
    # can correct it from the page's "Use a different address" field.
    typed = (request.POST.get("email") or "").strip()
    email = typed or request.session.get("pending_verification_email")

    if email and claim_resend_cooldown(email):
        held = _held_social_user_id(request) if typed else None
        held_email = request.session.get("pending_verification_email")
        email_address = (
            _replace_pending_social_address(request, typed) if typed else None
        )
        # A rejected correction (invalid, or another account's address)
        # keeps the session bound to the held account, so a later available
        # address can still repair it; the response below stays identical.
        keep_held_binding = bool(held) and email_address is None
        if email_address is None:
            email_address = EmailAddress.objects.filter(
                email__iexact=email, verified=False
            ).first()
        if email_address:
            # The response must not differ between a known and an unknown
            # address. A mail failure here would otherwise surface as an error
            # only for existing accounts, so it is logged and swallowed.
            # allauth's rate-limited send: a per-address confirm_email
            # cooldown, so rotating cookies or source IPs can't flood a
            # pending address through this public endpoint.
            from allauth.account.internal.flows.email_verification import (
                send_verification_email_to_address,
            )

            try:
                send_verification_email_to_address(request, email_address)
                logger.info("Resent verification email")
            except Exception:
                logger.exception("Resending the verification email failed")
        # Remember the address so this page can mask it and keep offering
        # resend without needing the visitor to retype it.
        if keep_held_binding:
            # The generic resend above may have re-stashed the typed address
            # (email_confirmation_sent signal), so restore the hold.
            request.session["pending_verification_email"] = held_email
            request.session["pending_verification_user_id"] = held
            # Retyping the held address itself resent to it and claimed its
            # cooldown, so the page's countdown must start for it too.
            if held_email and typed.lower() == held_email.lower():
                start_resend_cooldown_display(request, held_email)
        else:
            request.session["pending_verification_email"] = email
            _held_social_user_id(request)
            # Only for the address the page now shows: a rejected typed
            # address must not disable resend for the restored one.
            start_resend_cooldown_display(request, email)

    messages.success(
        request,
        _(
            "If an unverified account exists for that address, "
            "a new verification link has been sent."
        ),
    )
    return redirect("account_email_verification_sent")


@crush_login_required
def export_user_data(request):
    """
    GDPR Article 20 - Data Portability.
    Export all user's personal data as a JSON file download.
    """
    from crush_lu.models.profiles import UserDataConsent

    user = request.user
    data = {
        "export_date": timezone.now().isoformat(),
        "account": {
            "email": user.email,
            "username": user.username,
            "date_joined": user.date_joined.isoformat(),
            "last_login": user.last_login.isoformat() if user.last_login else None,
        },
    }

    # Profile data
    if hasattr(user, "crushprofile"):
        profile = user.crushprofile
        data["profile"] = {
            "display_name": profile.display_name,
            "gender": profile.gender,
            "date_of_birth": (
                str(profile.date_of_birth) if profile.date_of_birth else None
            ),
            "canton": getattr(profile, "canton", None),
            "bio": profile.bio,
            "interests": profile.interests,
            "status": getattr(profile, "status", None),
            "is_community_supporter": profile.is_community_supporter,
            "created_at": (
                profile.created_at.isoformat()
                if hasattr(profile, "created_at") and profile.created_at
                else None
            ),
        }

    # Event registrations
    registrations = (
        EventRegistration.objects.filter(user=user)
        .select_related("event", "preference")
        .prefetch_related(
            "curated_group_memberships__group",
            "curated_pairing_participations__pairing",
        )
    )
    if registrations.exists():
        registration_entries = []
        for reg in registrations:
            entry = {
                "event": reg.event.title,
                "event_date": (
                    reg.event.date_time.isoformat() if reg.event.date_time else None
                ),
                "status": reg.status,
                "registered_at": (
                    reg.created_at.isoformat()
                    if hasattr(reg, "created_at") and reg.created_at
                    else None
                ),
            }
            # Speed-dating applications carry a preference snapshot (preferred
            # genders / age range / languages). It is the member's own answer
            # and Art. 9-adjacent, so it belongs in an export that promises
            # "all of your personal data"; nested under its registration
            # because that is the only context in which it means anything.
            # Absent for every other event type, and gone from later exports
            # once the retention sweep prunes the row.
            pref = getattr(reg, "preference", None)
            if pref is not None:
                entry["speed_dating_preferences"] = {
                    "preferred_genders": pref.preferred_genders,
                    "preferred_age_min": pref.preferred_age_min,
                    "preferred_age_max": pref.preferred_age_max,
                    "languages": pref.languages,
                    "submitted_at": (
                        pref.created_at.isoformat() if pref.created_at else None
                    ),
                    "updated_at": (
                        pref.updated_at.isoformat() if pref.updated_at else None
                    ),
                }
            group_history = []
            own_pairings = list(reg.curated_pairing_participations.all())
            for membership in reg.curated_group_memberships.all():
                group = membership.group
                schedule = [
                    {
                        "round": participant.round_number,
                        "table": participant.pairing.table_number,
                        "seat": participant.seat,
                    }
                    for participant in sorted(
                        (
                            participant
                            for participant in own_pairings
                            if participant.group_id == group.pk
                        ),
                        key=lambda participant: (
                            participant.round_number,
                            participant.pairing.table_number,
                            participant.pk,
                        ),
                    )
                ]
                group_history.append(
                    {
                        "generation": group.generation,
                        "group_number": group.group_number,
                        "status": group.status,
                        "position": membership.position,
                        "assigned_at": membership.assigned_at.isoformat(),
                        "released_at": (
                            membership.released_at.isoformat()
                            if membership.released_at
                            else None
                        ),
                        # A member receives their own timetable, not another
                        # participant's name/email. The relational rows remain
                        # in the export without disclosing third-party PII.
                        "schedule": schedule,
                    }
                )
            if group_history:
                entry["curated_group_history"] = group_history
            registration_entries.append(entry)
        data["event_registrations"] = registration_entries

    # Crush Credit
    #
    # This endpoint promises "all of your personal data", and a credit ledger
    # is both personal data and money owed — arguably the single entry a member
    # is most likely to want a record of. Every field is included except the
    # staff ``note``: that is an internal free-text field where a coach writes
    # why goodwill was given, it can name other people and other incidents, and
    # portability of the member's own data does not extend to it.
    #
    # Redemptions are nested under the credit they came off rather than listed
    # separately, because "what happened to my €20" is only answerable in that
    # shape. The seat is named where it still exists; a redemption outlives a
    # deleted registration (SET_NULL), and reads "deleted event registration".
    from crush_lu.models.credits import CrushCredit
    from crush_lu.utils.formatting import format_cents

    credits = (
        CrushCredit.objects.filter(user=user)
        .prefetch_related("redemptions__event_registration__event")
        .order_by("issued_at")
    )
    credit_rows = list(credits)
    if credit_rows:
        exported_credits = []
        now = timezone.now()
        for credit in credit_rows:
            redemptions = list(credit.redemptions.all())
            redeemed_cents = sum(item.amount_cents for item in redemptions)
            effectively_expired = (
                credit.status == CrushCredit.Status.ACTIVE and credit.expires_at <= now
            )
            exported_credits.append(
                {
                    "amount": format_cents(credit.amount_cents),
                    "currency": credit.currency,
                    "reason": credit.get_reason_display(),
                    "status": (
                        str(CrushCredit.Status.EXPIRED.label)
                        if effectively_expired
                        else credit.get_status_display()
                    ),
                    "issued_at": credit.issued_at.isoformat(),
                    "expires_at": credit.expires_at.isoformat(),
                    "remaining": format_cents(
                        0
                        if effectively_expired
                        else max(0, credit.amount_cents - redeemed_cents)
                    ),
                    # The EFFECTIVE answer, not the issuance flag. Once any of the
                    # credit is spent — or it expires, or is voided after a cash
                    # refund has already been paid — the offer is closed, and the
                    # staff queue treats it as closed. Exporting the raw flag told
                    # the member they could still ask for money back when they
                    # could not.
                    "cash_refund_available_on_request": bool(
                        credit.cash_refund_eligible
                        and credit.status == CrushCredit.Status.ACTIVE
                        and credit.expires_at > now
                        and not redemptions
                    ),
                    "spent_on": [
                        {
                            "amount": format_cents(redemption.amount_cents),
                            "redeemed_at": redemption.redeemed_at.isoformat(),
                            "event": (
                                redemption.event_registration.event.title
                                if redemption.event_registration
                                else "deleted event registration"
                            ),
                        }
                        for redemption in redemptions
                    ],
                }
            )
        data["crush_credit"] = exported_credits

    # Connections
    connections = EventConnection.objects.filter(
        Q(requester=user) | Q(recipient=user)
    ).select_related("requester", "recipient", "event")
    # Privacy ("My Crush!", spec §5): a recipient's export must not name
    # their secret admirer — incoming pre-`shared` crush rows are suppressed
    # entirely. The requester's own export keeps their own outgoing
    # declaration (data portability for what THEY did), but its status is
    # normalized pre-`shared` so a coach-recorded decline or in-progress
    # review never leaks through the payload.
    #
    # The visible rows are built BEFORE the key is added: keying off
    # `connections.exists()` would emit `"connections": []` for a recipient
    # whose only row was the suppressed crush, while a member with no rows at
    # all gets no key — an empty array is then a reliable tell that a hidden
    # row exists. Byte-identical to the pre-declaration export either way.
    visible_connections = []
    for conn in connections:
        is_unshared_crush = (
            conn.flow == EventConnection.FLOW_CRUSH and conn.status != "shared"
        )
        if is_unshared_crush and conn.recipient_id == user.id:
            continue
        is_requester = conn.requester_id == user.id
        counterpart = conn.recipient if is_requester else conn.requester
        counterpart_shared_email = conn.status == "shared" and (
            conn.recipient_shares_email if is_requester else conn.requester_shares_email
        )
        counterpart_shared_phone = conn.status == "shared" and (
            conn.recipient_shares_phone if is_requester else conn.requester_shares_phone
        )
        counterpart_profile = getattr(counterpart, "crushprofile", None)
        visible_connections.append(
            {
                "event": conn.event.title if conn.event else None,
                # The other member's email is theirs, not the requester's: it
                # is exported only once they shared it with this member (the
                # connection is shared and they ticked email at consent), the
                # same rule connection_detail.html shows it by (UX Wave 4,
                # decision I). Otherwise it never appears, in any status.
                "connected_with": (
                    counterpart.email if counterpart_shared_email else None
                ),
                # Same opt-in rule as email: the counterpart's phone is
                # exported only once they chose to share it (UX Wave 5 WP4).
                "connected_with_phone": (
                    (counterpart_profile.phone_number or None)
                    if counterpart_shared_phone and counterpart_profile
                    else None
                ),
                # What THIS member chose to share with the counterpart.
                "you_shared_email": (
                    conn.requester_shares_email
                    if is_requester
                    else conn.recipient_shares_email
                ),
                "you_shared_phone": (
                    conn.requester_shares_phone
                    if is_requester
                    else conn.recipient_shares_phone
                ),
                "status": ("with your coach" if is_unshared_crush else conn.status),
                "created_at": (
                    conn.created_at.isoformat()
                    if hasattr(conn, "created_at") and conn.created_at
                    else None
                ),
            }
        )
    if visible_connections:
        data["connections"] = visible_connections

    # Messages
    sent_messages = ConnectionMessage.objects.filter(sender=user)
    if sent_messages.exists():
        data["messages_sent"] = [
            {
                "content": msg.content,
                "sent_at": (
                    msg.created_at.isoformat()
                    if hasattr(msg, "created_at") and msg.created_at
                    else None
                ),
            }
            for msg in sent_messages
        ]

    # Consent records
    try:
        consent = UserDataConsent.objects.get(user=user)
        data["consent"] = {
            "crushlu_consent_given": consent.crushlu_consent_given,
            "crushlu_consent_date": (
                consent.crushlu_consent_date.isoformat()
                if consent.crushlu_consent_date
                else None
            ),
            "powerup_consent_given": consent.powerup_consent_given,
            "powerup_consent_date": (
                consent.powerup_consent_date.isoformat()
                if consent.powerup_consent_date
                else None
            ),
            "marketing_consent": (
                consent.marketing_consent
                if hasattr(consent, "marketing_consent")
                else None
            ),
        }
    except UserDataConsent.DoesNotExist:
        pass

    response = HttpResponse(
        json.dumps(data, indent=2, ensure_ascii=False),
        content_type="application/json",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="crush_lu_data_{user.id}.json"'
    )
    return response
