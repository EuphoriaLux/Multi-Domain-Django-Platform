"""
Onboarding flow views for Crush.lu

Handles user registration, profile creation, and OAuth completion.
"""
from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.db import transaction
import logging

from ..models import CrushProfile, ProfileSubmission
from ..forms import CrushSignupForm, CrushProfileForm
from ..decorators import crush_login_required, ratelimit
from ..email_helpers import send_welcome_email, send_profile_submission_notifications
from ..notification_service import notify_profile_approved, notify_profile_revision, notify_profile_rejected
from ..coach_notifications import notify_coach_new_submission, notify_coach_user_revision
from ..referrals import capture_referral, capture_referral_from_request
from ..utils.i18n import is_valid_language

logger = logging.getLogger(__name__)


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
    try:
        profile = request.user.crushprofile
        if not profile.is_approved:
            final_destination = reverse('crush_lu:profile_submitted')
    except CrushProfile.DoesNotExist:
        # No profile yet - redirect to profile creation
        final_destination = reverse('crush_lu:create_profile')

    context = {
        'final_destination': final_destination,
    }
    return render(request, 'crush_lu/onboarding/oauth_complete.html', context)


def referral_redirect(request, code):
    """
    Referral landing route.
    Stores referral attribution and redirects to signup with code preserved.
    """
    referral = capture_referral(request, code, source="link")
    signup_url = reverse('crush_lu:signup')
    if referral:
        return redirect(f"{signup_url}?ref={referral.code}")
    return redirect(signup_url)


@ratelimit(key='ip', rate='5/h', method='POST')
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

                messages.success(request, _('Account created! Check your email and complete your profile.'))
                # Log the user in - set backend for multi-auth compatibility
                user.backend = 'django.contrib.auth.backends.ModelBackend'
                login(request, user)

                # Check if there's a pending gift to claim
                pending_gift_code = request.session.get('pending_gift_code')
                if pending_gift_code:
                    return redirect('crush_lu:gift_claim', gift_code=pending_gift_code)

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
    return render(request, 'crush_lu/onboarding/auth.html', context)


