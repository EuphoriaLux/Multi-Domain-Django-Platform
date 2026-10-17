"""
Premium as a monthly subscription (services/premium_billing.py).

Covers the provider-neutral lifecycle Tom set on 2026-10-17:

- the first period: one month after the onboarding fee (month 1 free), two
  months for an event-verified member (month 1 paid, month 2 free);
- renewal extends from the previous period end, even when it arrives late;
- a missing renewal keeps Premium for the 3-day grace, then lapses;
- a member who cancels keeps Premium to the period end, with no grace, and can
  undo it until then;
- comped memberships with no period end never expire;
- the lapse sweep ends what is due and releases the coach, through the admin
  endpoint the PremiumLapseSweep timer calls.
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from crush_lu.models import CrushCoach, CrushProfile, PremiumMembership
from crush_lu.models.profiles import UserDataConsent
from crush_lu.services import premium_billing

User = get_user_model()

API_KEY = "test-premium-lapse-key"
CRUSH_URLS = {"ROOT_URLCONF": "azureproject.urls_crush", "ADMIN_API_KEY": API_KEY}


def _at(year, month, day):
    return datetime(year, month, day, 12, 0, tzinfo=dt_timezone.utc)


class AddMonthsTests(TestCase):
    def test_clamps_to_the_end_of_a_shorter_month(self):
        self.assertEqual(
            premium_billing.add_months(_at(2027, 1, 31), 1), _at(2027, 2, 28)
        )
        self.assertEqual(
            premium_billing.add_months(_at(2028, 1, 31), 1), _at(2028, 2, 29)
        )

    def test_rolls_over_the_year(self):
        self.assertEqual(
            premium_billing.add_months(_at(2026, 12, 15), 2), _at(2027, 2, 15)
        )


@override_settings(**CRUSH_URLS, PREMIUM_REDIRECTS_TO_BETA=False)
class PremiumRenewalLifecycleTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )

    def setUp(self):
        self.member = User.objects.create_user(
            username="renew@example.com", email="renew@example.com", password="x"
        )
        UserDataConsent.objects.update_or_create(
            user=self.member,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.profile = CrushProfile.objects.create(
            user=self.member,
            gender="F",
            location="Luxembourg",
            verification_status="verified",
            completion_status="step4",
        )
        coach_user = User.objects.create_user(
            username="coach@example.com", email="coach@example.com", password="x"
        )
        self.coach = CrushCoach.objects.create(
            user=coach_user,
            is_active=True,
            accepting_premium=True,
            max_premium_members=5,
        )

    def _confirmed(self, onboarding_fee=None, at=None):
        membership = PremiumMembership.objects.create(
            user=self.member,
            coach=self.coach,
            status="pending",
            onboarding_fee=onboarding_fee,
        )
        with patch("django.utils.timezone.now", return_value=at or timezone.now()):
            membership.confirm()
        membership.refresh_from_db()
        return membership

    def _entitled(self, now):
        with patch("django.utils.timezone.now", return_value=now):
            self.profile.refresh_from_db()
            return self.profile.has_active_premium

    # -- first period ---------------------------------------------------------

    def test_onboarding_path_first_period_is_one_month(self):
        m = self._confirmed(onboarding_fee=Decimal("10.00"), at=_at(2027, 1, 10))
        self.assertEqual(m.current_period_end, _at(2027, 2, 10))
        self.assertFalse(m.cancel_at_period_end)

    def test_event_verified_first_period_is_two_months(self):
        m = self._confirmed(onboarding_fee=Decimal("0"), at=_at(2027, 1, 10))
        self.assertEqual(m.current_period_end, _at(2027, 3, 10))

    def test_staff_confirm_without_checkout_derives_the_fee(self):
        """A cash/staff confirm has no recorded fee: the member's current
        verification decides it, and it is recorded."""
        m = self._confirmed(onboarding_fee=None, at=_at(2027, 1, 10))
        self.assertEqual(m.onboarding_fee, Decimal("10.00"))
        self.assertEqual(m.current_period_end, _at(2027, 2, 10))

        self.profile.verification_method = "coach_event"
        self.profile.save(update_fields=["verification_method"])
        m.cancel_active(reason="test")
        m2 = self._confirmed(onboarding_fee=None, at=_at(2027, 1, 10))
        self.assertEqual(m2.onboarding_fee, Decimal("0"))
        self.assertEqual(m2.current_period_end, _at(2027, 3, 10))

    # -- entitlement ----------------------------------------------------------

    def test_entitled_through_the_grace_then_not(self):
        self._confirmed(onboarding_fee=Decimal("10.00"), at=_at(2027, 1, 10))
        self.assertTrue(self._entitled(_at(2027, 2, 9)))
        self.assertTrue(self._entitled(_at(2027, 2, 12)))  # in the 3-day grace
        self.assertFalse(self._entitled(_at(2027, 2, 13) + timedelta(minutes=1)))

    def test_cancelled_member_has_no_grace(self):
        m = self._confirmed(onboarding_fee=Decimal("10.00"), at=_at(2027, 1, 10))
        self.assertTrue(premium_billing.schedule_cancellation(m))
        self.assertTrue(self._entitled(_at(2027, 2, 10) - timedelta(minutes=1)))
        self.assertFalse(self._entitled(_at(2027, 2, 10) + timedelta(minutes=1)))

    def test_comped_membership_without_period_never_expires(self):
        PremiumMembership.objects.create(
            user=self.member, coach=self.coach, status="active"
        )
        self.assertTrue(self._entitled(_at(2040, 1, 1)))
        with patch("django.utils.timezone.now", return_value=_at(2040, 1, 1)):
            self.assertEqual(premium_billing.sweep_lapsed(), 0)
        self.assertTrue(
            PremiumMembership.objects.filter(user=self.member, status="active").exists()
        )

    # -- renewal --------------------------------------------------------------

    def test_late_renewal_extends_from_the_previous_period_end(self):
        m = self._confirmed(onboarding_fee=Decimal("10.00"), at=_at(2027, 1, 10))
        with patch("django.utils.timezone.now", return_value=_at(2027, 2, 12)):
            self.assertEqual(premium_billing.record_renewal(m), _at(2027, 3, 10))
        m.refresh_from_db()
        self.assertEqual(m.current_period_end, _at(2027, 3, 10))

    def test_renewal_refused_after_grace_or_cancellation(self):
        m = self._confirmed(onboarding_fee=Decimal("10.00"), at=_at(2027, 1, 10))
        with patch("django.utils.timezone.now", return_value=_at(2027, 2, 14)):
            with self.assertRaises(ValueError):
                premium_billing.record_renewal(m)
        m2 = self._confirmed_second()
        premium_billing.schedule_cancellation(m2)
        with self.assertRaises(ValueError):
            premium_billing.record_renewal(m2)

    def _confirmed_second(self):
        PremiumMembership.objects.filter(user=self.member).update(status="cancelled")
        return self._confirmed(onboarding_fee=Decimal("10.00"))

    def test_renewals_due_lists_renewing_rows_in_window(self):
        m = self._confirmed(onboarding_fee=Decimal("10.00"), at=_at(2027, 1, 10))
        due = lambda now: list(  # noqa: E731
            premium_billing.renewals_due(now=now).values_list("pk", flat=True)
        )
        self.assertEqual(due(_at(2027, 2, 9)), [])
        self.assertEqual(due(_at(2027, 2, 11)), [m.pk])
        self.assertEqual(due(_at(2027, 2, 14)), [])  # grace over: lapses instead
        premium_billing.schedule_cancellation(m)
        self.assertEqual(due(_at(2027, 2, 11)), [])

    # -- lapse sweep ----------------------------------------------------------

    def test_sweep_ends_lapsed_membership_and_releases_coach(self):
        m = self._confirmed(onboarding_fee=Decimal("10.00"), at=_at(2027, 1, 10))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.assigned_coach, self.coach)

        with patch("django.utils.timezone.now", return_value=_at(2027, 2, 12)):
            self.assertEqual(premium_billing.sweep_lapsed(), 0)  # still in grace
        with patch("django.utils.timezone.now", return_value=_at(2027, 2, 14)):
            self.assertEqual(premium_billing.sweep_lapsed(), 1)

        m.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(m.status, "cancelled")
        self.assertIsNone(self.profile.assigned_coach)

    def test_admin_endpoint_runs_the_sweep(self):
        self._confirmed(onboarding_fee=Decimal("10.00"), at=_at(2027, 1, 10))
        client = Client(HTTP_HOST="crush.lu")
        url = "/api/admin/premium-lapse-sweep/"
        self.assertIn(
            client.post(url, HTTP_AUTHORIZATION="Bearer wrong").status_code, (401, 403)
        )
        with patch("django.utils.timezone.now", return_value=_at(2027, 2, 14)):
            resp = client.post(url, HTTP_AUTHORIZATION=f"Bearer {API_KEY}")
        self.assertEqual(resp.status_code, 202)
        self.assertFalse(
            PremiumMembership.objects.filter(user=self.member, status="active").exists()
        )

    # -- member cancel / resume ----------------------------------------------

    def test_member_cancels_and_resumes_from_the_membership_page(self):
        m = self._confirmed(onboarding_fee=Decimal("10.00"))
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(self.member)

        page = client.get("/en/membership/").content.decode()
        self.assertIn("Renews on", page)

        client.post("/en/premium/renewal/cancel/")
        m.refresh_from_db()
        self.assertTrue(m.cancel_at_period_end)
        page = client.get("/en/membership/").content.decode()
        self.assertIn("will not renew", page)

        client.post("/en/premium/renewal/resume/")
        m.refresh_from_db()
        self.assertFalse(m.cancel_at_period_end)
