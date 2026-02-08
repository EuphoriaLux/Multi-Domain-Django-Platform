"""
Signal handlers for Crush.lu app
"""

import logging
import threading
from datetime import datetime

import requests
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from django.core.files.base import ContentFile
from django.db.models import Q
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.utils import timezone
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.signals import pre_social_login, social_account_updated

from .models import MeetupEvent, EventActivityOption, CrushProfile, SpecialUserExperience, EmailPreference, EventRegistration, CrushCoach
from .storage import initialize_user_storage
from .utils.i18n import is_valid_language

logger = logging.getLogger(__name__)

# Thread-local storage to pass domain context between signals
_thread_local = threading.local()


# =============================================================================
# WALLET PASS UPDATE TRIGGERS
# =============================================================================

# Fields that should trigger a wallet pass update when changed
WALLET_UPDATE_PROFILE_FIELDS = {
    'referral_points',
    'membership_tier',
    'show_photo_on_wallet',
    'photo_1',
    'display_name',
    'first_name',
    'last_name',
    'show_full_name',
}


def _trigger_apple_pass_refresh(profile):
    """
    Trigger Apple Wallet pass refresh via APNS push notification.

    When Apple Wallet receives this silent push, it calls our PassKit web service
    endpoint to fetch the updated pass.

    Args:
        profile: CrushProfile instance with apple_pass_serial set
    """
    if not profile.apple_pass_serial:
        return

    pass_type_id = getattr(settings, 'WALLET_APPLE_PASS_TYPE_IDENTIFIER', None)
    if not pass_type_id:
        logger.warning("Cannot trigger Apple pass refresh: WALLET_APPLE_PASS_TYPE_IDENTIFIER not configured")
        return

    try:
        from .wallet.passkit_apns import send_passkit_push_notifications

        result = send_passkit_push_notifications(
            pass_type_identifier=pass_type_id,
            serial_number=profile.apple_pass_serial,
        )

        if result['total'] > 0:
            logger.info(
                f"Apple Wallet pass refresh triggered for user {profile.user_id}: "
                f"success={result['success']}, failed={result['failed']}, total={result['total']}"
            )
        else:
            logger.debug(f"No Apple Wallet device registrations for user {profile.user_id}")

    except Exception as e:
        logger.error(f"Error triggering Apple pass refresh for user {profile.user_id}: {e}")


def _trigger_google_wallet_object_update(profile):
    """
    Trigger Google Wallet object update via REST API.

    For Google Wallet, we PATCH the object to update its content.
    Uses the Google Wallet REST API with service account authentication.

    Args:
        profile: CrushProfile instance with google_wallet_object_id set
    """
    if not profile.google_wallet_object_id:
        return

    try:
        from .wallet.google_api import update_google_wallet_pass

        result = update_google_wallet_pass(profile)

        if result["success"]:
            logger.info(
                f"Google Wallet pass updated for user {profile.user_id}: "
                f"object_id={profile.google_wallet_object_id}"
            )
        else:
            logger.warning(
                f"Google Wallet pass update failed for user {profile.user_id}: "
                f"{result['message']}"
            )

    except Exception as e:
        logger.error(
            f"Error updating Google Wallet pass for user {profile.user_id}: {e}"
        )


def trigger_wallet_pass_updates(profile):
    """
    Trigger updates for both Apple and Google Wallet passes.

    Args:
        profile: CrushProfile instance
    """
    _trigger_apple_pass_refresh(profile)
    _trigger_google_wallet_object_update(profile)


@receiver(post_save, sender=User)
def create_user_storage_folder(sender, instance, created, **kwargs):
    """
    Create user storage folder structure when a new user is created.

    This initializes the user's private storage folder in Azure Blob Storage
    (or local filesystem in development) with a marker file.

    Structure created:
        users/{user_id}/.user_created

    Note: This signal fires for all User creations across all domains.
    The storage is Crush.lu-specific (crush-profiles-private container),
    so it only affects Crush.lu photo storage.
    """
    if not created:
        return

    try:
        success = initialize_user_storage(instance.id)
        if success:
            logger.info(f"Created storage folder for new user {instance.id} ({instance.email})")
        else:
            logger.warning(f"Failed to create storage folder for user {instance.id}")
    except Exception as e:
        # Don't fail user creation if storage initialization fails
        logger.error(f"Error creating storage folder for user {instance.id}: {str(e)}")


@receiver(post_save, sender=User)
def create_email_preference_for_user(sender, instance, created, **kwargs):
    """
    Create EmailPreference with default settings when a new user is created.

    Default settings (GDPR compliant):
    - All transactional/engagement emails: ON
    - Marketing emails: OFF (requires explicit opt-in)

    This ensures every user has email preferences from the start,
    enabling proper unsubscribe functionality.
    """
    if not created:
        return

    try:
        EmailPreference.objects.get_or_create(
            user=instance,
            defaults={
                'email_profile_updates': True,
                'email_event_reminders': True,
                'email_new_connections': True,
                'email_new_messages': True,
                'email_marketing': False,  # OFF by default - GDPR compliance
            }
        )
        logger.info(f"Created email preferences for new user {instance.id} ({instance.email})")
    except Exception as e:
        # Don't fail user creation if email preference creation fails
        logger.error(f"Error creating email preferences for user {instance.id}: {str(e)}")


