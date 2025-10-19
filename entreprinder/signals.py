# entreprinder/signals.py

from django.dispatch import receiver
from allauth.socialaccount.signals import pre_social_login, social_account_updated
from entreprinder.models import EntrepreneurProfile
from django.db.models.signals import post_save
from allauth.socialaccount.models import SocialAccount
import logging

logger = logging.getLogger(__name__)

@receiver(pre_social_login)
def update_linkedin_photo_on_login(sender, request, sociallogin, **kwargs):
    """Update LinkedIn photo URL when user logs in with LinkedIn"""
    logger.info(f"pre_social_login signal received for provider: {sociallogin.account.provider}")
    
    if sociallogin.account.provider == 'openid_connect_linkedin':
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


    """Update LinkedIn photo URL when a social account is updated"""
    logger.info(f"social_account_updated signal received for provider: {sociallogin.account.provider}")
    
    if sociallogin.account.provider == 'openid_connect_linkedin':
        try:
            # Same logic as pre_social_login handler
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
    """Update LinkedIn photo URL when a SocialAccount is saved"""
    if instance.provider == 'openid_connect_linkedin':
        try:
            logger.info(f"SocialAccount post_save signal for {instance.provider} (user: {instance.user.email})")
            
            # Extract the photo URL
            extra_data = instance.extra_data
            photo_url = extra_data.get('picture', '')
            
            if photo_url:
                profile, created = EntrepreneurProfile.objects.get_or_create(user=instance.user)
                profile.linkedin_photo_url = photo_url
                profile.save()
                logger.info(f"Updated LinkedIn photo URL from post_save: {photo_url}")
        except Exception as e:
            logger.error(f"Error in SocialAccount post_save handler: {str(e)}", exc_info=True)