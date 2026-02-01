"""
Account management views for Crush.lu

Handles account settings, email preferences, password management, social account connections,
and GDPR-compliant account deletion.
"""
from django.shortcuts import render, redirect
from django.contrib.auth import logout
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.db.models import Q
from django.utils import timezone
import logging
import json
import uuid
import base64
import hashlib
import hmac

from ..models import (
    CrushProfile, ProfileSubmission, EventRegistration, EventConnection,
    ConnectionMessage, CoachSession, EmailPreference, PushSubscription,
    CoachPushSubscription, UserActivity
)
from ..decorators import crush_login_required

logger = logging.getLogger(__name__)


def data_deletion_request(request):
    """Data deletion instructions page"""
    return render(request, 'crush_lu/account/data_deletion.html')


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
        signed_request = request.POST.get('signed_request')
        if not signed_request:
            logger.error("Facebook data deletion: No signed_request provided")
            return JsonResponse({'error': 'No signed_request'}, status=400)

        # Parse and verify the signed request
        data = parse_facebook_signed_request(signed_request)
        if not data:
            logger.error("Facebook data deletion: Invalid signed_request")
            return JsonResponse({'error': 'Invalid signed_request'}, status=400)

        facebook_user_id = data.get('user_id')
        if not facebook_user_id:
            logger.error("Facebook data deletion: No user_id in signed_request")
            return JsonResponse({'error': 'No user_id'}, status=400)

        # Find the user by their Facebook social account
        from allauth.socialaccount.models import SocialAccount
        try:
            social_account = SocialAccount.objects.get(
                provider='facebook',
                uid=facebook_user_id
            )
            user = social_account.user

            # Generate a unique confirmation code
            confirmation_code = str(uuid.uuid4())

            # Log the deletion request
            logger.info(f"Facebook data deletion request for user {user.id} (FB ID: {facebook_user_id})")

            # Delete/anonymize user data
            delete_user_data(user, confirmation_code)

            # Build the status URL where user can check deletion status
            status_url = request.build_absolute_uri(
                f'/data-deletion/status/?code={confirmation_code}'
            )

            # Return the required JSON response
            return JsonResponse({
                'url': status_url,
                'confirmation_code': confirmation_code
            })

        except SocialAccount.DoesNotExist:
            # User not found - still return success (data already doesn't exist)
            logger.warning(f"Facebook data deletion: No user found for FB ID {facebook_user_id}")
            confirmation_code = str(uuid.uuid4())
            status_url = request.build_absolute_uri(
                f'/data-deletion/status/?code={confirmation_code}'
            )
            return JsonResponse({
                'url': status_url,
                'confirmation_code': confirmation_code
            })

    except Exception as e:
        logger.exception(f"Facebook data deletion error: {str(e)}")
        return JsonResponse({'error': 'Server error'}, status=500)


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
            facebook_app = SocialApp.objects.get(provider='facebook')
            app_secret = facebook_app.secret
        except SocialApp.DoesNotExist:
            logger.error("Facebook app not configured in SocialApp")
            return None

        # Split the signed request
        parts = signed_request.split('.')
        if len(parts) != 2:
            return None

        encoded_sig, payload = parts

        # Decode the signature
        # Facebook uses URL-safe base64, add padding if needed
        encoded_sig += '=' * (4 - len(encoded_sig) % 4)
        sig = base64.urlsafe_b64decode(encoded_sig)

        # Decode the payload
        payload += '=' * (4 - len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))

        # Verify the signature
        expected_sig = hmac.new(
            app_secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).digest()

        if not hmac.compare_digest(sig, expected_sig):
            logger.error("Facebook signed request signature mismatch")
            return None

        return data

    except Exception as e:
        logger.exception(f"Error parsing Facebook signed request: {str(e)}")
        return None


