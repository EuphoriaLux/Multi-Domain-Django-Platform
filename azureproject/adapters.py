"""
Multi-domain Allauth adapters.

Routes authentication to appropriate handlers based on request domain.
"""
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.adapter import DefaultAccountAdapter
from django.urls import reverse
import logging

logger = logging.getLogger(__name__)


def _is_oauth_callback(request):
    """Check if this is an OAuth callback request (coming from social provider)."""
    if not request:
        return False
    # OAuth callbacks typically come from external domains
    # Check referrer or specific callback patterns
    path = request.path
    return '/accounts/' in path and '/login/callback' in path


def _is_from_pwa(request):
    """
    Check if the request originated from the PWA.

    On Android, when OAuth opens in system browser, it loses the PWA context.
    We check various headers and session flags to detect this.
    """
    if not request:
        return False

    # Check if we set a PWA flag in session before OAuth redirect
    if request.session.get('oauth_from_pwa'):
        return True

    # Check Sec-Fetch-Dest header (standalone mode)
    sec_fetch_dest = request.META.get('HTTP_SEC_FETCH_DEST', '')
    if sec_fetch_dest == 'document':
        # Check display-mode in UA-CH or Sec-Fetch-Site
        sec_fetch_mode = request.META.get('HTTP_SEC_FETCH_MODE', '')
        if sec_fetch_mode in ('navigate', 'same-origin'):
            return True

    return False


def _get_domain(request):
    """Extract domain from request, removing port and www prefix."""
    if not request:
        return None
    host = request.get_host().split(':')[0].lower()
    if host.startswith('www.'):
        host = host[4:]
    return host


class MultiDomainSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Multi-domain social account adapter.
    Routes to appropriate signup flow based on domain.
    """

    def _is_delegation_domain(self, request):
        """Check if request is from delegation.crush.lu"""
        domain = _get_domain(request)
        return domain == 'delegation.crush.lu'

    def _is_crush_domain(self, request):
        """Check if request is from crush.lu or localhost (dev default)"""
        domain = _get_domain(request)
        # crush.lu is the main domain, localhost routes to crush.lu in development
        return domain == 'crush.lu' or domain == 'localhost'

    def pre_social_login(self, request, sociallogin):
        """
        Called after OAuth callback but before login/signup is complete.
        Track the OAuth provider and popup mode for redirect handling.
        """
        super().pre_social_login(request, sociallogin)

        # Store the OAuth provider in session for PWA redirect handling
        if self._is_crush_domain(request):
            request.session['oauth_provider'] = sociallogin.account.provider

            # Check if this is a popup OAuth flow (popup=1 query parameter)
            if request.GET.get('popup') == '1':
                request.session['oauth_popup_mode'] = True
                logger.debug(f"OAuth login via {sociallogin.account.provider} (popup mode)")
            else:
                logger.debug(f"OAuth login via {sociallogin.account.provider}")

    def is_auto_signup_allowed(self, request, sociallogin):
        """Allow automatic signup for social logins on all domains."""
        return True

    def is_open_for_signup(self, request, sociallogin):
        """
        Allow OAuth signup on all domains, including delegation.
        This overrides the AccountAdapter's is_open_for_signup for social logins.
        """
        return True

    def populate_user(self, request, sociallogin, data):
        """Populate user with data from social provider."""
        user = super().populate_user(request, sociallogin, data)

        # Handle Microsoft provider
        if sociallogin.account.provider == 'microsoft':
            extra_data = sociallogin.account.extra_data
            user.first_name = extra_data.get('givenName', '') or data.get('first_name', '')
            user.last_name = extra_data.get('surname', '') or data.get('last_name', '')
            email = extra_data.get('mail') or extra_data.get('userPrincipalName')
            if email:
                user.email = email
        else:
            # Other providers (Facebook, LinkedIn, etc.)
            if 'first_name' in data:
                user.first_name = data['first_name']
            if 'last_name' in data:
                user.last_name = data['last_name']

        return user

    def get_signup_redirect_url(self, request):
        """Redirect to appropriate page after social signup based on domain."""
        if self._is_delegation_domain(request):
            return '/dashboard/'
        elif self._is_crush_domain(request):
            # Check if this is popup OAuth flow (takes priority)
            if request.session.get('oauth_popup_mode'):
                request.session.pop('oauth_popup_mode', None)
                request.session.pop('oauth_provider', None)
                return '/oauth/popup-callback/'

            # Check if this is a mobile OAuth callback (legacy redirect flow)
            user_agent = request.META.get('HTTP_USER_AGENT', '').lower()
            is_mobile = 'mobile' in user_agent or 'android' in user_agent

            if is_mobile and request.session.get('oauth_provider'):
                # Clear the provider flag
                request.session.pop('oauth_provider', None)
                # Store the intended destination
                request.session['oauth_final_destination'] = '/create-profile/'
                # Redirect to PWA return handler
                return '/oauth-complete/'

            return '/create-profile/'
        else:
            return '/profile/'


class MultiDomainAccountAdapter(DefaultAccountAdapter):
    """
    Multi-domain account adapter.
    Routes to appropriate pages based on domain.
    """

    def _is_delegation_domain(self, request):
        """Check if request is from delegation.crush.lu"""
        domain = _get_domain(request)
        return domain == 'delegation.crush.lu'

    def _is_crush_domain(self, request):
        """Check if request is from crush.lu or localhost (dev default)"""
        domain = _get_domain(request)
        # crush.lu is the main domain, localhost routes to crush.lu in development
        return domain == 'crush.lu' or domain == 'localhost'

    def _get_crush_redirect_url(self, request):
        """Get the appropriate redirect URL for Crush.lu after login."""
        if hasattr(request.user, 'crushprofile'):
            return '/dashboard/'
        else:
            return '/create-profile/'

    def get_login_redirect_url(self, request):
        """Redirect to appropriate dashboard after login based on domain."""
        if self._is_delegation_domain(request):
            # Delegation domain: route based on profile status
            from crush_delegation.models import DelegationProfile
            try:
                profile = request.user.delegation_profile
                if profile.is_approved:
                    return '/dashboard/'
                elif profile.status == 'pending':
                    return '/pending-approval/'
                elif profile.status == 'no_company':
                    return '/no-company/'
                elif profile.status == 'rejected':
                    return '/access-denied/'
            except DelegationProfile.DoesNotExist:
                pass
            return '/dashboard/'

        elif self._is_crush_domain(request):
            # Check if this is popup OAuth flow (takes priority)
            if request.session.get('oauth_popup_mode'):
                request.session.pop('oauth_popup_mode', None)
                request.session.pop('oauth_provider', None)
                return '/oauth/popup-callback/'

            # Check if this is an OAuth callback that landed in the browser
            # instead of the PWA (common on Android) - legacy redirect flow
            user_agent = request.META.get('HTTP_USER_AGENT', '').lower()
            is_android = 'android' in user_agent
            is_mobile = 'mobile' in user_agent or 'android' in user_agent

            # If mobile OAuth callback, redirect to PWA return page
            # This page will attempt to return the user to the PWA
            if is_mobile and request.session.get('oauth_provider'):
                # Clear the provider flag
                provider = request.session.pop('oauth_provider', None)
                # Store the intended destination
                final_destination = self._get_crush_redirect_url(request)
                request.session['oauth_final_destination'] = final_destination
                # Redirect to PWA return handler
                return '/oauth-complete/'

            # Normal flow - direct to dashboard or profile creation
            return self._get_crush_redirect_url(request)
        else:
            # Default behavior for other domains
            return '/profile/'

    def get_signup_redirect_url(self, request):
        """Redirect to appropriate page after signup based on domain."""
        if self._is_delegation_domain(request):
            return '/dashboard/'
        elif self._is_crush_domain(request):
            return '/create-profile/'
        else:
            return '/profile/'

    def get_logout_redirect_url(self, request):
        """Redirect to home page after logout."""
        if self._is_delegation_domain(request):
            return '/'
        elif self._is_crush_domain(request):
            return '/'
        return '/'

    def is_open_for_signup(self, request):
        """
        Control signup availability per domain.
        Delegation domain only allows Microsoft OAuth, not form signup.
        """
        if self._is_delegation_domain(request):
            # Disable traditional signup form on delegation domain
            return False
        return True

    def get_login_url(self, request):
        """
        Return the domain-specific login URL.
        This controls where Allauth redirects for login pages.
        """
        if self._is_crush_domain(request):
            return '/login/'
        elif self._is_delegation_domain(request):
            return '/login/'
        # Default Allauth login URL for other domains
        return '/accounts/login/'
