"""
Multi-domain Allauth adapters.

Routes authentication to appropriate handlers based on request domain.
"""
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from django.conf import settings
from django.urls import reverse
from django.shortcuts import render
from django.http import HttpResponseForbidden
import os
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


def _is_crush_domain(request):
    """Check if request is from crush.lu or localhost (dev default)."""
    domain = _get_domain(request)
    # crush.lu is the main domain, localhost/127.0.0.1 routes to crush.lu in development
    return domain in ('crush.lu', 'localhost', '127.0.0.1')


def _is_delegation_domain(request):
    """Check if request is from delegations.lu."""
    domain = _get_domain(request)
    return domain == 'delegations.lu'


class MultiDomainSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Multi-domain social account adapter.
    Routes to appropriate signup flow based on domain.
    """

    def pre_social_login(self, request, sociallogin):
        """
        Called after OAuth callback but before login/signup is complete.
        Track the OAuth provider for redirect handling.
        Note: popup mode is stored in session by oauth_statekit.db_stash_state()
        """
        super().pre_social_login(request, sociallogin)

        # Debug logging for Microsoft OAuth troubleshooting
        provider = sociallogin.account.provider
        logger.info(f"[OAUTH-ADAPTER] pre_social_login: provider={provider}, "
                   f"is_existing={sociallogin.is_existing}, "
                   f"user_id={getattr(sociallogin.user, 'id', None)}, "
                   f"uid={sociallogin.account.uid[:20] if sociallogin.account.uid else 'None'}...")

        if provider == 'microsoft':
            extra = sociallogin.account.extra_data
            logger.info(f"[OAUTH-ADAPTER] Microsoft extra_data: "
                       f"displayName={extra.get('displayName')}, "
                       f"mail={extra.get('mail')}, "
                       f"userPrincipalName={extra.get('userPrincipalName')}")

            # Tenant validation for ADMIN PANEL access only
            # Consumers on crush.lu can use any Microsoft account
            # But admin panel requires users from the enterprise tenant
            next_url = request.session.get('next') or request.GET.get('next', '')
            is_admin_login = '/admin/' in next_url or '/crush-admin/' in next_url

            if is_admin_login:
                allowed_tenant = os.environ.get('GRAPH_TENANT_ID')
                if allowed_tenant:
                    # Get tenant ID from token (stored in extra_data by allauth)
                    user_tenant = extra.get('tid')  # Azure AD tenant ID claim
                    logger.info(f"[OAUTH-ADAPTER] Admin login attempt: "
                               f"user_tenant={user_tenant}, allowed_tenant={allowed_tenant}")

                    if user_tenant and user_tenant != allowed_tenant:
                        logger.warning(
                            f"[OAUTH-ADAPTER] Admin Microsoft login rejected: "
                            f"user tenant {user_tenant} != allowed tenant {allowed_tenant}"
                        )
                        raise ImmediateHttpResponse(
                            HttpResponseForbidden(
                                "Access denied. Your Microsoft account is not from the authorized organization. "
                                "Only accounts from the organization tenant can access the admin panel."
                            )
                        )

        # Store the OAuth provider in session for PWA redirect handling
        if _is_crush_domain(request):
            request.session['oauth_provider'] = sociallogin.account.provider
            is_popup = request.session.get('oauth_popup_mode', False)
            logger.debug(f"OAuth login via {sociallogin.account.provider} (popup: {is_popup})")

    def on_authentication_error(self, request, provider_id, error=None, exception=None, extra_context=None):
        """
        Handle authentication errors with detailed logging.
        This helps debug OAuth issues like Microsoft login failures.
        """
        logger.error(f"[OAUTH-ADAPTER] Authentication error: provider={provider_id}, "
                    f"error={error}, exception={exception}")

        if exception:
            import traceback
            logger.error(f"[OAUTH-ADAPTER] Exception traceback:\n{traceback.format_exc()}")

        # Let the default handler show the error page
        return super().on_authentication_error(request, provider_id, error, exception, extra_context)

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

        # IMPORTANT: first_name and last_name must never be None - database has NOT NULL constraint
        # Handle Microsoft provider
        if sociallogin.account.provider == 'microsoft':
            extra_data = sociallogin.account.extra_data
            user.first_name = extra_data.get('givenName', '') or data.get('first_name', '') or ''
            user.last_name = extra_data.get('surname', '') or data.get('last_name', '') or ''
            email = extra_data.get('mail') or extra_data.get('userPrincipalName')
            if email:
                user.email = email
        # Handle Google provider
        elif sociallogin.account.provider == 'google':
            extra_data = sociallogin.account.extra_data
            user.first_name = extra_data.get('given_name', '') or data.get('first_name', '') or ''
            user.last_name = extra_data.get('family_name', '') or data.get('last_name', '') or ''
            if extra_data.get('email'):
                user.email = extra_data['email']
        # Handle Facebook provider
        elif sociallogin.account.provider == 'facebook':
            extra_data = sociallogin.account.extra_data
            user.first_name = extra_data.get('first_name', '') or data.get('first_name', '') or ''
            user.last_name = extra_data.get('last_name', '') or data.get('last_name', '') or ''
            if extra_data.get('email'):
                user.email = extra_data['email']
        else:
            # Other providers (LinkedIn, etc.)
            user.first_name = data.get('first_name', '') or ''
            user.last_name = data.get('last_name', '') or ''

        return user

    def get_signup_redirect_url(self, request):
        """Redirect to appropriate page after social signup based on domain."""
        if _is_delegation_domain(request):
            return '/dashboard/'
        elif _is_crush_domain(request):
            # CRITICAL FIX: Always redirect to oauth_landing for Crush.lu
            # This returns 200 OK with JavaScript-delayed redirect to fix
            # Android PWA cookie timing issue (302 fires before cookie commit)
            return '/oauth/landing/'
        else:
            return '/profile/'

    def get_connect_redirect_url(self, request, socialaccount):
        """
        Redirect to appropriate page after connecting/disconnecting a social account.

        This handles both successful connections AND error cases (e.g., when trying
        to connect an account that's already linked to a different user).

        The error message is displayed via Django messages framework.
        """
        if _is_crush_domain(request):
            # Redirect back to Crush.lu account settings page
            return '/account/settings/'
        elif _is_delegation_domain(request):
            return '/account/settings/'
        else:
            # Default to Allauth's connections page for other domains
            from django.urls import reverse
            return reverse('socialaccount_connections')


class MultiDomainAccountAdapter(DefaultAccountAdapter):
    """
    Multi-domain account adapter.
    Routes to appropriate pages based on domain.
    """

    def login(self, request, user):
        """
        Override login to add session fixation protection.

        Rotates the session key after successful login to prevent
        session fixation attacks where an attacker sets a known
        session ID before authentication.
        """
        # Rotate session key BEFORE login to prevent session fixation
        # This creates a new session ID while preserving session data
        if hasattr(request, 'session') and request.session.session_key:
            request.session.cycle_key()
            logger.debug(f"[SECURITY] Session key rotated after login for user {user.id}")

        # Call parent login
        return super().login(request, user)

    def get_client_ip(self, request) -> str:
        """
        Get the client IP address from the request.

        Azure App Service sets X-Forwarded-For with IP:PORT format (e.g., '94.252.75.68:22272'),
        but allauth's default implementation expects just the IP address.
        This override strips the port number to prevent "Invalid IP address" errors.
        """
        import ipaddress
        from django.core.exceptions import PermissionDenied

        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            # Azure may include port: "94.252.75.68:22272" - extract just IP
            ip_value = x_forwarded_for.split(",")[0].strip()
            # Strip port if present (handles both IPv4:port and [IPv6]:port)
            if ':' in ip_value:
                # Check if it's IPv6 (contains multiple colons) or IPv4:port
                if ip_value.count(':') == 1:
                    # IPv4:port format - strip the port
                    ip_value = ip_value.split(':')[0]
                elif ip_value.startswith('['):
                    # [IPv6]:port format
                    ip_value = ip_value.split(']')[0][1:]
                # else: plain IPv6 without port, use as-is
        else:
            ip_value = request.META.get("REMOTE_ADDR", "")

        # Validate it's a proper IP address
        try:
            ip_addr = ipaddress.ip_address(ip_value)
        except ValueError:
            logger.warning(f"Invalid IP address in request: {ip_value!r}")
            raise PermissionDenied(f"Invalid IP address: {ip_value!r}")

        return str(ip_addr)

    def _get_crush_redirect_url(self, request):
        """Get the appropriate redirect URL for Crush.lu after login."""
        if hasattr(request.user, 'crushprofile'):
            return '/dashboard/'
        else:
            return '/create-profile/'

    def get_login_redirect_url(self, request):
        """Redirect to appropriate dashboard after login based on domain."""
        if _is_delegation_domain(request):
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

        elif _is_crush_domain(request):
            # Check for special user experience (set by user_logged_in signal)
            # This must be checked FIRST as it takes priority over other redirects
            if request.session.get('special_experience_active'):
                return '/special-welcome/'

            # Check if this is an OAuth login (has oauth_provider in session)
            # OAuth logins need the delayed redirect landing page
            if request.session.get('oauth_provider') or request.session.get('oauth_popup_mode'):
                # CRITICAL FIX: Redirect to oauth_landing for ALL OAuth flows
                # This returns 200 OK with JavaScript-delayed redirect to fix
                # Android PWA cookie timing issue (302 fires before cookie commit)
                return '/oauth/landing/'

            # Non-OAuth login (email/password) - direct to dashboard
            return self._get_crush_redirect_url(request)
        else:
            # Default behavior for other domains
            return '/profile/'

    def get_signup_redirect_url(self, request):
        """Redirect to appropriate page after signup based on domain."""
        if _is_delegation_domain(request):
            return '/dashboard/'
        elif _is_crush_domain(request):
            return '/create-profile/'
        else:
            return '/profile/'

    def get_logout_redirect_url(self, request):
        """Redirect to home page after logout."""
        if _is_delegation_domain(request):
            return '/'
        elif _is_crush_domain(request):
            return '/'
        return '/'

    def is_open_for_signup(self, request):
        """
        Control signup availability per domain.
        Delegation domain only allows Microsoft OAuth, not form signup.
        """
        if _is_delegation_domain(request):
            # Disable traditional signup form on delegation domain
            return False
        return True

    def get_login_url(self, request):
        """
        Return the domain-specific login URL.
        This controls where Allauth redirects for login pages.
        """
        if _is_crush_domain(request):
            return '/login/'
        elif _is_delegation_domain(request):
            return '/login/'
        # Default Allauth login URL for other domains
        return '/accounts/login/'
