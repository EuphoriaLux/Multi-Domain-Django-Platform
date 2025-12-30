"""
Allauth adapter for Crush.lu
Customizes social authentication behavior for the Crush.lu domain
"""
from django.urls import reverse
from django.utils.translation import get_language, activate
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.adapter import DefaultAccountAdapter


def get_i18n_redirect_url(request, url_name, user=None):
    """
    Get a language-prefixed redirect URL.

    Uses the user's preferred language if available, otherwise falls back to
    the current session language or 'en' as default.

    Args:
        request: The HTTP request
        url_name: The URL name to reverse (e.g., 'crush_lu:dashboard')
        user: Optional user object to get preferred language from
    """
    # Determine the language to use
    lang = 'en'  # Default fallback

    # Try to get user's preferred language from profile
    if user and hasattr(user, 'crushprofile') and user.crushprofile:
        profile_lang = getattr(user.crushprofile, 'preferred_language', None)
        if profile_lang:
            lang = profile_lang

    # If no user preference, try session/request language
    if lang == 'en':
        request_lang = getattr(request, 'LANGUAGE_CODE', None) or get_language()
        if request_lang and request_lang in ['en', 'de', 'fr']:
            lang = request_lang

    # Activate the language and generate the URL
    activate(lang)
    return reverse(url_name)


class CrushSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Domain-aware social account adapter
    Routes to appropriate signup flow based on domain
    """

    def _is_crush_domain(self, request):
        """Check if current request is from crush.lu domain"""
        host = request.get_host().split(':')[0].lower()
        return host in ['crush.lu', 'www.crush.lu']

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


class CrushAccountAdapter(DefaultAccountAdapter):
    """
    Domain-aware account adapter
    Routes to appropriate pages based on domain
    """

    def _is_crush_domain(self, request):
        """Check if current request is from crush.lu domain"""
        host = request.get_host().split(':')[0].lower()
        return host in ['crush.lu', 'www.crush.lu']

    def get_login_redirect_url(self, request):
        """
        Redirect to appropriate dashboard after login based on domain.
        Returns language-prefixed URL for Crush.lu domain.
        """
        if self._is_crush_domain(request):
            # Crush.lu: Check if user has a profile
            if hasattr(request.user, 'crushprofile'):
                return get_i18n_redirect_url(request, 'crush_lu:dashboard', request.user)
            else:
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
