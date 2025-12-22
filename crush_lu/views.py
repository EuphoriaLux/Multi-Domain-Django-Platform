from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from datetime import timedelta
import logging
import uuid
import json
import base64
import hashlib
import hmac

logger = logging.getLogger(__name__)

from .models import (
    CrushProfile, CrushCoach, ProfileSubmission,
    MeetupEvent, EventRegistration, CoachSession,
    EventConnection, ConnectionMessage,
    EventActivityOption, EventActivityVote, EventVotingSession,
    PresentationQueue, PresentationRating, SpeedDatingPair,
    SpecialUserExperience, EventInvitation
)
from .forms import (
    CrushSignupForm, CrushProfileForm, CrushCoachForm, ProfileReviewForm,
    CoachSessionForm, EventRegistrationForm
)
from .decorators import crush_login_required, ratelimit
from .email_helpers import (
    send_welcome_email,
    send_profile_submission_confirmation,
    send_coach_assignment_notification,
    send_profile_approved_notification,
    send_profile_revision_request,
    send_profile_rejected_notification,
    send_event_registration_confirmation,
    send_event_waitlist_notification,
    send_event_cancellation_confirmation,
)


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
        return redirect('crush_lu:login')

    # Get the intended destination from session, or default to dashboard
    final_destination = request.session.pop('oauth_final_destination', '/dashboard/')

    # Check if user has a profile
    if not hasattr(request.user, 'crushprofile'):
        final_destination = '/create-profile/'

    context = {
        'final_destination': final_destination,
        'user': request.user,
    }
    return render(request, 'crush_lu/oauth_complete.html', context)


# Public pages
def home(request):
    """Landing page - redirects authenticated users to dashboard"""
    # If user is logged in, redirect to their dashboard
    if request.user.is_authenticated:
        return redirect('crush_lu:dashboard')

    upcoming_events = MeetupEvent.objects.filter(
        is_published=True,
        is_cancelled=False,
        date_time__gte=timezone.now()
    )[:3]

    context = {
        'upcoming_events': upcoming_events,
    }
    return render(request, 'crush_lu/home.html', context)


def about(request):
    """About page"""
    return render(request, 'crush_lu/about.html')


def how_it_works(request):
    """How it works page"""
    return render(request, 'crush_lu/how_it_works.html')


def privacy_policy(request):
    """Privacy policy page"""
    return render(request, 'crush_lu/privacy_policy.html')


def terms_of_service(request):
    """Terms of service page"""
    return render(request, 'crush_lu/terms_of_service.html')