@receiver(post_save, sender=User)
def create_user_data_consent(sender, instance, created, **kwargs):
    """
    Create UserDataConsent record for every new user.
    PowerUp consent is given implicitly on account creation.
    Crush.lu consent must be explicitly given during profile creation (form signup)
    or implicitly during OAuth signup.
    """
    if not created:
        return

    try:
        from crush_lu.models.profiles import UserDataConsent

        # Check if OAuth consent data is available (set by pre_social_login)
        oauth_consent = getattr(_thread_local, 'oauth_consent_data', None)

        defaults = {
            'powerup_consent_given': True,  # Implicit consent on account creation
            'powerup_consent_date': timezone.now(),
            'crushlu_consent_given': False,  # Must be explicitly given (form) or implicit (OAuth)
        }

        # If OAuth signup, apply implicit Crush.lu consent
        if oauth_consent:
            defaults['crushlu_consent_given'] = oauth_consent.get('crushlu_consent', False)
            defaults['crushlu_consent_date'] = timezone.now()
            defaults['crushlu_consent_ip'] = oauth_consent.get('crushlu_consent_ip')
            logger.info(f"OAuth signup detected - applying implicit Crush.lu consent for user {instance.id}")

        UserDataConsent.objects.get_or_create(
            user=instance,
            defaults=defaults
        )
        logger.info(f"Created data consent for new user {instance.id} ({instance.email})")

        # Clear OAuth consent data after use
        if hasattr(_thread_local, 'oauth_consent_data'):
            delattr(_thread_local, 'oauth_consent_data')

    except Exception as e:
        # Don't fail user creation if consent creation fails
        logger.error(f"Error creating data consent for user {instance.id}: {str(e)}")


# List of Crush.lu domains - profile should be created for logins on these domains
CRUSH_LU_DOMAINS = ['crush.lu', 'www.crush.lu', 'localhost', '127.0.0.1']


def _is_crush_domain(request):
    """Check if current request is from crush.lu domain"""
    if not request:
        return False
    try:
        host = request.get_host().split(':')[0].lower()
    except (KeyError, AttributeError):
        return False
    return host in CRUSH_LU_DOMAINS


@receiver(user_logged_in)
def create_crush_profile_on_login(sender, request, user, **kwargs):
    """
    Create a CrushProfile when a user logs in on crush.lu domain.

    This ensures ALL users who log in via crush.lu (regardless of auth method:
    email/password, Facebook, Microsoft, LinkedIn) get a basic CrushProfile,
    even if they don't complete the full profile creation form.

    This does NOT create profiles for logins on other domains (powerup.lu,
    vinsdelux.com, delegations.lu).
    """
    if not request:
        return

    # Get the host without port - handle test client edge cases
    try:
        host = request.get_host().split(':')[0].lower()
    except (KeyError, AttributeError):
        # Test client may not have SERVER_NAME set
        logger.debug("Could not determine host for login signal - skipping CrushProfile creation")
        return

    # Only create profile for crush.lu domain logins
    if host not in CRUSH_LU_DOMAINS:
        logger.debug(f"Skipping CrushProfile creation for non-Crush domain login: {host}")
        return

    # Skip delegations.lu domain - they have their own profile system
    if 'delegations.lu' in host:
        logger.debug(f"Skipping CrushProfile creation for delegations.lu domain: {host}")
        return

    preferred_language = 'en'
    request_language = getattr(request, 'LANGUAGE_CODE', None)
    if is_valid_language(request_language):
        preferred_language = request_language
    else:
        session_language = request.session.get('django_language')
        if is_valid_language(session_language):
            preferred_language = session_language

    try:
        # Use get_or_create to avoid duplicates
        profile, created = CrushProfile.objects.get_or_create(
            user=user,
            defaults={
                'completion_status': 'not_started',  # Mark as not started
                'preferred_language': preferred_language,
            }
        )

        if created:
            logger.info(f"Created CrushProfile for user {user.email} on login (domain: {host})")
        else:
            logger.debug(f"CrushProfile already exists for user {user.email}")

    except Exception as e:
        # Don't fail login if profile creation fails
        logger.error(f"Error creating CrushProfile on login for user {user.id}: {str(e)}", exc_info=True)


@receiver(user_logged_in)
def sync_language_preference_on_login(sender, request, user, **kwargs):
    """
    Sync session language to CrushProfile.preferred_language on login.

    This handles the edge case where a user signs up on a non-English page
    (e.g., /fr/signup/) but the CrushProfile is created with default 'en'.

    On first login, if the profile has the default 'en' language but the session
    has a different language (de/fr), we update the profile to match.

    This ensures push notifications and emails are sent in the language the
    user was browsing in when they signed up.
    """
    if not request:
        return

    # Only process for crush.lu domain
    try:
        host = request.get_host().split(':')[0].lower()
    except (KeyError, AttributeError):
        return

    if host not in CRUSH_LU_DOMAINS:
        return

    try:
        if hasattr(user, 'crushprofile') and user.crushprofile:
            profile = user.crushprofile
            # Get session language (set by Django's LocaleMiddleware)
            session_lang = request.session.get('django_language', None)

            # If profile has default 'en' but session has different valid language,
            # update profile to match session (user's browsing language preference)
            if (profile.preferred_language == 'en' and
                    session_lang and is_valid_language(session_lang) and session_lang != 'en'):
                profile.preferred_language = session_lang
                profile.save(update_fields=['preferred_language'])
                logger.info(
                    f"Synced language preference for user {user.id}: "
                    f"'en' -> '{session_lang}' (from session)"
                )
    except Exception as e:
        # Don't fail login if language sync fails
        logger.warning(f"Error syncing language preference for user {user.id}: {e}")


@receiver(post_save, sender=MeetupEvent)
def create_default_activity_options(sender, instance, created, **kwargs):
    """
    Automatically create the 6 standard activity options when a new event is created.
    This ensures every Crush event has the same voting options without manual creation.
    """
    if created:  # Only for newly created events
        # Define the 6 standard activity options
        default_options = [
            # Phase 2: Presentation Style (3 options)
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'music',
                'display_name': 'With Favorite Music',
                'description': 'Introduce yourself while your favorite song plays in the background'
            },
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'questions',
                'display_name': '5 Predefined Questions',
                'description': 'Answer 5 fun questions about yourself (we provide the questions!)'
            },
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'picture_story',
                'display_name': 'Share Favorite Picture & Story',
                'description': 'Show us your favorite photo and tell us why it matters to you'
            },
            # Phase 3: Speed Dating Twist (3 options)
            {
                'activity_type': 'speed_dating_twist',
                'activity_variant': 'spicy_questions',
                'display_name': 'Spicy Questions First',
                'description': 'Break the ice with bold, fun questions right away'
            },
            {
                'activity_type': 'speed_dating_twist',
                'activity_variant': 'forbidden_word',
                'display_name': 'Forbidden Word Challenge',
                'description': 'Each pair gets a secret word they can\'t say during the date'
            },
            {
                'activity_type': 'speed_dating_twist',
                'activity_variant': 'algorithm_extended',
                'display_name': 'Algorithm\'s Choice Extended Time',
                'description': 'Trust our matching algorithm - your top match gets extra time!'
            },
        ]

        # Create all 6 options for this event
        for option_data in default_options:
            EventActivityOption.objects.create(
                event=instance,
                **option_data
            )


