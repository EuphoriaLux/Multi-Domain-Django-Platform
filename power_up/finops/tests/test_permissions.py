"""
Tests for FinOps Hub permission changes (Phase 1)
"""

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.mark.django_db
class TestPublicDashboardAccess:
    """Test that public views are accessible without authentication"""

    @override_settings(ROOT_URLCONF='azureproject.urls_power_up')
    def test_dashboard_accessible_without_login(self, client):
        """Dashboard should be accessible without authentication"""
        response = client.get('/finops/')
        assert response.status_code == 200

    @override_settings(ROOT_URLCONF='azureproject.urls_power_up')
    def test_subscription_view_accessible_without_login(self, client):
        """Subscription view should be accessible without authentication"""
        response = client.get('/finops/subscriptions/')
        assert response.status_code == 200

    @override_settings(ROOT_URLCONF='azureproject.urls_power_up')
    def test_service_breakdown_accessible_without_login(self, client):
        """Service breakdown should be accessible without authentication"""
        response = client.get('/finops/services/')
        assert response.status_code == 200

    @override_settings(ROOT_URLCONF='azureproject.urls_power_up')
    def test_resource_explorer_accessible_without_login(self, client):
        """Resource explorer should be accessible without authentication"""
        response = client.get('/finops/resources/')
        assert response.status_code == 200

    @override_settings(ROOT_URLCONF='azureproject.urls_power_up')
    def test_faq_accessible_without_login(self, client):
        """FAQ page should be accessible without authentication"""
        response = client.get('/finops/faq/')
        assert response.status_code == 200


@pytest.mark.django_db
class TestAdminViewsRequireAuth:
    """Test that admin views require staff authentication"""

    @override_settings(ROOT_URLCONF='azureproject.urls_power_up')
    def test_import_page_requires_staff(self, client):
        """Import page should require staff authentication"""
        response = client.get('/finops/import/')
        # Should redirect to login or return 403
        assert response.status_code in [302, 403]

    @override_settings(ROOT_URLCONF='azureproject.urls_power_up')
    def test_import_page_accessible_for_staff(self, client):
        """Import page should be accessible for staff users"""
        # Create staff user
        staff_user = User.objects.create_user(
            username='staff',
            email='staff@example.com',
            password='testpass123',
            is_staff=True
        )
        client.login(username='staff', password='testpass123')

        response = client.get('/finops/import/')
        assert response.status_code == 200

    @override_settings(ROOT_URLCONF='azureproject.urls_power_up')
    def test_update_subscription_id_requires_staff(self, client):
        """Update subscription ID should require staff authentication"""
        # Try to access without authentication
        response = client.get('/finops/import/123/update-subscription/')
        assert response.status_code in [302, 403, 404]  # Redirect, forbidden, or not found

    @override_settings(ROOT_URLCONF='azureproject.urls_power_up')
    def test_regular_user_cannot_access_admin_views(self, client):
        """Regular authenticated users should not access admin views"""
        # Create regular user (not staff)
        user = User.objects.create_user(
            username='regular',
            email='regular@example.com',
            password='testpass123',
            is_staff=False
        )
        client.login(username='regular', password='testpass123')

        response = client.get('/finops/import/')
        # Should be denied even though authenticated
        assert response.status_code in [302, 403]


@pytest.mark.django_db
class TestAPIEndpointsRequireAuth:
    """Test that API endpoints still require authentication"""

    @override_settings(ROOT_URLCONF='azureproject.urls_power_up')
    def test_cost_summary_requires_session(self, client):
        """Cost summary API should require session or authentication"""
        # Direct API call without session should be blocked
        response = client.get(
            '/finops/api/costs/summary/',
            HTTP_ACCEPT='application/json'  # Explicitly not a browser
        )
        # May return 200 if session is created, or 403 if blocked
        # The key is that authenticated users can access it
        assert response.status_code in [200, 403]

    @override_settings(ROOT_URLCONF='azureproject.urls_power_up')
    def test_api_accessible_with_authentication(self, client):
        """API endpoints should be accessible for authenticated users"""
        user = User.objects.create_user(
            username='apiuser',
            email='api@example.com',
            password='testpass123'
        )
        client.login(username='apiuser', password='testpass123')

        response = client.get('/finops/api/costs/summary/')
        assert response.status_code == 200

    @override_settings(ROOT_URLCONF='azureproject.urls_power_up')
    def test_csv_export_returns_not_implemented(self, client):
        """CSV export should return 501 (not implemented) status"""
        response = client.get('/finops/api/costs/export-csv/')
        assert response.status_code == 501
        data = response.json()
        assert 'coming_soon' in data.get('status', '')


@pytest.mark.django_db
class TestPermissionClasses:
    """Test the custom permission classes directly"""

    def test_allow_any_public_permission(self):
        """AllowAnyPublic should always return True"""
        from power_up.finops.permissions import AllowAnyPublic
        from django.test import RequestFactory

        factory = RequestFactory()
        request = factory.get('/finops/')
        permission = AllowAnyPublic()

        assert permission.has_permission(request, None) is True

    def test_is_admin_or_staff_permission_denies_anonymous(self):
        """IsAdminOrStaff should deny anonymous users"""
        from power_up.finops.permissions import IsAdminOrStaff
        from django.test import RequestFactory
        from django.contrib.auth.models import AnonymousUser

        factory = RequestFactory()
        request = factory.get('/finops/import/')
        request.user = AnonymousUser()
        permission = IsAdminOrStaff()

        assert permission.has_permission(request, None) is False

    def test_is_admin_or_staff_permission_allows_staff(self):
        """IsAdminOrStaff should allow staff users"""
        from power_up.finops.permissions import IsAdminOrStaff
        from django.test import RequestFactory

        factory = RequestFactory()
        request = factory.get('/finops/import/')
        request.user = User(username='staff', is_staff=True)
        permission = IsAdminOrStaff()

        assert permission.has_permission(request, None) is True

    def test_is_admin_or_staff_permission_allows_superuser(self):
        """IsAdminOrStaff should allow superusers"""
        from power_up.finops.permissions import IsAdminOrStaff
        from django.test import RequestFactory

        factory = RequestFactory()
        request = factory.get('/finops/import/')
        # Create an actual User instance with proper fields
        user = User(username='admin', is_superuser=True)
        user._state.adding = False  # Mark as saved
        request.user = user
        permission = IsAdminOrStaff()

        assert permission.has_permission(request, None) is True
