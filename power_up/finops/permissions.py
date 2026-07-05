# power_up/finops/permissions.py
"""
Custom permissions for FinOps Hub API.

FinOps exposes the operator's internal Azure billing data (subscription and
service names, resource IDs, per-service spend), so every dashboard view, cost
API, and export is restricted to authenticated staff/superusers via
``IsAdminOrStaff``.

Note: the previous ``AllowAnyPublic`` and ``HasSessionOrIsAuthenticated``
classes were removed. ``HasSessionOrIsAuthenticated`` was fail-open — it granted
access to any request advertising ``Accept: text/html`` (i.e. any browser) and
auto-created a session for the rest, which left the billing data publicly
readable. Do not reintroduce them.
"""

from rest_framework.permissions import BasePermission


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