def data_deletion_request(request):
    """Data deletion instructions page"""
    return render(request, 'crush_lu/data_deletion.html')


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
    1. Deletes CrushProfile and related data
    2. Deletes social account connections
    3. Anonymizes the user account (or deletes entirely)

    Args:
        user: The Django User instance
        confirmation_code: Unique code for tracking this deletion
    """
    from allauth.socialaccount.models import SocialAccount, SocialToken

    logger.info(f"Starting data deletion for user {user.id}, confirmation: {confirmation_code}")

    try:
        # Delete CrushProfile if exists
        if hasattr(user, 'crushprofile'):
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

        # Delete ProfileSubmissions
        ProfileSubmission.objects.filter(profile__user=user).delete()

        # Delete EventRegistrations
        EventRegistration.objects.filter(user=user).delete()

        # Delete EventConnections (both as user1 and user2)
        EventConnection.objects.filter(Q(user1=user) | Q(user2=user)).delete()

        # Delete ConnectionMessages
        ConnectionMessage.objects.filter(Q(sender=user) | Q(recipient=user)).delete()

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
    return render(request, 'crush_lu/data_deletion_status.html', {
        'confirmation_code': confirmation_code
    })


@crush_login_required
def account_settings(request):
    """
    Account settings page with delete account option, email preferences,
    and linked social accounts management.
    """
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site
    from .models import EmailPreference
    from .social_photos import get_all_social_photos

    # Get or create email preferences for this user
    email_prefs = EmailPreference.get_or_create_for_user(request.user)

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

    return render(request, 'crush_lu/account_settings.html', {
        'email_prefs': email_prefs,
        'google_connected': 'google' in connected_providers,
        'facebook_connected': 'facebook' in connected_providers,
        'microsoft_connected': 'microsoft' in connected_providers,
        'google_available': 'google' in available_providers,
        'facebook_available': 'facebook' in available_providers,
        'microsoft_available': 'microsoft' in available_providers,
        'crush_social_accounts': crush_social_accounts,  # Filtered list for display
        'social_photos': social_photos,  # Social photos for import
    })


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
    email_prefs.unsubscribed_all = 'unsubscribed_all' in request.POST
    email_prefs.email_profile_updates = 'email_profile_updates' in request.POST
    email_prefs.email_event_reminders = 'email_event_reminders' in request.POST
    email_prefs.email_new_connections = 'email_new_connections' in request.POST
    email_prefs.email_new_messages = 'email_new_messages' in request.POST
    email_prefs.email_marketing = 'email_marketing' in request.POST

    email_prefs.save()

    messages.success(request, 'Email preferences updated successfully!')
    return redirect('crush_lu:account_settings')


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
        messages.error(request, 'Invalid unsubscribe link. Please check your email or contact support.')
        return render(request, 'crush_lu/email_unsubscribe.html', {
            'error': True,
            'token': token,
        })

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'unsubscribe_all':
            # Unsubscribe from ALL emails
            email_prefs.unsubscribed_all = True
            email_prefs.save()
            messages.success(request, 'You have been unsubscribed from all Crush.lu emails.')

        elif action == 'unsubscribe_marketing':
            # Only unsubscribe from marketing emails
            email_prefs.email_marketing = False
            email_prefs.save()
            messages.success(request, 'You have been unsubscribed from marketing emails.')

        elif action == 'resubscribe':
            # Re-enable all emails
            email_prefs.unsubscribed_all = False
            email_prefs.email_profile_updates = True
            email_prefs.email_event_reminders = True
            email_prefs.email_new_connections = True
            email_prefs.email_new_messages = True
            email_prefs.save()
            messages.success(request, 'You have been re-subscribed to Crush.lu emails.')

        return render(request, 'crush_lu/email_unsubscribe.html', {
            'success': True,
            'email_prefs': email_prefs,
            'token': token,
        })

    # GET request - show unsubscribe form
    return render(request, 'crush_lu/email_unsubscribe.html', {
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
    from .forms import CrushSetPasswordForm

    # Check if user has social account
    has_social = request.user.socialaccount_set.exists()
    has_password = request.user.has_usable_password()

    # Only allow if user has social account but no password
    if not has_social:
        messages.info(request, 'This feature is only for users who signed up with Facebook.')
        return redirect('crush_lu:account_settings')

    if has_password:
        messages.info(request, 'You already have a password set. Use "Change Password" to update it.')
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

    return render(request, 'crush_lu/set_password.html', {
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
        messages.error(request, 'Social account not found.')
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
            messages.error(request, 'Please type "DELETE MY ACCOUNT" to confirm.')
            return render(request, 'crush_lu/delete_account_confirm.html')

        # If user has a usable password, verify it
        if request.user.has_usable_password():
            if not request.user.check_password(password):
                messages.error(request, 'Incorrect password. Please try again.')
                return render(request, 'crush_lu/delete_account_confirm.html')

        # Generate confirmation code
        confirmation_code = str(uuid.uuid4())

        # Log the deletion
        logger.info(f"User {request.user.id} ({request.user.email}) requested account deletion")

        # Delete user data
        try:
            delete_user_data(request.user, confirmation_code)

            # Log the user out
            logout(request)

            messages.success(request, 'Your account has been successfully deleted.')

            # Redirect to status page with confirmation
            return redirect(f'/data-deletion/status/?code={confirmation_code}')

        except Exception as e:
            logger.exception(f"Error deleting account for user {request.user.id}: {str(e)}")
            messages.error(request, 'An error occurred while deleting your account. Please contact support.')
            return render(request, 'crush_lu/delete_account_confirm.html')

    # GET request - show confirmation page
    return render(request, 'crush_lu/delete_account_confirm.html')


# Onboarding
@ratelimit(key='ip', rate='5/h', method='POST')
def signup(request):
    """
    User registration with Allauth integration
    Supports both manual signup and social login (LinkedIn, Google, etc.)
    Uses unified auth template with login/signup tabs
    """
    from allauth.account.forms import LoginForm

    signup_form = CrushSignupForm()
    login_form = LoginForm()

    if request.method == 'POST':
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
                    logger.error(f"❌ Failed to send welcome email to {user.email}: {e}", exc_info=True)
                    # Don't block signup if email fails

                messages.success(request, 'Account created! Check your email and complete your profile.')
                # Log the user in - set backend for multi-auth compatibility
                user.backend = 'django.contrib.auth.backends.ModelBackend'
                login(request, user)
                return redirect('crush_lu:create_profile')

            except Exception as e:
                # Handle duplicate email/username errors
                logger.error(f"❌ Signup failed for email: {e}", exc_info=True)

                # Check if it's a duplicate email error
                error_msg = str(e).lower()
                if 'unique' in error_msg or 'duplicate' in error_msg or 'already exists' in error_msg:
                    messages.error(
                        request,
                        'An account with this email already exists. '
                        'Please login or use a different email.'
                    )
                else:
                    messages.error(
                        request,
                        'An error occurred while creating your account. Please try again.'
                    )

    context = {
        'signup_form': signup_form,
        'login_form': login_form,
        'mode': 'signup',
    }
    return render(request, 'crush_lu/auth.html', context)


@crush_login_required
def create_profile(request):
    """Profile creation - coaches can also create dating profiles"""
    # If it's a POST request, process the form submission first
    if request.method == 'POST':
        # Get existing profile if it exists (from Steps 1-2 AJAX saves)
        try:
            existing_profile = CrushProfile.objects.get(user=request.user)
            form = CrushProfileForm(request.POST, request.FILES, instance=existing_profile)
        except CrushProfile.DoesNotExist:
            form = CrushProfileForm(request.POST, request.FILES)

        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = request.user

            # Check if this is first submission or resubmission
            is_first_submission = profile.completion_status != 'submitted'

            # Mark profile as completed and submitted
            profile.completion_status = 'submitted'

            # Note: Screening call handled in ProfileSubmission.review_call_completed
            # No need to set flags here - coach will do screening during review
            if is_first_submission:
                logger.info(f"First submission - screening call will be done during coach review for {request.user.email}")
            else:
                logger.info(f"Resubmission detected for {request.user.email}")

            profile.save()
            logger.info(f"Profile submitted for review: {request.user.email}")

            # Create profile submission for coach review (PREVENT DUPLICATES)
            # Use get_or_create to handle double-submissions gracefully
            submission, created = ProfileSubmission.objects.get_or_create(
                profile=profile,
                defaults={'status': 'pending'}
            )

            # Only assign coach and send emails for NEW submissions
            if created:
                submission.assign_coach()
                logger.info(f"NEW profile submission created for {request.user.email}")

                # Send confirmation email to user
                try:
                    result = send_profile_submission_confirmation(request.user, request)
                    logger.info(f"✅ Profile submission email sent to {request.user.email}: {result}")
                except Exception as e:
                    # Log error but don't block the flow
                    logger.error(f"❌ Failed to send profile submission confirmation to {request.user.email}: {e}", exc_info=True)
                    # Also show warning message to user
                    messages.warning(request, 'Profile submitted! (Email notification may have failed - check your spam folder)')

                # Send notification to assigned coach if one was assigned
                if submission.coach:
                    try:
                        result = send_coach_assignment_notification(submission.coach, submission, request)
                        logger.info(f"✅ Coach assignment email sent to {submission.coach.user.email}: {result}")
                    except Exception as e:
                        logger.error(f"❌ Failed to send coach assignment notification: {e}", exc_info=True)
            else:
                # Duplicate submission attempt - just log and continue
                logger.warning(f"⚠️ Duplicate submission attempt prevented for {request.user.email}")

            messages.success(request, 'Profile submitted for review!')
            return redirect('crush_lu:profile_submitted')
        else:
            # CRITICAL: Log validation errors
            logger.error(f"❌ Profile form validation failed for user {request.user.email}")
            logger.error(f"❌ Form errors: {form.errors.as_json()}")

            # Show user-friendly error messages
            for field, errors in form.errors.items():
                for error in errors:
                    if field == '__all__':
                        messages.error(request, f"Form error: {error}")
                    else:
                        messages.error(request, f"{field.replace('_', ' ').title()}: {error}")

            # When validation fails, show Step 4 (review step) so user can see errors
            # and resubmit from the review screen
            from .social_photos import get_all_social_photos
            context = {
                'form': form,
                'current_step': 'step3',  # Show review step where submit button is
                'social_photos': get_all_social_photos(request.user),
            }
            return render(request, 'crush_lu/create_profile.html', context)

    # GET request - check if profile already exists and redirect accordingly
    try:
        profile = CrushProfile.objects.get(user=request.user)

        # If profile is submitted, show status page instead of edit form
        if profile.completion_status == 'submitted':
            messages.info(request, 'Your profile has been submitted. Check the status below.')
            return redirect('crush_lu:profile_submitted')
        elif profile.completion_status == 'not_started':
            # Fresh profile (auto-created on login) - show creation form
            from .social_photos import get_all_social_photos
            form = CrushProfileForm(instance=profile)
            return render(request, 'crush_lu/create_profile.html', {
                'form': form,
                'social_photos': get_all_social_photos(request.user),
            })
        else:
            # Incomplete profile (in_progress, etc.) - redirect to edit
            messages.info(request, 'Continue completing your profile.')
            return redirect('crush_lu:edit_profile')
    except CrushProfile.DoesNotExist:
        # No profile yet - show creation form
        from .social_photos import get_all_social_photos
        form = CrushProfileForm()
        return render(request, 'crush_lu/create_profile.html', {
            'form': form,
            'social_photos': get_all_social_photos(request.user),
        })


@crush_login_required
def edit_profile_simple(request):
    """Simple single-page edit for approved profiles - coaches can also edit their dating profiles"""
    # Get existing profile
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        messages.info(request, 'You need to create a profile first.')
        return redirect('crush_lu:create_profile')

    # Only approved profiles use this simple edit page
    if not profile.is_approved:
        messages.warning(request, 'Your profile must be approved before using quick edit.')
        return redirect('crush_lu:edit_profile')

    from .social_photos import get_all_social_photos

    if request.method == 'POST':
        form = CrushProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            updated_profile = form.save(commit=False)

            # SECURITY: Phone number change protection
            new_phone = updated_profile.phone_number
            old_phone = profile.phone_number

            if profile.phone_verified:
                # Case 1: Phone is already verified - keep the original verified number
                updated_profile.phone_number = old_phone
                updated_profile.phone_verified = True
                updated_profile.phone_verified_at = profile.phone_verified_at
                updated_profile.phone_verification_uid = profile.phone_verification_uid
            elif new_phone != old_phone:
                # Case 2: Phone NOT verified AND user is trying to change it
                # Reject the change - they must verify their current number first
                updated_profile.phone_number = old_phone
                messages.warning(
                    request,
                    'To change your phone number, please verify your current number first. '
                    'Go to the phone verification page to verify.'
                )

            # Keep approved status - this is just an edit
            updated_profile.save()
            messages.success(request, 'Profile updated successfully!')
            return redirect('crush_lu:dashboard')
        else:
            # Show validation errors
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field.replace('_', ' ').title()}: {error}")
    else:
        form = CrushProfileForm(instance=profile)

    context = {
        'form': form,
        'profile': profile,
        'social_photos': get_all_social_photos(request.user),
    }
    return render(request, 'crush_lu/edit_profile_simple.html', context)


@crush_login_required
def edit_profile(request):
    """Edit existing profile - routes to appropriate edit flow"""
    # Try to get existing profile, redirect to create if doesn't exist
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        messages.info(request, 'You need to create a profile first.')
        return redirect('crush_lu:create_profile')

    # ROUTING LOGIC: Determine which edit flow to use

    # 1. If profile is approved → use simple single-page edit
    if profile.is_approved:
        return edit_profile_simple(request)

    # 2. If profile is submitted and under review → redirect to status page
    if profile.completion_status == 'submitted':
        try:
            submission = ProfileSubmission.objects.filter(profile=profile).latest('submitted_at')
            # If pending or under review, can't edit
            if submission.status in ['pending', 'under_review']:
                messages.info(request, 'Your profile is currently under review. You\'ll be notified once it\'s approved.')
                return redirect('crush_lu:profile_submitted')
            # If rejected or needs revision, allow multi-step editing with feedback
            elif submission.status in ['rejected', 'revision']:
                messages.warning(request, 'Your profile needs updates. Please review the coach feedback below.')
                # Fall through to multi-step edit with feedback context
        except ProfileSubmission.DoesNotExist:
            pass

    # 3. Default: Use multi-step form for incomplete or rejected profiles
    # Store original phone for change detection
    original_phone = profile.phone_number
    original_phone_verified = profile.phone_verified

    if request.method == 'POST':
        form = CrushProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            profile = form.save(commit=False)

            # SECURITY: Phone number change protection (same as edit_profile_simple)
            if original_phone_verified:
                # Phone was verified - keep the original
                profile.phone_number = original_phone
                profile.phone_verified = True
            elif profile.phone_number != original_phone and original_phone:
                # Phone NOT verified AND user is trying to change an existing number
                # Reject the change - they must verify first
                profile.phone_number = original_phone
                messages.warning(
                    request,
                    'To change your phone number, please verify your current number first.'
                )

            # Mark as submitted when completing the form
            profile.completion_status = 'submitted'
            profile.save()

            # Create or update profile submission for coach review
            submission, created = ProfileSubmission.objects.get_or_create(
                profile=profile,
                defaults={'status': 'pending'}
            )
            if created:
                submission.assign_coach()

                # Send confirmation email to user
                try:
                    result = send_profile_submission_confirmation(request.user, request)
                    logger.info(f"✅ Profile submission email sent to {request.user.email}: {result}")
                except Exception as e:
                    logger.error(f"❌ Failed to send profile submission confirmation to {request.user.email}: {e}", exc_info=True)
                    messages.warning(request, 'Profile submitted! (Email notification may have failed)')

                # Send notification to assigned coach if one was assigned
                if submission.coach:
                    try:
                        result = send_coach_assignment_notification(submission.coach, submission, request)
                        logger.info(f"✅ Coach assignment email sent to {submission.coach.user.email}: {result}")
                    except Exception as e:
                        logger.error(f"❌ Failed to send coach assignment notification: {e}", exc_info=True)

            messages.success(request, 'Profile submitted for review!')
            return redirect('crush_lu:profile_submitted')
    else:
        form = CrushProfileForm(instance=profile)

    # Get latest submission for feedback context
    latest_submission = None
    try:
        latest_submission = ProfileSubmission.objects.filter(profile=profile).latest('submitted_at')
    except ProfileSubmission.DoesNotExist:
        pass

    # Determine which step to show based on submission status
    current_step_to_show = None
    if latest_submission and latest_submission.status in ['rejected', 'revision']:
        # For rejected profiles, start from Step 1 so they can review everything
        current_step_to_show = None  # This will default to step 1 in JavaScript
    elif profile.completion_status == 'submitted':
        # If submitted but no rejection, default to None (step 1)
        current_step_to_show = None
    elif profile.completion_status == 'not_started':
        # Brand new profiles (e.g., Facebook signup) that haven't completed any steps
        current_step_to_show = None  # Start at step 1
    elif profile.completion_status == 'step1' and (not profile.date_of_birth or not profile.phone_number):
        # For incomplete Step 1 profiles (missing required fields)
        # Phone number and date_of_birth are required for Step 1 completion
        current_step_to_show = None  # Start at step 1
    else:
        # Resume from where they left off for incomplete profiles
        current_step_to_show = profile.completion_status

    context = {
        'form': form,
        'profile': profile,
        'is_editing': True,
        'current_step': current_step_to_show,
        'submission': latest_submission,  # Pass submission for feedback display
    }
    return render(request, 'crush_lu/create_profile.html', context)


@crush_login_required
def profile_submitted(request):
    """Confirmation page after profile submission"""
    try:
        profile = CrushProfile.objects.get(user=request.user)
        submission = ProfileSubmission.objects.filter(profile=profile).latest('submitted_at')
    except (CrushProfile.DoesNotExist, ProfileSubmission.DoesNotExist):
        messages.error(request, 'No profile submission found.')
        return redirect('crush_lu:create_profile')

    context = {
        'submission': submission,
    }
    return render(request, 'crush_lu/profile_submitted.html', context)


# User dashboard
@crush_login_required
def dashboard(request):
    """User dashboard - redirects ACTIVE coaches to their dashboard unless ?user_view=1"""
    # Check if user is an ACTIVE coach
    # Allow coaches to view their user dashboard via ?user_view=1 parameter
    user_view = request.GET.get('user_view') == '1'
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
        if not user_view:
            return redirect('crush_lu:coach_dashboard')
    except CrushCoach.DoesNotExist:
        # Either no coach record, or coach is inactive - show dating dashboard
        pass

    # Regular user dashboard
    try:
        profile = CrushProfile.objects.get(user=request.user)
        # Get latest submission status
        latest_submission = ProfileSubmission.objects.filter(
            profile=profile
        ).order_by('-submitted_at').first()

        # Get user's event registrations
        registrations = EventRegistration.objects.filter(
            user=request.user
        ).select_related('event').order_by('-event__date_time')

        # Get connection count
        connection_count = EventConnection.objects.filter(
            Q(requester=request.user) | Q(recipient=request.user),
            status__in=['accepted', 'coach_reviewing', 'coach_approved', 'shared']
        ).count()

        context = {
            'profile': profile,
            'submission': latest_submission,
            'registrations': registrations,
            'connection_count': connection_count,
        }
    except CrushProfile.DoesNotExist:
        messages.warning(request, 'Please complete your profile first.')
        return redirect('crush_lu:create_profile')

    return render(request, 'crush_lu/dashboard.html', context)


# Events
def event_list(request):
    """List of upcoming events - filters private invitation events"""
    # Base query: published, non-cancelled, future events
    events = MeetupEvent.objects.filter(
        is_published=True,
        is_cancelled=False,
        date_time__gte=timezone.now()
    ).order_by('date_time')

    # FILTER OUT PRIVATE EVENTS for non-invited users
    if request.user.is_authenticated:
        # Check if user has approved EventInvitation (external guests)
        user_invitations = EventInvitation.objects.filter(
            created_user=request.user,
            approval_status='approved'
        ).values_list('event_id', flat=True)

        # Show: public events + private events they're invited to (either as existing user OR external guest)
        events = events.filter(
            Q(is_private_invitation=False) |  # Public events
            Q(id__in=user_invitations) |  # Private events with approved external invitation
            Q(invited_users=request.user)  # Private events where they're invited as existing user
        )
    else:
        # Public visitors: only see public events
        events = events.filter(is_private_invitation=False)

    # Filter by event type if provided
    event_type = request.GET.get('type')
    if event_type:
        events = events.filter(event_type=event_type)

    # For coaches: show unpublished events count
    unpublished_count = 0
    if request.user.is_authenticated:
        try:
            coach = CrushCoach.objects.get(user=request.user, is_active=True)
            unpublished_count = MeetupEvent.objects.filter(
                is_published=False,
                is_cancelled=False,
                date_time__gte=timezone.now()
            ).count()
        except CrushCoach.DoesNotExist:
            pass

    context = {
        'events': events,
        'event_types': MeetupEvent.EVENT_TYPE_CHOICES,
        'unpublished_count': unpublished_count,
    }
    return render(request, 'crush_lu/event_list.html', context)


def event_detail(request, event_id):
    """Event detail page with access control for private events"""
    event = get_object_or_404(MeetupEvent, id=event_id, is_published=True)

    # ACCESS CONTROL for private invitation events
    if event.is_private_invitation:
        if not request.user.is_authenticated:
            messages.error(request, 'This is a private invitation-only event. Please log in.')
            return redirect('crush_lu:crush_login')

        # Check if user has approved external guest invitation OR is invited as existing user
        has_external_invitation = EventInvitation.objects.filter(
            event=event,
            created_user=request.user,
            approval_status='approved'
        ).exists()

        is_invited_existing_user = event.invited_users.filter(id=request.user.id).exists()

        if not has_external_invitation and not is_invited_existing_user:
            messages.error(request, 'You do not have access to this private event.')
            return redirect('crush_lu:event_list')

    # Check if user is registered
    user_registration = None
    if request.user.is_authenticated:
        user_registration = EventRegistration.objects.filter(
            event=event,
            user=request.user
        ).first()

    context = {
        'event': event,
        'user_registration': user_registration,
    }
    return render(request, 'crush_lu/event_detail.html', context)


@crush_login_required
def event_register(request, event_id):
    """Register for an event - bypasses approval for invited guests"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # FOR PRIVATE INVITATION EVENTS: Bypass normal profile approval flow
    if event.is_private_invitation:
        # Check if user is invited as existing user OR has approved external invitation
        is_invited_existing_user = event.invited_users.filter(id=request.user.id).exists()

        external_invitation = EventInvitation.objects.filter(
            event=event,
            created_user=request.user,
            approval_status='approved'
        ).first()

        if not is_invited_existing_user and not external_invitation:
            messages.error(request, 'You do not have an approved invitation for this event.')
            return redirect('crush_lu:event_detail', event_id=event_id)

        # EXISTING USERS: No profile creation needed - use their existing profile
        if is_invited_existing_user:
            try:
                profile = CrushProfile.objects.get(user=request.user)
                # Existing users keep their own profile approval status
            except CrushProfile.DoesNotExist:
                # Edge case: invited user doesn't have profile yet - create minimal one
                profile = CrushProfile.objects.create(
                    user=request.user,
                    is_approved=True,
                    approved_at=timezone.now(),
                    completion_status='completed',
                    date_of_birth=timezone.now().date() - timedelta(days=365*25),
                )
                logger.info(f"Auto-created profile for invited existing user: {request.user.email}")

        # EXTERNAL GUESTS: Create minimal profile with auto-approval
        else:
            profile, created = CrushProfile.objects.get_or_create(
                user=request.user,
                defaults={
                    'is_approved': True,  # Auto-approve VIP guests
                    'approved_at': timezone.now(),
                    'completion_status': 'completed',
                    'date_of_birth': timezone.now().date() - timedelta(days=365*25),  # Default age 25
                }
            )
            if created:
                logger.info(f"Auto-created approved profile for external VIP guest: {request.user.email}")
    else:
        # NORMAL EVENT: Require approved profile
        try:
            profile = CrushProfile.objects.get(user=request.user)
            if not profile.is_approved:
                messages.error(request, 'Your profile must be approved before registering for events.')
                return redirect('crush_lu:event_detail', event_id=event_id)
        except CrushProfile.DoesNotExist:
            messages.error(request, 'Please create a profile first.')
            return redirect('crush_lu:create_profile')

    # Check if already registered
    if EventRegistration.objects.filter(event=event, user=request.user).exists():
        messages.warning(request, 'You are already registered for this event.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Check if registration is open
    if not event.is_registration_open:
        messages.error(request, 'Registration is not available for this event.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    if request.method == 'POST':
        form = EventRegistrationForm(request.POST)
        if form.is_valid():
            registration = form.save(commit=False)
            registration.event = event
            registration.user = request.user

            # Set status based on availability
            if event.is_full:
                registration.status = 'waitlist'
                messages.info(request, 'Event is full. You have been added to the waitlist.')
            else:
                registration.status = 'confirmed'
                messages.success(request, 'Successfully registered for the event!')

            registration.save()

            # Send confirmation or waitlist email
            try:
                if registration.status == 'confirmed':
                    send_event_registration_confirmation(registration, request)
                elif registration.status == 'waitlist':
                    send_event_waitlist_notification(registration, request)
            except Exception as e:
                logger.error(f"Failed to send event registration email: {e}")

            return redirect('crush_lu:dashboard')
    else:
        form = EventRegistrationForm()

    context = {
        'event': event,
        'form': form,
    }
    return render(request, 'crush_lu/event_register.html', context)


@crush_login_required
def event_cancel(request, event_id):
    """Cancel event registration"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if request.method == 'POST':
        registration.status = 'cancelled'
        registration.save()
        messages.success(request, 'Your registration has been cancelled.')

        # Send cancellation confirmation email
        try:
            send_event_cancellation_confirmation(request.user, event, request)
        except Exception as e:
            logger.error(f"Failed to send event cancellation confirmation: {e}")

        return redirect('crush_lu:dashboard')

    context = {
        'event': event,
        'registration': registration,
    }
    return render(request, 'crush_lu/event_cancel.html', context)


# Coach views
@crush_login_required
def coach_dashboard(request):
    """Coach dashboard for reviewing profiles"""
    try:
        coach = CrushCoach.objects.get(user=request.user)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'You do not have coach access.')
        return redirect('crush_lu:dashboard')

    # Get pending submissions assigned to this coach
    pending_submissions = ProfileSubmission.objects.filter(
        coach=coach,
        status='pending'
    ).select_related('profile__user')

    # Get recently reviewed
    recent_reviews = ProfileSubmission.objects.filter(
        coach=coach,
        status__in=['approved', 'rejected', 'revision']
    ).select_related('profile__user').order_by('-reviewed_at')[:10]

    context = {
        'coach': coach,
        'pending_submissions': pending_submissions,
        'recent_reviews': recent_reviews,
    }
    return render(request, 'crush_lu/coach_dashboard.html', context)


@crush_login_required
def coach_mark_review_call_complete(request, submission_id):
    """Mark screening call as complete during profile review"""
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'You do not have coach access.')
        return redirect('crush_lu:dashboard')

    submission = get_object_or_404(
        ProfileSubmission,
        id=submission_id,
        coach=coach
    )

    if request.method == 'POST':
        submission.review_call_completed = True
        submission.review_call_date = timezone.now()
        submission.review_call_notes = request.POST.get('call_notes', '')
        submission.save()

        messages.success(request, f'✅ Screening call marked complete for {submission.profile.user.first_name}. You can now approve the profile.')
        return redirect('crush_lu:coach_review_profile', submission_id=submission.id)

    return redirect('crush_lu:coach_review_profile', submission_id=submission.id)


@crush_login_required
def coach_review_profile(request, submission_id):
    """Review a profile submission"""
    try:
        coach = CrushCoach.objects.get(user=request.user)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'You do not have coach access.')
        return redirect('crush_lu:dashboard')

    submission = get_object_or_404(
        ProfileSubmission,
        id=submission_id,
        coach=coach
    )

    if request.method == 'POST':
        form = ProfileReviewForm(request.POST, instance=submission)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.reviewed_at = timezone.now()

            # Update profile approval status and send notifications
            if submission.status == 'approved':
                # REQUIRE screening call before approval
                if not submission.review_call_completed:
                    messages.error(request, '⚠️ You must complete a screening call before approving this profile.')
                    form = ProfileReviewForm(instance=submission)
                    context = {
                        'coach': coach,
                        'submission': submission,
                        'form': form,
                    }
                    return render(request, 'crush_lu/coach_review_profile.html', context)

                submission.profile.is_approved = True
                submission.profile.approved_at = timezone.now()
                submission.profile.save()
                messages.success(request, 'Profile approved!')

                # Send approval notification to user
                try:
                    send_profile_approved_notification(
                        submission.profile,
                        request,
                        coach_notes=submission.feedback_to_user
                    )
                except Exception as e:
                    logger.error(f"Failed to send profile approval notification: {e}")

            elif submission.status == 'rejected':
                submission.profile.is_approved = False
                submission.profile.save()
                messages.info(request, 'Profile rejected.')

                # Send rejection notification to user
                try:
                    send_profile_rejected_notification(
                        submission.profile,
                        request,
                        reason=submission.feedback_to_user
                    )
                except Exception as e:
                    logger.error(f"Failed to send profile rejection notification: {e}")

            elif submission.status == 'revision':
                messages.info(request, 'Revision requested.')

                # Send revision request to user
                try:
                    send_profile_revision_request(
                        submission.profile,
                        request,
                        feedback=submission.feedback_to_user
                    )
                except Exception as e:
                    logger.error(f"Failed to send profile revision request: {e}")

            submission.save()
            return redirect('crush_lu:coach_dashboard')
    else:
        form = ProfileReviewForm(instance=submission)

    context = {
        'submission': submission,
        'profile': submission.profile,
        'form': form,
    }
    return render(request, 'crush_lu/coach_review_profile.html', context)


@crush_login_required
def coach_sessions(request):
    """View and manage coach sessions"""
    try:
        coach = CrushCoach.objects.get(user=request.user)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'You do not have coach access.')
        return redirect('crush_lu:dashboard')

    sessions = CoachSession.objects.filter(coach=coach).order_by('-created_at')

    context = {
        'coach': coach,
        'sessions': sessions,
    }
    return render(request, 'crush_lu/coach_sessions.html', context)


@crush_login_required
def coach_edit_profile(request):
    """Edit coach profile (bio, specializations, photo) - separate from dating profile"""
    try:
        coach = CrushCoach.objects.get(user=request.user)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'You do not have a coach profile.')
        return redirect('crush_lu:dashboard')

    if request.method == 'POST':
        form = CrushCoachForm(request.POST, request.FILES, instance=coach)
        if form.is_valid():
            form.save()
            messages.success(request, 'Coach profile updated successfully!')
            return redirect('crush_lu:coach_dashboard')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field.replace('_', ' ').title()}: {error}")
    else:
        form = CrushCoachForm(instance=coach)

    # Check if coach also has a dating profile
    has_dating_profile = hasattr(request.user, 'crushprofile')

    context = {
        'coach': coach,
        'form': form,
        'has_dating_profile': has_dating_profile,
    }
    return render(request, 'crush_lu/coach_edit_profile.html', context)


# ============================================================================
# COACH JOURNEY MANAGEMENT - Manage Wonderland Journey Experiences
# ============================================================================


@crush_login_required
def coach_journey_dashboard(request):
    """Coach dashboard for managing all active journeys and their challenges"""
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'You do not have coach access.')
        return redirect('crush_lu:dashboard')

    from .models import JourneyConfiguration, JourneyProgress

    # Get all active journeys
    active_journeys = JourneyConfiguration.objects.filter(
        is_active=True
    ).select_related('special_experience').prefetch_related('chapters__challenges')

    # Get user progress for each journey
    journeys_with_progress = []
    for journey in active_journeys:
        progress_list = JourneyProgress.objects.filter(
            journey=journey
        ).select_related('user').order_by('-last_activity')[:5]

        journeys_with_progress.append({
            'journey': journey,
            'recent_progress': progress_list,
            'total_users': JourneyProgress.objects.filter(journey=journey).count(),
            'completed_users': JourneyProgress.objects.filter(journey=journey, is_completed=True).count(),
        })

    context = {
        'coach': coach,
        'journeys_with_progress': journeys_with_progress,
    }
    return render(request, 'crush_lu/coach_journey_dashboard.html', context)


@crush_login_required
def coach_edit_journey(request, journey_id):
    """Edit a journey's chapters and challenges"""
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'You do not have coach access.')
        return redirect('crush_lu:dashboard')

    from .models import JourneyConfiguration

    journey = get_object_or_404(JourneyConfiguration, id=journey_id)

    # Get all chapters with challenges
    chapters = journey.chapters.all().prefetch_related('challenges', 'rewards')

    context = {
        'coach': coach,
        'journey': journey,
        'chapters': chapters,
    }
    return render(request, 'crush_lu/coach_edit_journey.html', context)


@crush_login_required
def coach_edit_challenge(request, challenge_id):
    """Edit an individual challenge's question and content"""
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'You do not have coach access.')
        return redirect('crush_lu:dashboard')

    from .models import JourneyChallenge, ChallengeAttempt

    challenge = get_object_or_404(JourneyChallenge, id=challenge_id)

    if request.method == 'POST':
        # Update challenge fields
        challenge.question = request.POST.get('question', challenge.question)
        challenge.correct_answer = request.POST.get('correct_answer', challenge.correct_answer)
        challenge.success_message = request.POST.get('success_message', challenge.success_message)

        # Update hints
        challenge.hint_1 = request.POST.get('hint_1', challenge.hint_1)
        challenge.hint_2 = request.POST.get('hint_2', challenge.hint_2)
        challenge.hint_3 = request.POST.get('hint_3', challenge.hint_3)

        # Update points
        try:
            challenge.points_awarded = int(request.POST.get('points_awarded', challenge.points_awarded))
            challenge.hint_1_cost = int(request.POST.get('hint_1_cost', challenge.hint_1_cost))
            challenge.hint_2_cost = int(request.POST.get('hint_2_cost', challenge.hint_2_cost))
            challenge.hint_3_cost = int(request.POST.get('hint_3_cost', challenge.hint_3_cost))
        except ValueError:
            messages.error(request, 'Points must be valid numbers.')
            return redirect('crush_lu:coach_edit_challenge', challenge_id=challenge_id)

        challenge.save()
        messages.success(request, f'Challenge "{challenge.question[:50]}..." updated successfully!')
        return redirect('crush_lu:coach_edit_journey', journey_id=challenge.chapter.journey.id)

    # Get all user answers for this challenge
    all_attempts = ChallengeAttempt.objects.filter(
        challenge=challenge
    ).select_related(
        'chapter_progress__journey_progress__user'
    ).order_by('-attempted_at')

    context = {
        'coach': coach,
        'challenge': challenge,
        'all_attempts': all_attempts,
        'total_responses': all_attempts.count(),
    }
    return render(request, 'crush_lu/coach_edit_challenge.html', context)


