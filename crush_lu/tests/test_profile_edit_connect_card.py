"""
Tests for the Crush Connect entry points added to the member-facing pages:

- The "Crush Connect Profile" section card on /profile/edit/ (unlocked once
  Connect onboarding is complete, locked upsell into the teaser otherwise,
  hidden entirely pre-launch for non-staff).
- The dashboard verifier card dedupe (the verifier highlight is dropped when
  the full "Your Crush Coach" section renders the same coach right below it).
"""

from datetime import date

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import CrushCoach, CrushConnectMembership
from crush_lu.models.profiles import (
    CrushProfile,
    ProfileSubmission,
    UserDataConsent,
)


def _make_member(username="member@example.com", *, is_staff=False):
    """A logged-in-ready, approved (=verified) crush.lu member."""
    user = User.objects.create_user(
        username=username,
        email=username,
        password="testpass123",
        first_name="Mem",
        is_staff=is_staff,
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender="M",
        location="canton-luxembourg",
        is_approved=True,
        is_active=True,
    )
    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save()
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"verified": True, "primary": True},
    )
    return user


def _make_coach(username, phone):
    coach_user = User.objects.create_user(
        username=username, email=f"{username}@example.com", first_name=username.title()
    )
    return CrushCoach.objects.create(
        user=coach_user,
        bio="Test coach",
        specializations="General",
        phone_number=phone,
        is_active=True,
    )


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class EditProfileConnectCardTests(TestCase):
    UNLOCKED_SUBTITLE = "Story, lifestyle, ideal match"
    LOCKED_SUBTITLE = "Not yet unlocked"

    def setUp(self):
        self.user = _make_member()
        self.client.login(username="member@example.com", password="testpass123")

    def _get(self):
        return self.client.get(reverse("crush_lu:edit_profile"), HTTP_HOST="crush.lu")

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_onboarded_member_gets_unlocked_card(self):
        CrushConnectMembership.objects.create(
            user=self.user, onboarded_at=timezone.now()
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["connect_onboarded"])
        self.assertContains(response, self.UNLOCKED_SUBTITLE)
        self.assertContains(response, reverse("crush_lu:crush_connect_profile_edit"))
        self.assertNotContains(response, self.LOCKED_SUBTITLE)

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_member_without_membership_gets_locked_card(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["connect_onboarded"])
        self.assertContains(response, self.LOCKED_SUBTITLE)
        self.assertContains(response, reverse("crush_lu:crush_connect_teaser"))
        # The Connect editor must not be reachable from the locked card (nor
        # from the navbar, which is gated on the same onboarded state).
        self.assertNotContains(response, reverse("crush_lu:crush_connect_profile_edit"))

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_coach_excluded_membership_stays_locked(self):
        CrushConnectMembership.objects.create(
            user=self.user, onboarded_at=timezone.now(), excluded_by_coach=True
        )
        response = self._get()
        self.assertFalse(response.context["connect_onboarded"])
        self.assertContains(response, self.LOCKED_SUBTITLE)

    @override_settings(CRUSH_CONNECT_LAUNCHED=False)
    def test_card_hidden_pre_launch_for_non_staff(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Crush Connect Profile")
        self.assertNotContains(response, self.LOCKED_SUBTITLE)

    @override_settings(CRUSH_CONNECT_LAUNCHED=False)
    def test_card_visible_pre_launch_for_staff(self):
        _make_member("staff@example.com", is_staff=True)
        self.client.login(username="staff@example.com", password="testpass123")
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Crush Connect Profile")

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_locked_card_renders_in_german(self):
        """The new strings ship translated (staging showed English fallbacks
        for untranslated Connect strings on /de/ pages)."""
        response = self.client.get("/de/profile/edit/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Crush-Connect-Profil")
        self.assertContains(response, "Noch nicht freigeschaltet")


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class DashboardVerifierDedupeTests(TestCase):
    def setUp(self):
        self.user = _make_member("dash@example.com")
        self.coach = _make_coach("coach_a", "+352111111")
        profile = self.user.crushprofile
        profile.assigned_coach = self.coach  # premium
        profile.save()
        self.client.login(username="dash@example.com", password="testpass123")

    def _get(self):
        return self.client.get(reverse("crush_lu:dashboard"), HTTP_HOST="crush.lu")

    def test_verifier_card_suppressed_when_same_coach_shown_below(self):
        ProfileSubmission.objects.create(
            profile=self.user.crushprofile, coach=self.coach, status="approved"
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["coach"].id, self.coach.id)
        self.assertIsNone(response.context["verifier"])

    def test_verifier_card_kept_when_coaches_differ(self):
        reviewer = _make_coach("coach_b", "+352222222")
        ProfileSubmission.objects.create(
            profile=self.user.crushprofile, coach=reviewer, status="approved"
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["coach"].id, reviewer.id)
        self.assertIsNotNone(response.context["verifier"])
        self.assertEqual(response.context["verifier"]["coach_id"], self.coach.id)

    def test_verifier_card_kept_without_submission(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["coach"])
        self.assertEqual(response.context["verifier"]["coach_id"], self.coach.id)

    def test_dead_waitlist_context_removed(self):
        response = self._get()
        self.assertNotIn("on_crush_connect_waitlist", response.context)
        self.assertNotIn("crush_connect_position", response.context)
        self.assertNotIn("crush_connect_total", response.context)

    def test_events_section_uses_du_form_in_german(self):
        """ "Your Events" was the formal-Sie straggler on an otherwise
        informal dashboard ("Ihre" → "Deine")."""
        response = self.client.get("/de/dashboard/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deine Veranstaltungen")
        self.assertNotContains(response, "Ihre Veranstaltungen")
