from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.db.models import Q
from django.db import transaction
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse
from django.conf import settings
from datetime import timedelta
import logging
import uuid
import json
import base64
import hashlib
import hmac
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

from .models import (
    CrushProfile,
    CrushCoach,
    ProfileSubmission,
    MeetupEvent,
    EventRegistration,
    CoachSession,
    EventConnection,
    ConnectionMessage,
    EventActivityOption,
    EventActivityVote,
    EventVotingSession,
    PresentationQueue,
    PresentationRating,
    SpeedDatingPair,
    SpecialUserExperience,
    EventInvitation,
    UserActivity,
    CoachPushSubscription,
)
from .forms import (
    CrushSignupForm,
    CrushProfileForm,
    CrushCoachForm,
    ProfileReviewForm,
    CoachSessionForm,
    EventRegistrationForm,
)
from .decorators import crush_login_required, ratelimit
from .email_helpers import (
    send_welcome_email,
    send_profile_submission_confirmation,
    send_coach_assignment_notification,
    send_profile_submission_notifications,
    send_profile_approved_notification,
    send_profile_revision_request,
    send_profile_rejected_notification,
    send_event_registration_confirmation,
    send_event_waitlist_notification,
    send_event_cancellation_confirmation,
)
from .notification_service import (
    NotificationService,
    NotificationType,
    notify_profile_approved,
    notify_profile_revision,
    notify_profile_rejected,
    notify_new_message,
    notify_new_connection,
    notify_connection_accepted,
)
from .coach_notifications import (
    notify_coach_new_submission,
    notify_coach_user_revision,
)
from .referrals import (
    capture_referral,
    capture_referral_from_request,
    apply_referral_to_user,
)
from .utils.i18n import is_valid_language

# Authentication views - login/logout are now handled by AllAuth
# See crush_lu/urls.py: login -> LoginView.as_view(), logout -> LogoutView.as_view()
# Special experience redirects are handled in azureproject/adapters.py:get_login_redirect_url()


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


# Public pages
def home(request):
    """Landing page - redirects authenticated users to dashboard"""
    # If user is logged in, redirect to their dashboard
    if request.user.is_authenticated:
        return redirect("crush_lu:dashboard")

    upcoming_events = MeetupEvent.objects.filter(
        is_published=True, is_cancelled=False, date_time__gte=timezone.now()
    )[:3]

    context = {
        "upcoming_events": upcoming_events,
    }
    return render(request, "crush_lu/home.html", context)


def test_ghost_story(request):
    """Test page for ghost story component - remove after verification"""
    return render(request, "crush_lu/test_ghost_story.html")


def about(request):
    """About page"""
    return render(request, "crush_lu/about.html")


def how_it_works(request):
    """How it works page"""
    return render(request, "crush_lu/how_it_works.html")


def privacy_policy(request):
    """Privacy policy page"""
    return render(request, "crush_lu/privacy_policy.html")


def terms_of_service(request):
    """Terms of service page"""
    return render(request, "crush_lu/terms_of_service.html")