def get_high_res_facebook_photo_url(facebook_id, access_token=None):
    """
    Get high-resolution Facebook profile photo URL.

    Facebook Graph API allows requesting specific dimensions.
    We request 720x720 which is the max for profile pictures.

    Args:
        facebook_id: The Facebook user ID
        access_token: Optional access token for authenticated requests

    Returns:
        str: URL to high-resolution photo, or None if unavailable
    """
    # Request 720x720 photo (maximum size for profile pictures)
    # Using redirect=false returns JSON with the actual URL
    # Use Graph API v24.0 to match settings.py
    url = f"https://graph.facebook.com/v24.0/{facebook_id}/picture?width=720&height=720&redirect=false"

    if access_token:
        url += f"&access_token={access_token}"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get('data', {}).get('url'):
            return data['data']['url']
    except Exception as e:
        logger.warning(f"Could not get high-res Facebook photo: {str(e)}")

    return None


# Centralized gender mapping to avoid duplication
FACEBOOK_GENDER_MAPPING = {
    'male': 'M',
    'female': 'F',
    'non-binary': 'NB',
    'nonbinary': 'NB',
    'trans': 'NB',
    'transgender': 'NB',
    'genderqueer': 'NB',
    'genderfluid': 'NB',
    'agender': 'NB',
    'bigender': 'NB',
    'pangender': 'NB',
    'two-spirit': 'NB',
}


def download_and_save_facebook_photo(profile, photo_url, user_id):
    """
    Download photo from URL and save to CrushProfile.

    Args:
        profile: CrushProfile instance
        photo_url: URL to download photo from
        user_id: User ID for filename

    Returns:
        bool: True if photo was saved, False otherwise
    """
    try:
        response = requests.get(photo_url, timeout=15)
        response.raise_for_status()

        # Validate content type is an image
        content_type = response.headers.get('Content-Type', '')
        if not content_type.startswith('image/'):
            logger.warning(f"Facebook photo URL returned non-image content: {content_type}")
            return False

        # Determine file extension from content type
        ext = 'jpg'  # Default
        if 'png' in content_type:
            ext = 'png'
        elif 'gif' in content_type:
            ext = 'gif'
        elif 'webp' in content_type:
            ext = 'webp'

        profile.photo_1.save(
            f'facebook_{user_id}.{ext}',
            ContentFile(response.content),
            save=False
        )
        return True
    except requests.exceptions.Timeout:
        logger.error(f"Timeout downloading Facebook photo for user {user_id}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading Facebook photo for user {user_id}: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error saving Facebook photo for user {user_id}: {str(e)}")

    return False


def update_profile_from_facebook_data(profile, extra_data):
    """
    Update CrushProfile fields from Facebook extra_data.

    Args:
        profile: CrushProfile instance
        extra_data: Dictionary of Facebook user data

    Returns:
        bool: True if any fields were updated
    """
    updated = False

    # Set birthday if available and not already set
    if extra_data.get('birthday') and not profile.date_of_birth:
        try:
            # Facebook birthday format: "MM/DD/YYYY"
            birthday = datetime.strptime(extra_data['birthday'], '%m/%d/%Y').date()
            profile.date_of_birth = birthday
            updated = True
            logger.info(f"Set date_of_birth from Facebook (year: {birthday.year})")
        except ValueError as e:
            logger.error(f"Error parsing Facebook birthday (format error): {str(e)}")

    # Set gender if available and not already set
    if extra_data.get('gender') and not profile.gender:
        fb_gender = extra_data['gender'].lower()
        # Map known genders, default to 'O' (Other) for custom/unknown
        profile.gender = FACEBOOK_GENDER_MAPPING.get(fb_gender, 'O')
        updated = True
        logger.info(f"Set gender from Facebook (mapped to: {profile.gender})")

    return updated


def _get_facebook_photo_url(extra_data, access_token=None):
    """
    Get the best available Facebook photo URL.

    Tries high-res first, falls back to standard picture from extra_data.

    Args:
        extra_data: Facebook extra_data dictionary
        access_token: Optional access token for authenticated requests

    Returns:
        str: Photo URL or None
    """
    facebook_id = extra_data.get('id')
    photo_url = None

    # Try high-resolution photo first
    if facebook_id:
        photo_url = get_high_res_facebook_photo_url(facebook_id, access_token)
        if photo_url:
            logger.info(f"Got high-res Facebook photo URL")
            return photo_url

    # Fallback to standard picture from extra_data
    if 'picture' in extra_data:
        if isinstance(extra_data['picture'], dict):
            photo_url = extra_data['picture'].get('data', {}).get('url')
        else:
            photo_url = extra_data['picture']
        if photo_url:
            logger.info(f"Using fallback Facebook photo URL")

    return photo_url


