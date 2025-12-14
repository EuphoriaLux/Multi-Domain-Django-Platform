"""
Signal handlers for Crush.lu app
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.signals import user_logged_in
from allauth.socialaccount.signals import pre_social_login, social_account_updated
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth.models import User
from django.utils import timezone
from .models import MeetupEvent, EventActivityOption, CrushProfile, SpecialUserExperience
from .storage import initialize_user_storage
import logging
from datetime import datetime
import requests
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)


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


@receiver(pre_social_login)
def update_facebook_profile_on_login(sender, request, sociallogin, **kwargs):
    """
    Update CrushProfile with Facebook data when user logs in with Facebook.
    Only processes Facebook logins for Crush.lu domain.
    """
    # Only process Facebook logins
    if sociallogin.account.provider != 'facebook':
        return

    # Only process for crush.lu domain
    host = request.get_host().split(':')[0].lower()
    if host not in ['crush.lu', 'www.crush.lu', 'localhost', '127.0.0.1']:
        logger.info(f"Skipping Facebook login processing for non-Crush domain: {host}")
        return

    logger.info(f"pre_social_login signal received for Facebook provider")

    try:
        extra_data = sociallogin.account.extra_data
        logger.info(f"Facebook extra_data: {extra_data}")

        # Extract profile photo URL
        photo_url = None
        if 'picture' in extra_data:
            if isinstance(extra_data['picture'], dict):
                photo_url = extra_data['picture'].get('data', {}).get('url')
            else:
                photo_url = extra_data['picture']

        logger.info(f"Extracted Facebook photo URL: {photo_url}")

        # If user exists, update their CrushProfile
        if hasattr(sociallogin.user, 'id') and sociallogin.user.id:
            try:
                profile = CrushProfile.objects.get(user=sociallogin.user)

                # Download and save profile photo if available and not already set
                if photo_url and not profile.photo_1:
                    try:
                        response = requests.get(photo_url, timeout=10)
                        response.raise_for_status()

                        # Save to photo_1 field
                        profile.photo_1.save(
                            f'facebook_{sociallogin.user.id}.jpg',
                            ContentFile(response.content),
                            save=False
                        )
                        logger.info(f"Saved Facebook photo to CrushProfile for user {sociallogin.user.email}")
                    except Exception as e:
                        logger.error(f"Error downloading Facebook photo: {str(e)}")

                # Update profile data from Facebook if not already set
                if extra_data.get('birthday') and not profile.date_of_birth:
                    try:
                        # Facebook birthday format: "MM/DD/YYYY"
                        birthday = datetime.strptime(extra_data['birthday'], '%m/%d/%Y').date()
                        profile.date_of_birth = birthday
                        logger.info(f"Set date_of_birth from Facebook: {birthday}")
                    except Exception as e:
                        logger.error(f"Error parsing Facebook birthday: {str(e)}")

                if extra_data.get('gender') and not profile.gender:
                    fb_gender = extra_data['gender'].lower()
                    gender_mapping = {
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
                    # Map known genders, default to 'O' (Other) for custom/unknown
                    profile.gender = gender_mapping.get(fb_gender, 'O')
                    logger.info(f"Set gender from Facebook: {fb_gender} -> {profile.gender}")

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
    """
    if instance.provider != 'facebook':
        return

    if not created:
        return

    logger.info(f"SocialAccount post_save signal for Facebook (user: {instance.user.email})")

    try:
        # Get or create CrushProfile
        profile, profile_created = CrushProfile.objects.get_or_create(user=instance.user)

        if profile_created:
            logger.info(f"Created new CrushProfile for Facebook user {instance.user.email}")

        extra_data = instance.extra_data

        # Download and save profile photo
        photo_url = None
        if 'picture' in extra_data:
            if isinstance(extra_data['picture'], dict):
                photo_url = extra_data['picture'].get('data', {}).get('url')
            else:
                photo_url = extra_data['picture']

        if photo_url and not profile.photo_1:
            try:
                response = requests.get(photo_url, timeout=10)
                response.raise_for_status()

                profile.photo_1.save(
                    f'facebook_{instance.user.id}.jpg',
                    ContentFile(response.content),
                    save=False
                )
                logger.info(f"Saved Facebook photo from post_save: {photo_url}")
            except Exception as e:
                logger.error(f"Error downloading Facebook photo in post_save: {str(e)}")

        # Set birthday if available
        if extra_data.get('birthday') and not profile.date_of_birth:
            try:
                birthday = datetime.strptime(extra_data['birthday'], '%m/%d/%Y').date()
                profile.date_of_birth = birthday
            except Exception as e:
                logger.error(f"Error parsing birthday in post_save: {str(e)}")

        # Set gender if available
        if extra_data.get('gender') and not profile.gender:
            fb_gender = extra_data['gender'].lower()
            gender_mapping = {
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
            # Map known genders, default to 'O' (Other) for custom/unknown
            profile.gender = gender_mapping.get(fb_gender, 'O')
            logger.info(f"Set gender from Facebook in post_save: {fb_gender} -> {profile.gender}")

        # Don't set completion_status - let model default handle it
        # The view will detect empty profiles and start at step 1

        profile.save()
        logger.info(f"Updated CrushProfile from Facebook data in post_save")

    except Exception as e:
        logger.error(f"Error in SocialAccount post_save handler: {str(e)}", exc_info=True)


@receiver(user_logged_in)
def check_special_user_experience(sender, request, user, **kwargs):
    """
    Check if the logged-in user matches a special user experience configuration.
    If matched, activate the special experience in the session.
    Only processes for crush.lu domain.
    """
    # Only process for crush.lu domain
    host = request.get_host().split(':')[0].lower()
    if host not in ['crush.lu', 'www.crush.lu', 'localhost', '127.0.0.1']:
        return

    try:
        # Check if there's a matching special experience
        special_experience = SpecialUserExperience.objects.filter(
            first_name__iexact=user.first_name,
            last_name__iexact=user.last_name,
            is_active=True
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