def data_deletion_request(request):
    """Data deletion instructions page"""
    return render(request, "crush_lu/data_deletion.html")


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
        pass

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
        pass

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

    messages.success(request, f"{provider_name} account has been disconnected.")
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
@ratelimit(key="user", rate="10/15m", method="POST", block=True)
def create_profile(request):
    """Profile creation - coaches can also create dating profiles"""
    from crush_lu.models.profiles import UserDataConsent

    # Check if user is banned from Crush.lu
    if hasattr(request.user, 'data_consent') and request.user.data_consent.crushlu_banned:
        messages.error(
            request,
            _('You cannot create a new Crush.lu profile. Your previous profile was permanently deleted.')
        )
        return redirect('crush_lu:account_settings')

    # If it's a POST request, process the form submission first
    if request.method == "POST":
        # Get existing profile if it exists (from Steps 1-2 AJAX saves)
        try:
            existing_profile = CrushProfile.objects.get(user=request.user)
            form = CrushProfileForm(
                request.POST, request.FILES, instance=existing_profile
            )
        except CrushProfile.DoesNotExist:
            form = CrushProfileForm(request.POST, request.FILES)

        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = request.user

            # Check if this is first submission or resubmission
            is_first_submission = profile.completion_status != "submitted"

            # Set preferred language from current request language on first submission
            # This respects the user's browser language detected by Django's LocaleMiddleware
            if is_first_submission and hasattr(request, "LANGUAGE_CODE"):
                current_lang = request.LANGUAGE_CODE
                # Only set if it's a supported language
                if is_valid_language(current_lang):
                    profile.preferred_language = current_lang
                    logger.debug(
                        f"Set preferred_language to '{current_lang}' for {request.user.email}"
                    )

            # Mark profile as completed and submitted
            profile.completion_status = "submitted"

            # Note: Screening call handled in ProfileSubmission.review_call_completed
            # No need to set flags here - coach will do screening during review
            if is_first_submission:
                logger.info(
                    f"First submission - screening call will be done during coach review for {request.user.email}"
                )
            else:
                logger.info(f"Resubmission detected for {request.user.email}")

            # Use atomic transaction to ensure data integrity
            # This prevents partial saves if submission creation fails
            try:
                with transaction.atomic():
                    profile.save()
                    logger.info(f"Profile submitted for review: {request.user.email}")

                    # Create profile submission for coach review (PREVENT DUPLICATES)
                    # Use select_for_update to prevent race conditions with concurrent requests
                    # Check if a pending submission already exists
                    existing_submission = (
                        ProfileSubmission.objects.select_for_update()
                        .filter(profile=profile, status="pending")
                        .first()
                    )

                    # Also check for revision/rejected submissions that user is resubmitting
                    revision_submission = (
                        ProfileSubmission.objects.select_for_update()
                        .filter(profile=profile, status__in=["revision", "rejected"])
                        .first()
                    )

                    is_revision = False
                    if existing_submission:
                        submission = existing_submission
                        created = False
                        logger.warning(
                            f"⚠️ Existing pending submission found for {request.user.email}"
                        )
                    elif revision_submission:
                        # User is resubmitting after revision request - update existing submission
                        submission = revision_submission
                        submission.status = "pending"
                        submission.submitted_at = timezone.now()
                        submission.save()
                        created = False
                        is_revision = True
                        logger.info(
                            f"✅ Revision submission updated to pending for {request.user.email}"
                        )
                    else:
                        # Create new submission
                        submission = ProfileSubmission.objects.create(
                            profile=profile, status="pending"
                        )
                        created = True

            except Exception as e:
                logger.error(
                    f"❌ Transaction failed for {request.user.email}: {e}",
                    exc_info=True,
                )
                messages.error(
                    request,
                    _(
                        "An error occurred while submitting your profile. Please try again."
                    ),
                )
                # Re-render the form
                from .social_photos import get_all_social_photos

                context = {
                    "form": form,
                    "profile": profile,
                    "current_step": "step3",
                    "social_photos": get_all_social_photos(request.user),
                }
                return render(request, "crush_lu/create_profile.html", context)

            # Only assign coach and send emails for NEW submissions (outside transaction for email reliability)
            if created:
                submission.assign_coach()
                logger.info(f"NEW profile submission created for {request.user.email}")

                # Send push notification to assigned coach
                if submission.coach:
                    try:
                        notify_coach_new_submission(submission.coach, submission)
                        logger.info(
                            f"Coach push notification sent for submission {submission.id}"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send coach push notification: {e}")

                # Send confirmation and coach notification emails using consolidated helper
                send_profile_submission_notifications(
                    submission,
                    request,
                    add_message_func=lambda msg: messages.warning(request, msg),
                )
            elif is_revision:
                # User resubmitted after revision request - notify the coach
                if submission.coach:
                    try:
                        notify_coach_user_revision(submission.coach, submission)
                        logger.info(
                            f"Coach revision notification sent for submission {submission.id}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to send coach revision notification: {e}"
                        )
            else:
                # Duplicate submission attempt - just log and continue
                logger.warning(
                    f"⚠️ Duplicate submission attempt prevented for {request.user.email}"
                )

            messages.success(request, _("Profile submitted for review!"))
            return redirect("crush_lu:profile_submitted")
        else:
            # CRITICAL: Log validation errors
            logger.error(
                f"❌ Profile form validation failed for user {request.user.email}"
            )
            logger.error(f"❌ Form errors: {form.errors.as_json()}")

            # Show user-friendly error messages
            for field, errors in form.errors.items():
                for error in errors:
                    if field == "__all__":
                        messages.error(request, f"Form error: {error}")
                    else:
                        messages.error(
                            request, f"{field.replace('_', ' ').title()}: {error}"
                        )

            # When validation fails, show Step 4 (review step) so user can see errors
            # and resubmit from the review screen
            from .social_photos import get_all_social_photos

            context = {
                "form": form,
                "current_step": "step3",  # Show review step where submit button is
                "social_photos": get_all_social_photos(request.user),
            }
            return render(request, "crush_lu/create_profile.html", context)

    # GET request - check if profile already exists and redirect accordingly
    try:
        profile = CrushProfile.objects.get(user=request.user)

        # If profile is submitted, show status page instead of edit form
        if profile.completion_status == "submitted":
            messages.info(
                request, _("Your profile has been submitted. Check the status below.")
            )
            return redirect("crush_lu:profile_submitted")
        elif profile.completion_status == "not_started":
            # Fresh profile (auto-created on login) - show creation form
            from .social_photos import get_all_social_photos

            form = CrushProfileForm(instance=profile)
            return render(
                request,
                "crush_lu/create_profile.html",
                {
                    "form": form,
                    "profile": profile,  # Required for phone verification status display
                    "social_photos": get_all_social_photos(request.user),
                },
            )
        elif profile.completion_status in ["step1", "step2", "step3"]:
            # Profile is in progress through the wizard - show the wizard at current step
            from .social_photos import get_all_social_photos

            form = CrushProfileForm(instance=profile)
            return render(
                request,
                "crush_lu/create_profile.html",
                {
                    "form": form,
                    "profile": profile,
                    "current_step": profile.completion_status,
                    "social_photos": get_all_social_photos(request.user),
                },
            )
        else:
            # Unknown status or 'completed' - redirect to edit
            return redirect("crush_lu:edit_profile")
    except CrushProfile.DoesNotExist:
        # No profile yet - show creation form
        from .social_photos import get_all_social_photos

        form = CrushProfileForm()
        return render(
            request,
            "crush_lu/create_profile.html",
            {
                "form": form,
                "profile": None,  # No profile yet, phone verification UI will show as not verified
                "social_photos": get_all_social_photos(request.user),
            },
        )


def _render_edit_profile_form(request):
    """Internal: Render single-page edit form for approved profiles.

    This is called by edit_profile() for approved profiles.
    Not exposed as a separate URL - use edit_profile() instead.
    """
    # Get existing profile
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        messages.info(request, _("You need to create a profile first."))
        return redirect("crush_lu:create_profile")

    # Only approved profiles use this simple edit page
    if not profile.is_approved:
        messages.warning(request, _("Your profile must be approved before editing."))
        return redirect("crush_lu:edit_profile")

    from .social_photos import get_all_social_photos

    if request.method == "POST":
        form = CrushProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            # Phone protection is handled at model level in CrushProfile.save()
            updated_profile = form.save()

            # HTMX: Return success partial without page reload
            if request.htmx:
                return render(
                    request,
                    "crush_lu/partials/edit_profile_success.html",
                    {
                        "profile": updated_profile,
                    },
                )

            messages.success(request, _("Profile updated successfully!"))
            return redirect("crush_lu:dashboard")
        else:
            # HTMX: Return form with errors for inline display
            if request.htmx:
                return render(
                    request,
                    "crush_lu/partials/edit_profile_form.html",
                    {
                        "form": form,
                        "profile": profile,
                        "social_photos": get_all_social_photos(request.user),
                        "has_errors": True,
                    },
                )

            # Traditional form: show validation errors via messages
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(
                        request, f"{field.replace('_', ' ').title()}: {error}"
                    )
    else:
        form = CrushProfileForm(instance=profile)

    context = {
        "form": form,
        "profile": profile,
        "social_photos": get_all_social_photos(request.user),
    }
    return render(request, "crush_lu/edit_profile.html", context)


@crush_login_required
def edit_profile(request):
    """Edit existing profile - routes to appropriate edit flow"""
    # Try to get existing profile, redirect to create if doesn't exist
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        messages.info(request, _("You need to create a profile first."))
        return redirect("crush_lu:create_profile")

    # ROUTING LOGIC: Determine which edit flow to use

    # 1. If profile is approved → use simple single-page edit
    if profile.is_approved:
        return _render_edit_profile_form(request)

    # 2. If profile is submitted and under review → redirect to status page
    if profile.completion_status == "submitted":
        try:
            submission = ProfileSubmission.objects.filter(profile=profile).latest(
                "submitted_at"
            )
            # If pending or under review, can't edit
            if submission.status in ["pending", "under_review"]:
                messages.info(
                    request,
                    _(
                        "Your profile is currently under review. You'll be notified once it's approved."
                    ),
                )
                return redirect("crush_lu:profile_submitted")
            # If rejected or needs revision, redirect to create_profile with feedback context
            elif submission.status in ["rejected", "revision"]:
                messages.warning(
                    request,
                    _(
                        "Your profile needs updates. Please review the coach feedback below."
                    ),
                )
                return redirect("crush_lu:create_profile")
        except ProfileSubmission.DoesNotExist:
            pass

    # 3. Profile is incomplete (not submitted yet) → redirect to create_profile
    # This ensures the URL matches the wizard content being displayed
    if profile.completion_status in [
        "not_started",
        "step1",
        "step2",
        "step3",
        "completed",
    ]:
        messages.info(request, _("Please complete your profile to continue."))
        return redirect("crush_lu:create_profile")

    # 4. Default: Use multi-step form for any other edge cases
    if request.method == "POST":
        form = CrushProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            # Phone protection is handled at model level in CrushProfile.save()
            profile = form.save(commit=False)

            # Mark as submitted when completing the form
            profile.completion_status = "submitted"
            profile.save()

            # Create or update profile submission for coach review
            submission, created = ProfileSubmission.objects.get_or_create(
                profile=profile, defaults={"status": "pending"}
            )
            if created:
                submission.assign_coach()

                # Send push notification to assigned coach
                if submission.coach:
                    try:
                        notify_coach_new_submission(submission.coach, submission)
                        logger.info(
                            f"Coach push notification sent for submission {submission.id}"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send coach push notification: {e}")

                # Send confirmation and coach notification emails using consolidated helper
                send_profile_submission_notifications(
                    submission,
                    request,
                    add_message_func=lambda msg: messages.warning(request, msg),
                )

            messages.success(request, _("Profile submitted for review!"))
            return redirect("crush_lu:profile_submitted")
    else:
        form = CrushProfileForm(instance=profile)

    # Get latest submission for feedback context
    latest_submission = None
    try:
        latest_submission = ProfileSubmission.objects.filter(profile=profile).latest(
            "submitted_at"
        )
    except ProfileSubmission.DoesNotExist:
        pass

    # Determine which step to show based on submission status
    current_step_to_show = None
    if latest_submission and latest_submission.status in ["rejected", "revision"]:
        # For rejected profiles, start from Step 1 so they can review everything
        current_step_to_show = None  # This will default to step 1 in JavaScript
    elif profile.completion_status == "submitted":
        # If submitted but no rejection, default to None (step 1)
        current_step_to_show = None
    elif profile.completion_status == "not_started":
        # Brand new profiles (e.g., Facebook signup) that haven't completed any steps
        current_step_to_show = None  # Start at step 1
    elif profile.completion_status == "step1" and (
        not profile.date_of_birth or not profile.phone_number
    ):
        # For incomplete Step 1 profiles (missing required fields)
        # Phone number and date_of_birth are required for Step 1 completion
        current_step_to_show = None  # Start at step 1
    else:
        # Resume from where they left off for incomplete profiles
        current_step_to_show = profile.completion_status

    context = {
        "form": form,
        "profile": profile,
        "is_editing": True,
        "current_step": current_step_to_show,
        "submission": latest_submission,  # Pass submission for feedback display
    }
    return render(request, "crush_lu/create_profile.html", context)


@crush_login_required
def profile_submitted(request):
    """Confirmation page after profile submission"""
    try:
        profile = CrushProfile.objects.get(user=request.user)
        submission = ProfileSubmission.objects.filter(profile=profile).latest(
            "submitted_at"
        )
    except (CrushProfile.DoesNotExist, ProfileSubmission.DoesNotExist):
        messages.error(request, _("No profile submission found."))
        return redirect("crush_lu:create_profile")

    context = {
        "submission": submission,
    }
    return render(request, "crush_lu/profile_submitted.html", context)


def luxid_mockup_view(request):
    """Mockup view for LuxID integration demonstration (NOT PRODUCTION)

    This view displays a visual mockup of how the profile submission page would
    look for users who authenticate via LuxID. It demonstrates the value proposition
    of LuxID integration: skipping the screening call and faster approval times.

    This is for stakeholder presentations and negotiations only.

    Access restrictions:
    - Available on: localhost, test.crush.lu (staging)
    - Blocked on: crush.lu (production)
    """
    from django.http import Http404
    from django.conf import settings

    # Check if we're on production (not DEBUG, not test.* subdomain)
    host = request.META.get("HTTP_HOST", "").split(":")[0].lower()
    is_staging = host.startswith("test.")
    is_development = settings.DEBUG or host in ["localhost", "127.0.0.1"]

    # Block access on production
    if not is_development and not is_staging:
        raise Http404(
            "This mockup is only available on staging and development environments"
        )

    # Create sample context data for the mockup
    context = {
        "submission": {
            "status": "pending",
            "submitted_at": timezone.now() - timedelta(hours=2),
            "coach": None,
            "get_status_display": lambda: _("Pending Review"),
        }
    }
    return render(request, "crush_lu/profile_submitted_luxid_mockup.html", context)


def luxid_auth_mockup_view(request):
    """Mockup view for LuxID login/signup integration (NOT PRODUCTION)

    This view displays a visual mockup of the login/signup page with LuxID
    as an authentication provider. It demonstrates how LuxID would appear
    alongside existing social login options (Google, Facebook, Microsoft).

    Key features shown:
    - LuxID button with Fast Track badge
    - Benefits callout (government-verified, skip screening, faster approval)
    - LuxID branding (rainbow gradient)
    - Hero banner highlighting LuxID integration

    This is for stakeholder presentations and negotiations only.

    Access restrictions:
    - Available on: localhost, test.crush.lu (staging)
    - Blocked on: crush.lu (production)
    """
    from django.http import Http404
    from django.conf import settings
    from .forms import CrushSignupForm
    from allauth.account.forms import LoginForm

    # Check if we're on production (not DEBUG, not test.* subdomain)
    host = request.META.get("HTTP_HOST", "").split(":")[0].lower()
    is_staging = host.startswith("test.")
    is_development = settings.DEBUG or host in ["localhost", "127.0.0.1"]

    # Block access on production
    if not is_development and not is_staging:
        raise Http404(
            "This mockup is only available on staging and development environments"
        )

    # Create context data for the mockup (similar to UnifiedAuthView)
    context = {
        "signup_form": CrushSignupForm(),
        "login_form": LoginForm(),
        "mode": request.GET.get("mode", "login"),  # Allow switching via ?mode=signup
    }
    return render(request, "crush_lu/auth_luxid_mockup.html", context)


# User dashboard
@crush_login_required
def dashboard(request):
    """User dashboard - redirects ACTIVE coaches to their dashboard unless ?user_view=1"""
    # Check if user is an ACTIVE coach
    # Allow coaches to view their user dashboard via ?user_view=1 parameter
    user_view = request.GET.get("user_view") == "1"
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
        if not user_view:
            return redirect("crush_lu:coach_dashboard")
    except CrushCoach.DoesNotExist:
        # Either no coach record, or coach is inactive - show dating dashboard
        pass

    # Regular user dashboard
    try:
        profile = CrushProfile.objects.get(user=request.user)
        # Get latest submission status
        latest_submission = (
            ProfileSubmission.objects.filter(profile=profile)
            .order_by("-submitted_at")
            .first()
        )

        # Get user's event registrations
        registrations = (
            EventRegistration.objects.filter(user=request.user)
            .select_related("event")
            .order_by("-event__date_time")
        )

        # Get connection count
        connection_count = EventConnection.objects.filter(
            Q(requester=request.user) | Q(recipient=request.user),
            status__in=["accepted", "coach_reviewing", "coach_approved", "shared"],
        ).count()

        # Check PWA status from UserActivity model (not CrushProfile)
        is_pwa_user = False
        try:
            activity = UserActivity.objects.filter(user=request.user).first()
            if activity:
                is_pwa_user = activity.is_pwa_user
        except Exception:
            pass
        # Get or create referral code for this user's profile
        from .models import ReferralCode
        from .referrals import build_referral_url

        referral_code = ReferralCode.get_or_create_for_profile(profile)
        referral_url = build_referral_url(referral_code.code, request=request)

        context = {
            "profile": profile,
            "submission": latest_submission,
            "registrations": registrations,
            "connection_count": connection_count,
            "is_pwa_user": is_pwa_user,
            "referral_url": referral_url,
        }
    except CrushProfile.DoesNotExist:
        messages.warning(request, _("Please complete your profile first."))
        return redirect("crush_lu:create_profile")

    return render(request, "crush_lu/dashboard.html", context)


# Membership program page
def membership(request):
    """
    Membership program landing page.
    Public access for viewing, login required for wallet actions.
    Explains membership tiers, how to earn points, and PWA benefits.
    """
    is_pwa_user = False
    profile = None
    referral_url = None

    if request.user.is_authenticated:
        # Check if user has installed PWA
        try:
            activity = UserActivity.objects.get(user=request.user)
            is_pwa_user = activity.is_pwa_user
        except UserActivity.DoesNotExist:
            pass

        # Get profile and referral URL if available
        try:
            profile = CrushProfile.objects.get(user=request.user)
            from .models import ReferralCode
            from .referrals import build_referral_url

            referral_code = ReferralCode.get_or_create_for_profile(profile)
            referral_url = build_referral_url(referral_code.code, request=request)
        except CrushProfile.DoesNotExist:
            pass

    # Membership tier data
    tiers = [
        {
            "name": _("Basic"),
            "key": "basic",
            "points": 0,
            "emoji": "💜",
            "benefits": [
                _("Access to public events"),
                _("Basic profile features"),
                _("Connection messaging"),
            ],
        },
        {
            "name": _("Bronze"),
            "key": "bronze",
            "points": 100,
            "emoji": "🥉",
            "benefits": [
                _("All Basic benefits"),
                _("Priority event registration"),
                _("Profile badge"),
            ],
        },
        {
            "name": _("Silver"),
            "key": "silver",
            "points": 500,
            "emoji": "🥈",
            "benefits": [
                _("All Bronze benefits"),
                _("Exclusive events access"),
                _("Extended profile features"),
            ],
        },
        {
            "name": _("Gold"),
            "key": "gold",
            "points": 1000,
            "emoji": "🥇",
            "benefits": [
                _("All Silver benefits"),
                _("VIP event access"),
                _("Personal coach session"),
            ],
        },
    ]

    context = {
        "is_pwa_user": is_pwa_user,
        "profile": profile,
        "referral_url": referral_url,
        "tiers": tiers,
        "current_tier": profile.membership_tier if profile else "basic",
        "current_points": profile.referral_points if profile else 0,
    }

    return render(request, "crush_lu/membership.html", context)


# Wallet
@crush_login_required
def wallet_apple_pass(request):
    """Redirect to the Apple Wallet pass URL if configured."""
    pass_url = getattr(settings, "APPLE_WALLET_PASS_URL", None)
    if pass_url:
        return redirect(pass_url)
    messages.error(
        request, _("Membership card is not available yet. Please try again later.")
    )
    return redirect("crush_lu:dashboard")


@crush_login_required
def wallet_google_save(request):
    """Redirect to the Google Wallet Save URL (JWT) if configured."""
    save_url = getattr(settings, "GOOGLE_WALLET_SAVE_URL", None)
    if save_url:
        return redirect(save_url)
    messages.error(
        request, _("Membership card is not available yet. Please try again later.")
    )
    return redirect("crush_lu:dashboard")


# Events
def event_list(request):
    """List of upcoming events - filters private invitation events"""
    # Base query: published, non-cancelled, future events
    events = MeetupEvent.objects.filter(
        is_published=True, is_cancelled=False, date_time__gte=timezone.now()
    ).order_by("date_time")

    # FILTER OUT PRIVATE EVENTS for non-invited users
    if request.user.is_authenticated:
        # Check if user has approved EventInvitation (external guests)
        user_invitations = EventInvitation.objects.filter(
            created_user=request.user, approval_status="approved"
        ).values_list("event_id", flat=True)

        # Show: public events + private events they're invited to (either as existing user OR external guest)
        events = events.filter(
            Q(is_private_invitation=False)  # Public events
            | Q(
                id__in=user_invitations
            )  # Private events with approved external invitation
            | Q(
                invited_users=request.user
            )  # Private events where they're invited as existing user
        )
    else:
        # Public visitors: only see public events
        events = events.filter(is_private_invitation=False)

    # Filter by event type if provided
    event_type = request.GET.get("type")
    if event_type:
        events = events.filter(event_type=event_type)

    # For coaches: show unpublished events count
    unpublished_count = 0
    if request.user.is_authenticated:
        try:
            coach = CrushCoach.objects.get(user=request.user, is_active=True)
            unpublished_count = MeetupEvent.objects.filter(
                is_published=False, is_cancelled=False, date_time__gte=timezone.now()
            ).count()
        except CrushCoach.DoesNotExist:
            pass

    context = {
        "events": events,
        "event_types": MeetupEvent.EVENT_TYPE_CHOICES,
        "unpublished_count": unpublished_count,
    }
    return render(request, "crush_lu/event_list.html", context)


def event_detail(request, event_id):
    """Event detail page with access control for private events"""
    event = get_object_or_404(MeetupEvent, id=event_id, is_published=True)

    # ACCESS CONTROL for private invitation events
    if event.is_private_invitation:
        if not request.user.is_authenticated:
            messages.error(
                request, _("This is a private invitation-only event. Please log in.")
            )
            return redirect("crush_lu:crush_login")

        # Check if user has approved external guest invitation OR is invited as existing user
        has_external_invitation = EventInvitation.objects.filter(
            event=event, created_user=request.user, approval_status="approved"
        ).exists()

        is_invited_existing_user = event.invited_users.filter(
            id=request.user.id
        ).exists()

        if not has_external_invitation and not is_invited_existing_user:
            messages.error(request, _("You do not have access to this private event."))
            return redirect("crush_lu:event_list")

    # Check if user is registered (exclude cancelled registrations)
    user_registration = None
    if request.user.is_authenticated:
        user_registration = EventRegistration.objects.filter(
            event=event, user=request.user
        ).exclude(status='cancelled').first()

    # Get user profile for template registration checks
    user_profile = None
    if request.user.is_authenticated:
        user_profile = CrushProfile.objects.filter(user=request.user).first()

    context = {
        "event": event,
        "user_registration": user_registration,
        "user_profile": user_profile,
    }
    return render(request, "crush_lu/event_detail.html", context)


def event_calendar_download(request, event_id):
    """Generate .ics calendar file for event"""
    event = get_object_or_404(MeetupEvent, id=event_id, is_published=True)

    # Calculate end time
    from datetime import timedelta, timezone as dt_timezone
    end_time = event.date_time + timedelta(minutes=event.duration_minutes)

    # Format dates for iCalendar (YYYYMMDDTHHMMSSZ in UTC)
    # Convert to UTC for iCalendar format
    start_utc = event.date_time.astimezone(dt_timezone.utc)
    end_utc = end_time.astimezone(dt_timezone.utc)

    # Format: YYYYMMDDTHHMMSSZ
    dtstart = start_utc.strftime('%Y%m%dT%H%M%SZ')
    dtend = end_utc.strftime('%Y%m%dT%H%M%SZ')
    dtstamp = timezone.now().astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')

    # Build location string
    if request.user.is_authenticated and hasattr(request.user, 'crushprofile'):
        location = f"{event.location}, {event.address}"
    else:
        location = event.canton or "Luxembourg"

    # Build event URL
    event_url = request.build_absolute_uri(
        reverse('crush_lu:event_detail', kwargs={'event_id': event.id})
    )

    # Clean description (remove newlines, escape special chars)
    description = event.description.replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;')

    # Generate unique UID
    uid = f"event-{event.id}@crush.lu"

    # Build iCalendar content
    ics_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Crush.lu//Event Calendar//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{dtstamp}
DTSTART:{dtstart}
DTEND:{dtend}
SUMMARY:{event.title}
DESCRIPTION:{description}\\n\\nRegister: {event_url}
LOCATION:{location}
URL:{event_url}
STATUS:CONFIRMED
SEQUENCE:0
END:VEVENT
END:VCALENDAR"""

    # Return as downloadable file
    response = HttpResponse(ics_content, content_type='text/calendar; charset=utf-8')
    filename = f"crush-event-{event.id}.ics"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Cache-Control'] = 'no-cache'

    return response


@crush_login_required
def event_register(request, event_id):
    """Register for an event - bypasses approval for invited guests"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # FOR PRIVATE INVITATION EVENTS: Bypass normal profile approval flow
    if event.is_private_invitation:
        # Check if user is invited as existing user OR has approved external invitation
        is_invited_existing_user = event.invited_users.filter(
            id=request.user.id
        ).exists()

        external_invitation = EventInvitation.objects.filter(
            event=event, created_user=request.user, approval_status="approved"
        ).first()

        if not is_invited_existing_user and not external_invitation:
            messages.error(
                request, _("You do not have an approved invitation for this event.")
            )
            return redirect("crush_lu:event_detail", event_id=event_id)

        # EXISTING USERS: No profile creation needed - use their existing profile
        if is_invited_existing_user:
            try:
                profile = CrushProfile.objects.get(user=request.user)
                # Existing users keep their own profile approval status
            except CrushProfile.DoesNotExist:
                # SECURITY FIX: Redirect to profile creation instead of auto-creating
                # This ensures proper age verification and data collection
                messages.warning(
                    request,
                    _(
                        "Please complete your profile before registering for events. "
                        "This is required for all users, even with invitations."
                    ),
                )
                return redirect("crush_lu:create_profile")

        # EXTERNAL GUESTS: Must have profile from invitation acceptance
        else:
            try:
                profile = CrushProfile.objects.get(user=request.user)
                # External guests already have profile created during invitation acceptance
                # with proper age verification and date of birth
            except CrushProfile.DoesNotExist:
                # SECURITY: This should never happen - external guests must accept invitation first
                # which creates their profile with age verification
                logger.error(
                    f"Security issue: External guest {request.user.email} trying to register "
                    f"without profile. Invitation ID: {external_invitation.id if external_invitation else 'None'}"
                )
                messages.error(
                    request,
                    _(
                        "Your profile is missing. Please contact support for assistance."
                    ),
                )
                return redirect("crush_lu:event_detail", event_id=event_id)
    else:
        # NORMAL EVENT: Check profile requirements based on event settings
        if event.require_approved_profile:
            # Strict events: Require approved profile
            try:
                profile = CrushProfile.objects.get(user=request.user)
                if not profile.is_approved:
                    messages.error(
                        request,
                        _(
                            "This event requires an approved profile. Your profile is currently under review."
                        ),
                    )
                    return redirect("crush_lu:event_detail", event_id=event_id)
            except CrushProfile.DoesNotExist:
                messages.error(
                    request,
                    _("This event requires a Crush profile. Please create one to register.")
                )
                return redirect("crush_lu:create_profile")
        else:
            # Open events: Profile optional, but check if exists for later use
            try:
                profile = CrushProfile.objects.get(user=request.user)
            except CrushProfile.DoesNotExist:
                profile = None  # No profile needed - proceed with registration

    # Age verification: Events with age restrictions require profile for verification
    if profile is None and (event.min_age > 18 or event.max_age < 99):
        messages.error(
            request,
            _("This event has age restrictions. Please create a profile to verify your age.")
        )
        return redirect("crush_lu:create_profile")

    # Check if already registered (exclude cancelled registrations)
    if EventRegistration.objects.filter(event=event, user=request.user).exclude(status='cancelled').exists():
        messages.warning(request, _("You are already registered for this event."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Check if registration is open
    if not event.is_registration_open:
        messages.error(request, _("Registration is not available for this event."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    if request.method == "POST":
        form = EventRegistrationForm(request.POST, event=event)
        if form.is_valid():
            # Check if there's a cancelled registration to reactivate
            cancelled_registration = EventRegistration.objects.filter(
                event=event, user=request.user, status='cancelled'
            ).first()

            if cancelled_registration:
                # Reactivate the cancelled registration
                registration = cancelled_registration
                registration.dietary_restrictions = form.cleaned_data.get('dietary_restrictions', '')
                registration.bringing_guest = form.cleaned_data.get('bringing_guest', False)
                registration.guest_name = form.cleaned_data.get('guest_name', '')
            else:
                # Create new registration
                registration = form.save(commit=False)
                registration.event = event
                registration.user = request.user

            # Set status based on availability
            if event.is_full:
                registration.status = "waitlist"
                messages.info(
                    request, _("Event is full. You have been added to the waitlist.")
                )
            else:
                registration.status = "confirmed"
                messages.success(request, _("Successfully registered for the event!"))

            registration.save()

            # Send confirmation or waitlist email
            try:
                if registration.status == "confirmed":
                    send_event_registration_confirmation(registration, request)
                elif registration.status == "waitlist":
                    send_event_waitlist_notification(registration, request)
            except Exception as e:
                logger.error(f"Failed to send event registration email: {e}")

            # Return HTMX partial or redirect
            if request.headers.get("HX-Request"):
                return render(
                    request,
                    "crush_lu/_event_registration_success.html",
                    {
                        "event": event,
                        "registration": registration,
                    },
                )
            return redirect("crush_lu:dashboard")
        else:
            # Form invalid - for HTMX, re-render the form with errors
            if request.headers.get("HX-Request"):
                return render(
                    request,
                    "crush_lu/_event_registration_form.html",
                    {
                        "event": event,
                        "form": form,
                    },
                )
    else:
        form = EventRegistrationForm(event=event)

    context = {
        "event": event,
        "form": form,
    }
    return render(request, "crush_lu/event_register.html", context)


@crush_login_required
def event_cancel(request, event_id):
    """Cancel event registration"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    registration = get_object_or_404(EventRegistration, event=event, user=request.user)

    if request.method == "POST":
        registration.status = "cancelled"
        registration.save()
        messages.success(request, _("Your registration has been cancelled."))

        # Send cancellation confirmation email
        try:
            send_event_cancellation_confirmation(request.user, event, request)
        except Exception as e:
            logger.error(f"Failed to send event cancellation confirmation: {e}")

        return redirect("crush_lu:dashboard")

    context = {
        "event": event,
        "registration": registration,
    }
    return render(request, "crush_lu/event_cancel.html", context)


# Coach views
@crush_login_required
def coach_dashboard(request):
    """Coach dashboard for reviewing profiles"""
    try:
        coach = CrushCoach.objects.get(user=request.user)
        if not coach.is_active:
            messages.error(
                request,
                _(
                    "Your coach account has been deactivated. Please contact an administrator."
                ),
            )
            return redirect("crush_lu:dashboard")
    except CrushCoach.DoesNotExist:
        messages.error(request, _("You do not have coach access."))
        return redirect("crush_lu:dashboard")

    # Get pending submissions assigned to this coach, ordered by oldest first
    pending_submissions = (
        ProfileSubmission.objects.filter(coach=coach, status="pending")
        .select_related("profile__user")
        .order_by("submitted_at")
    )

    # Calculate wait time and urgency for each submission
    now = timezone.now()
    for submission in pending_submissions:
        hours_waiting = (now - submission.submitted_at).total_seconds() / 3600
        submission.is_urgent = hours_waiting > 48  # Red: > 48 hours
        submission.is_warning = 24 < hours_waiting <= 48  # Yellow: 24-48 hours

    # Split by gender: Women (F), Men (M), Other (NB, O, P)
    pending_women = [s for s in pending_submissions if s.profile.gender == "F"]
    pending_men = [s for s in pending_submissions if s.profile.gender == "M"]
    pending_other = [
        s for s in pending_submissions if s.profile.gender in ["NB", "O", "P", ""]
    ]

    # Get recently reviewed
    recent_reviews = (
        ProfileSubmission.objects.filter(
            coach=coach, status__in=["approved", "rejected", "revision"]
        )
        .select_related("profile__user")
        .order_by("-reviewed_at")[:10]
    )

    # Note: Coach push notifications are now managed in Account Settings
    # (see account_settings view for coach push subscription handling)

    context = {
        "coach": coach,
        "pending_submissions": pending_submissions,
        "pending_women": pending_women,
        "pending_men": pending_men,
        "pending_other": pending_other,
        "recent_reviews": recent_reviews,
    }
    return render(request, "crush_lu/coach_dashboard.html", context)


@crush_login_required
@require_http_methods(["POST"])
def coach_mark_review_call_complete(request, submission_id):
    """Mark screening call as complete during profile review"""
    is_htmx = request.headers.get("HX-Request")

    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
    except CrushCoach.DoesNotExist:
        if is_htmx:
            return render(
                request,
                "crush_lu/_htmx_error.html",
                {
                    "message": _("You do not have coach access."),
                    "target_id": "screening-call-section",
                },
            )
        messages.error(request, _("You do not have coach access."))
        return redirect("crush_lu:dashboard")

    # Handle submission not found or not assigned to this coach
    # Use select_related to prefetch profile and user in single query (reduces latency)
    try:
        submission = ProfileSubmission.objects.select_related(
            "profile", "profile__user"
        ).get(id=submission_id, coach=coach)
    except ProfileSubmission.DoesNotExist:
        if is_htmx:
            return render(
                request,
                "crush_lu/_htmx_error.html",
                {
                    "message": _("Submission not found or not assigned to you."),
                    "target_id": "screening-call-section",
                },
            )
        messages.error(request, _("Submission not found."))
        return redirect("crush_lu:coach_dashboard")

    submission.review_call_completed = True
    submission.review_call_date = timezone.now()
    submission.review_call_notes = request.POST.get("call_notes", "")

    # Parse and save checklist data
    checklist_data_str = request.POST.get("checklist_data", "{}")
    try:
        checklist_data = json.loads(checklist_data_str) if checklist_data_str else {}
    except json.JSONDecodeError:
        checklist_data = {}
    submission.review_call_checklist = checklist_data

    # Only update specific fields (faster than full model save)
    submission.save(
        update_fields=[
            "review_call_completed",
            "review_call_date",
            "review_call_notes",
            "review_call_checklist",
        ]
    )

    # Return HTMX partial or redirect
    if is_htmx:
        return render(
            request,
            "crush_lu/_screening_call_section.html",
            {
                "submission": submission,
                "profile": submission.profile,
            },
        )

    messages.success(
        request,
        f"Screening call marked complete for {submission.profile.user.first_name}. You can now approve the profile.",
    )
    return redirect("crush_lu:coach_review_profile", submission_id=submission.id)


@crush_login_required
def coach_log_failed_call(request, submission_id):
    """Log a failed call attempt - HTMX endpoint"""
    from .models import CallAttempt
    from .forms import CallAttemptForm

    # Verify coach access
    try:
        coach = CrushCoach.objects.get(user=request.user)
        if not coach.is_active:
            messages.error(request, _("Your coach account has been deactivated."))
            return redirect("crush_lu:dashboard")
    except CrushCoach.DoesNotExist:
        messages.error(request, _("You do not have coach access."))
        return redirect("crush_lu:dashboard")

    submission = get_object_or_404(
        ProfileSubmission.objects.select_related("profile__user"),
        id=submission_id,
        coach=coach,
    )

    if request.method == "POST":
        form = CallAttemptForm(request.POST)
        if form.is_valid():
            # Create failed call attempt
            attempt = form.save(commit=False)
            attempt.submission = submission
            attempt.result = "failed"
            attempt.coach = coach
            attempt.save()

            messages.success(request, _("Failed call attempt logged."))

            # Return updated screening section via HTMX
            if request.headers.get("HX-Request"):
                context = {
                    "submission": submission,
                    "profile": submission.profile,
                }
                return render(request, "crush_lu/_screening_call_section.html", context)

            return redirect(
                "crush_lu:coach_review_profile", submission_id=submission.id
            )

    # For GET or invalid POST, return form
    context = {
        "submission": submission,
        "profile": submission.profile,
        "form": CallAttemptForm(),
    }

    if request.headers.get("HX-Request"):
        return render(request, "crush_lu/_call_attempt_form.html", context)

    return redirect("crush_lu:coach_review_profile", submission_id=submission.id)


@crush_login_required
def coach_review_profile(request, submission_id):
    """Review a profile submission"""
    try:
        coach = CrushCoach.objects.get(user=request.user)
        if not coach.is_active:
            messages.error(request, _("Your coach account has been deactivated."))
            return redirect("crush_lu:dashboard")
    except CrushCoach.DoesNotExist:
        messages.error(request, _("You do not have coach access."))
        return redirect("crush_lu:dashboard")

    submission = get_object_or_404(ProfileSubmission, id=submission_id, coach=coach)

    if request.method == "POST":
        form = ProfileReviewForm(request.POST, instance=submission)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.reviewed_at = timezone.now()

            # Update profile approval status and send notifications
            if submission.status == "approved":
                # REQUIRE screening call before approval
                if not submission.review_call_completed:
                    messages.error(
                        request,
                        _(
                            "You must complete a screening call before approving this profile."
                        ),
                    )
                    form = ProfileReviewForm(instance=submission)
                    context = {
                        "coach": coach,
                        "submission": submission,
                        "form": form,
                    }
                    return render(
                        request, "crush_lu/coach_review_profile.html", context
                    )
                submission.profile.is_approved = True
                submission.profile.approved_at = timezone.now()
                submission.profile.save()
                messages.success(request, _("Profile approved!"))

                # Send approval notification to user (push first, email fallback)
                try:
                    result = notify_profile_approved(
                        user=submission.profile.user,
                        profile=submission.profile,
                        coach_notes=submission.feedback_to_user,
                        request=request,
                    )
                    if result.any_delivered:
                        logger.info(
                            f"Profile approval notification sent: push={result.push_success}, email={result.email_sent}"
                        )
                except Exception as e:
                    logger.error(f"Failed to send profile approval notification: {e}")

            elif submission.status == "rejected":
                submission.profile.is_approved = False
                submission.profile.save()
                messages.info(request, _("Profile rejected."))

                # Send rejection notification to user (push first, email fallback)
                try:
                    result = notify_profile_rejected(
                        user=submission.profile.user,
                        profile=submission.profile,
                        feedback=submission.feedback_to_user,
                        request=request,
                    )
                    if result.any_delivered:
                        logger.info(
                            f"Profile rejection notification sent: push={result.push_success}, email={result.email_sent}"
                        )
                except Exception as e:
                    logger.error(f"Failed to send profile rejection notification: {e}")

            elif submission.status == "revision":
                messages.info(request, _("Revision requested."))

                # Send revision request to user (push first, email fallback)
                try:
                    result = notify_profile_revision(
                        user=submission.profile.user,
                        profile=submission.profile,
                        feedback=submission.feedback_to_user,
                        request=request,
                    )
                    if result.any_delivered:
                        logger.info(
                            f"Profile revision notification sent: push={result.push_success}, email={result.email_sent}"
                        )
                except Exception as e:
                    logger.error(f"Failed to send profile revision request: {e}")

            elif submission.status == "recontact_coach":
                messages.info(request, _("User asked to recontact coach."))

                # Send notification to user
                try:
                    from .notification_service import notify_profile_recontact

                    result = notify_profile_recontact(
                        user=submission.profile.user,
                        profile=submission.profile,
                        coach=coach,
                        request=request,
                    )
                    if result.any_delivered:
                        logger.info(
                            f"Recontact notification sent: push={result.push_success}, email={result.email_sent}"
                        )
                except Exception as e:
                    logger.error(f"Failed to send recontact notification: {e}")

            submission.save()
            return redirect("crush_lu:coach_dashboard")
    else:
        form = ProfileReviewForm(instance=submission)

    # Get social login provider if exists
    social_account = submission.profile.user.socialaccount_set.first()

    context = {
        "submission": submission,
        "profile": submission.profile,
        "form": form,
        "social_account": social_account,
    }
    return render(request, "crush_lu/coach_review_profile.html", context)


@crush_login_required
def coach_preview_email(request, submission_id):
    """Preview the email that will be sent for a review decision"""
    import traceback
    from django.utils import translation
    from django.utils.translation import gettext as _
    from django.http import HttpResponse
    from .utils.i18n import get_user_preferred_language
    from .email_helpers import (
        get_email_context_with_unsubscribe,
        get_email_base_urls,
        get_user_language_url,
    )
    from django.template.loader import render_to_string

    # Wrap entire function in try-except to catch any errors
    try:
        try:
            coach = CrushCoach.objects.get(user=request.user)
            if not coach.is_active:
                return HttpResponse("Coach account deactivated", status=403)
        except CrushCoach.DoesNotExist:
            return HttpResponse("Not a coach", status=403)

        submission = get_object_or_404(ProfileSubmission, id=submission_id, coach=coach)

        # Get parameters from request
        status = request.GET.get("status", "")
        feedback = request.GET.get("feedback_to_user", "")
        coach_notes = request.GET.get("coach_notes", "")

        profile = submission.profile
        user = profile.user

        # Get user's preferred language
        lang = get_user_preferred_language(user=user, request=request, default="en")

        # If no valid status selected, show a helpful message
        if not status or status == "pending":
            preview_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Email Preview</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    margin: 0;
                    padding: 40px;
                    background: #f3f4f6;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                }
                .message {
                    background: white;
                    padding: 32px;
                    border-radius: 12px;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                    text-align: center;
                    max-width: 500px;
                }
                .message h2 {
                    margin: 0 0 16px 0;
                    color: #6366f1;
                    font-size: 24px;
                }
                .message p {
                    margin: 0;
                    color: #6b7280;
                    line-height: 1.6;
                }
            </style>
        </head>
        <body>
            <div class="message">
                <h2>📧 No Preview Available</h2>
                <p>Please select a decision (Approved, Rejected, Revision Requested, or Recontact Coach) to preview the email that will be sent.</p>
            </div>
        </body>
        </html>
            """
            response = HttpResponse(preview_html, content_type="text/html")
            response["X-Frame-Options"] = "SAMEORIGIN"
            return response

        # Build context based on decision type
        if status == "approved":
            events_url = get_user_language_url(user, "crush_lu:event_list", request)
            context = get_email_context_with_unsubscribe(
                user,
                request,
                first_name=user.first_name,
                coach_notes=feedback or coach_notes,
                events_url=events_url,
            )
            template = "crush_lu/emails/profile_approved.html"
            with translation.override(lang):
                subject = _("Welcome to Crush.lu - Your Profile is Approved!")

        elif status == "rejected":
            base_urls = get_email_base_urls(user, request)
            context = {
                "user": user,
                "first_name": user.first_name,
                "reason": feedback,
                "LANGUAGE_CODE": lang,
                **base_urls,
            }
            template = "crush_lu/emails/profile_rejected.html"
            with translation.override(lang):
                subject = _("Profile Review Update - Crush.lu")

        elif status == "revision":
            edit_profile_url = get_user_language_url(
                user, "crush_lu:edit_profile", request
            )
            base_urls = get_email_base_urls(user, request)
            context = {
                "user": user,
                "first_name": user.first_name,
                "feedback": feedback,
                "edit_profile_url": edit_profile_url,
                "LANGUAGE_CODE": lang,
                **base_urls,
            }
            template = "crush_lu/emails/profile_revision_request.html"
            with translation.override(lang):
                subject = _("Profile Review Feedback - Crush.lu")

        elif status == "recontact_coach":
            base_urls = get_email_base_urls(user, request)
            context = {
                "user": user,
                "first_name": user.first_name,
                "coach": coach,
                "LANGUAGE_CODE": lang,
                **base_urls,
            }
            template = "crush_lu/emails/profile_recontact.html"
            with translation.override(lang):
                subject = _("Your Crush Coach Needs to Speak With You")
        else:
            return HttpResponse("Invalid status", status=400)

        # Render preview with language override
        try:
            with translation.override(lang):
                html_content = render_to_string(template, context)
        except Exception as e:
            logger.error(f"Error rendering email template: {e}")
            logger.error(traceback.format_exc())
            return HttpResponse(
                "An error occurred. Please try again later.",
                status=500,
            )

        # Wrap in preview container
        preview_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Email Preview</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                margin: 0;
                padding: 20px;
                background: #f3f4f6;
            }}
            .preview-header {{
                background: white;
                padding: 16px 24px;
                border-radius: 8px;
                margin-bottom: 16px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            }}
            .preview-header h2 {{
                margin: 0 0 8px 0;
                font-size: 18px;
                color: #111827;
            }}
            .preview-header p {{
                margin: 0;
                font-size: 14px;
                color: #6b7280;
            }}
            .email-container {{
                background: white;
                border-radius: 8px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
                overflow: hidden;
            }}
        </style>
    </head>
    <body>
        <div class="preview-header">
            <h2>📧 {subject}</h2>
            <p>Language: {lang.upper()} | To: {user.email}</p>
        </div>
        <div class="email-container">
            {html_content}
        </div>
    </body>
    </html>
    """

        response = HttpResponse(preview_html, content_type="text/html")
        response["X-Frame-Options"] = "SAMEORIGIN"
        return response

    except Exception as e:
        logger.error(f"Error in coach_preview_email: {e}")
        logger.error(traceback.format_exc())
        error_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Preview Error</title>
            <style>
                body {
                    font-family: monospace;
                    padding: 20px;
                    background: #fee;
                }
                .error {
                    background: white;
                    padding: 20px;
                    border: 2px solid #f00;
                    border-radius: 8px;
                }
                h2 { color: #c00; }
            </style>
        </head>
        <body>
            <div class="error">
                <h2>Preview Error</h2>
                <p>An error occurred while generating the email preview. Please check the server logs for details.</p>
            </div>
        </body>
        </html>
        """
        return HttpResponse(error_html, status=500)


@crush_login_required
def coach_sessions(request):
    """View and manage coach sessions"""
    try:
        coach = CrushCoach.objects.get(user=request.user)
        if not coach.is_active:
            messages.error(request, _("Your coach account has been deactivated."))
            return redirect("crush_lu:dashboard")
    except CrushCoach.DoesNotExist:
        messages.error(request, _("You do not have coach access."))
        return redirect("crush_lu:dashboard")

    sessions = CoachSession.objects.filter(coach=coach).order_by("-created_at")

    context = {
        "coach": coach,
        "sessions": sessions,
    }
    return render(request, "crush_lu/coach_sessions.html", context)


@crush_login_required
def coach_edit_profile(request):
    """Edit coach profile (bio, specializations, photo) - separate from dating profile"""
    try:
        coach = CrushCoach.objects.get(user=request.user)
        if not coach.is_active:
            messages.error(request, _("Your coach account has been deactivated."))
            return redirect("crush_lu:dashboard")
    except CrushCoach.DoesNotExist:
        messages.error(request, _("You do not have a coach profile."))
        return redirect("crush_lu:dashboard")

    if request.method == "POST":
        form = CrushCoachForm(request.POST, request.FILES, instance=coach)
        if form.is_valid():
            form.save()
            messages.success(request, _("Coach profile updated successfully!"))
            return redirect("crush_lu:coach_dashboard")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(
                        request, f"{field.replace('_', ' ').title()}: {error}"
                    )
    else:
        form = CrushCoachForm(instance=coach)

    # Check if coach also has a dating profile
    try:
        profile = request.user.crushprofile
        has_dating_profile = True
    except CrushProfile.DoesNotExist:
        has_dating_profile = False

    context = {
        "coach": coach,
        "form": form,
        "has_dating_profile": has_dating_profile,
    }
    return render(request, "crush_lu/coach_edit_profile.html", context)


# ============================================================================
# COACH JOURNEY MANAGEMENT - Manage Wonderland Journey Experiences
# ============================================================================


@crush_login_required
def coach_journey_dashboard(request):
    """Coach dashboard for managing all active journeys and their challenges"""
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
    except CrushCoach.DoesNotExist:
        messages.error(request, _("You do not have coach access."))
        return redirect("crush_lu:dashboard")

    from .models import JourneyConfiguration, JourneyProgress

    # Get all active journeys
    active_journeys = (
        JourneyConfiguration.objects.filter(is_active=True)
        .select_related("special_experience")
        .prefetch_related("chapters__challenges")
    )

    # Get user progress for each journey
    journeys_with_progress = []
    for journey in active_journeys:
        progress_list = (
            JourneyProgress.objects.filter(journey=journey)
            .select_related("user")
            .order_by("-last_activity")[:5]
        )

        journeys_with_progress.append(
            {
                "journey": journey,
                "recent_progress": progress_list,
                "total_users": JourneyProgress.objects.filter(journey=journey).count(),
                "completed_users": JourneyProgress.objects.filter(
                    journey=journey, is_completed=True
                ).count(),
            }
        )

    context = {
        "coach": coach,
        "journeys_with_progress": journeys_with_progress,
    }
    return render(request, "crush_lu/coach_journey_dashboard.html", context)


@crush_login_required
def coach_edit_journey(request, journey_id):
    """Edit a journey's chapters and challenges"""
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
    except CrushCoach.DoesNotExist:
        messages.error(request, _("You do not have coach access."))
        return redirect("crush_lu:dashboard")

    from .models import JourneyConfiguration

    journey = get_object_or_404(JourneyConfiguration, id=journey_id)

    # Get all chapters with challenges
    chapters = journey.chapters.all().prefetch_related("challenges", "rewards")

    context = {
        "coach": coach,
        "journey": journey,
        "chapters": chapters,
    }
    return render(request, "crush_lu/coach_edit_journey.html", context)


@crush_login_required
def coach_edit_challenge(request, challenge_id):
    """Edit an individual challenge's question and content"""
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
    except CrushCoach.DoesNotExist:
        messages.error(request, _("You do not have coach access."))
        return redirect("crush_lu:dashboard")

    from .models import JourneyChallenge, ChallengeAttempt

    challenge = get_object_or_404(JourneyChallenge, id=challenge_id)

    if request.method == "POST":
        # Update challenge fields
        challenge.question = request.POST.get("question", challenge.question)
        challenge.correct_answer = request.POST.get(
            "correct_answer", challenge.correct_answer
        )
        challenge.success_message = request.POST.get(
            "success_message", challenge.success_message
        )

        # Update hints
        challenge.hint_1 = request.POST.get("hint_1", challenge.hint_1)
        challenge.hint_2 = request.POST.get("hint_2", challenge.hint_2)
        challenge.hint_3 = request.POST.get("hint_3", challenge.hint_3)

        # Update points
        try:
            challenge.points_awarded = int(
                request.POST.get("points_awarded", challenge.points_awarded)
            )
            challenge.hint_1_cost = int(
                request.POST.get("hint_1_cost", challenge.hint_1_cost)
            )
            challenge.hint_2_cost = int(
                request.POST.get("hint_2_cost", challenge.hint_2_cost)
            )
            challenge.hint_3_cost = int(
                request.POST.get("hint_3_cost", challenge.hint_3_cost)
            )
        except ValueError:
            messages.error(request, _("Points must be valid numbers."))
            return redirect("crush_lu:coach_edit_challenge", challenge_id=challenge_id)

        challenge.save()
        messages.success(
            request, f'Challenge "{challenge.question[:50]}..." updated successfully!'
        )
        return redirect(
            "crush_lu:coach_edit_journey", journey_id=challenge.chapter.journey.id
        )

    # Get all user answers for this challenge
    all_attempts = (
        ChallengeAttempt.objects.filter(challenge=challenge)
        .select_related("chapter_progress__journey_progress__user")
        .order_by("-attempted_at")
    )

    context = {
        "coach": coach,
        "challenge": challenge,
        "all_attempts": all_attempts,
        "total_responses": all_attempts.count(),
    }
    return render(request, "crush_lu/coach_edit_challenge.html", context)


@crush_login_required
def coach_view_user_progress(request, progress_id):
    """View a specific user's journey progress and answers - Enhanced Report View"""
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
    except CrushCoach.DoesNotExist:
        messages.error(request, _("You do not have coach access."))
        return redirect("crush_lu:dashboard")

    from .models import (
        JourneyProgress,
        ChallengeAttempt,
        JourneyChapter,
        JourneyChallenge,
    )
    from collections import defaultdict

    progress = get_object_or_404(
        JourneyProgress.objects.select_related("user", "journey"), id=progress_id
    )

    # Get all chapters for this journey with their challenges
    chapters = (
        JourneyChapter.objects.filter(journey=progress.journey)
        .prefetch_related("challenges")
        .order_by("chapter_number")
    )

    # Get all challenge attempts for this journey
    all_attempts = (
        ChallengeAttempt.objects.filter(chapter_progress__journey_progress=progress)
        .select_related("challenge", "challenge__chapter", "chapter_progress__chapter")
        .order_by("attempted_at")
    )

    # Build a structured report: for each challenge, get the FINAL successful attempt
    # or the last attempt if none were successful
    challenge_results = {}  # challenge_id -> best attempt
    challenge_attempt_counts = defaultdict(int)  # challenge_id -> total attempts

    for attempt in all_attempts:
        challenge_id = attempt.challenge_id
        challenge_attempt_counts[challenge_id] += 1

        # Keep the attempt if:
        # 1. No attempt recorded yet for this challenge
        # 2. This attempt is correct (overrides incorrect)
        # 3. This attempt earned more points
        if challenge_id not in challenge_results:
            challenge_results[challenge_id] = attempt
        elif attempt.is_correct and not challenge_results[challenge_id].is_correct:
            challenge_results[challenge_id] = attempt
        elif attempt.points_earned > challenge_results[challenge_id].points_earned:
            challenge_results[challenge_id] = attempt

    # Build chapter data with challenges and results
    chapter_data = []
    total_challenges = 0
    completed_challenges = 0
    questionnaire_responses = []

    for chapter in chapters:
        chapter_info = {
            "chapter": chapter,
            "challenges": [],
            "chapter_points": 0,
            "is_completed": False,
        }

        # Check if chapter is completed
        chapter_progress = progress.chapter_completions.filter(chapter=chapter).first()
        if chapter_progress:
            chapter_info["is_completed"] = chapter_progress.is_completed
            chapter_info["chapter_points"] = chapter_progress.points_earned

        for challenge in chapter.challenges.all():
            total_challenges += 1
            result = challenge_results.get(challenge.id)
            attempt_count = challenge_attempt_counts.get(challenge.id, 0)

            # Determine if this is a questionnaire challenge (no correct answer)
            is_questionnaire = (
                not challenge.correct_answer
                or challenge.challenge_type in ["open_text", "would_you_rather"]
            )

            # Parse the user's answer for multiple choice to show the full option text
            display_answer = None
            if result:
                completed_challenges += 1
                display_answer = result.user_answer

                # For multiple choice, map the letter to the full option
                if challenge.challenge_type == "multiple_choice" and challenge.options:
                    answer_key = result.user_answer.strip().upper()
                    if answer_key in challenge.options:
                        display_answer = (
                            f"{answer_key}: {challenge.options[answer_key]}"
                        )

                # For timeline sorting, show as readable list
                if challenge.challenge_type == "timeline_sort" and challenge.options:
                    try:
                        order = result.user_answer.split(",")
                        items = challenge.options.get("items", [])
                        if items:
                            display_answer = [
                                items[int(i)]
                                for i in order
                                if i.strip().isdigit() and int(i) < len(items)
                            ]
                    except (ValueError, IndexError):
                        pass

                # Collect questionnaire responses for insights section
                if is_questionnaire and result.user_answer:
                    questionnaire_responses.append(
                        {
                            "chapter": chapter,
                            "challenge": challenge,
                            "answer": result.user_answer,
                            "display_answer": display_answer,
                        }
                    )

            challenge_info = {
                "challenge": challenge,
                "result": result,
                "attempt_count": attempt_count,
                "is_questionnaire": is_questionnaire,
                "display_answer": display_answer,
                "options": challenge.options,
            }
            chapter_info["challenges"].append(challenge_info)

        chapter_data.append(chapter_info)

    # Calculate journey statistics
    journey_duration = None
    if progress.started_at and progress.final_response_at:
        journey_duration = progress.final_response_at - progress.started_at

    stats = {
        "total_challenges": total_challenges,
        "completed_challenges": completed_challenges,
        "total_attempts": sum(challenge_attempt_counts.values()),
        "avg_attempts_per_challenge": round(
            sum(challenge_attempt_counts.values()) / max(completed_challenges, 1), 1
        ),
        "journey_duration": journey_duration,
        "hardest_challenge": (
            max(challenge_attempt_counts.items(), key=lambda x: x[1])
            if challenge_attempt_counts
            else None
        ),
    }

    # Find the hardest challenge details
    if stats["hardest_challenge"]:
        hardest_id = stats["hardest_challenge"][0]
        hardest_challenge = JourneyChallenge.objects.filter(id=hardest_id).first()
        stats["hardest_challenge_obj"] = hardest_challenge
        stats["hardest_challenge_attempts"] = stats["hardest_challenge"][1]

    context = {
        "coach": coach,
        "progress": progress,
        "chapter_data": chapter_data,
        "stats": stats,
        "questionnaire_responses": questionnaire_responses,
        "all_attempts": all_attempts,  # Keep for backward compatibility
    }
    return render(request, "crush_lu/coach_view_user_progress.html", context)


# Post-Event Connection Views
@crush_login_required
def event_attendees(request, event_id):
    """Show attendees after user has attended event - allows connection requests"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user attended this event
    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user
    )

    if not user_registration.can_make_connections:
        messages.error(
            request, _("You must attend this event before making connections.")
        )
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Get other attendees (status='attended')
    attendees = (
        EventRegistration.objects.filter(event=event, status="attended")
        .exclude(user=request.user)
        .select_related("user__crushprofile")
    )

    # Get user's existing connection requests for this event
    sent_requests = EventConnection.objects.filter(
        requester=request.user, event=event
    ).values_list("recipient_id", flat=True)

    received_requests = EventConnection.objects.filter(
        recipient=request.user, event=event
    ).values_list("requester_id", flat=True)

    # Annotate attendees with connection status
    attendee_data = []
    for reg in attendees:
        attendee_user = reg.user
        connection_status = None
        connection_id = None

        if attendee_user.id in sent_requests:
            connection = EventConnection.objects.get(
                requester=request.user, recipient=attendee_user, event=event
            )
            connection_status = "sent"
            connection_id = connection.id
        elif attendee_user.id in received_requests:
            connection = EventConnection.objects.get(
                requester=attendee_user, recipient=request.user, event=event
            )
            connection_status = "received"
            connection_id = connection.id

        attendee_data.append(
            {
                "user": attendee_user,
                "profile": getattr(attendee_user, "crushprofile", None),
                "connection_status": connection_status,
                "connection_id": connection_id,
            }
        )

    context = {
        "event": event,
        "attendees": attendee_data,
    }
    return render(request, "crush_lu/event_attendees.html", context)


@crush_login_required
def request_connection(request, event_id, user_id):
    """Request connection with another event attendee"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    recipient = get_object_or_404(CrushProfile, user_id=user_id).user

    # Verify requester attended the event
    requester_reg = get_object_or_404(EventRegistration, event=event, user=request.user)

    if not requester_reg.can_make_connections:
        messages.error(
            request, _("You must attend this event before making connections.")
        )
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Verify recipient attended the event
    recipient_reg = get_object_or_404(EventRegistration, event=event, user=recipient)

    if not recipient_reg.can_make_connections:
        messages.error(request, _("This person did not attend the event."))
        return redirect("crush_lu:event_attendees", event_id=event_id)

    # Check if connection already exists
    existing = EventConnection.objects.filter(
        Q(requester=request.user, recipient=recipient, event=event)
        | Q(requester=recipient, recipient=request.user, event=event)
    ).first()

    if existing:
        messages.warning(request, _("Connection request already exists."))
        return redirect("crush_lu:event_attendees", event_id=event_id)

    if request.method == "POST":
        note = request.POST.get("note", "").strip()

        # Create connection request
        connection = EventConnection.objects.create(
            requester=request.user,
            recipient=recipient,
            event=event,
            requester_note=note,
        )

        # Check if this is mutual (recipient already requested requester)
        reverse_connection = EventConnection.objects.filter(
            requester=recipient, recipient=request.user, event=event
        ).first()

        if reverse_connection:
            # Mutual interest! Both move to accepted
            connection.status = "accepted"
            connection.save()
            reverse_connection.status = "accepted"
            reverse_connection.save()

            # Assign coach to facilitate
            connection.assign_coach()
            reverse_connection.assigned_coach = connection.assigned_coach
            reverse_connection.save()

            # Notify both users about mutual connection
            try:
                notify_connection_accepted(
                    recipient=recipient,
                    connection=connection,
                    accepter=request.user,
                    request=request,
                )
                notify_connection_accepted(
                    recipient=request.user,
                    connection=reverse_connection,
                    accepter=recipient,
                    request=request,
                )
            except Exception as e:
                logger.error(f"Failed to send mutual connection notifications: {e}")

            messages.success(
                request,
                f"Mutual connection! 🎉 A coach will help facilitate your introduction.",
            )
        else:
            # Notify recipient about the connection request
            try:
                notify_new_connection(
                    recipient=recipient,
                    connection=connection,
                    requester=request.user,
                    request=request,
                )
            except Exception as e:
                logger.error(f"Failed to send connection request notification: {e}")

            messages.success(request, _("Connection request sent!"))

        return redirect("crush_lu:event_attendees", event_id=event_id)

    context = {
        "event": event,
        "recipient": recipient,
    }
    return render(request, "crush_lu/request_connection.html", context)


@crush_login_required
@require_http_methods(["GET", "POST"])
def request_connection_inline(request, event_id, user_id):
    """HTMX: Inline connection request form and processing"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    recipient = get_object_or_404(CrushProfile, user_id=user_id).user

    # Verify requester attended the event
    requester_reg = get_object_or_404(EventRegistration, event=event, user=request.user)

    if not requester_reg.can_make_connections:
        return render(
            request,
            "crush_lu/_htmx_error.html",
            {"message": "You must attend this event before making connections."},
        )

    # Verify recipient attended the event
    recipient_reg = get_object_or_404(EventRegistration, event=event, user=recipient)

    if not recipient_reg.can_make_connections:
        return render(
            request,
            "crush_lu/_htmx_error.html",
            {"message": "This person did not attend the event."},
        )

    # Check if connection already exists
    existing = EventConnection.objects.filter(
        Q(requester=request.user, recipient=recipient, event=event)
        | Q(requester=recipient, recipient=request.user, event=event)
    ).first()

    if existing:
        return render(
            request,
            "crush_lu/_htmx_error.html",
            {"message": "Connection request already exists."},
        )

    if request.method == "POST":
        note = request.POST.get("note", "").strip()

        # Create connection request
        connection = EventConnection.objects.create(
            requester=request.user,
            recipient=recipient,
            event=event,
            requester_note=note,
        )

        # Check if this is mutual (recipient already requested requester)
        reverse_connection = EventConnection.objects.filter(
            requester=recipient, recipient=request.user, event=event
        ).first()

        is_mutual = False
        if reverse_connection:
            # Mutual interest! Both move to accepted
            connection.status = "accepted"
            connection.save()
            reverse_connection.status = "accepted"
            reverse_connection.save()

            # Assign coach to facilitate
            connection.assign_coach()
            reverse_connection.assigned_coach = connection.assigned_coach
            reverse_connection.save()
            is_mutual = True

        return render(
            request,
            "crush_lu/_connection_request_success.html",
            {"recipient": recipient, "is_mutual": is_mutual},
        )

    # GET: Show inline form
    return render(
        request,
        "crush_lu/_request_connection_form.html",
        {
            "event": event,
            "recipient": recipient,
        },
    )


@crush_login_required
def connection_actions(request, event_id, user_id):
    """HTMX: Get current connection actions for an attendee"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    target_user = get_object_or_404(CrushProfile, user_id=user_id).user

    # Determine connection status
    connection_status = None
    connection_id = None

    # Check if current user sent a request to target
    sent = EventConnection.objects.filter(
        requester=request.user, recipient=target_user, event=event
    ).first()

    if sent:
        if sent.status in ["accepted", "coach_reviewing", "coach_approved", "shared"]:
            connection_status = "mutual"
        else:
            connection_status = "sent"

    # Check if target user sent a request to current user
    received = EventConnection.objects.filter(
        requester=target_user, recipient=request.user, event=event
    ).first()

    if received:
        if received.status == "pending":
            connection_status = "received"
            connection_id = received.id
        elif received.status in [
            "accepted",
            "coach_reviewing",
            "coach_approved",
            "shared",
        ]:
            connection_status = "mutual"

    # Build attendee object for template
    attendee = {
        "user": target_user,
        "connection_status": connection_status,
        "connection_id": connection_id,
    }

    return render(
        request,
        "crush_lu/_attendee_connection_actions.html",
        {
            "attendee": attendee,
            "event": event,
        },
    )


@crush_login_required
@require_http_methods(["GET", "POST"])
def respond_connection(request, connection_id, action):
    """Accept or decline a connection request"""
    connection = get_object_or_404(
        EventConnection, id=connection_id, recipient=request.user, status="pending"
    )

    # Security: Verify user actually attended the event
    try:
        user_registration = EventRegistration.objects.get(
            event=connection.event, user=request.user
        )
        if not user_registration.can_make_connections:
            if request.headers.get("HX-Request"):
                return render(
                    request,
                    "crush_lu/_htmx_error.html",
                    {
                        "message": "You must have attended this event to respond to connections."
                    },
                )
            messages.error(
                request,
                _("You must have attended this event to respond to connections."),
            )
            return redirect("crush_lu:my_connections")
    except EventRegistration.DoesNotExist:
        if request.headers.get("HX-Request"):
            return render(
                request,
                "crush_lu/_htmx_error.html",
                {"message": "You are not registered for this event."},
            )
        messages.error(request, _("You are not registered for this event."))
        return redirect("crush_lu:my_connections")

    # Determine which template to use based on HX-Target
    # If coming from attendees page, target is connection-actions-{user_id}
    # If coming from my_connections, target is connection-{connection_id}
    hx_target = request.headers.get("HX-Target", "")
    is_attendees_page = "connection-actions-" in hx_target

    if action == "accept":
        connection.status = "accepted"
        connection.save()

        # Assign coach
        connection.assign_coach()

        # Notify requester that their connection was accepted
        try:
            notify_connection_accepted(
                recipient=connection.requester,
                connection=connection,
                accepter=request.user,
                request=request,
            )
        except Exception as e:
            logger.error(f"Failed to send connection accepted notification: {e}")

        # Return HTMX partial or redirect
        if request.headers.get("HX-Request"):
            if is_attendees_page:
                # For attendees page, return simpler response with attendee context
                attendee = {
                    "user": connection.requester,
                    "connection_status": "mutual",
                    "connection_id": connection.id,
                }
                return render(
                    request,
                    "crush_lu/_attendee_connection_response.html",
                    {"attendee": attendee, "action": "accept"},
                )
            return render(
                request,
                "crush_lu/_connection_response.html",
                {"connection": connection, "action": "accept"},
            )
        messages.success(
            request,
            _("Connection accepted! A coach will help facilitate your introduction."),
        )
    elif action == "decline":
        connection.status = "declined"
        connection.save()

        # Return HTMX partial or redirect
        if request.headers.get("HX-Request"):
            if is_attendees_page:
                attendee = {
                    "user": connection.requester,
                }
                return render(
                    request,
                    "crush_lu/_attendee_connection_response.html",
                    {"attendee": attendee, "action": "decline"},
                )
            return render(
                request,
                "crush_lu/_connection_response.html",
                {"connection": connection, "action": "decline"},
            )
        messages.info(request, _("Connection request declined."))
    else:
        if request.headers.get("HX-Request"):
            return render(
                request, "crush_lu/_htmx_error.html", {"message": "Invalid action."}
            )
        messages.error(request, _("Invalid action."))

    return redirect("crush_lu:my_connections")


@crush_login_required
def my_connections(request):
    """View all connections (sent, received, active)"""
    # Sent requests
    sent = (
        EventConnection.objects.filter(requester=request.user)
        .select_related("recipient__crushprofile", "event", "assigned_coach")
        .order_by("-requested_at")
    )

    # Received requests (pending only)
    received_pending = (
        EventConnection.objects.filter(recipient=request.user, status="pending")
        .select_related("requester__crushprofile", "event")
        .order_by("-requested_at")
    )

    # Active connections (accepted, coach_reviewing, coach_approved, shared)
    active = (
        EventConnection.objects.filter(
            Q(requester=request.user) | Q(recipient=request.user),
            status__in=["accepted", "coach_reviewing", "coach_approved", "shared"],
        )
        .select_related(
            "requester__crushprofile",
            "recipient__crushprofile",
            "event",
            "assigned_coach",
        )
        .order_by("-requested_at")
    )

    context = {
        "sent_requests": sent,
        "received_requests": received_pending,
        "active_connections": active,
    }
    return render(request, "crush_lu/my_connections.html", context)


@crush_login_required
def connection_detail(request, connection_id):
    """View connection details and provide consent"""
    connection = get_object_or_404(
        EventConnection,
        Q(requester=request.user) | Q(recipient=request.user),
        id=connection_id,
    )

    # Determine if current user is requester or recipient
    is_requester = connection.requester == request.user

    if request.method == "POST":
        # Handle consent
        if "consent" in request.POST:
            consent_value = request.POST.get("consent") == "yes"

            if is_requester:
                connection.requester_consents_to_share = consent_value
            else:
                connection.recipient_consents_to_share = consent_value

            connection.save()

            # Check if both consented and coach approved
            if connection.can_share_contacts:
                connection.status = "shared"
                connection.save()
                messages.success(request, _("Contact information is now shared!"))
            else:
                messages.success(request, _("Your consent has been recorded."))

            return redirect("crush_lu:connection_detail", connection_id=connection_id)

        # Handle message sending
        elif "message" in request.POST:
            message_text = request.POST.get("message", "").strip()
            if message_text and len(message_text) <= 2000:
                # Only allow messaging for accepted/shared connections
                if connection.status in [
                    "accepted",
                    "coach_reviewing",
                    "coach_approved",
                    "shared",
                ]:
                    # Determine the recipient
                    recipient = (
                        connection.recipient if is_requester else connection.requester
                    )

                    # Create the message
                    new_message = ConnectionMessage.objects.create(
                        connection=connection, sender=request.user, message=message_text
                    )

                    # Send notification to recipient (push first, email fallback)
                    try:
                        notify_new_message(
                            recipient=recipient, message=new_message, request=request
                        )
                    except Exception as e:
                        logger.error(f"Failed to send new message notification: {e}")

                    # For HTMX requests, return just the message partial
                    if request.headers.get("HX-Request"):
                        return render(
                            request,
                            "crush_lu/_connection_message.html",
                            {
                                "msg": new_message,
                                "is_own_message": True,
                            },
                        )

                    messages.success(request, _("Message sent!"))
                else:
                    messages.error(
                        request, _("You can only message accepted connections.")
                    )
            else:
                messages.error(
                    request, _("Please enter a valid message (max 2000 characters).")
                )

            return redirect("crush_lu:connection_detail", connection_id=connection_id)

    # Get the other person in the connection
    other_user = connection.recipient if is_requester else connection.requester

    # Get messages for this connection
    connection_messages = (
        ConnectionMessage.objects.filter(connection=connection)
        .select_related("sender")
        .order_by("sent_at")
    )

    context = {
        "connection": connection,
        "is_requester": is_requester,
        "other_user": other_user,
        "other_profile": getattr(other_user, "crushprofile", None),
        "messages": connection_messages,
    }
    return render(request, "crush_lu/connection_detail.html", context)


# Event Activity Voting Views
@crush_login_required
def event_voting_lobby(request, event_id):
    """Pre-voting lobby with countdown and activity previews"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Check if activity voting is enabled for this event
    if not event.enable_activity_voting:
        messages.error(request, _("Activity voting is not enabled for this event."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user
    )

    # Only confirmed or attended registrations can vote
    if user_registration.status not in ["confirmed", "attended"]:
        messages.error(request, _("Only confirmed attendees can access event voting."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Get or create voting session
    voting_session, created = EventVotingSession.objects.get_or_create(event=event)

    # Get all activity options
    activity_options = EventActivityOption.objects.filter(event=event).order_by(
        "activity_type", "activity_variant"
    )

    # Check if user has already voted
    user_vote = EventActivityVote.objects.filter(event=event, user=request.user).first()

    # Get total confirmed attendees count
    total_attendees = EventRegistration.objects.filter(
        event=event, status__in=["confirmed", "attended"]
    ).count()

    context = {
        "event": event,
        "voting_session": voting_session,
        "activity_options": activity_options,
        "user_vote": user_vote,
        "total_attendees": total_attendees,
    }
    return render(request, "crush_lu/event_voting_lobby.html", context)


@crush_login_required
def event_activity_vote(request, event_id):
    """Active voting interface"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Check if activity voting is enabled for this event
    if not event.enable_activity_voting:
        messages.error(request, _("Activity voting is not enabled for this event."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user
    )

    if user_registration.status not in ["confirmed", "attended"]:
        messages.error(request, _("Only confirmed attendees can vote."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Get voting session
    voting_session = get_object_or_404(EventVotingSession, event=event)

    # Check if voting is open
    if not voting_session.is_voting_open:
        messages.warning(request, _("Voting is not currently open for this event."))
        return redirect("crush_lu:event_voting_lobby", event_id=event_id)

    if request.method == "POST":
        presentation_option_id = request.POST.get("presentation_option_id")
        twist_option_id = request.POST.get("twist_option_id")

        if not presentation_option_id or not twist_option_id:
            messages.error(
                request,
                _(
                    "Please vote on BOTH categories: Presentation Style AND Speed Dating Twist."
                ),
            )
        else:
            try:
                from .models import GlobalActivityOption

                presentation_option = GlobalActivityOption.objects.get(
                    id=presentation_option_id,
                    activity_type="presentation_style",
                    is_active=True,
                )
                twist_option = GlobalActivityOption.objects.get(
                    id=twist_option_id,
                    activity_type="speed_dating_twist",
                    is_active=True,
                )

                # Handle presentation style vote
                presentation_vote = EventActivityVote.objects.filter(
                    event=event,
                    user=request.user,
                    selected_option__activity_type="presentation_style",
                ).first()

                if presentation_vote:
                    # Update existing vote
                    presentation_vote.selected_option = presentation_option
                    presentation_vote.save()
                else:
                    # Create new vote
                    EventActivityVote.objects.create(
                        event=event,
                        user=request.user,
                        selected_option=presentation_option,
                    )

                # Handle speed dating twist vote
                twist_vote = EventActivityVote.objects.filter(
                    event=event,
                    user=request.user,
                    selected_option__activity_type="speed_dating_twist",
                ).first()

                if twist_vote:
                    # Update existing vote
                    twist_vote.selected_option = twist_option
                    twist_vote.save()
                else:
                    # Create new vote
                    EventActivityVote.objects.create(
                        event=event, user=request.user, selected_option=twist_option
                    )

                # Update total votes only if this is first complete vote
                if not (presentation_vote and twist_vote):
                    voting_session.total_votes += 1
                    voting_session.save()

                messages.success(
                    request, _("Your votes have been recorded for both categories!")
                )
                return redirect("crush_lu:event_voting_results", event_id=event_id)

            except GlobalActivityOption.DoesNotExist:
                messages.error(request, _("Invalid activity option selected."))

    # Get all GLOBAL activity options (not per-event anymore!)
    from .models import GlobalActivityOption

    presentation_style_options = GlobalActivityOption.objects.filter(
        activity_type="presentation_style", is_active=True
    ).order_by("sort_order")

    speed_dating_twist_options = GlobalActivityOption.objects.filter(
        activity_type="speed_dating_twist", is_active=True
    ).order_by("sort_order")

    # Check if user has voted on BOTH categories
    presentation_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type="presentation_style",
    ).first()

    twist_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type="speed_dating_twist",
    ).first()

    context = {
        "event": event,
        "voting_session": voting_session,
        "presentation_style_options": presentation_style_options,
        "speed_dating_twist_options": speed_dating_twist_options,
        "presentation_vote": presentation_vote,
        "twist_vote": twist_vote,
        "has_voted_both": presentation_vote and twist_vote,
    }
    return render(request, "crush_lu/event_activity_vote.html", context)


@crush_login_required
def event_voting_results(request, event_id):
    """Display voting results and transition to presentations when ready"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user
    )

    if user_registration.status not in ["confirmed", "attended"]:
        messages.error(request, _("Only confirmed attendees can view results."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Get voting session
    voting_session = get_object_or_404(EventVotingSession, event=event)

    # Check if user has voted on BOTH categories
    presentation_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type="presentation_style",
    ).first()

    twist_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type="speed_dating_twist",
    ).first()

    user_has_voted_both = presentation_vote and twist_vote

    # If voting ended and user hasn't voted, redirect back to voting with message
    if not voting_session.is_voting_open and not user_has_voted_both:
        messages.warning(
            request,
            _(
                "Voting has ended. You did not vote, but you can still participate in presentations!"
            ),
        )
        # Allow them to continue to presentations anyway
        return redirect("crush_lu:event_presentations", event_id=event_id)

    # Get vote counts for each GlobalActivityOption
    from .models import GlobalActivityOption
    from django.db.models import Count

    # Get all active global options with their vote counts for THIS event
    activity_options_with_votes = []

    for option in GlobalActivityOption.objects.filter(is_active=True).order_by(
        "activity_type", "sort_order"
    ):
        vote_count = EventActivityVote.objects.filter(
            event=event, selected_option=option
        ).count()

        # Add vote_count attribute for template compatibility
        option.vote_count = vote_count
        option.is_winner = False  # Will be set below
        activity_options_with_votes.append(option)

    # Calculate total votes
    total_votes = voting_session.total_votes

    # If voting has ended, calculate winners and initialize presentation queue
    if not voting_session.is_voting_open:
        # Calculate winners if not already done
        if not voting_session.winning_presentation_style:
            voting_session.calculate_winner()
            voting_session.initialize_presentation_queue()
            voting_session.save()

        # Mark winners in the activity_options list
        for option in activity_options_with_votes:
            if (
                option == voting_session.winning_presentation_style
                or option == voting_session.winning_speed_dating_twist
            ):
                option.is_winner = True

        # Check if presentations have started
        has_presentations = PresentationQueue.objects.filter(event=event).exists()

        if has_presentations:
            # Automatically redirect to presentations after 5 seconds
            messages.success(
                request, _("Voting complete! Redirecting to presentations...")
            )
            context = {
                "event": event,
                "voting_session": voting_session,
                "activity_options": activity_options_with_votes,
                "user_has_voted_both": user_has_voted_both,
                "total_votes": total_votes,
                "redirect_to_presentations": True,  # Signal to template
            }
            return render(request, "crush_lu/event_voting_results.html", context)

    context = {
        "event": event,
        "voting_session": voting_session,
        "activity_options": activity_options_with_votes,
        "user_has_voted_both": user_has_voted_both,
        "total_votes": total_votes,
        "redirect_to_presentations": False,
    }
    return render(request, "crush_lu/event_voting_results.html", context)


# Presentation Round Views (Phase 2)
@crush_login_required
def event_presentations(request, event_id):
    """Phase 2: Live presentation view - shows current presenter and allows rating"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Check if activity voting is enabled for this event
    if not event.enable_activity_voting:
        messages.error(request, _("Activity voting is not enabled for this event."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user
    )

    if user_registration.status not in ["confirmed", "attended"]:
        messages.error(request, _("Only confirmed attendees can view presentations."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Get the current presenter (status='presenting')
    current_presentation = (
        PresentationQueue.objects.filter(event=event, status="presenting")
        .select_related("user__crushprofile")
        .first()
    )

    # Get next presenter (for preview)
    next_presentation = (
        PresentationQueue.objects.filter(event=event, status="waiting")
        .order_by("presentation_order")
        .first()
    )

    # Get total presentation stats
    total_presentations = PresentationQueue.objects.filter(event=event).count()
    completed_presentations = PresentationQueue.objects.filter(
        event=event, status="completed"
    ).count()

    # Check if user has rated current presenter
    user_has_rated = False
    if current_presentation:
        user_has_rated = PresentationRating.objects.filter(
            event=event, presenter=current_presentation.user, rater=request.user
        ).exists()

    # Get voting session and winning presentation style
    voting_session = get_object_or_404(EventVotingSession, event=event)
    winning_style = voting_session.winning_presentation_style

    context = {
        "event": event,
        "current_presentation": current_presentation,
        "next_presentation": next_presentation,
        "total_presentations": total_presentations,
        "completed_presentations": completed_presentations,
        "user_has_rated": user_has_rated,
        "winning_style": winning_style,
        "is_presenting": current_presentation
        and current_presentation.user == request.user,
    }
    return render(request, "crush_lu/event_presentations.html", context)


@crush_login_required
@require_http_methods(["POST"])
def submit_presentation_rating(request, event_id, presenter_id):
    """Submit anonymous 1-5 star rating for a presenter"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    presenter = get_object_or_404(User, id=presenter_id)

    # Check if activity voting is enabled for this event
    if not event.enable_activity_voting:
        return JsonResponse(
            {
                "success": False,
                "error": "Activity voting is not enabled for this event.",
            },
            status=403,
        )

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user
    )

    if user_registration.status not in ["confirmed", "attended"]:
        return JsonResponse(
            {"success": False, "error": "Only confirmed attendees can rate."},
            status=403,
        )

    # Cannot rate yourself
    if presenter == request.user:
        return JsonResponse(
            {"success": False, "error": "You cannot rate yourself."}, status=400
        )

    # Get rating from request
    rating_value = request.POST.get("rating")

    try:
        rating_value = int(rating_value)
        if rating_value < 1 or rating_value > 5:
            raise ValueError
    except (TypeError, ValueError):
        return JsonResponse(
            {"success": False, "error": "Rating must be between 1 and 5."}, status=400
        )

    # Create or update rating
    rating, created = PresentationRating.objects.update_or_create(
        event=event,
        presenter=presenter,
        rater=request.user,
        defaults={"rating": rating_value},
    )

    return JsonResponse(
        {
            "success": True,
            "message": (
                "Rating submitted anonymously!" if created else "Rating updated!"
            ),
            "rating": rating_value,
        }
    )


# Coach Presentation Control Panel
@crush_login_required
def coach_presentation_control(request, event_id):
    """Coach control panel for managing presentation queue"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is an active coach
    try:
        coach = CrushCoach.objects.get(user=request.user)
        if not coach.is_active:
            messages.error(request, _("Your coach account has been deactivated."))
            return redirect("crush_lu:event_detail", event_id=event_id)
    except CrushCoach.DoesNotExist:
        messages.error(request, _("Only coaches can access presentation controls."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Get all presentations
    presentations = (
        PresentationQueue.objects.filter(event=event)
        .select_related("user__crushprofile")
        .order_by("presentation_order")
    )

    # Get current presenter
    current_presentation = presentations.filter(status="presenting").first()

    # Get next presenter
    next_presentation = (
        presentations.filter(status="waiting").order_by("presentation_order").first()
    )

    # Get stats
    total_presentations = presentations.count()
    completed_presentations = presentations.filter(status="completed").count()

    # Get voting session and winning presentation style
    voting_session = get_object_or_404(EventVotingSession, event=event)
    winning_style = voting_session.winning_presentation_style

    context = {
        "event": event,
        "presentations": presentations,
        "current_presentation": current_presentation,
        "next_presentation": next_presentation,
        "total_presentations": total_presentations,
        "completed_presentations": completed_presentations,
        "winning_style": winning_style,
    }
    return render(request, "crush_lu/coach_presentation_control.html", context)


@crush_login_required
@require_http_methods(["POST"])
def coach_advance_presentation(request, event_id):
    """Advance to next presenter in the queue"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is an active coach
    try:
        coach = CrushCoach.objects.get(user=request.user)
        if not coach.is_active:
            return JsonResponse(
                {"success": False, "error": "Your coach account has been deactivated."},
                status=403,
            )
    except CrushCoach.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": "Only coaches can advance presentations."},
            status=403,
        )

    # End current presentation if exists
    current_presentation = PresentationQueue.objects.filter(
        event=event, status="presenting"
    ).first()

    if current_presentation:
        current_presentation.status = "completed"
        current_presentation.completed_at = timezone.now()
        current_presentation.save()

    # Start next presentation
    next_presentation = (
        PresentationQueue.objects.filter(event=event, status="waiting")
        .order_by("presentation_order")
        .first()
    )

    if next_presentation:
        next_presentation.status = "presenting"
        next_presentation.started_at = timezone.now()
        next_presentation.save()

        return JsonResponse(
            {
                "success": True,
                "message": f"Now presenting: {next_presentation.user.crushprofile.display_name}",
                "presenter_name": next_presentation.user.crushprofile.display_name,
                "presentation_order": next_presentation.presentation_order,
            }
        )
    else:
        return JsonResponse(
            {
                "success": True,
                "message": "All presentations completed!",
                "all_completed": True,
            }
        )


@crush_login_required
def my_presentation_scores(request, event_id):
    """Show user their personal presentation scores (private view)"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration, event=event, user=request.user
    )

    if user_registration.status not in ["confirmed", "attended"]:
        messages.error(request, _("Only confirmed attendees can view scores."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Check if all presentations are completed
    total_presentations = PresentationQueue.objects.filter(event=event).count()
    completed_presentations = PresentationQueue.objects.filter(
        event=event, status="completed"
    ).count()

    all_completed = (
        total_presentations > 0 and completed_presentations == total_presentations
    )

    if not all_completed:
        messages.warning(
            request,
            _("Scores will be available after all presentations are completed."),
        )
        return redirect("crush_lu:event_presentations", event_id=event_id)

    # Get ratings received by this user
    ratings_received = PresentationRating.objects.filter(
        event=event, presenter=request.user
    ).select_related("rater__crushprofile")

    # Calculate average score
    if ratings_received.exists():
        total_score = sum(r.rating for r in ratings_received)
        average_score = total_score / ratings_received.count()
        rating_count = ratings_received.count()
    else:
        average_score = 0
        rating_count = 0

    # Get individual ratings (without showing who rated)
    individual_ratings = [r.rating for r in ratings_received]
    individual_ratings.sort(reverse=True)  # Sort highest to lowest

    # Calculate rating distribution
    rating_distribution = {
        5: ratings_received.filter(rating=5).count(),
        4: ratings_received.filter(rating=4).count(),
        3: ratings_received.filter(rating=3).count(),
        2: ratings_received.filter(rating=2).count(),
        1: ratings_received.filter(rating=1).count(),
    }

    # Get user's rank among all participants
    from django.db.models import Avg, Count

    all_participants = PresentationQueue.objects.filter(event=event).values_list(
        "user_id", flat=True
    )

    participant_scores = []
    for participant_id in all_participants:
        participant_ratings = PresentationRating.objects.filter(
            event=event, presenter_id=participant_id
        )
        if participant_ratings.exists():
            avg = participant_ratings.aggregate(Avg("rating"))["rating__avg"]
            participant_scores.append((participant_id, avg))

    # Sort by average score (highest first)
    participant_scores.sort(key=lambda x: x[1], reverse=True)

    # Find user's rank
    user_rank = None
    for idx, (participant_id, score) in enumerate(participant_scores, start=1):
        if participant_id == request.user.id:
            user_rank = idx
            break

    context = {
        "event": event,
        "average_score": average_score,
        "rating_count": rating_count,
        "individual_ratings": individual_ratings,
        "rating_distribution": rating_distribution,
        "user_rank": user_rank,
        "total_participants": len(participant_scores),
    }
    return render(request, "crush_lu/my_presentation_scores.html", context)


@crush_login_required
def get_current_presenter_api(request, event_id):
    """API endpoint to get current presenter status (for polling)"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    try:
        user_registration = EventRegistration.objects.get(
            event=event, user=request.user
        )
        if user_registration.status not in ["confirmed", "attended"]:
            return JsonResponse({"error": "Not authorized"}, status=403)
    except EventRegistration.DoesNotExist:
        return JsonResponse({"error": "Not registered for this event"}, status=403)

    # Get current presenter
    current_presentation = (
        PresentationQueue.objects.filter(event=event, status="presenting")
        .select_related("user__crushprofile")
        .first()
    )

    if current_presentation:
        # Check if user has rated this presenter
        user_has_rated = PresentationRating.objects.filter(
            event=event, presenter=current_presentation.user, rater=request.user
        ).exists()

        # Calculate time remaining
        time_remaining = 90
        if current_presentation.started_at:
            from django.utils import timezone

            elapsed = (timezone.now() - current_presentation.started_at).total_seconds()
            time_remaining = max(0, int(90 - elapsed))

        return JsonResponse(
            {
                "has_presenter": True,
                "presenter_id": current_presentation.user.id,
                "presenter_name": current_presentation.user.crushprofile.display_name,
                "presentation_order": current_presentation.presentation_order,
                "started_at": (
                    current_presentation.started_at.isoformat()
                    if current_presentation.started_at
                    else None
                ),
                "time_remaining": time_remaining,
                "user_has_rated": user_has_rated,
                "is_presenting": current_presentation.user == request.user,
            }
        )
    else:
        # Check if all presentations are completed
        total_presentations = PresentationQueue.objects.filter(event=event).count()
        completed_presentations = PresentationQueue.objects.filter(
            event=event, status="completed"
        ).count()

        return JsonResponse(
            {
                "has_presenter": False,
                "all_completed": total_presentations > 0
                and completed_presentations == total_presentations,
                "completed_count": completed_presentations,
                "total_count": total_presentations,
            }
        )


# Demo/Guided Tour View
def voting_demo(request):
    """Interactive demo of the voting system for new users"""
    from .models import GlobalActivityOption

    # Get actual activity options from database
    presentation_options = GlobalActivityOption.objects.filter(
        activity_type="presentation_style", is_active=True
    ).order_by("sort_order")

    twist_options = GlobalActivityOption.objects.filter(
        activity_type="speed_dating_twist", is_active=True
    ).order_by("sort_order")

    context = {
        "presentation_options": presentation_options,
        "twist_options": twist_options,
    }
    return render(request, "crush_lu/voting_demo.html", context)


# ============================================================================
# PRIVATE INVITATION SYSTEM - Invitation Flow Views
# ============================================================================


def invitation_landing(request, code):
    """
    PUBLIC ACCESS: Landing page for invitation link.
    Accessible without login - shows event details and invitation info.
    """
    invitation = get_object_or_404(EventInvitation, invitation_code=code)

    # Check expiration
    if invitation.is_expired:
        invitation.status = "expired"
        invitation.save()
        return render(
            request, "crush_lu/invitation_expired.html", {"invitation": invitation}
        )

    # Check if already accepted
    if invitation.status == "accepted":
        messages.info(
            request,
            _("You have already accepted this invitation. Please log in to continue."),
        )
        return redirect("crush_lu:crush_login")

    # Show invitation details
    context = {
        "invitation": invitation,
        "event": invitation.event,
    }
    return render(request, "crush_lu/invitation_landing.html", context)


@ratelimit(key="ip", rate="10/h", method="POST")
def invitation_accept(request, code):
    """
    PUBLIC ACCESS: Accept invitation and create guest account with age verification.

    Security Requirements:
    - Validates 18+ age requirement
    - Captures actual date of birth (no hardcoded ages)
    - Creates profile pending coach approval (no auto-approval)
    """
    from .forms import InvitationAcceptanceForm

    invitation = get_object_or_404(EventInvitation, invitation_code=code)

    # Check if already accepted
    if invitation.status == "accepted":
        messages.info(request, _("This invitation has already been accepted."))
        return redirect("crush_lu:crush_login")

    # Check expiration
    if invitation.is_expired:
        messages.error(request, _("This invitation has expired."))
        return redirect("crush_lu:invitation_landing", code=code)

    if request.method == "POST":
        form = InvitationAcceptanceForm(request.POST, invitation=invitation)

        if form.is_valid():
            try:
                date_of_birth = form.cleaned_data["date_of_birth"]

                # Create user account with random password
                username = f"guest_{invitation.guest_email.split('@')[0]}_{uuid.uuid4().hex[:6]}"
                from django.contrib.auth.hashers import make_password
                import secrets
                import string

                # Generate secure random password
                alphabet = string.ascii_letters + string.digits + string.punctuation
                random_password = "".join(secrets.choice(alphabet) for _ in range(16))

                user = User.objects.create_user(
                    username=username,
                    email=invitation.guest_email,
                    first_name=invitation.guest_first_name,
                    last_name=invitation.guest_last_name,
                    password=random_password,
                )

                # Create minimal profile with actual date of birth
                # NOTE: Profile requires coach approval - no auto-approval for security
                from .utils.i18n import validate_language

                preferred_lang = validate_language(
                    getattr(request, "LANGUAGE_CODE", "en"), default="en"
                )

                profile = CrushProfile.objects.create(
                    user=user,
                    date_of_birth=date_of_birth,  # SECURITY: Use actual DOB from form
                    is_approved=False,  # SECURITY: Requires coach approval
                    completion_status="completed",
                    preferred_language=preferred_lang,
                )

                # Update invitation
                invitation.created_user = user
                invitation.status = "accepted"
                invitation.accepted_at = timezone.now()
                invitation.save()

                # Log the user in
                user.backend = "django.contrib.auth.backends.ModelBackend"
                login(request, user)

                logger.info(
                    f"Guest account created with age verification: {user.email} "
                    f"(age: {profile.age}) for event {invitation.event.title}"
                )

                messages.success(
                    request,
                    _(
                        "Welcome! Your invitation has been accepted. "
                        "You will receive an email once your attendance is approved by our team."
                    ),
                )

                return render(
                    request,
                    "crush_lu/invitation_pending_approval.html",
                    {
                        "invitation": invitation,
                        "event": invitation.event,
                    },
                )

            except Exception as e:
                logger.error(f"Error creating guest account: {e}")
                messages.error(
                    request,
                    _(
                        "An error occurred while accepting your invitation. Please try again."
                    ),
                )
                return redirect("crush_lu:invitation_landing", code=code)
        # If form is invalid, fall through to re-render with errors
    else:
        form = InvitationAcceptanceForm(invitation=invitation)

    context = {
        "invitation": invitation,
        "event": invitation.event,
        "form": form,
    }
    return render(request, "crush_lu/invitation_accept_form.html", context)


@crush_login_required
def coach_manage_invitations(request, event_id):
    """
    COACH ONLY: Dashboard for managing event invitations.
    Send invitations, approve guests, and track invitation status.
    """
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
    except CrushCoach.DoesNotExist:
        messages.error(request, _("Only coaches can manage invitations."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    event = get_object_or_404(MeetupEvent, id=event_id, is_private_invitation=True)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "send_invitation":
            # Send new invitation
            email = request.POST.get("email", "").strip()
            first_name = request.POST.get("first_name", "").strip()
            last_name = request.POST.get("last_name", "").strip()

            if not email or not first_name or not last_name:
                messages.error(
                    request, _("Please provide email, first name, and last name.")
                )
            else:
                # Check if invitation already exists
                existing = EventInvitation.objects.filter(
                    event=event, guest_email=email
                ).first()

                if existing:
                    messages.warning(
                        request, f"An invitation for {email} already exists."
                    )
                else:
                    invitation = EventInvitation.objects.create(
                        event=event,
                        guest_email=email,
                        guest_first_name=first_name,
                        guest_last_name=last_name,
                        invited_by=request.user,
                    )

                    # Send email invitation
                    from .email_notifications import (
                        send_external_guest_invitation_email,
                    )

                    email_sent = send_external_guest_invitation_email(
                        invitation, request
                    )
                    if email_sent:
                        messages.success(request, f"Invitation sent to {email}.")
                    else:
                        messages.warning(
                            request,
                            f"Invitation created for {email}, but email could not be sent. Code: {invitation.invitation_code}",
                        )
                    logger.info(
                        f"Invitation created for {email} to event {event.title}"
                    )

        elif action == "approve_guest":
            invitation_id = request.POST.get("invitation_id")
            try:
                invitation = EventInvitation.objects.get(id=invitation_id, event=event)
                invitation.approval_status = "approved"
                invitation.approved_at = timezone.now()
                invitation.save()

                # Send approval email with login instructions
                from .email_notifications import send_invitation_approval_email

                email_sent = send_invitation_approval_email(invitation, request)
                if email_sent:
                    messages.success(
                        request,
                        f"Guest {invitation.guest_first_name} {invitation.guest_last_name} approved and notified!",
                    )
                else:
                    messages.success(
                        request,
                        f"Guest {invitation.guest_first_name} {invitation.guest_last_name} approved! (Email notification could not be sent)",
                    )
                logger.info(
                    f"Guest approved: {invitation.guest_email} for event {event.title}"
                )
            except EventInvitation.DoesNotExist:
                messages.error(request, _("Invitation not found."))

        elif action == "reject_guest":
            invitation_id = request.POST.get("invitation_id")
            notes = request.POST.get("rejection_notes", "")
            try:
                invitation = EventInvitation.objects.get(id=invitation_id, event=event)
                invitation.approval_status = "rejected"
                invitation.approval_notes = notes
                invitation.save()

                # Send rejection email
                from .email_notifications import send_invitation_rejection_email

                email_sent = send_invitation_rejection_email(invitation, request)
                if email_sent:
                    messages.info(
                        request,
                        f"Guest {invitation.guest_first_name} {invitation.guest_last_name} rejected and notified.",
                    )
                else:
                    messages.info(
                        request,
                        f"Guest {invitation.guest_first_name} {invitation.guest_last_name} rejected. (Email notification could not be sent)",
                    )
                logger.info(
                    f"Guest rejected: {invitation.guest_email} for event {event.title}"
                )
            except EventInvitation.DoesNotExist:
                messages.error(request, _("Invitation not found."))

    # Get all invitations for this event
    invitations = EventInvitation.objects.filter(event=event).order_by(
        "-invitation_sent_at"
    )

    # Separate by status
    pending_approvals = invitations.filter(
        status="accepted", approval_status="pending_approval"
    )
    approved_guests = invitations.filter(approval_status="approved")
    rejected_guests = invitations.filter(approval_status="rejected")
    pending_invitations = invitations.filter(status="pending")

    context = {
        "event": event,
        "invitations": invitations,
        "pending_approvals": pending_approvals,
        "approved_guests": approved_guests,
        "rejected_guests": rejected_guests,
        "pending_invitations": pending_invitations,
        "coach": coach,
    }
    return render(request, "crush_lu/coach_invitation_dashboard.html", context)


# Special User Experience View
@crush_login_required
def special_welcome(request):
    """
    Special welcome page for VIP users with custom animations and experience.
    Only accessible if special_experience_active is set in session.

    If a journey is configured for this user, redirect to journey_map instead.
    """
    # Check if special experience is active in session
    if not request.session.get("special_experience_active"):
        messages.warning(request, _("This page is not available."))
        # Redirect to home instead of dashboard (special users don't need profiles)
        return redirect("crush_lu:home")

    # Get special experience data from session
    special_experience_data = request.session.get("special_experience_data", {})

    # Get the full SpecialUserExperience object for complete data
    special_experience_id = request.session.get("special_experience_id")
    try:
        special_experience = SpecialUserExperience.objects.get(
            id=special_experience_id, is_active=True
        )
    except SpecialUserExperience.DoesNotExist:
        # Clear session data if experience is not found or inactive
        request.session.pop("special_experience_active", None)
        request.session.pop("special_experience_id", None)
        request.session.pop("special_experience_data", None)
        messages.warning(request, _("This special experience is no longer available."))
        return redirect("crush_lu:home")

    # ============================================================================
    # NEW: Check if journey is configured - redirect to appropriate journey type
    # ============================================================================
    from .models import JourneyConfiguration

    # First check for Wonderland journey
    wonderland_journey = JourneyConfiguration.objects.filter(
        special_experience=special_experience, journey_type="wonderland", is_active=True
    ).first()

    if wonderland_journey:
        logger.info(
            f"🎮 Redirecting {request.user.username} to Wonderland journey: {wonderland_journey.journey_name}"
        )
        return redirect("crush_lu:journey_map")

    # Check for Advent Calendar journey
    advent_journey = JourneyConfiguration.objects.filter(
        special_experience=special_experience,
        journey_type="advent_calendar",
        is_active=True,
    ).first()

    if advent_journey:
        logger.info(f"🎄 Redirecting {request.user.username} to Advent Calendar")
        return redirect("crush_lu:advent_calendar")

    # No journey configured - show simple welcome page
    # ============================================================================

    context = {
        "special_experience": special_experience,
    }

    # Mark session as viewed (only show once per login)
    request.session["special_experience_viewed"] = True

    return render(request, "crush_lu/special_welcome.html", context)


# ============================================================================
# PWA Views
# ============================================================================


def offline_view(request):
    """
    Offline fallback page for PWA
    Displayed when user is offline and tries to access unavailable content
    """
    return render(request, "crush_lu/offline.html")


def service_worker_view(request):
    """
    Serve the Workbox service worker from root path
    This is required to give the service worker access to the entire site scope
    """
    from django.http import HttpResponse
    from django.conf import settings
    import os

    # Read the service worker file from app-specific static folder
    sw_path = os.path.join(
        settings.BASE_DIR, "crush_lu", "static", "crush_lu", "sw-workbox.js"
    )

    try:
        with open(sw_path, "r", encoding="utf-8") as f:
            sw_content = f.read()

        # Return with correct MIME type
        response = HttpResponse(sw_content, content_type="application/javascript")

        # IMPORTANT: Never cache SW script as immutable - it must be revalidated
        # so updates propagate to users. The SW itself handles internal caching.
        response["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"

        return response
    except FileNotFoundError:
        return HttpResponse("Service worker not found", status=404)


def manifest_view(request):
    """
    Serve the PWA manifest.json with correct static URLs.
    Adds a version query param to force icon refresh on Android/Chrome.
    """
    from django.http import JsonResponse
    from django.templatetags.static import static
    from django.conf import settings

    # Get manifest version for cache busting
    MANIFEST_VERSION = getattr(settings, "PWA_MANIFEST_VERSION", "v1")

    def s(path: str) -> str:
        """Return static URL with version query param for cache busting."""
        return f"{static(path)}?v={MANIFEST_VERSION}"

    manifest = {
        "name": "Crush.lu - Privacy-First Dating in Luxembourg",
        "short_name": "Crush.lu",
        "description": "Event-based dating platform for Luxembourg. Meet people at real events, not endless swiping.",
        "id": "/?source=pwa",
        "start_url": "/?source=pwa",
        "display": "standalone",
        "background_color": "#9B59B6",
        "theme_color": "#9B59B6",
        "orientation": "portrait-primary",
        "scope": "/",
        "icons": [
            {
                "src": s("crush_lu/icons/android-launchericon-48-48.png"),
                "sizes": "48x48",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-72-72.png"),
                "sizes": "72x72",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-96-96.png"),
                "sizes": "96x96",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-144-144.png"),
                "sizes": "144x144",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-192-192.png"),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-512-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-192-192-maskable.png"),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "maskable",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-512-512-maskable.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
        "screenshots": [
            {
                "src": s("crush_lu/icons/android-launchericon-512-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "form_factor": "narrow",
                "label": "Crush.lu Mobile View",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-512-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "form_factor": "wide",
                "label": "Crush.lu Desktop View",
            },
        ],
        "categories": ["social", "lifestyle"],
        "prefer_related_applications": False,
        "related_applications": [],
        "shortcuts": [
            {
                "name": "Browse Events",
                "short_name": "Events",
                "description": "Browse upcoming meetup events",
                "url": "/events/",
                "icons": [
                    {"src": s("crush_lu/icons/shortcut-events.png"), "sizes": "96x96"}
                ],
            },
            {
                "name": "My Dashboard",
                "short_name": "Dashboard",
                "description": "View your profile and registrations",
                "url": "/dashboard/",
                "icons": [
                    {
                        "src": s("crush_lu/icons/shortcut-dashboard.png"),
                        "sizes": "96x96",
                    }
                ],
            },
            {
                "name": "Connections",
                "short_name": "Connections",
                "description": "View your event connections",
                "url": "/connections/",
                "icons": [
                    {
                        "src": s("crush_lu/icons/shortcut-connections.png"),
                        "sizes": "96x96",
                    }
                ],
            },
        ],
    }

    response = JsonResponse(manifest)
    response["Content-Type"] = "application/manifest+json"
    # Prevent aggressive caching to avoid stale icon issues during updates
    response["Cache-Control"] = "no-cache"
    return response


def assetlinks_view(request):
    """
    Serve assetlinks.json for Android App Links verification.

    This enables Android to verify that Crush.lu PWA can handle all URLs
    from the crush.lu domain, enabling a better OAuth experience in installed PWAs.

    See: https://developer.android.com/training/app-links/verify-android-applinks
    """
    from django.http import JsonResponse

    assetlinks = [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {"namespace": "web", "site": "https://crush.lu"},
        }
    ]

    response = JsonResponse(assetlinks, safe=False)
    response["Content-Type"] = "application/json"
    # Allow caching for 24 hours
    response["Cache-Control"] = "public, max-age=86400"
    return response


@login_required
def pwa_debug_view(request):
    """
    Superuser-only PWA debug page showing service worker state, cache info, and diagnostics.
    Useful for debugging PWA issues in production.
    """
    # Only allow Django superusers
    if not request.user.is_superuser:
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden("Superuser access required")

    return render(
        request,
        "crush_lu/pwa_debug.html",
        {
            "sw_version": "crush-v16-icon-cache-fix",  # Keep in sync with sw-workbox.js
        },
    )
