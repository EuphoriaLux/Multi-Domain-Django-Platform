"""
Allauth adapter for Crush.lu
Customizes social authentication behavior for the Crush.lu domain
"""
from django.urls import reverse
from django.utils.translation import override
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.adapter import DefaultAccountAdapter

from azureproject.domains import DOMAINS
from .referrals import apply_referral_to_user
from .utils.i18n import get_user_preferred_language


# Build set of all Crush.lu domains (primary + aliases) for efficient lookup
CRUSH_DOMAINS = {'crush.lu'}
CRUSH_DOMAINS.update(DOMAINS['crush.lu'].get('aliases', []))


def get_i18n_redirect_url(request, url_name, user=None):
    """
    Get a language-prefixed redirect URL.

    Uses the user's preferred language if available, otherwise falls back to
    the current session language or 'en' as default.

    Uses override() context manager for thread-safety in production.

    Args:
        request: The HTTP request
        url_name: The URL name to reverse (e.g., 'crush_lu:dashboard')
        user: Optional user object to get preferred language from
    """
    lang = get_user_preferred_language(user=user, request=request, default='en')

    # Use override() context manager for thread-safety
    with override(lang):
        return reverse(url_name)


class CrushSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Domain-aware social account adapter
    Routes to appropriate signup flow based on domain
    """

    def _is_crush_domain(self, request):
        """Check if current request is from crush.lu domain (including test.crush.lu)"""
        host = request.get_host().split(':')[0].lower()
        return host in CRUSH_DOMAINS

    def get_signup_redirect_url(self, request):
        """
        Redirect to appropriate profile creation based on domain.
        Returns language-prefixed URL for Crush.lu domain.
        """
        if self._is_crush_domain(request):
            return get_i18n_redirect_url(request, 'crush_lu:create_profile')
        else:
            # Default behavior for other domains
            return '/profile/'

    def is_auto_signup_allowed(self, request, sociallogin):
        """
        Allow automatic signup for social logins
        If email exists, connect to existing account
        """
        return True

    def populate_user(self, request, sociallogin, data):
        """
        Populate user with data from social provider
        """
        user = super().populate_user(request, sociallogin, data)

        # Get first_name and last_name from social data
        if 'first_name' in data:
            user.first_name = data['first_name']
        if 'last_name' in data:
            user.last_name = data['last_name']

        return user

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form=form)
        apply_referral_to_user(request, user)
        return user


class CrushAccountAdapter(DefaultAccountAdapter):
    """
    Domain-aware account adapter
    Routes to appropriate pages based on domain
    """

    def _is_crush_domain(self, request):
        """Check if current request is from crush.lu domain (including test.crush.lu)"""
        host = request.get_host().split(':')[0].lower()
        return host in CRUSH_DOMAINS

    def get_login_redirect_url(self, request):
        """
        Redirect to appropriate dashboard after login based on domain.
        Returns language-prefixed URL for Crush.lu domain.
        """
        if self._is_crush_domain(request):
            # Crush.lu: Check if user has a profile
            from .models import CrushProfile
            try:
                profile = request.user.crushprofile
                return get_i18n_redirect_url(request, 'crush_lu:dashboard', request.user)
            except CrushProfile.DoesNotExist:
                # No profile yet - redirect to profile creation
                return get_i18n_redirect_url(request, 'crush_lu:create_profile')
        else:
            # Default behavior for other domains
            return '/profile/'

    def get_signup_redirect_url(self, request):
        """
        Redirect to appropriate page after signup based on domain.
        Returns language-prefixed URL for Crush.lu domain.
        """
        if self._is_crush_domain(request):
            return get_i18n_redirect_url(request, 'crush_lu:create_profile')
        else:
            return '/profile/'

    def is_open_for_signup(self, request):
        """
        Allow signups for all domains
        """
        return True

    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit=commit)
        apply_referral_to_user(request, user)
        return user
