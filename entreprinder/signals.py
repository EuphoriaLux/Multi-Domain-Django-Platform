# entreprinder/signals.py
from django.dispatch import receiver
from allauth.socialaccount.signals import social_account_added, social_account_updated
from allauth.socialaccount.models import SocialAccount
from entreprinder.models import EntrepreneurProfile

@receiver(social_account_added)
def update_linkedin_photo_url_added(request, sociallogin, **kwargs):
    """
    Fired when a user adds a new LinkedIn OIDC account.
    """
    if sociallogin.account.provider == 'openid_connect_linkedin':
        user = sociallogin.user
        extra_data = sociallogin.account.extra_data
        photo_url = extra_data.get("picture", "")

        profile, _ = EntrepreneurProfile.objects.get_or_create(user=user)
        profile.linkedin_photo_url = photo_url
        profile.save()

@receiver(social_account_updated)
def update_linkedin_photo_url_updated(request, sociallogin, **kwargs):
    """
    Fired when a user re-authenticates or updates their LinkedIn OIDC account.
    """
    if sociallogin.account.provider == 'openid_connect_linkedin':
        user = sociallogin.user
        extra_data = sociallogin.account.extra_data
        photo_url = extra_data.get("picture", "")

        profile, _ = EntrepreneurProfile.objects.get_or_create(user=user)
        profile.linkedin_photo_url = photo_url
        profile.save()
