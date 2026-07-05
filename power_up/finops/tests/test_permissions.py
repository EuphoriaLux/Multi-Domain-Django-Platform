"""
Tests for FinOps Hub access control.

FinOps exposes the operator's internal Azure billing data (subscription/service
names, resource IDs, per-service spend), so every dashboard view, cost API, and
export must require an authenticated staff/superuser. These tests assert that
lockdown: dashboards use ``@staff_member_required`` and the DRF endpoints use
``IsAdminOrStaff``. They replace an earlier suite that (incorrectly) asserted
the dashboards were publicly accessible.
"""

import pytest
from django.contrib.auth import get_user_model

User = get_user_model()

# Dashboard HTML views — must never be reachable without staff.
DASHBOARD_URLS = [
    "/finops/",
    "/finops/subscriptions/",
    "/finops/services/",
    "/finops/resources/",
    "/finops/anomalies/",
    "/finops/forecast/",
    "/finops/compare/",
    "/finops/resource-groups/",
    "/finops/faq/",
]

# Subset known to render 200 with an empty DB (used for the staff-allowed case
# to avoid coupling to template/data availability of the heavier views).
DASHBOARD_URLS_STAFF_OK = [
    "/finops/",
    "/finops/subscriptions/",
    "/finops/services/",
    "/finops/resources/",
    "/finops/faq/",
]

# DRF cost APIs + CSV export + ViewSet lists — must reject anonymous/non-staff.
API_URLS = [
    "/finops/api/costs/summary/",
    "/finops/api/costs/by-subscription/",
    "/finops/api/costs/by-service/",
    "/finops/api/costs/by-resource-group/",
    "/finops/api/costs/trend/",
    "/finops/api/costs/export-csv/",
    "/finops/api/anomalies/",
    "/finops/api/exports/",  # CostExportViewSet (list)
    "/finops/api/records/",  # CostRecordViewSet (list)
    "/finops/api/aggregations/",  # CostAggregationViewSet (list)
    "/finops/api/exports/status/",  # shadowed to exports/<pk>/ — must still require auth
]

# Endpoints that return 200 for a staff user against an empty DB.
# '/finops/api/exports/status/' is intentionally excluded: the router's
# exports/<pk>/ detail route shadows the export_status view, so a staff request
# 404s on pk='status'. That is a pre-existing routing bug, unrelated to this
# access-control change.
API_URLS_STAFF_OK = [
    "/finops/api/costs/summary/",
    "/finops/api/costs/by-subscription/",
    "/finops/api/costs/by-service/",
    "/finops/api/costs/by-resource-group/",
    "/finops/api/costs/trend/",
    "/finops/api/costs/export-csv/",
    "/finops/api/anomalies/",
    "/finops/api/exports/",
    "/finops/api/records/",
    "/finops/api/aggregations/",
]


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        username="staff",
        email="staff@example.com",
        password="pw",
        is_staff=True,
    )


@pytest.fixture
def regular_user(db):
    return User.objects.create_user(
        username="regular",
        email="regular@example.com",
        password="pw",
        is_staff=False,
    )


@pytest.mark.django_db
class TestDashboardRequiresStaff:
    """FinOps dashboards must not be publicly accessible."""

    @pytest.mark.parametrize("url", DASHBOARD_URLS)
    def test_anonymous_is_denied(self, client, url):
        # @staff_member_required redirects to the admin login (302).
        assert client.get(url).status_code in (302, 403), url

    @pytest.mark.parametrize("url", DASHBOARD_URLS)
    def test_regular_user_is_denied(self, client, regular_user, url):
        client.force_login(regular_user)
        assert client.get(url).status_code in (302, 403), url

    @pytest.mark.parametrize("url", DASHBOARD_URLS_STAFF_OK)
    def test_staff_is_allowed(self, client, staff_user, url):
        client.force_login(staff_user)
        assert client.get(url).status_code == 200, url


@pytest.mark.django_db
class TestAdminMutationViewsRequireStaff:
    """Import/config mutation views require staff (unchanged behaviour)."""

    def test_import_denied_for_anonymous(self, client):
        assert client.get("/finops/import/").status_code in (302, 403)

    def test_import_denied_for_regular_user(self, client, regular_user):
        client.force_login(regular_user)
        assert client.get("/finops/import/").status_code in (302, 403)

    def test_import_allowed_for_staff(self, client, staff_user):
        client.force_login(staff_user)
        assert client.get("/finops/import/").status_code == 200

    def test_update_subscription_denied_for_anonymous(self, client):
        assert client.get("/finops/import/123/update-subscription/").status_code in (
            302,
            403,
            404,
        )


@pytest.mark.django_db
class TestCostApiRequiresStaff:
    """Cost JSON/CSV APIs must reject anonymous and non-staff users."""

    @pytest.mark.parametrize("url", API_URLS)
    def test_anonymous_is_forbidden(self, client, url):
        assert client.get(url).status_code in (401, 403), url

    @pytest.mark.parametrize("url", API_URLS)
    def test_regular_user_is_forbidden(self, client, regular_user, url):
        client.force_login(regular_user)
        assert client.get(url).status_code in (401, 403), url

    @pytest.mark.parametrize("url", API_URLS_STAFF_OK)
    def test_staff_is_allowed(self, client, staff_user, url):
        client.force_login(staff_user)
        assert client.get(url).status_code == 200, url


@pytest.mark.django_db
class TestPermissionClasses:
    """Unit tests for IsAdminOrStaff (the only remaining permission class)."""

    def _request(self):
        from django.test import RequestFactory

        return RequestFactory().get("/finops/")

    def test_denies_anonymous(self):
        from power_up.finops.permissions import IsAdminOrStaff
        from django.contrib.auth.models import AnonymousUser

        request = self._request()
        request.user = AnonymousUser()
        assert IsAdminOrStaff().has_permission(request, None) is False

    def test_denies_regular_user(self):
        from power_up.finops.permissions import IsAdminOrStaff

        request = self._request()
        request.user = User(username="regular", is_staff=False)
        assert IsAdminOrStaff().has_permission(request, None) is False

    def test_allows_staff(self):
        from power_up.finops.permissions import IsAdminOrStaff

        request = self._request()
        request.user = User(username="staff", is_staff=True)
        assert IsAdminOrStaff().has_permission(request, None) is True

    def test_allows_superuser(self):
        from power_up.finops.permissions import IsAdminOrStaff

        request = self._request()
        request.user = User(username="admin", is_superuser=True)
        assert IsAdminOrStaff().has_permission(request, None) is True

    def test_fail_open_classes_are_removed(self):
        """The fail-open permission classes must not be reintroduced."""
        import power_up.finops.permissions as perms

        assert not hasattr(perms, "HasSessionOrIsAuthenticated")
        assert not hasattr(perms, "AllowAnyPublic")
