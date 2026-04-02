# entreprinder/signals.py

import logging
import threading
from django.dispatch import receiver
from django.db.models.signals import post_save
from allauth.socialaccount.signals import pre_social_login, social_account_updated
from allauth.socialaccount.models import SocialAccount
from entreprinder.models import EntrepreneurProfile

logger = logging.getLogger(__name__)

# Thread-local storage to pass domain context between signals
_thread_local = threading.local()

# Entreprinder domains - profile should be created for logins on these domains
ENTREPRINDER_DOMAINS = ['entreprinder.lu', 'www.entreprinder.lu', 'localhost', '127.0.0.1']


def _is_entreprinder_domain(request):
    """Check if current request is from entreprinder.lu domain"""
    if not request:
        return False
    try:
        host = request.get_host().split(':')[0].lower()
    except (KeyError, AttributeError):
        return False
    return host in ENTREPRINDER_DOMAINS

@receiver(pre_social_login)
def update_linkedin_photo_on_login(sender, request, sociallogin, **kwargs):
    """
    Update LinkedIn photo URL when user logs in with LinkedIn.

    Also sets a thread-local flag to indicate this is an entreprinder.lu login,
    which is used by post_save handlers to decide whether to create profiles.
    """
    # Reset the flag at the start of each login attempt
    _thread_local.is_entreprinder_login = False

    if sociallogin.account.provider != 'openid_connect_linkedin':
        return

    # Check if this is an entreprinder.lu domain login
    if not _is_entreprinder_domain(request):
        logger.info(f"Skipping LinkedIn processing for non-Entreprinder domain")
        return

    # Set flag to indicate this is an entreprinder.lu login
    _thread_local.is_entreprinder_login = True

    logger.info(f"pre_social_login signal received for LinkedIn on entreprinder.lu")

    try:
        # Log the full extra_data for debugging
        extra_data = sociallogin.account.extra_data
        logger.info(f"LinkedIn extra_data: {extra_data}")

        # Extract the photo URL from the OpenID Connect data
        photo_url = extra_data.get('picture', '')
        logger.info(f"Extracted LinkedIn photo URL: {photo_url}")

        if photo_url and hasattr(sociallogin.user, 'id') and sociallogin.user.id:
            # User already exists, update their profile
            try:
                profile = EntrepreneurProfile.objects.get(user=sociallogin.user)
                profile.linkedin_photo_url = photo_url
                profile.save()
                logger.info(f"Updated LinkedIn photo URL for existing user {sociallogin.user.email}")
            except EntrepreneurProfile.DoesNotExist:
                # Profile doesn't exist yet, will be created later
                logger.info(f"No profile exists yet for user {sociallogin.user.email}")
    except Exception as e:
        logger.error(f"Error in pre_social_login handler: {str(e)}", exc_info=True)

@receiver(social_account_updated)
def update_linkedin_photo_on_account_update(sender, request, sociallogin, **kwargs):
    """Update LinkedIn photo URL when a social account is updated on entreprinder.lu"""
    if sociallogin.account.provider != 'openid_connect_linkedin':
        return

    # Only process for entreprinder.lu domain
    if not _is_entreprinder_domain(request):
        return

    logger.info(f"social_account_updated signal received for LinkedIn on entreprinder.lu")

    try:
        extra_data = sociallogin.account.extra_data
        photo_url = extra_data.get('picture', '')
        logger.info(f"Extracted LinkedIn photo URL: {photo_url}")

        if photo_url:
            user = sociallogin.user
            profile, created = EntrepreneurProfile.objects.get_or_create(user=user)
            profile.linkedin_photo_url = photo_url
            profile.save()
            logger.info(f"Updated LinkedIn photo URL for user {user.email} (created: {created})")
    except Exception as e:
        logger.error(f"Error in social_account_updated handler: {str(e)}", exc_info=True)


@receiver(post_save, sender=SocialAccount)
def update_linkedin_photo_on_social_account_save(sender, instance, created, **kwargs):
    """
    Update LinkedIn photo URL when a SocialAccount is saved.

    Only creates/updates EntrepreneurProfile if the login originated from entreprinder.lu.
    We check for a thread-local flag set by the pre_social_login signal.
    """
    if instance.provider != 'openid_connect_linkedin':
        return

    if not created:
        return

    # Only create EntrepreneurProfile if login was from entreprinder.lu
    # This flag is set by update_linkedin_photo_on_login in pre_social_login
    if not getattr(_thread_local, 'is_entreprinder_login', False):
        logger.info(f"Skipping EntrepreneurProfile creation for {instance.user.email} - not an entreprinder.lu login")
        return

    try:
        logger.info(f"SocialAccount post_save signal for LinkedIn on entreprinder.lu (user: {instance.user.email})")

        # Extract the photo URL
        extra_data = instance.extra_data
        photo_url = extra_data.get('picture', '')

        if photo_url:
            profile, profile_created = EntrepreneurProfile.objects.get_or_create(user=instance.user)
            profile.linkedin_photo_url = photo_url
            profile.save()
            logger.info(f"Updated LinkedIn photo URL from post_save: {photo_url}")
    except Exception as e:
        logger.error(f"Error in SocialAccount post_save handler: {str(e)}", exc_info=True)