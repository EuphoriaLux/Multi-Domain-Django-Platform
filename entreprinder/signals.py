# entreprinder/signals.py
from django.dispatch import receiver
from allauth.socialaccount.signals import social_account_added, social_account_updated
from allauth.socialaccount.models import SocialAccount
from entreprinder.models import EntrepreneurProfile
import logging
import json

logger = logging.getLogger(__name__)

def extract_linkedin_photo_url(provider, extra_data):
    """
    Extract the photo URL from LinkedIn account data based on provider type.
    """
    logger.debug(f"Extracting LinkedIn photo URL for provider: {provider}")
    logger.debug(f"Extra data keys: {list(extra_data.keys())}")
    
    photo_url = None
    
    if provider == 'openid_connect_linkedin':
        # OIDC provider includes the picture in the root of extra_data
        photo_url = extra_data.get("picture", "")
        logger.debug(f"OpenID Connect LinkedIn photo URL: {photo_url}")
    
    elif provider == 'linkedin_oauth2':
        logger.debug(f"Raw LinkedIn OAuth2 extra_data: {json.dumps(extra_data, indent=2)}")
        
        # LinkedIn API v2 structure with profilePicture
        if 'profilePicture' in extra_data:
            try:
                logger.debug("Found profilePicture field in LinkedIn data")
                # Check for displayImage structure
                if 'displayImage~' in extra_data['profilePicture']:
                    elements = extra_data['profilePicture']['displayImage~'].get('elements', [])
                    logger.debug(f"Found displayImage elements: {len(elements)}")
                    
                    if elements:
                        # Find the highest resolution image
                        for element in elements:
                            identifiers = element.get('identifiers', [])
                            if identifiers and len(identifiers) > 0:
                                photo_url = identifiers[0].get('identifier', '')
                                logger.debug(f"Found photo URL from identifiers: {photo_url}")
                                break
            except Exception as e:
                logger.exception(f"Error extracting profilePicture: {str(e)}")
        
        # Legacy LinkedIn API structure
        if not photo_url and 'pictureUrl' in extra_data:
            photo_url = extra_data.get('pictureUrl')
            logger.debug(f"Found pictureUrl: {photo_url}")
        
        # Check for picture-url in extra_data
        if not photo_url and 'picture-url' in extra_data:
            photo_url = extra_data.get('picture-url')
            logger.debug(f"Found picture-url: {photo_url}")
        
        # Some versions might have a direct picture field
        if not photo_url and 'picture' in extra_data:
            photo_url = extra_data.get('picture')
            logger.debug(f"Found picture field: {photo_url}")
    
    # If we still don't have a photo URL, search for any field containing possible image URLs
    if not photo_url:
        logger.debug("No standard picture field found, searching all fields...")
        # Look for any field that might contain an image URL
        for key, value in extra_data.items():
            if isinstance(value, str) and any(term in key.lower() for term in ['picture', 'photo', 'image']):
                if value.startswith('http'):
                    photo_url = value
                    logger.debug(f"Found potential image URL in field {key}: {photo_url}")
                    break
    
    return photo_url

@receiver(social_account_added)
def update_linkedin_photo_url_added(request, sociallogin, **kwargs):
    """
    Fired when a user adds a new LinkedIn account.
    """
    provider = sociallogin.account.provider
    logger.info(f"Social account added signal received for provider: {provider}")
    
    if provider == 'openid_connect_linkedin':
        user = sociallogin.user
        extra_data = sociallogin.account.extra_data
        
        # For OpenID Connect, try multiple possible field names for the picture
        photo_url = None
        possible_fields = ['picture', 'profile_picture', 'profilePicture', 'avatar', 'image']
        
        for field in possible_fields:
            if field in extra_data and extra_data[field]:
                photo_url = extra_data[field]
                logger.info(f"Found photo URL in field '{field}': {photo_url}")
                break
        
        # If still no photo URL, check for nested structures
        if not photo_url and 'profile' in extra_data:
            profile = extra_data['profile']
            for field in possible_fields:
                if field in profile and profile[field]:
                    photo_url = profile[field]
                    logger.info(f"Found photo URL in profile.{field}: {photo_url}")
                    break
        
        # If we found a photo URL, update the profile
        if photo_url:
            profile, _ = EntrepreneurProfile.objects.get_or_create(user=user)
            profile.linkedin_photo_url = photo_url
            profile.save()
            logger.info(f"Successfully updated LinkedIn photo URL for user {user.email}: {photo_url}")
        else:
            # Log the entire extra_data structure to see what we're getting
            logger.warning(f"Could not find a photo URL for user {user.email}")
            logger.debug(f"Complete extra_data from OpenID Connect: {json.dumps(extra_data, indent=2)}")
            
    elif provider == 'linkedin_oauth2':
        user = sociallogin.user
        extra_data = sociallogin.account.extra_data
        logger.debug(f"LinkedIn OAuth2 extra_data: {json.dumps(extra_data, indent=2)}")
        
        # LinkedIn API v2 structure with profilePicture
        photo_url = None
        if 'profilePicture' in extra_data:
            try:
                logger.debug("Found profilePicture field in LinkedIn data")
                # Check for displayImage structure
                if 'displayImage~' in extra_data['profilePicture']:
                    elements = extra_data['profilePicture']['displayImage~'].get('elements', [])
                    
                    if elements and len(elements) > 0:
                        # Get highest quality image (usually the last)
                        identifiers = elements[-1].get('identifiers', [])
                        
                        if identifiers and len(identifiers) > 0:
                            photo_url = identifiers[0].get('identifier', '')
                            logger.debug(f"Found photo URL from identifiers: {photo_url}")
            except Exception as e:
                logger.exception(f"Error parsing profilePicture: {str(e)}")
        
        # Legacy LinkedIn API structure
        if not photo_url and 'pictureUrl' in extra_data:
            photo_url = extra_data.get('pictureUrl')
            logger.debug(f"Found pictureUrl: {photo_url}")
            
        # Check for picture-url in extra_data
        if not photo_url and 'picture-url' in extra_data:
            photo_url = extra_data.get('picture-url')
            logger.debug(f"Found picture-url: {photo_url}")
        
        # If we found a photo URL, update the profile
        if photo_url:
            profile, _ = EntrepreneurProfile.objects.get_or_create(user=user)
            profile.linkedin_photo_url = photo_url
            profile.save()
            logger.info(f"Successfully updated LinkedIn photo URL for user {user.email}: {photo_url}")
        else:
            logger.warning(f"Could not find a photo URL for user {user.email} from LinkedIn OAuth2")

@receiver(social_account_updated)
def update_linkedin_photo_url_updated(request, sociallogin, **kwargs):
    """
    Fired when a user re-authenticates or updates their LinkedIn account.
    """
    # Just call the same function used for social_account_added
    update_linkedin_photo_url_added(request, sociallogin, **kwargs)