@crush_login_required
@ratelimit(key='user', rate='10/15m', method='POST', block=True)
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

            # Set preferred language from current request language on first submission
            # This respects the user's browser language detected by Django's LocaleMiddleware
            if is_first_submission and hasattr(request, 'LANGUAGE_CODE'):
                current_lang = request.LANGUAGE_CODE
                # Only set if it's a supported language
                if is_valid_language(current_lang):
                    profile.preferred_language = current_lang
                    logger.debug(f"Set preferred_language to '{current_lang}' for {request.user.email}")

            # Mark profile as completed and submitted
            profile.completion_status = 'submitted'

            # Note: Screening call handled in ProfileSubmission.review_call_completed
            # No need to set flags here - coach will do screening during review
            if is_first_submission:
                logger.info(f"First submission - screening call will be done during coach review for {request.user.email}")
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
                    existing_submission = ProfileSubmission.objects.select_for_update().filter(
                        profile=profile,
                        status='pending'
                    ).first()

                    # Also check for revision/rejected submissions that user is resubmitting
                    revision_submission = ProfileSubmission.objects.select_for_update().filter(
                        profile=profile,
                        status__in=['revision', 'rejected']
                    ).first()

                    is_revision = False
                    if existing_submission:
                        submission = existing_submission
                        created = False
                        logger.warning(f"⚠️ Existing pending submission found for {request.user.email}")
                    elif revision_submission:
                        # User is resubmitting after revision request - update existing submission
                        submission = revision_submission
                        submission.status = 'pending'
                        submission.submitted_at = timezone.now()
                        submission.save()
                        created = False
                        is_revision = True
                        logger.info(f"✅ Revision submission updated to pending for {request.user.email}")
                    else:
                        # Create new submission
                        submission = ProfileSubmission.objects.create(
                            profile=profile,
                            status='pending'
                        )
                        created = True

            except Exception as e:
                logger.error(f"❌ Transaction failed for {request.user.email}: {e}", exc_info=True)
                messages.error(request, _('An error occurred while submitting your profile. Please try again.'))
                # Re-render the form
                from ..social_photos import get_all_social_photos
                context = {
                    'form': form,
                    'current_step': 'step3',
                    'social_photos': get_all_social_photos(request.user),
                }
                return render(request, 'crush_lu/onboarding/create_profile.html', context)

            # Only assign coach and send emails for NEW submissions (outside transaction for email reliability)
            if created:
                submission.assign_coach()
                logger.info(f"NEW profile submission created for {request.user.email}")

                # Send push notification to assigned coach
                if submission.coach:
                    try:
                        notify_coach_new_submission(submission.coach, submission)
                        logger.info(f"Coach push notification sent for submission {submission.id}")
                    except Exception as e:
                        logger.warning(f"Failed to send coach push notification: {e}")

                # Send confirmation and coach notification emails using consolidated helper
                send_profile_submission_notifications(
                    submission,
                    request,
                    add_message_func=lambda msg: messages.warning(request, msg)
                )
            elif is_revision:
                # User resubmitted after revision request - notify the coach
                if submission.coach:
                    try:
                        notify_coach_user_revision(submission.coach, submission)
                        logger.info(f"Coach revision notification sent for submission {submission.id}")
                    except Exception as e:
                        logger.warning(f"Failed to send coach revision notification: {e}")
            else:
                # Duplicate submission attempt - just log and continue
                logger.warning(f"⚠️ Duplicate submission attempt prevented for {request.user.email}")

            messages.success(request, _('Profile submitted for review!'))
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
            from ..social_photos import get_all_social_photos
            context = {
                'form': form,
                'current_step': 'step3',  # Show review step where submit button is
                'social_photos': get_all_social_photos(request.user),
            }
            return render(request, 'crush_lu/onboarding/create_profile.html', context)

    # GET request - check if profile already exists and redirect accordingly
    try:
        profile = CrushProfile.objects.get(user=request.user)

        # If profile is submitted, show status page instead of edit form
        if profile.completion_status == 'submitted':
            messages.info(request, _('Your profile has been submitted. Check the status below.'))
            return redirect('crush_lu:profile_submitted')
        elif profile.completion_status == 'not_started':
            # Fresh profile (auto-created on login) - show creation form
            from ..social_photos import get_all_social_photos
            form = CrushProfileForm(instance=profile)
            return render(request, 'crush_lu/onboarding/create_profile.html', {
                'form': form,
                'profile': profile,  # Required for phone verification status display
                'social_photos': get_all_social_photos(request.user),
            })
        elif profile.completion_status in ['step1', 'step2', 'step3']:
            # Profile is in progress through the wizard - show the wizard at current step
            from ..social_photos import get_all_social_photos
            form = CrushProfileForm(instance=profile)
            return render(request, 'crush_lu/onboarding/create_profile.html', {
                'form': form,
                'profile': profile,
                'current_step': profile.completion_status,
                'social_photos': get_all_social_photos(request.user),
            })
        else:
            # Unknown status or 'completed' - redirect to edit
            return redirect('crush_lu:edit_profile')
    except CrushProfile.DoesNotExist:
        # No profile yet - show creation form
        from ..social_photos import get_all_social_photos
        form = CrushProfileForm()
        return render(request, 'crush_lu/onboarding/create_profile.html', {
            'form': form,
            'profile': None,  # No profile yet, phone verification UI will show as not verified
            'social_photos': get_all_social_photos(request.user),
        })


@crush_login_required
def profile_submitted(request):
    """Confirmation page after profile submission"""
    try:
        profile = CrushProfile.objects.get(user=request.user)
        submission = ProfileSubmission.objects.filter(profile=profile).latest('submitted_at')
    except (CrushProfile.DoesNotExist, ProfileSubmission.DoesNotExist):
        messages.error(request, _('No profile submission found.'))
        return redirect('crush_lu:create_profile')

    context = {
        'submission': submission,
    }
    return render(request, 'crush_lu/onboarding/profile_submitted.html', context)
