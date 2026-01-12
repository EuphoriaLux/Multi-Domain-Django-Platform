"""
Allauth adapters for Crush Delegation.

Customizes Microsoft authentication behavior for delegations.lu domain.
"""
from django.shortcuts import redirect
from django.contrib import messages
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.adapter import DefaultAccountAdapter
import logging

logger = logging.getLogger(__name__)


class DelegationSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Domain-aware social account adapter for delegations.lu.
    Handles Microsoft OAuth with role-based access control.
    """

    def _is_delegation_domain(self, request):
        """Check if current request is from delegations.lu domain"""
        if not request:
            return False
        host = request.get_host().split(':')[0].lower()
        return host in ['delegations.lu', 'localhost', '127.0.0.1']

    def is_auto_signup_allowed(self, request, sociallogin):
        """
        Allow automatic signup for Microsoft logins on delegation domain.
        Access control is handled post-signup in signals.
        """
        if not self._is_delegation_domain(request):
            return super().is_auto_signup_allowed(request, sociallogin)

        # Always allow auto-signup for Microsoft on delegation domain
        if sociallogin.account.provider == 'microsoft':
            return True

        return super().is_auto_signup_allowed(request, sociallogin)

    def populate_user(self, request, sociallogin, data):
        """
        Populate user with data from Microsoft account.
        """
        user = super().populate_user(request, sociallogin, data)

        if sociallogin.account.provider == 'microsoft':
            extra_data = sociallogin.account.extra_data

            # Microsoft provides these in extra_data
            user.first_name = extra_data.get('givenName', '') or data.get('first_name', '')
            user.last_name = extra_data.get('surname', '') or data.get('last_name', '')

            # Email from Microsoft
            email = extra_data.get('mail') or extra_data.get('userPrincipalName')
            if email:
                user.email = email

        return user

    def get_signup_redirect_url(self, request):
        """Redirect to dashboard after signup on delegation domain"""
        if self._is_delegation_domain(request):
            return '/dashboard/'
        return super().get_signup_redirect_url(request)


class DelegationAccountAdapter(DefaultAccountAdapter):
    """
    Domain-aware account adapter for delegations.lu.
    Routes users based on profile status.
    """

    def _is_delegation_domain(self, request):
        """Check if current request is from delegations.lu domain"""
        if not request:
            return False
        host = request.get_host().split(':')[0].lower()
        return host in ['delegations.lu', 'localhost', '127.0.0.1']

    def get_login_redirect_url(self, request):
        """
        Redirect to appropriate page after login based on profile status.
        """
        if not self._is_delegation_domain(request):
            return super().get_login_redirect_url(request)

        # Check if user has a DelegationProfile
        from .models import DelegationProfile

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

        # No profile yet - will be created by signal, go to dashboard
        return '/dashboard/'

    def is_open_for_signup(self, request):
        """
        Disable traditional signup form on delegation domain.
        Only Microsoft OAuth signup is allowed.
        """
        if self._is_delegation_domain(request):
            # Traditional signup is disabled - only Microsoft OAuth
            return False
        return super().is_open_for_signup(request)

    def get_logout_redirect_url(self, request):
        """Redirect to home page after logout"""
        if self._is_delegation_domain(request):
            return '/'
        return super().get_logout_redirect_url(request)