@crush_login_required
def coach_view_user_progress(request, progress_id):
    """View a specific user's journey progress and answers"""
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'You do not have coach access.')
        return redirect('crush_lu:dashboard')

    from .models import JourneyProgress, ChallengeAttempt

    progress = get_object_or_404(
        JourneyProgress.objects.select_related('user', 'journey'),
        id=progress_id
    )

    # Get all chapter progress with attempts
    chapter_progress_list = progress.chapter_completions.all().select_related('chapter').prefetch_related('attempts__challenge')

    # Get all challenge attempts for this journey
    all_attempts = ChallengeAttempt.objects.filter(
        chapter_progress__journey_progress=progress
    ).select_related('challenge', 'chapter_progress__chapter').order_by('attempted_at')

    context = {
        'coach': coach,
        'progress': progress,
        'chapter_progress_list': chapter_progress_list,
        'all_attempts': all_attempts,
    }
    return render(request, 'crush_lu/coach_view_user_progress.html', context)


# Post-Event Connection Views
@crush_login_required
def event_attendees(request, event_id):
    """Show attendees after user has attended event - allows connection requests"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user attended this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if not user_registration.can_make_connections:
        messages.error(request, 'You must attend this event before making connections.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get other attendees (status='attended')
    attendees = EventRegistration.objects.filter(
        event=event,
        status='attended'
    ).exclude(user=request.user).select_related('user__crushprofile')

    # Get user's existing connection requests for this event
    sent_requests = EventConnection.objects.filter(
        requester=request.user,
        event=event
    ).values_list('recipient_id', flat=True)

    received_requests = EventConnection.objects.filter(
        recipient=request.user,
        event=event
    ).values_list('requester_id', flat=True)

    # Annotate attendees with connection status
    attendee_data = []
    for reg in attendees:
        attendee_user = reg.user
        connection_status = None
        connection_id = None

        if attendee_user.id in sent_requests:
            connection = EventConnection.objects.get(
                requester=request.user,
                recipient=attendee_user,
                event=event
            )
            connection_status = 'sent'
            connection_id = connection.id
        elif attendee_user.id in received_requests:
            connection = EventConnection.objects.get(
                requester=attendee_user,
                recipient=request.user,
                event=event
            )
            connection_status = 'received'
            connection_id = connection.id

        attendee_data.append({
            'user': attendee_user,
            'profile': getattr(attendee_user, 'crushprofile', None),
            'connection_status': connection_status,
            'connection_id': connection_id,
        })

    context = {
        'event': event,
        'attendees': attendee_data,
    }
    return render(request, 'crush_lu/event_attendees.html', context)


@crush_login_required
def request_connection(request, event_id, user_id):
    """Request connection with another event attendee"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    recipient = get_object_or_404(CrushProfile, user_id=user_id).user

    # Verify requester attended the event
    requester_reg = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if not requester_reg.can_make_connections:
        messages.error(request, 'You must attend this event before making connections.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Verify recipient attended the event
    recipient_reg = get_object_or_404(
        EventRegistration,
        event=event,
        user=recipient
    )

    if not recipient_reg.can_make_connections:
        messages.error(request, 'This person did not attend the event.')
        return redirect('crush_lu:event_attendees', event_id=event_id)

    # Check if connection already exists
    existing = EventConnection.objects.filter(
        Q(requester=request.user, recipient=recipient, event=event) |
        Q(requester=recipient, recipient=request.user, event=event)
    ).first()

    if existing:
        messages.warning(request, 'Connection request already exists.')
        return redirect('crush_lu:event_attendees', event_id=event_id)

    if request.method == 'POST':
        note = request.POST.get('note', '').strip()

        # Create connection request
        connection = EventConnection.objects.create(
            requester=request.user,
            recipient=recipient,
            event=event,
            requester_note=note
        )

        # Check if this is mutual (recipient already requested requester)
        reverse_connection = EventConnection.objects.filter(
            requester=recipient,
            recipient=request.user,
            event=event
        ).first()

        if reverse_connection:
            # Mutual interest! Both move to accepted
            connection.status = 'accepted'
            connection.save()
            reverse_connection.status = 'accepted'
            reverse_connection.save()

            # Assign coach to facilitate
            connection.assign_coach()
            reverse_connection.assigned_coach = connection.assigned_coach
            reverse_connection.save()

            messages.success(request, f'Mutual connection! 🎉 A coach will help facilitate your introduction.')
        else:
            messages.success(request, 'Connection request sent!')

        return redirect('crush_lu:event_attendees', event_id=event_id)

    context = {
        'event': event,
        'recipient': recipient,
    }
    return render(request, 'crush_lu/request_connection.html', context)


@crush_login_required
def respond_connection(request, connection_id, action):
    """Accept or decline a connection request"""
    connection = get_object_or_404(
        EventConnection,
        id=connection_id,
        recipient=request.user,
        status='pending'
    )

    # Security: Verify user actually attended the event
    try:
        user_registration = EventRegistration.objects.get(
            event=connection.event,
            user=request.user
        )
        if not user_registration.can_make_connections:
            messages.error(request, 'You must have attended this event to respond to connections.')
            return redirect('crush_lu:my_connections')
    except EventRegistration.DoesNotExist:
        messages.error(request, 'You are not registered for this event.')
        return redirect('crush_lu:my_connections')

    if action == 'accept':
        connection.status = 'accepted'
        connection.save()

        # Assign coach
        connection.assign_coach()

        messages.success(request, 'Connection accepted! A coach will help facilitate your introduction.')
    elif action == 'decline':
        connection.status = 'declined'
        connection.save()
        messages.info(request, 'Connection request declined.')
    else:
        messages.error(request, 'Invalid action.')

    return redirect('crush_lu:my_connections')


@crush_login_required
def my_connections(request):
    """View all connections (sent, received, active)"""
    # Sent requests
    sent = EventConnection.objects.filter(
        requester=request.user
    ).select_related('recipient__crushprofile', 'event', 'assigned_coach').order_by('-requested_at')

    # Received requests (pending only)
    received_pending = EventConnection.objects.filter(
        recipient=request.user,
        status='pending'
    ).select_related('requester__crushprofile', 'event').order_by('-requested_at')

    # Active connections (accepted, coach_reviewing, coach_approved, shared)
    active = EventConnection.objects.filter(
        Q(requester=request.user) | Q(recipient=request.user),
        status__in=['accepted', 'coach_reviewing', 'coach_approved', 'shared']
    ).select_related('requester__crushprofile', 'recipient__crushprofile', 'event', 'assigned_coach').order_by('-requested_at')

    context = {
        'sent_requests': sent,
        'received_requests': received_pending,
        'active_connections': active,
    }
    return render(request, 'crush_lu/my_connections.html', context)


@crush_login_required
def connection_detail(request, connection_id):
    """View connection details and provide consent"""
    connection = get_object_or_404(
        EventConnection,
        Q(requester=request.user) | Q(recipient=request.user),
        id=connection_id
    )

    # Determine if current user is requester or recipient
    is_requester = (connection.requester == request.user)

    if request.method == 'POST':
        # Handle consent
        if 'consent' in request.POST:
            consent_value = request.POST.get('consent') == 'yes'

            if is_requester:
                connection.requester_consents_to_share = consent_value
            else:
                connection.recipient_consents_to_share = consent_value

            connection.save()

            # Check if both consented and coach approved
            if connection.can_share_contacts:
                connection.status = 'shared'
                connection.save()
                messages.success(request, 'Contact information is now shared! 🎉')
            else:
                messages.success(request, 'Your consent has been recorded.')

            return redirect('crush_lu:connection_detail', connection_id=connection_id)

    # Get the other person in the connection
    other_user = connection.recipient if is_requester else connection.requester

    # Get messages for this connection
    connection_messages = ConnectionMessage.objects.filter(
        connection=connection
    ).select_related('sender').order_by('sent_at')

    context = {
        'connection': connection,
        'is_requester': is_requester,
        'other_user': other_user,
        'other_profile': getattr(other_user, 'crushprofile', None),
        'messages': connection_messages,
    }
    return render(request, 'crush_lu/connection_detail.html', context)


# Event Activity Voting Views
@crush_login_required
def event_voting_lobby(request, event_id):
    """Pre-voting lobby with countdown and activity previews"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    # Only confirmed or attended registrations can vote
    if user_registration.status not in ['confirmed', 'attended']:
        messages.error(request, 'Only confirmed attendees can access event voting.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get or create voting session
    voting_session, created = EventVotingSession.objects.get_or_create(event=event)

    # Get all activity options
    activity_options = EventActivityOption.objects.filter(event=event).order_by('activity_type', 'activity_variant')

    # Check if user has already voted
    user_vote = EventActivityVote.objects.filter(event=event, user=request.user).first()

    # Get total confirmed attendees count
    total_attendees = EventRegistration.objects.filter(
        event=event,
        status__in=['confirmed', 'attended']
    ).count()

    context = {
        'event': event,
        'voting_session': voting_session,
        'activity_options': activity_options,
        'user_vote': user_vote,
        'total_attendees': total_attendees,
    }
    return render(request, 'crush_lu/event_voting_lobby.html', context)


@crush_login_required
def event_activity_vote(request, event_id):
    """Active voting interface"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if user_registration.status not in ['confirmed', 'attended']:
        messages.error(request, 'Only confirmed attendees can vote.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get voting session
    voting_session = get_object_or_404(EventVotingSession, event=event)

    # Check if voting is open
    if not voting_session.is_voting_open:
        messages.warning(request, 'Voting is not currently open for this event.')
        return redirect('crush_lu:event_voting_lobby', event_id=event_id)

    if request.method == 'POST':
        presentation_option_id = request.POST.get('presentation_option_id')
        twist_option_id = request.POST.get('twist_option_id')

        if not presentation_option_id or not twist_option_id:
            messages.error(request, 'Please vote on BOTH categories: Presentation Style AND Speed Dating Twist.')
        else:
            try:
                from .models import GlobalActivityOption

                presentation_option = GlobalActivityOption.objects.get(
                    id=presentation_option_id,
                    activity_type='presentation_style',
                    is_active=True
                )
                twist_option = GlobalActivityOption.objects.get(
                    id=twist_option_id,
                    activity_type='speed_dating_twist',
                    is_active=True
                )

                # Handle presentation style vote
                presentation_vote = EventActivityVote.objects.filter(
                    event=event,
                    user=request.user,
                    selected_option__activity_type='presentation_style'
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
                        selected_option=presentation_option
                    )

                # Handle speed dating twist vote
                twist_vote = EventActivityVote.objects.filter(
                    event=event,
                    user=request.user,
                    selected_option__activity_type='speed_dating_twist'
                ).first()

                if twist_vote:
                    # Update existing vote
                    twist_vote.selected_option = twist_option
                    twist_vote.save()
                else:
                    # Create new vote
                    EventActivityVote.objects.create(
                        event=event,
                        user=request.user,
                        selected_option=twist_option
                    )

                # Update total votes only if this is first complete vote
                if not (presentation_vote and twist_vote):
                    voting_session.total_votes += 1
                    voting_session.save()

                messages.success(request, 'Your votes have been recorded for both categories!')
                return redirect('crush_lu:event_voting_results', event_id=event_id)

            except GlobalActivityOption.DoesNotExist:
                messages.error(request, 'Invalid activity option selected.')

    # Get all GLOBAL activity options (not per-event anymore!)
    from .models import GlobalActivityOption
    presentation_style_options = GlobalActivityOption.objects.filter(
        activity_type='presentation_style',
        is_active=True
    ).order_by('sort_order')

    speed_dating_twist_options = GlobalActivityOption.objects.filter(
        activity_type='speed_dating_twist',
        is_active=True
    ).order_by('sort_order')

    # Check if user has voted on BOTH categories
    presentation_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type='presentation_style'
    ).first()

    twist_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type='speed_dating_twist'
    ).first()

    context = {
        'event': event,
        'voting_session': voting_session,
        'presentation_style_options': presentation_style_options,
        'speed_dating_twist_options': speed_dating_twist_options,
        'presentation_vote': presentation_vote,
        'twist_vote': twist_vote,
        'has_voted_both': presentation_vote and twist_vote,
    }
    return render(request, 'crush_lu/event_activity_vote.html', context)