def delete_user_data(user, confirmation_code):
    """
    Delete or anonymize a user's data.

    This function:
    1. Deletes user's blob storage folder (photos, exports, etc.)
    2. Deletes CrushProfile and related data
    3. Deletes social account connections
    4. Anonymizes the user account (or deletes entirely)

    Args:
        user: The Django User instance
        confirmation_code: Unique code for tracking this deletion
    """
    from allauth.socialaccount.models import SocialAccount, SocialToken
    from crush_lu.storage import delete_user_storage

    logger.info(f"Starting data deletion for user {user.id}, confirmation: {confirmation_code}")

    # Clean up blob storage folder (users/{user_id}/)
    # This removes the marker file and any orphaned files
    # Individual photo files are also deleted below, but this ensures the folder is removed
    success, deleted_count = delete_user_storage(user.id)
    if success and deleted_count > 0:
        logger.info(f"Deleted {deleted_count} blob(s) from storage for user {user.id}")
    elif not success:
        logger.warning(f"Failed to clean up storage for user {user.id}, continuing with deletion")

    try:
        # Delete CrushProfile if exists
        try:
            profile = user.crushprofile

            # Delete profile photos from storage
            for photo_field in ['photo_1', 'photo_2', 'photo_3']:
                photo = getattr(profile, photo_field, None)
                if photo:
                    try:
                        photo.delete(save=False)
                    except Exception as e:
                        logger.warning(f"Could not delete {photo_field}: {e}")

            # Delete the profile
            profile.delete()
            logger.info(f"Deleted CrushProfile for user {user.id}")
        except CrushProfile.DoesNotExist:
            # No profile to delete
            pass

        # Delete ProfileSubmissions
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

        # Delete social accounts and tokens
        SocialToken.objects.filter(account__user=user).delete()
        SocialAccount.objects.filter(user=user).delete()

        # Anonymize user account (keep for record-keeping, but remove PII)
        user.email = f'deleted_{user.id}@deleted.crush.lu'
        user.username = f'deleted_user_{user.id}'
        user.first_name = ''
        user.last_name = ''
        user.is_active = False
        user.set_unusable_password()
        user.save()

        logger.info(f"Successfully anonymized user {user.id}")

    except Exception as e:
        logger.exception(f"Error during data deletion for user {user.id}: {str(e)}")
        raise


def data_deletion_status(request):
    """
    Page where users can check the status of their data deletion request.
    """
    confirmation_code = request.GET.get('code', '')
    return render(request, 'crush_lu/account/data_deletion_status.html', {
        'confirmation_code': confirmation_code
    })