@receiver(pre_social_login)
def update_facebook_profile_on_login(sender, request, sociallogin, **kwargs):
    """
    Update CrushProfile with Facebook data when user logs in with Facebook.
    Only processes Facebook logins for Crush.lu domain.

    This handles EXISTING users logging in again - updates profile if photo missing.
    Also sets a thread-local flag for the post_save handler.
    """
    # Reset the flag at the start (only for Facebook provider)
    if sociallogin.account.provider == 'facebook':
        _thread_local.is_crush_facebook_login = False

    # Only process Facebook logins
    if sociallogin.account.provider != 'facebook':
        return

    # Only process for crush.lu domain
    if not _is_crush_domain(request):
        logger.info(f"Skipping Facebook login processing for non-Crush domain")
        return

    # Set flag to indicate this is a crush.lu Facebook login
    _thread_local.is_crush_facebook_login = True

    # Track Crush.lu consent for OAuth signups (implicit consent via OAuth)
    if not sociallogin.is_existing:
        from crush_lu.models.profiles import UserDataConsent
        from crush_lu.oauth_statekit import get_client_ip
        _thread_local.oauth_consent_data = {
            'crushlu_consent': True,  # Implicit consent via OAuth signup
            'crushlu_consent_ip': get_client_ip(request),
        }

    logger.info(f"pre_social_login signal received for Facebook provider on crush.lu")

    try:
        extra_data = sociallogin.account.extra_data
        logger.debug(f"Facebook extra_data keys: {list(extra_data.keys())}")

        # If user exists, update their CrushProfile
        if hasattr(sociallogin.user, 'id') and sociallogin.user.id:
            try:
                profile = CrushProfile.objects.get(user=sociallogin.user)

                # Get access token for authenticated request
                access_token = sociallogin.token.token if sociallogin.token else None

                # Download and save profile photo if available and not already set
                if not profile.photo_1:
                    photo_url = _get_facebook_photo_url(extra_data, access_token)
                    if photo_url:
                        if download_and_save_facebook_photo(profile, photo_url, sociallogin.user.id):
                            logger.info(f"Saved Facebook photo for user {sociallogin.user.email}")

                # Update profile data from Facebook
                update_profile_from_facebook_data(profile, extra_data)

                profile.save()
                logger.info(f"Updated CrushProfile for existing user {sociallogin.user.email}")

            except CrushProfile.DoesNotExist:
                logger.info(f"No CrushProfile exists yet for user {sociallogin.user.email}")

    except Exception as e:
        logger.error(f"Error in pre_social_login handler: {str(e)}", exc_info=True)


@receiver(post_save, sender=SocialAccount)
def create_crush_profile_from_facebook(sender, instance, created, **kwargs):
    """
    Create CrushProfile when a new Facebook SocialAccount is created.
    Pre-fill profile data from Facebook information.

    This handles NEW users signing up via Facebook - creates profile with Facebook data.
    Only creates profile if the login originated from crush.lu domain.
    """
    if instance.provider != 'facebook':
        return

    if not created:
        return

    # Only create CrushProfile if login was from crush.lu
    # This flag is set by update_facebook_profile_on_login in pre_social_login
    if not getattr(_thread_local, 'is_crush_facebook_login', False):
        logger.info(f"Skipping CrushProfile creation for Facebook user {instance.user.email} - not a crush.lu login")
        return

    logger.info(f"SocialAccount post_save signal for Facebook on crush.lu (user: {instance.user.email})")

    try:
        # Get or create CrushProfile
        profile, profile_created = CrushProfile.objects.get_or_create(user=instance.user)

        if profile_created:
            logger.info(f"Created new CrushProfile for Facebook user {instance.user.email}")

        extra_data = instance.extra_data

        # Download and save Facebook photo if not already set
        # Note: We don't have access token in post_save, but high-res endpoint works without it
        if not profile.photo_1:
            photo_url = _get_facebook_photo_url(extra_data)
            if photo_url:
                if download_and_save_facebook_photo(profile, photo_url, instance.user.id):
                    logger.info(f"Saved Facebook photo for new user {instance.user.email}")

        # Update profile data from Facebook
        update_profile_from_facebook_data(profile, extra_data)

        # Don't set completion_status - let model default handle it
        # The view will detect empty profiles and start at step 1

        profile.save()
        logger.info(f"Updated CrushProfile from Facebook data in post_save")

    except Exception as e:
        logger.error(f"Error in SocialAccount post_save handler: {str(e)}", exc_info=True)


@receiver(user_logged_in)
def store_oauth_result_for_duplicate_handling(sender, request, user, **kwargs):
    """
    Store OAuth result in database for handling duplicate callback requests.

    On Android PWA, duplicate OAuth callbacks can arrive before cookies are committed.
    The first callback processes successfully but the second sees no session.
    By storing the result in the database, the second request can recover the auth.

    This signal fires AFTER Allauth successfully logs in the user.
    We store the user_id and redirect URL so duplicate requests can complete.
    """
    # Get state_id from request GET params (should be available on callback URL)
    state_id = request.GET.get('state')
    if not state_id:
        # Not an OAuth login, skip
        return

    try:
        from crush_lu.models import OAuthState

        # Determine redirect URL based on profile existence
        has_profile = hasattr(user, 'crushprofile')
        redirect_url = '/dashboard/' if has_profile else '/create-profile/'

        # Update the OAuth state with completion info
        updated = OAuthState.objects.filter(state_id=state_id).update(
            auth_completed=True,
            auth_user_id=user.id,
            auth_redirect_url=redirect_url,
            last_callback_at=timezone.now(),
        )

        if updated:
            logger.info(
                f"[OAUTH-RESULT] Stored OAuth result for state {state_id[:8]}... "
                f"(user_id={user.id}, redirect={redirect_url})"
            )
        else:
            logger.warning(
                f"[OAUTH-RESULT] Could not find OAuth state {state_id[:8]}... to store result"
            )

    except Exception as e:
        # Non-critical - just log and continue
        logger.error(f"[OAUTH-RESULT] Error storing OAuth result: {e}")


