"""Onboarding/dashboard "next event" teasers respect invitation visibility.

Audit A5: the pending-submission page and the dashboard picked the next
published event without the private-invitation filter the event list uses.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import CrushProfile, EventInvitation, ProfileSubmission
from crush_lu.tests.test_event_time_states import _grant_crush_access, _make_event
from crush_lu.views_events import next_visible_event

User = get_user_model()


def _member(email):
    user = User.objects.create_user(
        username=email, email=email, password="testpass123", first_name="Teaser"
    )
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 1, 1),
        gender="M",
        location="Luxembourg",
        verification_status="pending",
    )
    _grant_crush_access(user)
    return user, profile


@override_settings(ROOT_URLCONF="azureproject.urls_crush", SITE_ID=1)
class OnboardingTeaserVisibilityTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(
            id=1, defaults={"domain": "crush.lu", "name": "Crush.lu"}
        )
        self.user, self.profile = _member("teaser-a@example.com")
        ProfileSubmission.objects.create(profile=self.profile, status="pending")
        soon = timezone.now() + timedelta(days=1)
        self.private = _make_event("Secret Invitation Gala", soon)
        self.private.is_private_invitation = True
        self.private.save()
        self.public = _make_event("Open Mixer", soon + timedelta(days=2))

    def _submitted_context(self):
        client = Client()
        client.force_login(self.user)
        response = client.get(
            reverse("crush_lu:profile_submitted"), HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 200)
        return response

    def test_uninvited_member_skips_private_event(self):
        response = self._submitted_context()

        self.assertEqual(response.context["next_event"], self.public)
        self.assertNotContains(response, "Secret Invitation Gala")

    def test_uninvited_member_with_only_private_event_sees_no_teaser(self):
        self.public.delete()

        response = self._submitted_context()

        self.assertIsNone(response.context["next_event"])
        self.assertNotContains(response, "Secret Invitation Gala")

    def test_invited_member_sees_private_event(self):
        self.private.invited_users.add(self.user)

        response = self._submitted_context()

        self.assertEqual(response.context["next_event"], self.private)

    def test_approved_external_invitation_sees_private_event(self):
        EventInvitation.objects.create(
            event=self.private,
            guest_email="teaser-a@example.com",
            guest_first_name="Teaser",
            guest_last_name="A",
            created_user=self.user,
            approval_status="approved",
        )

        self.assertEqual(next_visible_event(self.user, timezone.now()), self.private)

    def test_pending_external_invitation_stays_hidden(self):
        EventInvitation.objects.create(
            event=self.private,
            guest_email="teaser-a@example.com",
            guest_first_name="Teaser",
            guest_last_name="A",
            created_user=self.user,
            approval_status="pending",
        )

        self.assertEqual(next_visible_event(self.user, timezone.now()), self.public)

    def test_dashboard_next_event_hides_private_event(self):
        self.profile.verification_status = "verified"
        self.profile.save()
        client = Client()
        client.force_login(self.user)

        response = client.get(reverse("crush_lu:dashboard"), HTTP_HOST="crush.lu")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Secret Invitation Gala")