@crush_login_required
def account_settings(request):
    """
    Account settings page with delete account option, email preferences,
    push notification preferences, and linked social accounts management.
    """
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site
    from ..social_photos import get_all_social_photos

    # Helper function to determine device type from device name
    def get_device_type(device_name):
        """Return 'mobile' or 'desktop' based on device name."""
        mobile_devices = ['Android Chrome', 'iPhone Safari']
        return 'mobile' if device_name in mobile_devices else 'desktop'

    # Get or create email preferences for this user
    email_prefs = EmailPreference.get_or_create_for_user(request.user)

    # Get push subscriptions - show card to all users (JS detects browser support)
    # PWA status is tracked on UserActivity model for analytics
    push_subscriptions = []
    push_subscriptions_json = '[]'
    is_pwa_user = False
    try:
        activity = UserActivity.objects.filter(user=request.user).first()
        if activity:
            is_pwa_user = activity.is_pwa_user

        # Always fetch push subscriptions - card visibility is controlled by JS
        subs = PushSubscription.objects.filter(
            user=request.user,
            enabled=True
        )
        for sub in subs:
            push_subscriptions.append({
                'id': sub.id,
                'endpoint': sub.endpoint,  # For current device detection
                'device_fingerprint': sub.device_fingerprint or '',  # Stable device identifier
                'device_name': sub.device_name or 'Unknown Device',
                'device_type': get_device_type(sub.device_name or ''),
                'last_used_at': sub.last_used_at,  # Keep as datetime for template filters
                'notify_new_messages': sub.notify_new_messages,
                'notify_event_reminders': sub.notify_event_reminders,
                'notify_new_connections': sub.notify_new_connections,
                'notify_profile_updates': sub.notify_profile_updates,
            })
        push_subscriptions_json = json.dumps(push_subscriptions, default=str)
    except Exception:
        pass

    # Check if user is a coach and get coach push subscriptions
    is_coach = False
    coach_push_subscriptions = []
    coach_push_subscriptions_json = '[]'
    try:
        if hasattr(request.user, 'crushcoach') and request.user.crushcoach.is_active:
            is_coach = True
            coach = request.user.crushcoach
            coach_subs = CoachPushSubscription.objects.filter(
                coach=coach,
                enabled=True
            )
            for sub in coach_subs:
                coach_push_subscriptions.append({
                    'id': sub.id,
                    'endpoint': sub.endpoint,  # For current device detection
                    'device_fingerprint': sub.device_fingerprint or '',  # Stable device identifier
                    'device_name': sub.device_name or 'Unknown Device',
                    'device_type': get_device_type(sub.device_name or ''),
                    'last_used_at': sub.last_used_at,  # Keep as datetime for template filters
                    'notify_new_submissions': sub.notify_new_submissions,
                    'notify_screening_reminders': sub.notify_screening_reminders,
                    'notify_user_responses': sub.notify_user_responses,
                    'notify_system_alerts': sub.notify_system_alerts,
                })
            coach_push_subscriptions_json = json.dumps(coach_push_subscriptions, default=str)
    except Exception:
        pass

    # Crush.lu only supports these social providers
    # (LinkedIn is PowerUP-only, not shown in Crush.lu account settings)
    CRUSH_SOCIAL_PROVIDERS = ['google', 'facebook', 'microsoft']

    # Get connected social providers for this user (filtered to Crush.lu providers)
    connected_providers = set(
        request.user.socialaccount_set.values_list('provider', flat=True)
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
            SocialApp.objects.filter(sites=current_site).values_list('provider', flat=True)
        )
    except Exception:
        available_providers = set()

    return render(request, 'crush_lu/account/account_settings.html', {
        'email_prefs': email_prefs,
        'google_connected': 'google' in connected_providers,
        'facebook_connected': 'facebook' in connected_providers,
        'microsoft_connected': 'microsoft' in connected_providers,
        'google_available': 'google' in available_providers,
        'facebook_available': 'facebook' in available_providers,
        'microsoft_available': 'microsoft' in available_providers,
        'crush_social_accounts': crush_social_accounts,  # Filtered list for display
        'social_photos': social_photos,  # Social photos for import
        # Push notification preferences (PWA users only)
        'is_pwa_user': is_pwa_user,
        'push_subscriptions': push_subscriptions,
        'push_subscriptions_json': push_subscriptions_json,
        # Coach push notification preferences (coaches only)
        'is_coach': is_coach,
        'coach_push_subscriptions': coach_push_subscriptions,
        'coach_push_subscriptions_json': coach_push_subscriptions_json,
    })


@crush_login_required
@require_http_methods(["POST"])
def update_email_preferences(request):
    """
    Handle email preference form submission.
    """
    email_prefs = EmailPreference.get_or_create_for_user(request.user)

    # Update preferences from form data
    # Checkboxes: if checked, the name is in POST data; if unchecked, it's absent
    email_prefs.unsubscribed_all = 'unsubscribed_all' in request.POST
    email_prefs.email_profile_updates = 'email_profile_updates' in request.POST
    email_prefs.email_event_reminders = 'email_event_reminders' in request.POST
    email_prefs.email_new_connections = 'email_new_connections' in request.POST
    email_prefs.email_new_messages = 'email_new_messages' in request.POST
    email_prefs.email_marketing = 'email_marketing' in request.POST

    email_prefs.save()

    messages.success(request, _('Email preferences updated successfully!'))
    return redirect('crush_lu:account_settings')