@receiver(user_logged_in)
def check_special_user_experience(sender, request, user, **kwargs):
    """
    Check if the logged-in user matches a special user experience configuration.
    If matched, activate the special experience in the session.
    Only processes for crush.lu domain.
    """
    # Only process for crush.lu domain
    try:
        host = request.get_host().split(':')[0].lower()
    except KeyError:
        # During tests, the request may not have SERVER_NAME set
        return
    if host not in ['crush.lu', 'www.crush.lu', 'localhost', '127.0.0.1']:
        return

    try:
        # Check if there's a matching special experience
        # Prioritize linked_user (gifts), then fall back to name match (legacy)
        special_experience = SpecialUserExperience.objects.filter(
            Q(is_active=True) &
            (
                Q(linked_user=user) |  # Direct link (gifts)
                Q(
                    first_name__iexact=user.first_name,
                    last_name__iexact=user.last_name,
                    linked_user__isnull=True  # Only name-match if no linked_user
                )
            )
        ).first()

        if special_experience:
            # Activate special experience in session
            request.session['special_experience_active'] = True
            request.session['special_experience_id'] = special_experience.id
            request.session['special_experience_data'] = {
                'welcome_title': special_experience.custom_welcome_title,
                'welcome_message': special_experience.custom_welcome_message,
                'theme_color': special_experience.custom_theme_color,
                'animation_style': special_experience.animation_style,
                'vip_badge': special_experience.vip_badge,
                'custom_landing_url': special_experience.custom_landing_url,
            }

            # Track the trigger
            special_experience.trigger()

            # Auto-approve profile if configured
            if special_experience.auto_approve_profile:
                try:
                    profile = CrushProfile.objects.get(user=user)
                    if not profile.is_approved:
                        profile.is_approved = True
                        profile.approved_at = timezone.now()
                        profile.save()
                        logger.info(f"Auto-approved profile for special user: {user.email}")
                except CrushProfile.DoesNotExist:
                    pass

            logger.info(f"✨ Special experience activated for {user.first_name} {user.last_name}")
        else:
            # Clear any existing special experience from session
            request.session.pop('special_experience_active', None)
            request.session.pop('special_experience_id', None)
            request.session.pop('special_experience_data', None)

    except Exception as e:
        logger.error(f"Error in check_special_user_experience handler: {str(e)}", exc_info=True)


# =============================================================================
# GOOGLE OAUTH PROFILE INTEGRATION
# =============================================================================

def get_high_res_google_photo_url(extra_data):
    """
    Get high-resolution Google profile photo URL.

    Google profile photos can be resized by modifying the URL:
    - Original: https://lh3.googleusercontent.com/a/...=s96-c
    - High-res: https://lh3.googleusercontent.com/a/...=s720-c

    Args:
        extra_data: Google extra_data dictionary containing 'picture' field

    Returns:
        str: URL to high-resolution photo, or None if unavailable
    """
    picture_url = extra_data.get('picture')
    if not picture_url:
        return None

    # Google photo URLs end with size parameter like =s96-c
    # We can replace this with =s720-c for higher resolution
    import re
    # Match patterns like =s96-c, =s96, ?sz=96
    if '=s' in picture_url:
        # Replace size parameter with 720
        high_res_url = re.sub(r'=s\d+(-c)?', '=s720-c', picture_url)
        logger.info(f"Enhanced Google photo URL to high-res")
        return high_res_url
    elif '?sz=' in picture_url:
        # Alternative size parameter format
        high_res_url = re.sub(r'\?sz=\d+', '?sz=720', picture_url)
        logger.info(f"Enhanced Google photo URL to high-res (sz format)")
        return high_res_url

    # Return original URL if no size parameter found
    return picture_url


def download_and_save_google_photo(profile, photo_url, user_id):
    """
    Download photo from Google URL and save to CrushProfile.

    Args:
        profile: CrushProfile instance
        photo_url: URL to download photo from
        user_id: User ID for filename

    Returns:
        bool: True if photo was saved, False otherwise
    """
    try:
        response = requests.get(photo_url, timeout=15)
        response.raise_for_status()

        # Validate content type is an image
        content_type = response.headers.get('Content-Type', '')
        if not content_type.startswith('image/'):
            logger.warning(f"Google photo URL returned non-image content: {content_type}")
            return False

        # Determine file extension from content type
        ext = 'jpg'  # Default
        if 'png' in content_type:
            ext = 'png'
        elif 'gif' in content_type:
            ext = 'gif'
        elif 'webp' in content_type:
            ext = 'webp'

        profile.photo_1.save(
            f'google_{user_id}.{ext}',
            ContentFile(response.content),
            save=False
        )
        return True
    except requests.exceptions.Timeout:
        logger.error(f"Timeout downloading Google photo for user {user_id}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading Google photo for user {user_id}: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error saving Google photo for user {user_id}: {str(e)}")

    return False


@receiver(pre_social_login)
def update_google_profile_on_login(sender, request, sociallogin, **kwargs):
    """
    Update CrushProfile with Google data when user logs in with Google.
    Only processes Google logins for Crush.lu domain.

    This handles EXISTING users logging in again - updates profile if photo missing.
    Also sets a thread-local flag for the post_save handler.
    """
    # Reset the flag at the start (only for Google provider)
    if sociallogin.account.provider == 'google':
        _thread_local.is_crush_google_login = False

    # Only process Google logins
    if sociallogin.account.provider != 'google':
        return

    # Only process for crush.lu domain
    if not _is_crush_domain(request):
        logger.info(f"Skipping Google login processing for non-Crush domain")
        return

    # Set flag to indicate this is a crush.lu Google login
    _thread_local.is_crush_google_login = True

    # Track Crush.lu consent for OAuth signups (implicit consent via OAuth)
    if not sociallogin.is_existing:
        from crush_lu.models.profiles import UserDataConsent
        from crush_lu.oauth_statekit import get_client_ip
        _thread_local.oauth_consent_data = {
            'crushlu_consent': True,  # Implicit consent via OAuth signup
            'crushlu_consent_ip': get_client_ip(request),
        }

    logger.info(f"pre_social_login signal received for Google provider on crush.lu")

    try:
        extra_data = sociallogin.account.extra_data
        logger.debug(f"Google extra_data keys: {list(extra_data.keys())}")

        # If user exists, update their CrushProfile
        if hasattr(sociallogin.user, 'id') and sociallogin.user.id:
            try:
                profile = CrushProfile.objects.get(user=sociallogin.user)

                # Download and save profile photo if available and not already set
                if not profile.photo_1:
                    photo_url = get_high_res_google_photo_url(extra_data)
                    if photo_url:
                        if download_and_save_google_photo(profile, photo_url, sociallogin.user.id):
                            logger.info(f"Saved Google photo for user {sociallogin.user.email}")

                profile.save()
                logger.info(f"Updated CrushProfile for existing Google user {sociallogin.user.email}")

            except CrushProfile.DoesNotExist:
                logger.info(f"No CrushProfile exists yet for user {sociallogin.user.email}")

    except Exception as e:
        logger.error(f"Error in Google pre_social_login handler: {str(e)}", exc_info=True)


