"""
Regression suite for the CrushCoach-vs-Power-Up-staff access-control fix.

Background: CrushCoach(is_active=True) accounts on crush.lu are granted
``is_staff=True`` by ``crush_lu.signals.manage_coach_staff_status`` so they can
reach the *crush.lu* admin panel. Power-Up's internal surfaces (CRM,
onboarding, FinOps dashboards/APIs, the power-up.lu admin site) used to gate
on that same ``is_staff`` flag, which meant every active coach could also
reach power-up.lu's internal tooling — data that belongs to an entirely
different business unit. The fix is ``power_up.permissions.is_power_up_staff``:
it requires membership in the dedicated ``power_up_staff`` Group (or
superuser), so plain ``is_staff`` is no longer sufficient anywhere on
power-up.lu.

This module is the single place that asserts the fix holistically across every
gated surface, using a CrushCoach fixture rather than a bare ``is_staff=True``
user, so a regression that reintroduces an ``is_staff``-only check anywhere
is caught here even if the narrower per-surface test files miss it.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings

from crush_lu.models.profiles import CrushCoach
from power_up.admin import power_up_admin_site

User = get_user_model()

POWER_UP_STAFF_GROUP = "power_up_staff"
POWER_UP_HOST = "power-up.lu"

# ---------------------------------------------------------------------------
# URL surfaces under test
# ---------------------------------------------------------------------------

# CRM dashboards (power_up/crm/urls.py, mounted at /crm/) — GET-safe pages.
CRM_URLS = [
    "/crm/",
    "/crm/tickets/",
    "/crm/tickets/new/",
]

# Onboarding dashboards (power_up/onboarding/urls.py, mounted at /onboarding/).
ONBOARDING_URLS = [
    "/onboarding/",
    "/onboarding/slots-partial/",
]

# FinOps HTML dashboards (power_up/finops/urls.py, mounted at /finops/).
# Kept to the subset that renders 200 against an empty DB — mirrors the
# existing FINOPS_URLS_STAFF_OK convention in
# power_up/finops/tests/test_permissions.py to avoid coupling this suite to
# template/data availability of the heavier views.
FINOPS_DASHBOARD_URLS = [
    "/finops/",
    "/finops/subscriptions/",
    "/finops/services/",
    "/finops/resources/",
    "/finops/faq/",
]

# The public-facing-but-gated retail price tracker (still Power-Up staff only).
FINOPS_PRICES_URL = "/finops/prices/"

# FinOps JSON/CSV APIs — must reject anonymous/non-staff, admit staff.
FINOPS_API_URLS_STAFF_OK = [
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

# Webhook-style sync endpoint: gated by a sync token, not by a logged-in user
# at all — separately asserted below (anonymous callers without the header
# must be rejected, not merely "non-staff").
FINOPS_SYNC_STATUS_URL = "/finops/api/sync/status/"

# The custom Power-Up admin site (power_up/admin.py), mounted at /power-admin/.
POWER_ADMIN_URL = "/power-admin/"


def _make_crushcoach_user(username="active-coach", is_active=True):
    """An active CrushCoach: has ``is_staff=True`` via the coach-management
    signal, but is NOT a member of the power_up_staff Group. This is exactly
    the account shape the vulnerability allowed through.
    """
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )
    CrushCoach.objects.create(user=user, is_active=is_active)
    user.refresh_from_db()
    return user


def _make_power_up_staff_user(username="power-up-member", group=None):
    """A legitimate Power-Up staff member: is_staff + power_up_staff group."""
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
        is_staff=True,
    )
    user.groups.add(group)
    return user


@override_settings(ALLOWED_HOSTS=["*"])
class CrushCoachCannotReachPowerUpInternalsTests(TestCase):
    """CrushCoach(is_active=True) users must be refused everywhere on
    power-up.lu's internal surfaces, despite carrying ``is_staff=True``."""

    @classmethod
    def setUpTestData(cls):
        cls.group, _ = Group.objects.get_or_create(name=POWER_UP_STAFF_GROUP)

    def setUp(self):
        self.client = Client(HTTP_HOST=POWER_UP_HOST)
        self.coach = _make_crushcoach_user()
        # Sanity: the signal actually granted is_staff, or this whole test
        # class would be exercising the wrong precondition.
        self.assertTrue(self.coach.is_staff)
        self.assertFalse(
            self.coach.groups.filter(name=POWER_UP_STAFF_GROUP).exists()
        )
        self.client.force_login(self.coach)

    def _assert_refused(self, url):
        response = self.client.get(url)
        self.assertIn(
            response.status_code,
            (302, 403),
            f"{url} did not refuse a CrushCoach-only staff account "
            f"(got {response.status_code})",
        )
        if response.status_code == 302:
            self.assertNotIn(
                response["Location"],
                (url,),
                f"{url} redirected to itself instead of a login page",
            )

    def test_crm_urls_refuse_crushcoach(self):
        for url in CRM_URLS:
            with self.subTest(url=url):
                self._assert_refused(url)

    def test_onboarding_urls_refuse_crushcoach(self):
        for url in ONBOARDING_URLS:
            with self.subTest(url=url):
                self._assert_refused(url)

    def test_finops_dashboard_urls_refuse_crushcoach(self):
        for url in FINOPS_DASHBOARD_URLS:
            with self.subTest(url=url):
                self._assert_refused(url)

    def test_finops_retail_prices_refuses_crushcoach(self):
        self._assert_refused(FINOPS_PRICES_URL)

    def test_finops_api_urls_refuse_crushcoach(self):
        for url in FINOPS_API_URLS_STAFF_OK:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertIn(
                    response.status_code,
                    (401, 403),
                    f"{url} did not refuse a CrushCoach-only staff account "
                    f"(got {response.status_code})",
                )

    def test_power_admin_refuses_crushcoach(self):
        self._assert_refused(POWER_ADMIN_URL)
        self.assertFalse(
            power_up_admin_site.has_permission(
                type("Req", (), {"user": self.coach})()
            )
        )

    def test_inactive_crushcoach_is_also_refused(self):
        """An inactive coach never had staff intended for it in the first
        place; confirm the same lockdown holds once the coach signal has
        revoked is_staff too."""
        inactive_coach = _make_crushcoach_user(
            username="inactive-coach", is_active=False
        )
        inactive_coach.refresh_from_db()
        self.assertFalse(inactive_coach.is_staff)

        client = Client(HTTP_HOST=POWER_UP_HOST)
        client.force_login(inactive_coach)
        response = client.get(FINOPS_DASHBOARD_URLS[0])
        self.assertIn(response.status_code, (302, 403))


