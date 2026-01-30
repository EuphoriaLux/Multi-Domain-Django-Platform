# power_up/finops/permissions.py
"""
Custom permissions for FinOps Hub API
"""

from rest_framework.permissions import BasePermission


class AllowAnyPublic(BasePermission):
    """
    Allow public access to read-only dashboard views.

    Used for dashboard pages that should be publicly accessible
    without authentication.
    """

    def has_permission(self, request, view):
        return True


class IsAdminOrStaff(BasePermission):
    """
    Require admin or staff for management views.

    Used for sensitive operations like triggering imports,
    updating configuration, etc.
    """

    def has_permission(self, request, view):
        return request.user.is_authenticated and (
            request.user.is_staff or request.user.is_superuser
        )


class HasSessionOrIsAuthenticated(BasePermission):
    """
    Allow access if:
    1. User is authenticated (logged in), OR
    2. Request comes from a browser (has cookies/referer)

    This blocks direct API access (curl, postman, etc.) while allowing
    browser-based access from users visiting the dashboard.
    """

    def has_permission(self, request, view):
        # Allow authenticated users (logged in)
        if request.user and request.user.is_authenticated:
            return True

        # Check if this is a browser request
        referer = request.META.get('HTTP_REFERER', '')
        accept = request.META.get('HTTP_ACCEPT', '')

        # If request came from our own site (referer check)
        host = request.get_host()
        if referer and host in referer:
            return True

        # If this is a browser (accepts HTML)
        if 'text/html' in accept:
            return True

        # For AJAX requests from browsers, check for session
        if not request.session.session_key:
            request.session.create()

        # Allow requests that have a session
        if request.session.session_key:
            return True

        # Block all other access (direct API calls)
        return False