@receiver(post_save, sender=SocialAccount)
def create_crush_profile_from_google(sender, instance, created, **kwargs):
    """
    Create CrushProfile when a new Google SocialAccount is created.
    Pre-fill profile data from Google information including profile photo.

    This handles NEW users signing up via Google - creates profile with Google photo.
    Only creates profile if the login originated from crush.lu domain.
    """
    if instance.provider != 'google':
        return

    if not created:
        return

    # Only create CrushProfile if login was from crush.lu
    # This flag is set by update_google_profile_on_login in pre_social_login
    if not getattr(_thread_local, 'is_crush_google_login', False):
        logger.info(f"Skipping CrushProfile creation for Google user {instance.user.email} - not a crush.lu login")
        return

    logger.info(f"SocialAccount post_save signal for Google on crush.lu (user: {instance.user.email})")

    try:
        # Get or create CrushProfile
        profile, profile_created = CrushProfile.objects.get_or_create(user=instance.user)

        if profile_created:
            logger.info(f"Created new CrushProfile for Google user {instance.user.email}")

        extra_data = instance.extra_data

        # Download and save Google photo if not already set
        if not profile.photo_1:
            photo_url = get_high_res_google_photo_url(extra_data)
            if photo_url:
                if download_and_save_google_photo(profile, photo_url, instance.user.id):
                    logger.info(f"Saved Google photo for new user {instance.user.email}")

        profile.save()
        logger.info(f"Updated CrushProfile from Google data in post_save")

    except Exception as e:
        logger.error(f"Error in Google SocialAccount post_save handler: {str(e)}", exc_info=True)


# =============================================================================
# MICROSOFT OAUTH PROFILE INTEGRATION
# =============================================================================

def get_microsoft_photo_url(extra_data):
    """
    Get Microsoft profile photo URL.

    Microsoft Graph API provides profile photos via a separate endpoint.
    The extra_data doesn't typically include the photo URL directly.
    We need to use the Graph API to fetch it.

    Args:
        extra_data: Microsoft extra_data dictionary

    Returns:
        str: URL to fetch photo from, or None if unavailable
    """
    # Microsoft doesn't provide photo URL in extra_data like Google/Facebook
    # The photo must be fetched from Graph API: /me/photo/$value
    # For now, we return None - photos would need to be fetched with access token
    return None


@receiver(pre_social_login)
def update_microsoft_profile_on_login(sender, request, sociallogin, **kwargs):
    """
    Update CrushProfile with Microsoft data when user logs in with Microsoft.
    Only processes Microsoft logins for Crush.lu domain.

    This handles EXISTING users logging in again.
    Also sets a thread-local flag for the post_save handler.
    """
    # Reset the flag at the start (only for Microsoft provider)
    if sociallogin.account.provider == 'microsoft':
        _thread_local.is_crush_microsoft_login = False

    # Only process Microsoft logins
    if sociallogin.account.provider != 'microsoft':
        return

    # Only process for crush.lu domain
    if not _is_crush_domain(request):
        logger.info(f"Skipping Microsoft login processing for non-Crush domain")
        return

    # Set flag to indicate this is a crush.lu Microsoft login
    _thread_local.is_crush_microsoft_login = True

    # Track Crush.lu consent for OAuth signups (implicit consent via OAuth)
    if not sociallogin.is_existing:
        from crush_lu.models.profiles import UserDataConsent
        from crush_lu.oauth_statekit import get_client_ip
        _thread_local.oauth_consent_data = {
            'crushlu_consent': True,  # Implicit consent via OAuth signup
            'crushlu_consent_ip': get_client_ip(request),
        }

    logger.info(f"pre_social_login signal received for Microsoft provider on crush.lu")

    try:
        extra_data = sociallogin.account.extra_data
        logger.debug(f"Microsoft extra_data keys: {list(extra_data.keys())}")
        logger.info(f"Microsoft extra_data: displayName={extra_data.get('displayName')}, "
                   f"givenName={extra_data.get('givenName')}, surname={extra_data.get('surname')}, "
                   f"mail={extra_data.get('mail')}, userPrincipalName={extra_data.get('userPrincipalName')}")

        # If user exists, update their CrushProfile
        if hasattr(sociallogin.user, 'id') and sociallogin.user.id:
            try:
                profile = CrushProfile.objects.get(user=sociallogin.user)
                profile.save()
                logger.info(f"Updated CrushProfile for existing Microsoft user {sociallogin.user.email}")

            except CrushProfile.DoesNotExist:
                logger.info(f"No CrushProfile exists yet for user {sociallogin.user.email}")

    except Exception as e:
        logger.error(f"Error in Microsoft pre_social_login handler: {str(e)}", exc_info=True)


@receiver(post_save, sender=SocialAccount)
def create_crush_profile_from_microsoft(sender, instance, created, **kwargs):
    """
    Create CrushProfile when a new Microsoft SocialAccount is created.

    This handles NEW users signing up via Microsoft - creates basic profile.
    Note: Microsoft doesn't provide profile photo in OAuth extra_data like Google/Facebook.
    Only creates profile if the login originated from crush.lu domain.
    """
    if instance.provider != 'microsoft':
        return

    if not created:
        return

    # Only create CrushProfile if login was from crush.lu
    # This flag is set by update_microsoft_profile_on_login in pre_social_login
    if not getattr(_thread_local, 'is_crush_microsoft_login', False):
        logger.info(f"Skipping CrushProfile creation for Microsoft user {instance.user.email} - not a crush.lu login")
        return

    logger.info(f"SocialAccount post_save signal for Microsoft on crush.lu (user: {instance.user.email})")

    try:
        # Get or create CrushProfile
        profile, profile_created = CrushProfile.objects.get_or_create(user=instance.user)

        if profile_created:
            logger.info(f"Created new CrushProfile for Microsoft user {instance.user.email}")

        profile.save()
        logger.info(f"Updated CrushProfile from Microsoft data in post_save")

    except Exception as e:
        logger.error(f"Error in Microsoft SocialAccount post_save handler: {str(e)}", exc_info=True)


