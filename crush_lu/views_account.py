from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.db.models import Q
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
import logging
import uuid
import json
import base64
import hashlib
import hmac

logger = logging.getLogger(__name__)

from .models import (
    CrushProfile,
    CrushCoach,
    ProfileSubmission,
    UserActivity,
    CoachPushSubscription,
    EventRegistration,
    EventConnection,
    ConnectionMessage,
    CoachSession,
)
from .forms import CrushSignupForm
from .decorators import crush_login_required, ratelimit
from .email_helpers import send_welcome_email
from .referrals import (
    capture_referral,
    capture_referral_from_request,
    apply_referral_to_user,
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

    # Check if user has a profile
    try:
        profile = request.user.crushprofile
    except CrushProfile.DoesNotExist:
        final_destination = "/create-profile/"

    context = {
        "final_destination": final_destination,
        "user": request.user,
    }
    return render(request, "crush_lu/oauth_complete.html", context)


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
            delete_user_data(user, confirmation_code)

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
    from crush_lu.models.profiles import UserDataConsent

    logger.info(f"Starting Crush.lu profile deletion for user {user.id}")

    # Delete profile photos from Azure Blob
    if hasattr(user, 'crushprofile'):
        profile = user.crushprofile

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

    # Delete EventRegistrations
    EventRegistration.objects.filter(user=user).delete()

    # Delete ConnectionMessages
    ConnectionMessage.objects.filter(
        Q(sender=user)
        | Q(connection__requester=user)
        | Q(connection__recipient=user)
    ).delete()

    # Delete EventConnections (both as requester and recipient)
    EventConnection.objects.filter(Q(requester=user) | Q(recipient=user)).delete()

    # Delete CoachSessions
    CoachSession.objects.filter(user=user).delete()

    # Clear Crush.lu consent and set permanent ban
    if hasattr(user, 'data_consent'):
        consent = user.data_consent
        consent.crushlu_consent_given = False
        consent.crushlu_consent_date = None
        consent.crushlu_consent_ip = None
        consent.crushlu_banned = True
        consent.crushlu_ban_date = timezone.now()
        consent.crushlu_ban_reason = 'user_deletion'
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
    from allauth.socialaccount.models import SocialAccount, SocialToken

    logger.info(f"Starting full account deletion for user {user.id}")

    # First delete Crush.lu profile
    delete_crushlu_profile_only(user)

    # Anonymize User record (instead of deleting to preserve referential integrity)
    user.email = f'deleted_{user.id}@deleted.crush.lu'
    user.username = f'deleted_user_{user.id}'
    user.first_name = ''
    user.last_name = ''
    user.set_unusable_password()
    user.is_active = False
    user.save()

    # Delete social accounts and tokens (allauth)
    SocialToken.objects.filter(account__user=user).delete()
    SocialAccount.objects.filter(user=user).delete()

    logger.info(f"Full account deleted for user {user.id} (all platforms)")


def delete_user_data(user, confirmation_code):
    """
    DEPRECATED: Legacy function for backwards compatibility.
    Use delete_crushlu_profile_only() or delete_full_account() instead.

    This function now calls delete_full_account() for backwards compatibility
    with existing code that may reference it.
    """
    logger.warning(
        f"delete_user_data() is deprecated. Use delete_full_account() instead. "
        f"Called for user {user.id}, confirmation: {confirmation_code}"
    )
    delete_full_account(user)


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


@crush_login_required
def account_settings(request):
    """
    Account settings page with delete account option, email preferences,
    push notification preferences, and linked social accounts management.
    """
    import json
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site
    from .models import EmailPreference, PushSubscription, CoachPushSubscription
    from .social_photos import get_all_social_photos

    # Helper function to determine device type from device name
    def get_device_type(device_name):
        """Return 'mobile' or 'desktop' based on device name."""
        mobile_devices = ["Android Chrome", "iPhone Safari"]
        return "mobile" if device_name in mobile_devices else "desktop"

    # Get or create email preferences for this user
    email_prefs = EmailPreference.get_or_create_for_user(request.user)

    # Get push subscriptions - show card to all users (JS detects browser support)
    # PWA status is tracked on UserActivity model for analytics
    push_subscriptions = []
    push_subscriptions_json = "[]"
    is_pwa_user = False
    try:
        from .models import UserActivity

        activity = UserActivity.objects.filter(user=request.user).first()
        if activity:
            is_pwa_user = activity.is_pwa_user

        # Always fetch push subscriptions - card visibility is controlled by JS
        subs = PushSubscription.objects.filter(user=request.user, enabled=True)
        for sub in subs:
            push_subscriptions.append(
                {
                    "id": sub.id,
                    "endpoint": sub.endpoint,  # For current device detection
                    "device_fingerprint": sub.device_fingerprint
                    or "",  # Stable device identifier
                    "device_name": sub.device_name or "Unknown Device",
                    "device_type": get_device_type(sub.device_name or ""),
                    "last_used_at": sub.last_used_at,  # Keep as datetime for template filters
                    "notify_new_messages": sub.notify_new_messages,
                    "notify_event_reminders": sub.notify_event_reminders,
                    "notify_new_connections": sub.notify_new_connections,
                    "notify_profile_updates": sub.notify_profile_updates,
                }
            )
        push_subscriptions_json = json.dumps(push_subscriptions, default=str)
    except Exception:
        logger.warning("Failed to fetch push subscriptions for user %s", request.user.id, exc_info=True)

    # Check if user is a coach and get coach push subscriptions
    is_coach = False
    coach_push_subscriptions = []
    coach_push_subscriptions_json = "[]"
    try:
        if hasattr(request.user, "crushcoach") and request.user.crushcoach.is_active:
            is_coach = True
            coach = request.user.crushcoach
            coach_subs = CoachPushSubscription.objects.filter(coach=coach, enabled=True)
            for sub in coach_subs:
                coach_push_subscriptions.append(
                    {
                        "id": sub.id,
                        "endpoint": sub.endpoint,  # For current device detection
                        "device_fingerprint": sub.device_fingerprint
                        or "",  # Stable device identifier
                        "device_name": sub.device_name or "Unknown Device",
                        "device_type": get_device_type(sub.device_name or ""),
                        "last_used_at": sub.last_used_at,  # Keep as datetime for template filters
                        "notify_new_submissions": sub.notify_new_submissions,
                        "notify_screening_reminders": sub.notify_screening_reminders,
                        "notify_user_responses": sub.notify_user_responses,
                        "notify_system_alerts": sub.notify_system_alerts,
                    }
                )
            coach_push_subscriptions_json = json.dumps(
                coach_push_subscriptions, default=str
            )
    except Exception:
        logger.warning("Failed to fetch coach push subscriptions for user %s", request.user.id, exc_info=True)

    # Crush.lu only supports these social providers
    # (LinkedIn is PowerUP-only, not shown in Crush.lu account settings)
    CRUSH_SOCIAL_PROVIDERS = ["google", "facebook", "microsoft"]

    # Get connected social providers for this user (filtered to Crush.lu providers)
    connected_providers = set(
        request.user.socialaccount_set.values_list("provider", flat=True)
    )

    # Filter social accounts to only show Crush.lu-supported providers
    crush_social_accounts = request.user.socialaccount_set.filter(
        provider__in=CRUSH_SOCIAL_PROVIDERS
    )

    # Get social photos for import functionality
    social_photos = get_all_social_photos(request.user)

    # Check which providers are actually configured for this site
    # This prevents template errors when SocialApp doesn't exist
    try:
        current_site = Site.objects.get_current(request)
        available_providers = set(
            SocialApp.objects.filter(sites=current_site).values_list(
                "provider", flat=True
            )
        )
    except Exception:
        logger.warning("Failed to fetch available social providers", exc_info=True)
        available_providers = set()

    return render(
        request,
        "crush_lu/account_settings.html",
        {
            "email_prefs": email_prefs,
            "google_connected": "google" in connected_providers,
            "facebook_connected": "facebook" in connected_providers,
            "microsoft_connected": "microsoft" in connected_providers,
            "google_available": "google" in available_providers,
            "facebook_available": "facebook" in available_providers,
            "microsoft_available": "microsoft" in available_providers,
            "crush_social_accounts": crush_social_accounts,  # Filtered list for display
            "social_photos": social_photos,  # Social photos for import
            # Push notification preferences (PWA users only)
            "is_pwa_user": is_pwa_user,
            "push_subscriptions": push_subscriptions,
            "push_subscriptions_json": push_subscriptions_json,
            # Coach push notification preferences (coaches only)
            "is_coach": is_coach,
            "coach_push_subscriptions": coach_push_subscriptions,
            "coach_push_subscriptions_json": coach_push_subscriptions_json,
        },
    )


@crush_login_required
@require_http_methods(["POST"])
def update_email_preferences(request):
    """
    Handle email preference form submission.
    """
    from .models import EmailPreference

    email_prefs = EmailPreference.get_or_create_for_user(request.user)

    # Update preferences from form data
    # Checkboxes: if checked, the name is in POST data; if unchecked, it's absent
    email_prefs.unsubscribed_all = "unsubscribed_all" in request.POST
    email_prefs.email_profile_updates = "email_profile_updates" in request.POST
    email_prefs.email_event_reminders = "email_event_reminders" in request.POST
    email_prefs.email_new_connections = "email_new_connections" in request.POST
    email_prefs.email_new_messages = "email_new_messages" in request.POST
    email_prefs.email_marketing = "email_marketing" in request.POST

    email_prefs.save()

    messages.success(request, _("Email preferences updated successfully!"))
    return redirect("crush_lu:account_settings")


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
        return redirect("crush_lu:account_settings")

    if has_password:
        messages.info(
            request,
            _('You already have a password set. Use "Change Password" to update it.'),
        )
        return redirect("crush_lu:account_settings")

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
            return redirect("crush_lu:account_settings")
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
        return redirect("crush_lu:account_settings")

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
        return redirect("crush_lu:account_settings")

    # Log the disconnection
    provider_name = social_account.provider.title()
    logger.info(f"User {request.user.id} disconnected {provider_name} account")

    # Delete the social account
    social_account.delete()

    messages.success(request, _("%(provider_name)s account has been disconnected.") % {"provider_name": provider_name})
    return redirect("crush_lu:account_settings")


@crush_login_required
@require_http_methods(["GET", "POST"])
def delete_crushlu_profile_view(request):
    """
    Simplified view for deleting Crush.lu profile only (default action).
    Permanent deletion - user cannot rejoin Crush.lu with this account.
    """
    if not hasattr(request.user, 'crushprofile'):
        messages.info(request, _('You do not have a Crush.lu profile to delete.'))
        return redirect('crush_lu:account_settings')

    if request.method == 'POST':
        confirm_email = request.POST.get('confirm_email', '').strip()

        if confirm_email != request.user.email:
            messages.error(request, _('Email confirmation does not match'))
            return redirect('crush_lu:delete_crushlu_profile')

        # Delete Crush.lu profile (sets permanent ban)
        try:
            delete_crushlu_profile_only(request.user)
            messages.success(
                request,
                _('Your Crush.lu profile has been permanently deleted. You cannot create a new profile with this account.')
            )
            return redirect('crush_lu:account_settings')
        except Exception as e:
            logger.exception(f"Error deleting Crush.lu profile for user {request.user.id}: {e}")
            messages.error(request, _('An error occurred while deleting your profile. Please try again.'))
            return redirect('crush_lu:delete_crushlu_profile')

    context = {
        'profile': request.user.crushprofile,
    }
    return render(request, 'crush_lu/delete_crushlu_profile_confirm.html', context)


@crush_login_required
@require_http_methods(["GET", "POST"])
def gdpr_data_management(request):
    """
    GDPR data management dashboard.
    Shows two deletion options:
    1. Delete Crush.lu profile only (keeps PowerUp account)
    2. Delete entire PowerUp account (erases everything)
    """
    from crush_lu.models.profiles import UserDataConsent

    consent, created = UserDataConsent.objects.get_or_create(
        user=request.user,
        defaults={
            'powerup_consent_given': True,
            'powerup_consent_date': timezone.now(),
        }
    )

    if request.method == 'POST':
        deletion_type = request.POST.get('deletion_type')
        confirm_email = request.POST.get('confirm_email', '').strip()

        if confirm_email != request.user.email:
            messages.error(request, _('Email confirmation does not match'))
            return redirect('crush_lu:gdpr_data_management')

        if deletion_type == 'crushlu_only':
            # Delete Crush.lu profile only
            try:
                delete_crushlu_profile_only(request.user)
                messages.success(request, _('Your Crush.lu profile has been deleted. Your PowerUp account remains active.'))
                return redirect('crush_lu:account_settings')
            except Exception as e:
                logger.exception(f"Error deleting Crush.lu profile for user {request.user.id}: {e}")
                messages.error(request, _('An error occurred. Please try again.'))
                return redirect('crush_lu:gdpr_data_management')

        elif deletion_type == 'full_account':
            # Delete entire PowerUp account
            try:
                delete_full_account(request.user)
                logout(request)
                messages.success(request, _('Your account has been completely deleted from all platforms.'))
                return redirect('crush_lu:home')
            except Exception as e:
                logger.exception(f"Error deleting full account for user {request.user.id}: {e}")
                messages.error(request, _('An error occurred. Please try again.'))
                return redirect('crush_lu:gdpr_data_management')

    context = {
        'consent': consent,
        'has_crushlu_profile': hasattr(request.user, 'crushprofile'),
    }
    return render(request, 'crush_lu/gdpr_data_management.html', context)


@crush_login_required
@require_http_methods(["GET", "POST"])
def delete_account(request):
    """
    DEPRECATED: Legacy account deletion view.
    Redirects to new GDPR data management dashboard.
    """
    messages.info(request, _('Account deletion has been moved to the data management page.'))
    return redirect('crush_lu:gdpr_data_management')


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

    # Check if user already has consent (shouldn't happen, but be safe)
    if hasattr(request.user, 'data_consent') and request.user.data_consent.crushlu_consent_given:
        messages.info(request, _('You have already given consent.'))
        return redirect('crush_lu:dashboard')

    if request.method == 'POST':
        # Get consent checkboxes
        crushlu_consent = request.POST.get('crushlu_consent') == 'on'
        marketing_consent = request.POST.get('marketing_consent') == 'on'

        if not crushlu_consent:
            messages.error(request, _('You must consent to continue using Crush.lu.'))
            return redirect('crush_lu:consent_confirm')

        # Update or create consent record
        consent, created = UserDataConsent.objects.get_or_create(user=request.user)
        consent.crushlu_consent_given = True
        consent.crushlu_consent_date = timezone.now()
        consent.crushlu_consent_ip = get_client_ip(request)
        consent.marketing_consent = marketing_consent
        consent.marketing_consent_date = timezone.now() if marketing_consent else None
        consent.save()

        logger.info(f"User {request.user.id} retroactively gave Crush.lu consent")
        messages.success(request, _('Thank you for confirming your consent!'))
        return redirect('crush_lu:dashboard')

    # GET request - show consent form
    context = {}
    return render(request, 'crush_lu/consent_confirm.html', context)


# Onboarding
def referral_redirect(request, code):
    """
    Referral landing route.
    Stores referral attribution and redirects to signup with code preserved.
    """
    referral = capture_referral(request, code, source="link")
    signup_url = reverse("crush_lu:signup")
    if referral:
        return redirect(f"{signup_url}?ref={referral.code}")
    return redirect(signup_url)


@ratelimit(key="ip", rate="5/h", method="POST")
def signup(request):
    """
    User registration with Allauth integration
    Supports both manual signup and social login (LinkedIn, Google, etc.)
    Uses unified auth template with login/signup tabs
    """
    from allauth.account.forms import LoginForm

    capture_referral_from_request(request)
    signup_form = CrushSignupForm()
    login_form = LoginForm()

    if request.method == "POST":
        signup_form = CrushSignupForm(request.POST)
        if signup_form.is_valid():
            try:
                # Allauth's save() method handles EmailAddress creation automatically
                # This will raise IntegrityError if email/username already exists
                user = signup_form.save(request)

                # Send welcome email immediately after account creation
                try:
                    result = send_welcome_email(user, request)
                    logger.info(f"✅ Welcome email sent to {user.email}: {result}")
                except Exception as e:
                    logger.error(
                        f"❌ Failed to send welcome email to {user.email}: {e}",
                        exc_info=True,
                    )
                    # Don't block signup if email fails

                messages.success(
                    request,
                    _("Account created! Check your email and complete your profile."),
                )
                # Log the user in - set backend for multi-auth compatibility
                user.backend = "django.contrib.auth.backends.ModelBackend"
                login(request, user)

                # Check if there's a pending gift to claim
                pending_gift_code = request.session.get("pending_gift_code")
                if pending_gift_code:
                    return redirect("crush_lu:gift_claim", gift_code=pending_gift_code)

                return redirect("crush_lu:create_profile")

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
                        "An account with this email already exists. "
                        "Please login or use a different email.",
                    )
                else:
                    messages.error(
                        request,
                        "An error occurred while creating your account. Please try again.",
                    )

    context = {
        "signup_form": signup_form,
        "login_form": login_form,
        "mode": "signup",
    }
    return render(request, "crush_lu/auth.html", context)


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
            "date_of_birth": str(profile.date_of_birth) if profile.date_of_birth else None,
            "canton": profile.canton,
            "bio": profile.bio,
            "interests": profile.interests,
            "looking_for": profile.looking_for,
            "status": profile.status,
            "created_at": profile.created_at.isoformat() if hasattr(profile, "created_at") and profile.created_at else None,
        }

    # Event registrations
    registrations = EventRegistration.objects.filter(user=user).select_related("event")
    if registrations.exists():
        data["event_registrations"] = [
            {
                "event": reg.event.title,
                "event_date": reg.event.date_time.isoformat() if reg.event.date_time else None,
                "status": reg.status,
                "registered_at": reg.created_at.isoformat() if hasattr(reg, "created_at") and reg.created_at else None,
            }
            for reg in registrations
        ]

    # Connections
    connections = EventConnection.objects.filter(
        Q(requester=user) | Q(recipient=user)
    ).select_related("requester", "recipient", "event")
    if connections.exists():
        data["connections"] = [
            {
                "event": conn.event.title if conn.event else None,
                "connected_with": conn.recipient.email if conn.requester == user else conn.requester.email,
                "status": conn.status,
                "created_at": conn.created_at.isoformat() if hasattr(conn, "created_at") and conn.created_at else None,
            }
            for conn in connections
        ]

    # Messages
    sent_messages = ConnectionMessage.objects.filter(sender=user)
    if sent_messages.exists():
        data["messages_sent"] = [
            {
                "content": msg.content,
                "sent_at": msg.created_at.isoformat() if hasattr(msg, "created_at") and msg.created_at else None,
            }
            for msg in sent_messages
        ]

    # Consent records
    try:
        consent = UserDataConsent.objects.get(user=user)
        data["consent"] = {
            "crushlu_consent_given": consent.crushlu_consent_given,
            "crushlu_consent_date": consent.crushlu_consent_date.isoformat() if consent.crushlu_consent_date else None,
            "powerup_consent_given": consent.powerup_consent_given,
            "powerup_consent_date": consent.powerup_consent_date.isoformat() if consent.powerup_consent_date else None,
            "marketing_consent": consent.marketing_consent if hasattr(consent, "marketing_consent") else None,
        }
    except UserDataConsent.DoesNotExist:
        pass

    response = HttpResponse(
        json.dumps(data, indent=2, ensure_ascii=False),
        content_type="application/json",
    )
    response["Content-Disposition"] = f'attachment; filename="crush_lu_data_{user.id}.json"'
    return response