@override_settings(ALLOWED_HOSTS=["*"])
class PowerUpStaffCanReachEverySurfaceTests(TestCase):
    """Legitimate power_up_staff group members must retain full access."""

    @classmethod
    def setUpTestData(cls):
        cls.group, _ = Group.objects.get_or_create(name=POWER_UP_STAFF_GROUP)

    def setUp(self):
        self.client = Client(HTTP_HOST=POWER_UP_HOST)
        self.staff = _make_power_up_staff_user(group=self.group)
        self.client.force_login(self.staff)

    def test_crm_urls_allow_power_up_staff(self):
        for url in CRM_URLS:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, url)

    def test_onboarding_urls_allow_power_up_staff(self):
        for url in ONBOARDING_URLS:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, url)

    def test_finops_dashboard_urls_allow_power_up_staff(self):
        for url in FINOPS_DASHBOARD_URLS:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, url)

    def test_finops_retail_prices_allows_power_up_staff(self):
        response = self.client.get(FINOPS_PRICES_URL)
        self.assertEqual(response.status_code, 200)

    def test_finops_api_urls_allow_power_up_staff(self):
        for url in FINOPS_API_URLS_STAFF_OK:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, url)

    def test_power_admin_allows_power_up_staff(self):
        response = self.client.get(POWER_ADMIN_URL)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            power_up_admin_site.has_permission(
                type("Req", (), {"user": self.staff})()
            )
        )

    def test_superuser_also_retains_access(self):
        """Superusers are not necessarily power_up_staff group members, but
        must always get through (mirrors the crush.lu / power-up.lu admin
        conventions elsewhere in the codebase)."""
        superuser = User.objects.create_superuser(
            username="global-admin",
            email="global-admin@example.com",
            password="test-password",
        )
        client = Client(HTTP_HOST=POWER_UP_HOST)
        client.force_login(superuser)

        self.assertEqual(client.get(FINOPS_DASHBOARD_URLS[0]).status_code, 200)
        self.assertEqual(client.get(CRM_URLS[0]).status_code, 200)
        self.assertEqual(client.get(ONBOARDING_URLS[0]).status_code, 200)
        self.assertEqual(client.get(POWER_ADMIN_URL).status_code, 200)


@override_settings(ALLOWED_HOSTS=["*"])
class FinOpsSyncStatusAndRetailPricesEdgeCaseTests(TestCase):
    """Endpoints called out explicitly in the acceptance criteria:
    ``/finops/api/sync/status/`` (token-gated webhook, no user session) and
    ``/finops/prices/`` (regular login-required, not staff-only historically —
    now Power-Up staff only, same as the rest of FinOps)."""

    def setUp(self):
        self.client = Client(HTTP_HOST=POWER_UP_HOST)

    def test_anonymous_is_rejected_from_sync_status(self):
        with self.settings(SECRET_SYNC_TOKEN="test-sync-token"):
            response = self.client.get(FINOPS_SYNC_STATUS_URL)
        self.assertEqual(response.status_code, 403)

    def test_sync_status_with_invalid_token_is_rejected(self):
        with self.settings(SECRET_SYNC_TOKEN="test-sync-token"):
            response = self.client.get(
                FINOPS_SYNC_STATUS_URL, HTTP_X_SYNC_TOKEN="wrong-token"
            )
        self.assertEqual(response.status_code, 403)

    def test_sync_status_with_valid_token_succeeds_without_any_user(self):
        """Confirms the endpoint is intentionally session-independent — a
        valid sync token alone is sufficient, no login required."""
        with self.settings(SECRET_SYNC_TOKEN="test-sync-token"):
            response = self.client.get(
                FINOPS_SYNC_STATUS_URL, HTTP_X_SYNC_TOKEN="test-sync-token"
            )
        self.assertEqual(response.status_code, 200)

    def test_non_staff_user_is_rejected_from_retail_prices(self):
        non_staff = User.objects.create_user(
            username="ordinary-member",
            email="ordinary-member@example.com",
            password="test-password",
        )
        self.client.force_login(non_staff)

        response = self.client.get(FINOPS_PRICES_URL)

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_anonymous_user_is_rejected_from_retail_prices(self):
        response = self.client.get(FINOPS_PRICES_URL)

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])