# =============================================================================
# WALLET PASS UPDATE SIGNAL HANDLERS
# =============================================================================

@receiver(post_save, sender=CrushProfile)
def trigger_wallet_pass_update_on_profile_change(sender, instance, created, update_fields, **kwargs):
    """
    Trigger wallet pass updates when relevant profile fields change.

    This signal fires on CrushProfile save and checks if any wallet-relevant
    fields were updated. If so, it triggers push notifications to Apple Wallet
    devices and updates Google Wallet objects.

    Wallet-relevant fields include:
    - referral_points, membership_tier (rewards/status)
    - show_photo_on_wallet, photo_1 (appearance)
    - display_name, first_name, last_name, show_full_name (identity)
    """
    # Skip if this is a new profile (no pass exists yet)
    if created:
        return

    # Skip if profile has no wallet passes registered
    if not instance.apple_pass_serial and not instance.google_wallet_object_id:
        return

    # Check if any wallet-relevant fields were updated
    should_update = False

    if update_fields is not None:
        # Specific fields were updated - check if any are wallet-relevant
        updated_fields = set(update_fields)
        if updated_fields & WALLET_UPDATE_PROFILE_FIELDS:
            should_update = True
            logger.debug(
                f"Wallet-relevant fields updated for user {instance.user_id}: "
                f"{updated_fields & WALLET_UPDATE_PROFILE_FIELDS}"
            )
    else:
        # Full save - trigger update to be safe
        # This happens when save() is called without update_fields
        should_update = True
        logger.debug(f"Full profile save for user {instance.user_id}, triggering wallet update")

    if should_update:
        try:
            trigger_wallet_pass_updates(instance)
        except Exception as e:
            # Don't fail the save if wallet update fails
            logger.error(f"Error triggering wallet update for user {instance.user_id}: {e}")


@receiver(post_save, sender=EventRegistration)
def trigger_wallet_pass_update_on_registration_change(sender, instance, created, **kwargs):
    """
    Trigger wallet pass updates when event registrations change.

    The wallet pass displays "Next Event" information, so when a user:
    - Registers for a new event
    - Changes registration status (confirmed, waitlist, cancelled)
    - Registration is deleted

    We need to update the pass to reflect the new "next event" info.
    """
    # Get the user's profile
    try:
        profile = CrushProfile.objects.get(user=instance.user)
    except CrushProfile.DoesNotExist:
        return

    # Skip if profile has no wallet passes registered
    if not profile.apple_pass_serial and not profile.google_wallet_object_id:
        return

    # Only trigger for status changes that affect "next event" display
    # - New confirmed/waitlist registration (shows new event)
    # - Status change to/from confirmed/waitlist (changes next event)
    relevant_statuses = {'confirmed', 'waitlist'}

    if created and instance.status in relevant_statuses:
        # New registration for upcoming event
        logger.debug(f"New event registration for user {instance.user_id}, triggering wallet update")
        try:
            trigger_wallet_pass_updates(profile)
        except Exception as e:
            logger.error(f"Error triggering wallet update on registration for user {instance.user_id}: {e}")
    elif not created:
        # Registration was updated (status change, etc.)
        # We can't easily check what changed without pre_save tracking,
        # so trigger update for any modification to relevant statuses
        logger.debug(f"Event registration updated for user {instance.user_id}, triggering wallet update")
        try:
            trigger_wallet_pass_updates(profile)
        except Exception as e:
            logger.error(f"Error triggering wallet update on registration change for user {instance.user_id}: {e}")


# =============================================================================
# COACH STAFF STATUS MANAGEMENT
# =============================================================================

@receiver(post_save, sender=CrushCoach)
def manage_coach_staff_status(sender, instance, created, **kwargs):
    """
    Automatically manage staff status for Crush coaches.

    When a CrushCoach record is created or updated:
    - If coach is_active=True: Grant is_staff=True to allow admin panel access
    - If coach is_active=False: Revoke is_staff=True (unless user is superuser)

    This enables coaches to log into the admin panel via social login (Google, Microsoft)
    without requiring manual staff status assignment.

    Note: Superusers are never affected - their staff status is preserved.
    """
    user = instance.user

    # Never modify superuser accounts
    if user.is_superuser:
        return

    try:
        if instance.is_active:
            # Grant staff status to active coaches
            if not user.is_staff:
                user.is_staff = True
                user.save(update_fields=['is_staff'])
                logger.info(f"Granted staff status to coach: {user.email}")
        else:
            # Revoke staff status from inactive coaches
            # Only revoke if they don't have other admin roles
            if user.is_staff:
                user.is_staff = False
                user.save(update_fields=['is_staff'])
                logger.info(f"Revoked staff status from inactive coach: {user.email}")

    except Exception as e:
        logger.error(f"Error managing staff status for coach {user.id}: {e}")


# =============================================================================
# OUTLOOK CONTACT SYNC
# =============================================================================

# Fields that should trigger an Outlook contact update when changed
OUTLOOK_SYNC_PROFILE_FIELDS = {
    'phone_number',
    'phone_verified',
    'location',
    'date_of_birth',
    'gender',
    'photo_1',
}

# User fields that should trigger an Outlook contact update
OUTLOOK_SYNC_USER_FIELDS = {
    'first_name',
    'last_name',
    'email',
}

