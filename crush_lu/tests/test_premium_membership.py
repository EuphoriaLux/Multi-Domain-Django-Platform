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

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=False)
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

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_premium_seeker_redirected_to_beta(self):
        """With the beta funnel on, a premium-seeker (no pending request) is
        sent to the Crush Connect waitlist instead of the coach directory."""
        self._make_coach("ava")
        self.client.force_login(self.member)
        resp = self.client.get(reverse("crush_lu:premium_choose_coach"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("crush_lu:crush_connect_teaser"), resp.url)

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_pending_member_not_redirected_to_beta(self):
        """A member with an in-flight request still reaches the directory so
        they can change or cancel it."""
        from crush_lu.models import PremiumMembership

        coach = self._make_coach("pip")
        PremiumMembership.objects.create(
            user=self.member, coach=coach, status="pending"
        )
        self.client.force_login(self.member)
        resp = self.client.get(reverse("crush_lu:premium_choose_coach"))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.context["pending_membership"])

    def test_cancel_pending_membership(self):
        from crush_lu.models import PremiumMembership

        coach = self._make_coach("cara")
        membership = PremiumMembership.objects.create(
            user=self.member, coach=coach, status="pending"
        )

        self.client.force_login(self.member)
        resp = self.client.post(reverse("crush_lu:premium_cancel_membership"))
        self.assertEqual(resp.status_code, 302)

        membership.refresh_from_db()
        self.assertEqual(membership.status, "cancelled")
        # A cancelled request frees re-selection: a new select creates a fresh
        # pending row (the cancelled one no longer matches status="pending").
        resp = self.client.post(
            reverse("crush_lu:premium_select_coach", kwargs={"coach_id": coach.id})
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            PremiumMembership.objects.filter(
                user=self.member, status="pending"
            ).count(),
            1,
        )

    def test_cancel_method_noop_on_active(self):
        from crush_lu.models import PremiumMembership

        coach = self._make_coach("nick")
        membership = PremiumMembership.objects.create(
            user=self.member, coach=coach, status="pending"
        )
        membership.confirm()  # → active, assigns coach
        self.assertFalse(membership.cancel())
        membership.refresh_from_db()
        self.assertEqual(membership.status, "active")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.assigned_coach_id, coach.id)

    def test_cancel_requires_post(self):
        self.client.force_login(self.member)
        resp = self.client.get(reverse("crush_lu:premium_cancel_membership"))
        self.assertEqual(resp.status_code, 405)

    def test_confirm_rejects_cancelled_membership(self):
        """A cancelled request must not be reactivated by confirm() — otherwise
        the admin 'confirm payment' action could resurrect it and assign a
        coach despite the user cancelling."""
        from crush_lu.models import PremiumMembership

        coach = self._make_coach("cleo")
        membership = PremiumMembership.objects.create(
            user=self.member, coach=coach, status="cancelled"
        )
        with self.assertRaises(ValueError):
            membership.confirm()

        membership.refresh_from_db()
        self.assertEqual(membership.status, "cancelled")
        self.assertFalse(membership.payment_confirmed)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.assigned_coach_id)

    def test_dashboard_shows_premium_pending_path(self):
        """End-to-end: a pending premium member sees the premium-pending hero
        (view → _verification_path_context → journey partial)."""
        from crush_lu.models import PremiumMembership

        self.profile.verification_status = "pending"
        self.profile.save(update_fields=["verification_status"])
        coach = self._make_coach("dana")
        PremiumMembership.objects.create(
            user=self.member, coach=coach, status="pending"
        )

        self.client.force_login(self.member)
        resp = self.client.get(reverse("crush_lu:dashboard"))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("payment pending", body.lower())
        self.assertIn("Dana", body)

    def test_dashboard_shows_event_pending_path(self):
        """End-to-end: a pending user with an upcoming confirmed registration
        sees the at-event hero."""
        from datetime import timedelta

        from crush_lu.models import EventRegistration, MeetupEvent

        self.profile.verification_status = "pending"
        self.profile.save(update_fields=["verification_status"])
        event = MeetupEvent.objects.create(
            title="Test Event",
            description="x",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=7),
            location="Luxembourg",
            address="1 Test St",
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True,
        )
        EventRegistration.objects.create(
            event=event, user=self.member, status="confirmed"
        )

        self.client.force_login(self.member)
        resp = self.client.get(reverse("crush_lu:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("verified at your next event", resp.content.decode())
