"""
Tests for the premium membership flow (member chooses their coach).

Covers:
- The coach directory only lists coaches open to new premium members.
- Selecting a coach creates a pending PremiumMembership.
- Confirming a membership assigns the coach (the single writer of assigned_coach)
  and is capacity-safe.
- An already-premium member is redirected away from the directory.
"""

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}


class SiteTestMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class PremiumMembershipTests(SiteTestMixin, TestCase):
    def setUp(self):
        from crush_lu.models import CrushCoach, CrushProfile
        from crush_lu.models.profiles import UserDataConsent

        self.client = Client()
        self.CrushCoach = CrushCoach
        self.CrushProfile = CrushProfile

        self.member = User.objects.create_user(
            username="m@example.com", email="m@example.com", password="pass12345"
        )
        UserDataConsent.objects.filter(user=self.member).update(
            crushlu_consent_given=True
        )
        self.profile = CrushProfile.objects.create(
            user=self.member, gender="F", location="Luxembourg"
        )

    def _make_coach(self, name, **kwargs):
        opts = dict(is_active=True, accepting_premium=True, max_premium_members=2)
        opts.update(kwargs)
        user = User.objects.create_user(
            username=f"{name}@example.com",
            email=f"{name}@example.com",
            password="pass12345",
            first_name=name.capitalize(),
        )
        return self.CrushCoach.objects.create(user=user, **opts)

    def test_directory_lists_only_available_coaches(self):
        available = self._make_coach("ava")
        self._make_coach("inactive", is_active=False)
        self._make_coach("notpremium", accepting_premium=False)
        self._make_coach("away", is_away=True)
        full = self._make_coach("full", max_premium_members=1)
        # Fill the "full" coach to capacity.
        other = User.objects.create_user(
            username="taken@example.com", email="taken@example.com", password="x"
        )
        p = self.CrushProfile.objects.create(user=other, gender="M")
        p.assigned_coach = full
        p.assigned_coach_at = timezone.now()
        p.save()

        self.client.force_login(self.member)
        resp = self.client.get(reverse("crush_lu:premium_choose_coach"))
        self.assertEqual(resp.status_code, 200)
        ids = {c.id for c in resp.context["coaches"]}
        self.assertEqual(ids, {available.id})

    def test_select_creates_pending_membership(self):
        from crush_lu.models import PremiumMembership

        coach = self._make_coach("pat")
        self.client.force_login(self.member)
        resp = self.client.post(
            reverse("crush_lu:premium_select_coach", kwargs={"coach_id": coach.id})
        )
        self.assertEqual(resp.status_code, 302)
        membership = PremiumMembership.objects.get(user=self.member)
        self.assertEqual(membership.status, "pending")
        self.assertEqual(membership.coach_id, coach.id)
        self.assertFalse(membership.payment_confirmed)
        # Not premium yet — assignment only happens on confirm.
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.assigned_coach_id)

    def test_select_rejects_full_coach(self):
        from crush_lu.models import PremiumMembership

        coach = self._make_coach("full", max_premium_members=1)
        other = User.objects.create_user(
            username="t2@example.com", email="t2@example.com", password="x"
        )
        p = self.CrushProfile.objects.create(user=other, gender="M")
        p.assigned_coach = coach
        p.assigned_coach_at = timezone.now()
        p.save()

        self.client.force_login(self.member)
        resp = self.client.post(
            reverse("crush_lu:premium_select_coach", kwargs={"coach_id": coach.id})
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(PremiumMembership.objects.filter(user=self.member).exists())

    def test_confirm_assigns_coach(self):
        from crush_lu.models import PremiumMembership

        coach = self._make_coach("max")
        membership = PremiumMembership.objects.create(
            user=self.member, coach=coach, status="pending"
        )
        membership.confirm()

        membership.refresh_from_db()
        self.assertEqual(membership.status, "active")
        self.assertTrue(membership.payment_confirmed)
        self.assertIsNotNone(membership.payment_date)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.assigned_coach_id, coach.id)
        self.assertIsNotNone(self.profile.assigned_coach_at)

    def test_confirm_respects_capacity(self):
        from crush_lu.models import PremiumMembership

        coach = self._make_coach("solo", max_premium_members=1)
        # Fill the only seat.
        other = User.objects.create_user(
            username="t3@example.com", email="t3@example.com", password="x"
        )
        p = self.CrushProfile.objects.create(user=other, gender="M")
        p.assigned_coach = coach
        p.assigned_coach_at = timezone.now()
        p.save()

        membership = PremiumMembership.objects.create(
            user=self.member, coach=coach, status="pending"
        )
        with self.assertRaises(ValueError):
            membership.confirm()
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.assigned_coach_id)

    def test_already_premium_redirected(self):
        coach = self._make_coach("rex")
        self.profile.assigned_coach = coach
        self.profile.assigned_coach_at = timezone.now()
        self.profile.save()

        self.client.force_login(self.member)
        resp = self.client.get(reverse("crush_lu:premium_choose_coach"))
        self.assertEqual(resp.status_code, 302)
