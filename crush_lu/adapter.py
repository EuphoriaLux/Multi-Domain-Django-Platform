"""
Allauth adapter for Crush.lu
Customizes social authentication behavior for the Crush.lu domain
"""
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.adapter import DefaultAccountAdapter


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
        Redirect to appropriate profile creation based on domain
        """
        if self._is_crush_domain(request):
            return '/create-profile/'
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
        Redirect to appropriate dashboard after login based on domain
        """
        if self._is_crush_domain(request):
            # Crush.lu: Check if user has a profile
            if hasattr(request.user, 'crushprofile'):
                return '/dashboard/'
            else:
                # No profile yet - redirect to profile creation
                return '/create-profile/'
        else:
            # Default behavior for other domains
            return '/profile/'

    def get_signup_redirect_url(self, request):
        """
        Redirect to appropriate page after signup based on domain
        """
        if self._is_crush_domain(request):
            return '/create-profile/'
        else:
            return '/profile/'

    def is_open_for_signup(self, request):
        """
        Allow signups for all domains
        """
        return True