@crush_login_required
def event_voting_results(request, event_id):
    """Display voting results and transition to presentations when ready"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if user_registration.status not in ['confirmed', 'attended']:
        messages.error(request, 'Only confirmed attendees can view results.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get voting session
    voting_session = get_object_or_404(EventVotingSession, event=event)

    # Check if user has voted on BOTH categories
    presentation_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type='presentation_style'
    ).first()

    twist_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type='speed_dating_twist'
    ).first()

    user_has_voted_both = presentation_vote and twist_vote

    # If voting ended and user hasn't voted, redirect back to voting with message
    if not voting_session.is_voting_open and not user_has_voted_both:
        messages.warning(request, 'Voting has ended. You did not vote, but you can still participate in presentations!')
        # Allow them to continue to presentations anyway
        return redirect('crush_lu:event_presentations', event_id=event_id)

    # Get vote counts for each GlobalActivityOption
    from .models import GlobalActivityOption
    from django.db.models import Count

    # Get all active global options with their vote counts for THIS event
    activity_options_with_votes = []

    for option in GlobalActivityOption.objects.filter(is_active=True).order_by('activity_type', 'sort_order'):
        vote_count = EventActivityVote.objects.filter(
            event=event,
            selected_option=option
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
            if option == voting_session.winning_presentation_style or option == voting_session.winning_speed_dating_twist:
                option.is_winner = True

        # Check if presentations have started
        has_presentations = PresentationQueue.objects.filter(event=event).exists()

        if has_presentations:
            # Automatically redirect to presentations after 5 seconds
            messages.success(request, 'Voting complete! Redirecting to presentations...')
            context = {
                'event': event,
                'voting_session': voting_session,
                'activity_options': activity_options_with_votes,
                'user_has_voted_both': user_has_voted_both,
                'total_votes': total_votes,
                'redirect_to_presentations': True,  # Signal to template
            }
            return render(request, 'crush_lu/event_voting_results.html', context)

    context = {
        'event': event,
        'voting_session': voting_session,
        'activity_options': activity_options_with_votes,
        'user_has_voted_both': user_has_voted_both,
        'total_votes': total_votes,
        'redirect_to_presentations': False,
    }
    return render(request, 'crush_lu/event_voting_results.html', context)


# Presentation Round Views (Phase 2)
@crush_login_required
def event_presentations(request, event_id):
    """Phase 2: Live presentation view - shows current presenter and allows rating"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if user_registration.status not in ['confirmed', 'attended']:
        messages.error(request, 'Only confirmed attendees can view presentations.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get the current presenter (status='presenting')
    current_presentation = PresentationQueue.objects.filter(
        event=event,
        status='presenting'
    ).select_related('user__crushprofile').first()

    # Get next presenter (for preview)
    next_presentation = PresentationQueue.objects.filter(
        event=event,
        status='waiting'
    ).order_by('presentation_order').first()

    # Get total presentation stats
    total_presentations = PresentationQueue.objects.filter(event=event).count()
    completed_presentations = PresentationQueue.objects.filter(event=event, status='completed').count()

    # Check if user has rated current presenter
    user_has_rated = False
    if current_presentation:
        user_has_rated = PresentationRating.objects.filter(
            event=event,
            presenter=current_presentation.user,
            rater=request.user
        ).exists()

    # Get voting session and winning presentation style
    voting_session = get_object_or_404(EventVotingSession, event=event)
    winning_style = voting_session.winning_presentation_style

    context = {
        'event': event,
        'current_presentation': current_presentation,
        'next_presentation': next_presentation,
        'total_presentations': total_presentations,
        'completed_presentations': completed_presentations,
        'user_has_rated': user_has_rated,
        'winning_style': winning_style,
        'is_presenting': current_presentation and current_presentation.user == request.user,
    }
    return render(request, 'crush_lu/event_presentations.html', context)


@crush_login_required
@require_http_methods(["POST"])
def submit_presentation_rating(request, event_id, presenter_id):
    """Submit anonymous 1-5 star rating for a presenter"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    presenter = get_object_or_404(User, id=presenter_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if user_registration.status not in ['confirmed', 'attended']:
        return JsonResponse({'success': False, 'error': 'Only confirmed attendees can rate.'}, status=403)

    # Cannot rate yourself
    if presenter == request.user:
        return JsonResponse({'success': False, 'error': 'You cannot rate yourself.'}, status=400)

    # Get rating from request
    rating_value = request.POST.get('rating')

    try:
        rating_value = int(rating_value)
        if rating_value < 1 or rating_value > 5:
            raise ValueError
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Rating must be between 1 and 5.'}, status=400)

    # Create or update rating
    rating, created = PresentationRating.objects.update_or_create(
        event=event,
        presenter=presenter,
        rater=request.user,
        defaults={'rating': rating_value}
    )

    return JsonResponse({
        'success': True,
        'message': 'Rating submitted anonymously!' if created else 'Rating updated!',
        'rating': rating_value
    })


# Coach Presentation Control Panel
@crush_login_required
def coach_presentation_control(request, event_id):
    """Coach control panel for managing presentation queue"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is a coach
    try:
        coach = CrushCoach.objects.get(user=request.user)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'Only coaches can access presentation controls.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get all presentations
    presentations = PresentationQueue.objects.filter(
        event=event
    ).select_related('user__crushprofile').order_by('presentation_order')

    # Get current presenter
    current_presentation = presentations.filter(status='presenting').first()

    # Get next presenter
    next_presentation = presentations.filter(status='waiting').order_by('presentation_order').first()

    # Get stats
    total_presentations = presentations.count()
    completed_presentations = presentations.filter(status='completed').count()

    # Get voting session and winning presentation style
    voting_session = get_object_or_404(EventVotingSession, event=event)
    winning_style = voting_session.winning_presentation_style

    context = {
        'event': event,
        'presentations': presentations,
        'current_presentation': current_presentation,
        'next_presentation': next_presentation,
        'total_presentations': total_presentations,
        'completed_presentations': completed_presentations,
        'winning_style': winning_style,
    }
    return render(request, 'crush_lu/coach_presentation_control.html', context)


@crush_login_required
@require_http_methods(["POST"])
def coach_advance_presentation(request, event_id):
    """Advance to next presenter in the queue"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is a coach
    try:
        coach = CrushCoach.objects.get(user=request.user)
    except CrushCoach.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Only coaches can advance presentations.'}, status=403)

    # End current presentation if exists
    current_presentation = PresentationQueue.objects.filter(
        event=event,
        status='presenting'
    ).first()

    if current_presentation:
        current_presentation.status = 'completed'
        current_presentation.completed_at = timezone.now()
        current_presentation.save()

    # Start next presentation
    next_presentation = PresentationQueue.objects.filter(
        event=event,
        status='waiting'
    ).order_by('presentation_order').first()

    if next_presentation:
        next_presentation.status = 'presenting'
        next_presentation.started_at = timezone.now()
        next_presentation.save()

        return JsonResponse({
            'success': True,
            'message': f'Now presenting: {next_presentation.user.crushprofile.display_name}',
            'presenter_name': next_presentation.user.crushprofile.display_name,
            'presentation_order': next_presentation.presentation_order
        })
    else:
        return JsonResponse({
            'success': True,
            'message': 'All presentations completed!',
            'all_completed': True
        })


@crush_login_required
def my_presentation_scores(request, event_id):
    """Show user their personal presentation scores (private view)"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if user_registration.status not in ['confirmed', 'attended']:
        messages.error(request, 'Only confirmed attendees can view scores.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Check if all presentations are completed
    total_presentations = PresentationQueue.objects.filter(event=event).count()
    completed_presentations = PresentationQueue.objects.filter(event=event, status='completed').count()

    all_completed = total_presentations > 0 and completed_presentations == total_presentations

    if not all_completed:
        messages.warning(request, 'Scores will be available after all presentations are completed.')
        return redirect('crush_lu:event_presentations', event_id=event_id)

    # Get ratings received by this user
    ratings_received = PresentationRating.objects.filter(
        event=event,
        presenter=request.user
    ).select_related('rater__crushprofile')

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
    all_participants = PresentationQueue.objects.filter(event=event).values_list('user_id', flat=True)

    participant_scores = []
    for participant_id in all_participants:
        participant_ratings = PresentationRating.objects.filter(
            event=event,
            presenter_id=participant_id
        )
        if participant_ratings.exists():
            avg = participant_ratings.aggregate(Avg('rating'))['rating__avg']
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
        'event': event,
        'average_score': average_score,
        'rating_count': rating_count,
        'individual_ratings': individual_ratings,
        'rating_distribution': rating_distribution,
        'user_rank': user_rank,
        'total_participants': len(participant_scores),
    }
    return render(request, 'crush_lu/my_presentation_scores.html', context)


@crush_login_required
def get_current_presenter_api(request, event_id):
    """API endpoint to get current presenter status (for polling)"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    try:
        user_registration = EventRegistration.objects.get(
            event=event,
            user=request.user
        )
        if user_registration.status not in ['confirmed', 'attended']:
            return JsonResponse({'error': 'Not authorized'}, status=403)
    except EventRegistration.DoesNotExist:
        return JsonResponse({'error': 'Not registered for this event'}, status=403)

    # Get current presenter
    current_presentation = PresentationQueue.objects.filter(
        event=event,
        status='presenting'
    ).select_related('user__crushprofile').first()

    if current_presentation:
        # Check if user has rated this presenter
        user_has_rated = PresentationRating.objects.filter(
            event=event,
            presenter=current_presentation.user,
            rater=request.user
        ).exists()

        # Calculate time remaining
        time_remaining = 90
        if current_presentation.started_at:
            from django.utils import timezone
            elapsed = (timezone.now() - current_presentation.started_at).total_seconds()
            time_remaining = max(0, int(90 - elapsed))

        return JsonResponse({
            'has_presenter': True,
            'presenter_id': current_presentation.user.id,
            'presenter_name': current_presentation.user.crushprofile.display_name,
            'presentation_order': current_presentation.presentation_order,
            'started_at': current_presentation.started_at.isoformat() if current_presentation.started_at else None,
            'time_remaining': time_remaining,
            'user_has_rated': user_has_rated,
            'is_presenting': current_presentation.user == request.user
        })
    else:
        # Check if all presentations are completed
        total_presentations = PresentationQueue.objects.filter(event=event).count()
        completed_presentations = PresentationQueue.objects.filter(event=event, status='completed').count()

        return JsonResponse({
            'has_presenter': False,
            'all_completed': total_presentations > 0 and completed_presentations == total_presentations,
            'completed_count': completed_presentations,
            'total_count': total_presentations
        })


# Demo/Guided Tour View
def voting_demo(request):
    """Interactive demo of the voting system for new users"""
    return render(request, 'crush_lu/voting_demo.html')


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
        invitation.status = 'expired'
        invitation.save()
        return render(request, 'crush_lu/invitation_expired.html', {'invitation': invitation})

    # Check if already accepted
    if invitation.status == 'accepted':
        messages.info(request, 'You have already accepted this invitation. Please log in to continue.')
        return redirect('crush_lu:crush_login')

    # Show invitation details
    context = {
        'invitation': invitation,
        'event': invitation.event,
    }
    return render(request, 'crush_lu/invitation_landing.html', context)


@ratelimit(key='ip', rate='10/h', method='POST')
def invitation_accept(request, code):
    """
    PUBLIC ACCESS: Accept invitation and create guest account.
    """
    invitation = get_object_or_404(EventInvitation, invitation_code=code)

    # Check if already accepted
    if invitation.status == 'accepted':
        messages.info(request, 'This invitation has already been accepted.')
        return redirect('crush_lu:crush_login')

    # Check expiration
    if invitation.is_expired:
        messages.error(request, 'This invitation has expired.')
        return redirect('crush_lu:invitation_landing', code=code)

    if request.method == 'POST':
        try:
            # Create user account with random password
            username = f"guest_{invitation.guest_email.split('@')[0]}_{uuid.uuid4().hex[:6]}"
            random_password = User.objects.make_random_password(length=16)

            user = User.objects.create_user(
                username=username,
                email=invitation.guest_email,
                first_name=invitation.guest_first_name,
                last_name=invitation.guest_last_name,
                password=random_password
            )

            # Update invitation
            invitation.created_user = user
            invitation.status = 'accepted'
            invitation.accepted_at = timezone.now()
            invitation.save()

            # Log the user in
            user.backend = 'django.contrib.auth.backends.ModelBackend'
            login(request, user)

            logger.info(f"Guest account created and logged in: {user.email} for event {invitation.event.title}")

            messages.success(request,
                f'Welcome! Your invitation has been accepted. '
                f'You will receive an email once your attendance is approved by our team.')

            return render(request, 'crush_lu/invitation_pending_approval.html', {
                'invitation': invitation,
                'event': invitation.event,
            })

        except Exception as e:
            logger.error(f"Error creating guest account: {e}")
            messages.error(request, 'An error occurred while accepting your invitation. Please try again.')
            return redirect('crush_lu:invitation_landing', code=code)

    context = {
        'invitation': invitation,
        'event': invitation.event,
    }
    return render(request, 'crush_lu/invitation_accept_form.html', context)


@crush_login_required
def coach_manage_invitations(request, event_id):
    """
    COACH ONLY: Dashboard for managing event invitations.
    Send invitations, approve guests, and track invitation status.
    """
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'Only coaches can manage invitations.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    event = get_object_or_404(MeetupEvent, id=event_id, is_private_invitation=True)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'send_invitation':
            # Send new invitation
            email = request.POST.get('email', '').strip()
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()

            if not email or not first_name or not last_name:
                messages.error(request, 'Please provide email, first name, and last name.')
            else:
                # Check if invitation already exists
                existing = EventInvitation.objects.filter(
                    event=event,
                    guest_email=email
                ).first()

                if existing:
                    messages.warning(request, f'An invitation for {email} already exists.')
                else:
                    invitation = EventInvitation.objects.create(
                        event=event,
                        guest_email=email,
                        guest_first_name=first_name,
                        guest_last_name=last_name,
                        invited_by=request.user,
                    )

                    # TODO: Send email invitation
                    # send_event_invitation_email(invitation, request)

                    messages.success(request, f'Invitation sent to {email}. Invitation code: {invitation.invitation_code}')
                    logger.info(f"Invitation created for {email} to event {event.title}")

        elif action == 'approve_guest':
            invitation_id = request.POST.get('invitation_id')
            try:
                invitation = EventInvitation.objects.get(id=invitation_id, event=event)
                invitation.approval_status = 'approved'
                invitation.approved_at = timezone.now()
                invitation.save()

                # TODO: Send approval email with login instructions
                # send_guest_approved_notification(invitation, request)

                messages.success(request, f'Guest {invitation.guest_first_name} {invitation.guest_last_name} approved!')
                logger.info(f"Guest approved: {invitation.guest_email} for event {event.title}")
            except EventInvitation.DoesNotExist:
                messages.error(request, 'Invitation not found.')

        elif action == 'reject_guest':
            invitation_id = request.POST.get('invitation_id')
            notes = request.POST.get('rejection_notes', '')
            try:
                invitation = EventInvitation.objects.get(id=invitation_id, event=event)
                invitation.approval_status = 'rejected'
                invitation.approval_notes = notes
                invitation.save()

                # TODO: Send rejection email
                # send_guest_rejected_notification(invitation, request)

                messages.info(request, f'Guest {invitation.guest_first_name} {invitation.guest_last_name} rejected.')
                logger.info(f"Guest rejected: {invitation.guest_email} for event {event.title}")
            except EventInvitation.DoesNotExist:
                messages.error(request, 'Invitation not found.')

    # Get all invitations for this event
    invitations = EventInvitation.objects.filter(event=event).order_by('-invitation_sent_at')

    # Separate by status
    pending_approvals = invitations.filter(
        status='accepted',
        approval_status='pending_approval'
    )
    approved_guests = invitations.filter(approval_status='approved')
    rejected_guests = invitations.filter(approval_status='rejected')
    pending_invitations = invitations.filter(status='pending')

    context = {
        'event': event,
        'invitations': invitations,
        'pending_approvals': pending_approvals,
        'approved_guests': approved_guests,
        'rejected_guests': rejected_guests,
        'pending_invitations': pending_invitations,
        'coach': coach,
    }
    return render(request, 'crush_lu/coach_invitation_dashboard.html', context)


# Special User Experience View
@crush_login_required
def special_welcome(request):
    """
    Special welcome page for VIP users with custom animations and experience.
    Only accessible if special_experience_active is set in session.

    If a journey is configured for this user, redirect to journey_map instead.
    """
    # Check if special experience is active in session
    if not request.session.get('special_experience_active'):
        messages.warning(request, 'This page is not available.')
        # Redirect to home instead of dashboard (special users don't need profiles)
        return redirect('crush_lu:home')

    # Get special experience data from session
    special_experience_data = request.session.get('special_experience_data', {})

    # Get the full SpecialUserExperience object for complete data
    special_experience_id = request.session.get('special_experience_id')
    try:
        special_experience = SpecialUserExperience.objects.get(
            id=special_experience_id,
            is_active=True
        )
    except SpecialUserExperience.DoesNotExist:
        # Clear session data if experience is not found or inactive
        request.session.pop('special_experience_active', None)
        request.session.pop('special_experience_id', None)
        request.session.pop('special_experience_data', None)
        messages.warning(request, 'This special experience is no longer available.')
        return redirect('crush_lu:home')

    # ============================================================================
    # NEW: Check if journey is configured - redirect to appropriate journey type
    # ============================================================================
    from .models import JourneyConfiguration

    # First check for Wonderland journey
    wonderland_journey = JourneyConfiguration.objects.filter(
        special_experience=special_experience,
        journey_type='wonderland',
        is_active=True
    ).first()

    if wonderland_journey:
        logger.info(f"🎮 Redirecting {request.user.username} to Wonderland journey: {wonderland_journey.journey_name}")
        return redirect('crush_lu:journey_map')

    # Check for Advent Calendar journey
    advent_journey = JourneyConfiguration.objects.filter(
        special_experience=special_experience,
        journey_type='advent_calendar',
        is_active=True
    ).first()

    if advent_journey:
        logger.info(f"🎄 Redirecting {request.user.username} to Advent Calendar")
        return redirect('crush_lu:advent_calendar')

    # No journey configured - show simple welcome page
    # ============================================================================

    context = {
        'special_experience': special_experience,
    }

    # Mark session as viewed (only show once per login)
    request.session['special_experience_viewed'] = True

    return render(request, 'crush_lu/special_welcome.html', context)


# ============================================================================
# PWA Views
# ============================================================================

def offline_view(request):
    """
    Offline fallback page for PWA
    Displayed when user is offline and tries to access unavailable content
    """
    return render(request, 'crush_lu/offline.html')


def service_worker_view(request):
    """
    Serve the Workbox service worker from root path
    This is required to give the service worker access to the entire site scope
    """
    from django.http import HttpResponse
    from django.conf import settings
    import os

    # Read the service worker file
    sw_path = os.path.join(settings.BASE_DIR, 'static', 'crush_lu', 'sw-workbox.js')

    try:
        with open(sw_path, 'r', encoding='utf-8') as f:
            sw_content = f.read()

        # Return with correct MIME type
        response = HttpResponse(sw_content, content_type='application/javascript')

        # IMPORTANT: Never cache SW script as immutable - it must be revalidated
        # so updates propagate to users. The SW itself handles internal caching.
        response['Cache-Control'] = 'no-cache, max-age=0, must-revalidate'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'

        return response
    except FileNotFoundError:
        return HttpResponse('Service worker not found', status=404)


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
                "src": s('crush_lu/icons/android-launchericon-48-48.png'),
                "sizes": "48x48",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-72-72.png'),
                "sizes": "72x72",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-96-96.png'),
                "sizes": "96x96",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-144-144.png'),
                "sizes": "144x144",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-192-192.png'),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-512-512.png'),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-192-192-maskable.png'),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "maskable"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-512-512-maskable.png'),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable"
            }
        ],
        "screenshots": [
            {
                "src": s('crush_lu/icons/android-launchericon-512-512.png'),
                "sizes": "512x512",
                "type": "image/png",
                "form_factor": "narrow",
                "label": "Crush.lu Mobile View"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-512-512.png'),
                "sizes": "512x512",
                "type": "image/png",
                "form_factor": "wide",
                "label": "Crush.lu Desktop View"
            }
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
                    {
                        "src": s('crush_lu/icons/shortcut-events.png'),
                        "sizes": "96x96"
                    }
                ]
            },
            {
                "name": "My Dashboard",
                "short_name": "Dashboard",
                "description": "View your profile and registrations",
                "url": "/dashboard/",
                "icons": [
                    {
                        "src": s('crush_lu/icons/shortcut-dashboard.png'),
                        "sizes": "96x96"
                    }
                ]
            },
            {
                "name": "Connections",
                "short_name": "Connections",
                "description": "View your event connections",
                "url": "/connections/",
                "icons": [
                    {
                        "src": s('crush_lu/icons/shortcut-connections.png'),
                        "sizes": "96x96"
                    }
                ]
            }
        ]
    }

    response = JsonResponse(manifest)
    response['Content-Type'] = 'application/manifest+json'
    # Prevent aggressive caching to avoid stale icon issues during updates
    response['Cache-Control'] = 'no-cache'
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
        return HttpResponseForbidden('Superuser access required')

    return render(request, 'crush_lu/pwa_debug.html', {
        'sw_version': 'crush-v16-icon-cache-fix',  # Keep in sync with sw-workbox.js
    })