# Test email domains that should NEVER sync to Outlook
TEST_EMAIL_DOMAINS = ['example.com', 'example.org', 'test.com', 'localhost', 'crush.test']


def is_test_user(user):
    """
    Check if user is a test/sample user that should never sync to Outlook.

    This is a defense-in-depth check - is_sync_enabled() should already
    block test execution, but this catches edge cases.

    Args:
        user: Django User instance

    Returns:
        bool: True if user is a test user, False otherwise
    """
    email_domain = user.email.split('@')[-1] if user.email and '@' in user.email else ''
    return email_domain.lower() in TEST_EMAIL_DOMAINS


@receiver(post_save, sender=CrushProfile)
def sync_profile_to_outlook(sender, instance, created, update_fields, **kwargs):
    """
    Automatically sync phone-verified profiles to Outlook contacts (production only).

    Only syncs profiles that:
    1. Have a verified phone number (phone_verified=True, required for caller ID)
    2. Are NOT test users (no example.com, test.com, etc.)

    This enables caller ID recognition when Crush.lu users call, regardless of
    profile approval status.

    Args:
        sender: The model class (CrushProfile)
        instance: The CrushProfile instance being saved
        created: True if this is a new profile
        update_fields: Set of field names being updated (if using update_fields)
        **kwargs: Additional signal arguments
    """
    from crush_lu.services.graph_contacts import GraphContactsService, is_sync_enabled

    # Skip sync if not in production environment
    if not is_sync_enabled():
        return

    # CRITICAL: Never sync test users (example.com, test.com, localhost)
    if is_test_user(instance.user):
        logger.debug(f"Skipping Outlook sync for test user: {instance.user.email}")
        return

    # Only sync if phone number exists (required for caller ID)
    if not instance.phone_number:
        return

    # Only sync phone-verified profiles (caller ID requires verified phone)
    if not instance.phone_verified:
        # If profile was previously synced but phone is now unverified, delete the contact
        if instance.outlook_contact_id:
            try:
                service = GraphContactsService()
                service.delete_contact(instance.outlook_contact_id)
                # Clear the contact ID using update() to avoid infinite recursion
                CrushProfile.objects.filter(pk=instance.pk).update(outlook_contact_id="")
                logger.info(f"Deleted Outlook contact for unverified phone {instance.pk}")
            except Exception as e:
                logger.warning(f"Failed to delete Outlook contact for unverified phone {instance.pk}: {e}")
        return

    # Check if relevant fields were updated (if update_fields provided)
    should_sync = False

    if created:
        # Always sync new profiles with phone numbers
        should_sync = True
    elif update_fields is not None:
        # Check if any Outlook-relevant fields were updated
        updated_fields = set(update_fields)
        if updated_fields & OUTLOOK_SYNC_PROFILE_FIELDS:
            should_sync = True
    else:
        # Full save (no update_fields) - sync to be safe
        should_sync = True

    if should_sync:
        try:
            service = GraphContactsService()
            service.sync_profile(instance)
        except Exception as e:
            # Don't fail the save if Outlook sync fails
            logger.warning(f"Failed to sync profile {instance.pk} to Outlook: {e}")


@receiver(post_save, sender=User)
def sync_user_to_outlook_on_name_change(sender, instance, created, update_fields, **kwargs):
    """
    Sync Outlook contact when User name/email changes.

    This handles cases where the User model is updated independently
    of CrushProfile (e.g., admin changes name, user changes email).

    Only runs in production when OUTLOOK_CONTACT_SYNC_ENABLED=true.
    """
    from crush_lu.services.graph_contacts import GraphContactsService, is_sync_enabled

    # Skip for new users (no profile yet)
    if created:
        return

    # Skip sync if not in production environment
    if not is_sync_enabled():
        return

    # Check if relevant fields were updated
    if update_fields is not None:
        updated_fields = set(update_fields)
        if not (updated_fields & OUTLOOK_SYNC_USER_FIELDS):
            return

    # Check if user has a CrushProfile with Outlook sync
    try:
        profile = instance.crushprofile
    except CrushProfile.DoesNotExist:
        return

    # Only sync if profile has phone number and is already synced
    if not profile.phone_number or not profile.outlook_contact_id:
        return

    try:
        service = GraphContactsService()
        service.sync_profile(profile)
    except Exception as e:
        logger.warning(f"Failed to sync user {instance.pk} to Outlook: {e}")


@receiver(pre_delete, sender=CrushProfile)
def delete_outlook_contact_on_profile_delete(sender, instance, **kwargs):
    """
    Delete Outlook contact when CrushProfile is deleted (production only).

    This handler fires when:
    1. A CrushProfile is deleted directly via admin
    2. A User is deleted (cascades to CrushProfile due to OneToOneField)

    The contact is removed from the shared mailbox to prevent orphaned entries.
    Only runs in production when OUTLOOK_CONTACT_SYNC_ENABLED=true.

    Args:
        sender: The model class (CrushProfile)
        instance: The CrushProfile instance being deleted
        **kwargs: Additional signal arguments
    """
    from crush_lu.services.graph_contacts import GraphContactsService, is_sync_enabled

    # Skip deletion if not in production environment
    if not is_sync_enabled():
        return

    # Only delete if profile has an Outlook contact
    if not instance.outlook_contact_id:
        logger.debug(f"Profile {instance.pk} has no Outlook contact ID - skipping deletion")
        return

    try:
        service = GraphContactsService()
        success = service.delete_contact(instance.outlook_contact_id)

        if success:
            logger.info(
                f"Deleted Outlook contact {instance.outlook_contact_id} for profile {instance.pk} "
                f"(user: {instance.user.email})"
            )
        else:
            logger.warning(
                f"Failed to delete Outlook contact {instance.outlook_contact_id} for profile {instance.pk}"
            )
    except Exception as e:
        # Don't fail the deletion if Outlook sync fails
        # The profile will be deleted regardless, just log the error
        logger.error(
            f"Error deleting Outlook contact {instance.outlook_contact_id} for profile {instance.pk}: {e}"
        )
