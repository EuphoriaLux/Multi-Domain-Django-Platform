"""
Tests for the in-person event verification endpoint (Option 2 / premium Option 3).

Covers ``crush_lu.views_checkin.coach_mark_verified``:
- A coach verifies an ordinary walk-in -> verified + method 'coach_event'.
- An open ProfileSubmission is approved as a side effect.
- A premium member (with assigned_coach) can only be verified by that coach,
  and the method is recorded as 'premium_coach'.
- Already-verified attendees return an idempotent success.
- Guards: missing registration, non-confirmed status.
"""

from datetime import timedelta
from unittest.mock import patch

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
# Avoid hitting the websocket layer during the verify side effect.
@patch("crush_lu.views_checkin._broadcast_checkin", lambda *a, **k: None)
class CoachMarkVerifiedTests(SiteTestMixin, TestCase):
    def setUp(self):
        from crush_lu.models import (
            CrushCoach,
            CrushProfile,
            EventRegistration,
            MeetupEvent,
        )
        from crush_lu.models.profiles import UserDataConsent

        self.client = Client()
        self.EventRegistration = EventRegistration
        self.CrushProfile = CrushProfile

        def _consent(user):
            UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)

        # Two coaches: one runs the event, one is a premium member's assigned coach.
        self.coach_user = User.objects.create_user(
            username="coach@example.com",
            email="coach@example.com",
            password="pass12345",
            first_name="Cam",
        )
        _consent(self.coach_user)
        self.coach = CrushCoach.objects.create(
            user=self.coach_user, is_active=True, max_active_reviews=10
        )

        self.other_coach_user = User.objects.create_user(
            username="coach2@example.com",
            email="coach2@example.com",
            password="pass12345",
            first_name="Dana",
        )
        _consent(self.other_coach_user)
        self.other_coach = CrushCoach.objects.create(
            user=self.other_coach_user, is_active=True, max_active_reviews=10
        )

        self.event = MeetupEvent.objects.create(
            title="Verify Night",
            description="event",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=2),
            location="Luxembourg",
            address="1 Test St",
            max_participants=20,
            min_age=18,
            max_age=99,
            registration_deadline=timezone.now() + timedelta(days=1),
            registration_fee=0,
            is_published=True,
        )

    def _make_attendee(self, username, assigned_coach=None, status="confirmed"):
        user = User.objects.create_user(
            username=username, email=username, password="pass12345", first_name="Al"
        )
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
        profile = self.CrushProfile.objects.create(
            user=user,
            gender="M",
            location="Luxembourg",
            is_approved=False,
            verification_status="pending",
            phone_number="+352123456789",
            phone_verified=True,
        )
        if assigned_coach is not None:
            profile.assigned_coach = assigned_coach
            profile.assigned_coach_at = timezone.now()
            profile.save(update_fields=["assigned_coach", "assigned_coach_at"])
        reg = self.EventRegistration.objects.create(
            event=self.event, user=user, status=status
        )
        return profile, reg

    def _url(self, reg):
        return reverse(
            "coach_mark_verified",
            kwargs={"event_id": self.event.id, "registration_id": reg.id},
        )

    def test_walkin_verified_as_coach_event(self):
        from crush_lu.models import ProfileSubmission

        profile, reg = self._make_attendee("walkin@example.com")
        sub = ProfileSubmission.objects.create(profile=profile, status="pending")

        self.client.force_login(self.coach_user)
        resp = self.client.post(self._url(reg))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

        profile.refresh_from_db()
        self.assertEqual(profile.verification_status, "verified")
        self.assertEqual(profile.verification_method, "coach_event")
        self.assertTrue(profile.is_approved)
        self.assertIsNotNone(profile.approved_at)

        sub.refresh_from_db()
        self.assertEqual(sub.status, "approved")
        self.assertEqual(sub.coach_id, self.coach.id)
        self.assertTrue(sub.review_call_completed)

    def test_premium_member_requires_assigned_coach(self):
        profile, reg = self._make_attendee(
            "premium@example.com", assigned_coach=self.other_coach
        )

        # The event-running coach is NOT the assigned coach -> 403.
        self.client.force_login(self.coach_user)
        resp = self.client.post(self._url(reg))
        self.assertEqual(resp.status_code, 403)
        profile.refresh_from_db()
        self.assertEqual(profile.verification_status, "pending")

        # The assigned coach succeeds, recorded as premium_coach.
        self.client.force_login(self.other_coach_user)
        resp = self.client.post(self._url(reg))
        self.assertEqual(resp.status_code, 200)
        profile.refresh_from_db()
        self.assertEqual(profile.verification_status, "verified")
        self.assertEqual(profile.verification_method, "premium_coach")

    def test_already_verified_is_idempotent(self):
        profile, reg = self._make_attendee("done@example.com")
        profile.verification_status = "verified"
        profile.verification_method = "luxid"
        profile.is_approved = True
        profile.save()

        self.client.force_login(self.coach_user)
        resp = self.client.post(self._url(reg))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["already_verified"])
        profile.refresh_from_db()
        # Method is not overwritten.
        self.assertEqual(profile.verification_method, "luxid")

    def test_missing_registration_returns_404(self):
        self.client.force_login(self.coach_user)
        resp = self.client.post(
            reverse(
                "coach_mark_verified",
                kwargs={"event_id": self.event.id, "registration_id": 999999},
            )
        )
        self.assertEqual(resp.status_code, 404)

    def test_cancelled_registration_rejected(self):
        profile, reg = self._make_attendee("cx@example.com", status="cancelled")
        self.client.force_login(self.coach_user)
        resp = self.client.post(self._url(reg))
        self.assertEqual(resp.status_code, 400)
        profile.refresh_from_db()
        self.assertEqual(profile.verification_status, "pending")

    def test_requires_coach(self):
        """A non-coach user is redirected (coach_required guard)."""
        profile, reg = self._make_attendee("plainuser@example.com")
        plain = User.objects.create_user(
            username="plain@example.com",
            email="plain@example.com",
            password="pass12345",
        )
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.filter(user=plain).update(crushlu_consent_given=True)
        self.client.force_login(plain)
        resp = self.client.post(self._url(reg))
        self.assertEqual(resp.status_code, 302)
        profile.refresh_from_db()
        self.assertEqual(profile.verification_status, "pending")