def email_unsubscribe(request, token):
    """
    One-click unsubscribe view.
    Accessible without login - uses secure token for authentication.

    GET: Show unsubscribe confirmation page
    POST: Process unsubscribe action
    """
    try:
        email_prefs = EmailPreference.objects.get(unsubscribe_token=token)
    except EmailPreference.DoesNotExist:
        messages.error(request, _('Invalid unsubscribe link. Please check your email or contact support.'))
        return render(request, 'crush_lu/account/email_unsubscribe.html', {
            'error': True,
            'token': token,
        })

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'unsubscribe_all':
            # Unsubscribe from ALL emails
            email_prefs.unsubscribed_all = True
            email_prefs.save()
            messages.success(request, _('You have been unsubscribed from all Crush.lu emails.'))

        elif action == 'unsubscribe_marketing':
            # Only unsubscribe from marketing emails
            email_prefs.email_marketing = False
            email_prefs.save()
            messages.success(request, _('You have been unsubscribed from marketing emails.'))

        elif action == 'resubscribe':
            # Re-enable all emails
            email_prefs.unsubscribed_all = False
            email_prefs.email_profile_updates = True
            email_prefs.email_event_reminders = True
            email_prefs.email_new_connections = True
            email_prefs.email_new_messages = True
            email_prefs.save()
            messages.success(request, _('You have been re-subscribed to Crush.lu emails.'))

        return render(request, 'crush_lu/account/email_unsubscribe.html', {
            'success': True,
            'email_prefs': email_prefs,
            'token': token,
        })

    # GET request - show unsubscribe form
    return render(request, 'crush_lu/account/email_unsubscribe.html', {
        'email_prefs': email_prefs,
        'token': token,
        'user': email_prefs.user,
    })


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
    from ..forms import CrushSetPasswordForm

    # Check if user has social account
    has_social = request.user.socialaccount_set.exists()
    has_password = request.user.has_usable_password()

    # Only allow if user has social account but no password
    if not has_social:
        messages.info(request, _('This feature is only for users who signed up with Facebook.'))
        return redirect('crush_lu:account_settings')

    if has_password:
        messages.info(request, _('You already have a password set. Use "Change Password" to update it.'))
        return redirect('crush_lu:account_settings')

    if request.method == 'POST':
        form = CrushSetPasswordForm(request.user, request.POST)
        if form.is_valid():
            form.save()

            # Keep user logged in after password change
            update_session_auth_hash(request, request.user)

            messages.success(
                request,
                'Password set successfully! You can now log in with your email and password.'
            )
            return redirect('crush_lu:account_settings')
    else:
        form = CrushSetPasswordForm(request.user)

    return render(request, 'crush_lu/account/set_password.html', {
        'form': form,
        'social_accounts': request.user.socialaccount_set.all(),
    })


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
        social_account = SocialAccount.objects.get(id=social_account_id, user=request.user)
    except SocialAccount.DoesNotExist:
        messages.error(request, _('Social account not found.'))
        return redirect('crush_lu:account_settings')

    # Security check: ensure user has another login method
    other_social_accounts = request.user.socialaccount_set.exclude(id=social_account_id).count()
    has_password = request.user.has_usable_password()

    if not has_password and other_social_accounts == 0:
        messages.error(
            request,
            f'Cannot disconnect {social_account.provider.title()} - you need at least one login method. '
            'Set a password first or connect another social account.'
        )
        return redirect('crush_lu:account_settings')

    # Log the disconnection
    provider_name = social_account.provider.title()
    logger.info(f"User {request.user.id} disconnected {provider_name} account")

    # Delete the social account
    social_account.delete()

    messages.success(request, f'{provider_name} account has been disconnected.')
    return redirect('crush_lu:account_settings')


@crush_login_required
@require_http_methods(["GET", "POST"])
def delete_account(request):
    """
    User-initiated account deletion.

    GET: Shows confirmation page
    POST: Deletes the account and logs out
    """
    if request.method == 'POST':
        # Verify password for security (if user has a password)
        password = request.POST.get('password', '')
        confirm_text = request.POST.get('confirm_text', '')

        # Check confirmation text
        if confirm_text.lower() != 'delete my account':
            messages.error(request, _('Please type "DELETE MY ACCOUNT" to confirm.'))
            return render(request, 'crush_lu/account/delete_account_confirm.html')

        # If user has a usable password, verify it
        if request.user.has_usable_password():
            if not request.user.check_password(password):
                messages.error(request, _('Incorrect password. Please try again.'))
                return render(request, 'crush_lu/account/delete_account_confirm.html')

        # Generate confirmation code
        confirmation_code = str(uuid.uuid4())

        # Log the deletion
        logger.info(f"User {request.user.id} ({request.user.email}) requested account deletion")

        # Delete user data
        try:
            delete_user_data(request.user, confirmation_code)

            # Log the user out
            logout(request)

            messages.success(request, _('Your account has been successfully deleted.'))

            # Redirect to status page with confirmation
            return redirect(f'/data-deletion/status/?code={confirmation_code}')

        except Exception as e:
            logger.exception(f"Error deleting account for user {request.user.id}: {str(e)}")
            messages.error(request, _('An error occurred while deleting your account. Please contact support.'))
            return render(request, 'crush_lu/account/delete_account_confirm.html')

    # GET request - show confirmation page
    return render(request, 'crush_lu/account/delete_account_confirm.html')
