"""Speed-dating preference collection at event registration (Phase 1).

Speed-dating registrations collect a per-application preference snapshot
(age range / languages / gender preference) into EventRegistrationPreference,
a side row mirroring CrushConnectMembership's field names. Every other event
type's registration form must stay byte-identical — the section renders only
when ``event.event_type == "speed_dating"``.

Paths are literal because the host middleware swaps the urlconf per domain --
``reverse("crush_lu:event_register")`` builds a ``/crush/...`` path that 404s
under ``HTTP_HOST=crush.lu``.

Run with: pytest crush_lu/tests/test_event_preferences.py -v
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone

User = get_user_model()


class EventPreferenceTestBase(TestCase):
    """Shared fixtures: one speed-dating event, one mixer, a verified member."""

    def setUp(self):
        from django.core.cache import cache

        from crush_lu.models import MeetupEvent

        # event_register is @ratelimit(key="user", rate="5/h", method="POST")
        # and the cache is NOT rolled back between tests while the user PK
        # sequence is (on SQLite) — without this every test's viewer shares
        # one counter and the sixth POST in the file 429s.
        cache.clear()

        self.client = Client(HTTP_HOST="crush.lu")
        self.speed_dating = MeetupEvent.objects.create(
            title="Curated Speed Dating",
            description="Preference collection test",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=7),
            location="Luxembourg",
            address="123 Test Street",
            max_participants=10,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True,
            profile_requirement="none",
        )
        self.mixer = MeetupEvent.objects.create(
            title="Social Mixer",
            description="Control event",
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=7),
            location="Luxembourg",
            address="123 Test Street",
            max_participants=10,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True,
            profile_requirement="none",
        )
        self.user = self._create_member("member@example.com")

    def _create_member(self, username, gender="M", event_languages=None):
        from crush_lu.models import CrushProfile, UserDataConsent

        user = User.objects.create_user(
            username=username,
            email=username,
            password="testpass123",
            first_name=username.split("@")[0],
        )
        # consent_middleware is scoped to urls_crush and 302s every page
        # without this.
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender=gender,
            location="Luxembourg",
            event_languages=event_languages or ["en"],
        )
        return user

    def _login(self, user):
        self.client.login(username=user.username, password="testpass123")

    def _register_url(self, event):
        return f"/en/events/{event.id}/register/"


class PreferenceSectionRenderingTests(EventPreferenceTestBase):
    def test_section_rendered_for_speed_dating(self):
        self._login(self.user)
        response = self.client.get(self._register_url(self.speed_dating))
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["pref_form"])
        self.assertContains(response, 'name="preferred_genders"')
        self.assertContains(response, 'name="preferred_age_min"')
        self.assertContains(response, 'name="languages"')

    def test_section_absent_for_other_event_types(self):
        self._login(self.user)
        response = self.client.get(self._register_url(self.mixer))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["pref_form"])
        self.assertNotContains(response, 'name="preferred_genders"')
        self.assertNotContains(response, 'name="preferred_age_min"')

    def test_language_choices_limited_to_event_languages(self):
        from crush_lu.models import CrushProfile

        self.speed_dating.languages = ["fr", "en"]
        self.speed_dating.save()
        profile = CrushProfile.objects.get(user=self.user)
        profile.event_languages = ["fr"]
        profile.save(update_fields=["event_languages"])

        self._login(self.user)
        response = self.client.get(self._register_url(self.speed_dating))
        self.assertEqual(response.status_code, 200)
        codes = [
            code
            for code, _label in response.context["pref_form"].fields[
                "languages"
            ].choices
        ]
        self.assertEqual(sorted(codes), ["en", "fr"])


class PreferenceSubmissionTests(EventPreferenceTestBase):
    def test_registration_without_preferences_creates_open_row(self):
        from crush_lu.models import EventRegistration, EventRegistrationPreference

        self._login(self.user)
        response = self.client.post(self._register_url(self.speed_dating), {})
        self.assertEqual(response.status_code, 302)

        registration = EventRegistration.objects.get(
            event=self.speed_dating, user=self.user
        )
        self.assertEqual(registration.status, "confirmed")
        pref = EventRegistrationPreference.objects.get(registration=registration)
        self.assertEqual(pref.preferred_genders, [])
        self.assertEqual(pref.preferred_age_min, 18)
        self.assertEqual(pref.preferred_age_max, 99)
        self.assertEqual(pref.languages, [])

    def test_submitted_preferences_are_stored(self):
        from crush_lu.models import EventRegistration, EventRegistrationPreference

        self._login(self.user)
        response = self.client.post(
            self._register_url(self.speed_dating),
            {
                "preferred_genders": ["F", "NB"],
                "preferred_age_min": "25",
                "preferred_age_max": "35",
                "languages": ["fr"],
            },
        )
        self.assertEqual(response.status_code, 302)

        registration = EventRegistration.objects.get(
            event=self.speed_dating, user=self.user
        )
        pref = EventRegistrationPreference.objects.get(registration=registration)
        self.assertEqual(pref.preferred_genders, ["F", "NB"])
        self.assertEqual(pref.preferred_age_min, 25)
        self.assertEqual(pref.preferred_age_max, 35)
        self.assertEqual(pref.languages, ["fr"])

    def test_crossed_age_range_is_swapped(self):
        from crush_lu.models import EventRegistration, EventRegistrationPreference

        self._login(self.user)
        self.client.post(
            self._register_url(self.speed_dating),
            {"preferred_age_min": "40", "preferred_age_max": "25"},
        )
        registration = EventRegistration.objects.get(
            event=self.speed_dating, user=self.user
        )
        pref = EventRegistrationPreference.objects.get(registration=registration)
        self.assertEqual(pref.preferred_age_min, 25)
        self.assertEqual(pref.preferred_age_max, 40)

    def test_out_of_range_age_rejected(self):
        from crush_lu.models import EventRegistration

        self._login(self.user)
        response = self.client.post(
            self._register_url(self.speed_dating),
            {"preferred_age_min": "17", "preferred_age_max": "35"},
        )
        # Invalid form re-renders the page; nothing is persisted.
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            EventRegistration.objects.filter(
                event=self.speed_dating, user=self.user
            ).exists()
        )

    def test_mixer_post_creates_no_preference_row(self):
        from crush_lu.models import EventRegistration, EventRegistrationPreference

        self._login(self.user)
        response = self.client.post(
            self._register_url(self.mixer),
            # Preference keys on a non-speed-dating event are simply ignored.
            {"preferred_genders": ["F"], "preferred_age_min": "20"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            EventRegistration.objects.filter(
                event=self.mixer, user=self.user
            ).exists()
        )
        self.assertEqual(EventRegistrationPreference.objects.count(), 0)

    def test_row_reuse_overwrites_stale_preferences(self):
        from crush_lu.models import EventRegistration, EventRegistrationPreference

        self._login(self.user)
        self.client.post(
            self._register_url(self.speed_dating),
            {
                "preferred_genders": ["F"],
                "preferred_age_min": "25",
                "preferred_age_max": "35",
                "languages": ["fr"],
            },
        )
        registration = EventRegistration.objects.get(
            event=self.speed_dating, user=self.user
        )
        registration.status = "cancelled"
        registration.save()

        self.client.post(
            self._register_url(self.speed_dating),
            {
                "preferred_genders": ["M"],
                "preferred_age_min": "30",
                "preferred_age_max": "45",
                "languages": ["en"],
            },
        )
        # The registration row is reused, and so is its preference row —
        # exactly one of each, carrying the SECOND application's answers.
        self.assertEqual(
            EventRegistration.objects.filter(
                event=self.speed_dating, user=self.user
            ).count(),
            1,
        )
        pref = EventRegistrationPreference.objects.get(
            registration=registration
        )
        self.assertEqual(pref.preferred_genders, ["M"])
        self.assertEqual(pref.preferred_age_min, 30)
        self.assertEqual(pref.preferred_age_max, 45)
        self.assertEqual(pref.languages, ["en"])


class PreferencePrefillTests(EventPreferenceTestBase):
    def test_prefill_from_connect_membership(self):
        from crush_lu.models import CrushConnectMembership

        CrushConnectMembership.objects.create(
            user=self.user,
            onboarded_at=timezone.now(),
            preferred_genders=["F"],
            preferred_age_min=28,
            preferred_age_max=38,
            # Connect's 8-code vocabulary: "pt" must be filtered out of the
            # 4-code event set on prefill.
            languages=["fr", "pt"],
        )
        self._login(self.user)
        response = self.client.get(self._register_url(self.speed_dating))
        initial = response.context["pref_form"].initial
        self.assertEqual(initial["preferred_genders"], ["F"])
        self.assertEqual(initial["preferred_age_min"], 28)
        self.assertEqual(initial["preferred_age_max"], 38)
        self.assertEqual(initial["languages"], ["fr"])

    def test_not_onboarded_membership_falls_back_to_profile(self):
        from crush_lu.models import CrushConnectMembership, CrushProfile

        CrushConnectMembership.objects.create(
            user=self.user,
            onboarded_at=None,
            preferred_genders=["F"],
        )
        profile = CrushProfile.objects.get(user=self.user)
        profile.preferred_genders = ["M", "NB"]
        profile.preferred_age_min = 30
        profile.preferred_age_max = 45
        profile.event_languages = ["de", "en"]
        profile.save()

        self._login(self.user)
        response = self.client.get(self._register_url(self.speed_dating))
        initial = response.context["pref_form"].initial
        self.assertEqual(initial["preferred_genders"], ["M", "NB"])
        self.assertEqual(initial["preferred_age_min"], 30)
        self.assertEqual(initial["preferred_age_max"], 45)
        self.assertEqual(initial["languages"], ["de", "en"])

    def test_prefill_from_legacy_profile(self):
        from crush_lu.models import CrushProfile

        profile = CrushProfile.objects.get(user=self.user)
        profile.preferred_genders = ["M"]
        profile.preferred_age_min = 22
        profile.preferred_age_max = 33
        profile.event_languages = ["fr"]
        profile.save()

        self._login(self.user)
        response = self.client.get(self._register_url(self.speed_dating))
        initial = response.context["pref_form"].initial
        self.assertEqual(initial["preferred_genders"], ["M"])
        self.assertEqual(initial["preferred_age_min"], 22)
        self.assertEqual(initial["preferred_age_max"], 33)
        self.assertEqual(initial["languages"], ["fr"])


class PreferenceDisplayHelperTests(EventPreferenceTestBase):
    def test_display_helpers_translate_codes(self):
        from crush_lu.models import EventRegistration, EventRegistrationPreference

        registration = EventRegistration.objects.create(
            event=self.speed_dating, user=self.user, status="confirmed"
        )
        pref = EventRegistrationPreference.objects.create(
            registration=registration,
            preferred_genders=["F", "NB", "zz"],  # unknown codes are skipped
            languages=["fr", "zz"],
        )
        self.assertEqual(
            [str(label) for label in pref.preferred_genders_display()],
            ["Female", "Non-binary"],
        )
        self.assertEqual(
            [str(label) for label in pref.languages_display()], ["Français"]
        )


class PreferenceRetentionTests(EventPreferenceTestBase):
    def _preference_on_event_days_ago(self, days_ago, username):
        from crush_lu.models import (
            EventRegistration,
            EventRegistrationPreference,
            MeetupEvent,
        )

        event = MeetupEvent.objects.create(
            title=f"Past speed dating {days_ago}",
            description="Retention fixture",
            event_type="speed_dating",
            date_time=timezone.now() - timedelta(days=days_ago),
            location="Luxembourg",
            address="123 Test Street",
            max_participants=10,
            registration_deadline=timezone.now() - timedelta(days=days_ago + 2),
            is_published=True,
            profile_requirement="none",
        )
        user = self._create_member(username)
        registration = EventRegistration.objects.create(
            event=event, user=user, status="attended"
        )
        return EventRegistrationPreference.objects.create(
            registration=registration, preferred_genders=["F"]
        )

    def test_retention_sweep_prunes_only_past_events(self):
        from crush_lu.models import EventRegistration, EventRegistrationPreference

        old_pref = self._preference_on_event_days_ago(40, "old@example.com")
        recent_pref = self._preference_on_event_days_ago(5, "recent@example.com")

        call_command("gdpr_retention_cleanup", "--apply")

        self.assertFalse(
            EventRegistrationPreference.objects.filter(pk=old_pref.pk).exists()
        )
        self.assertTrue(
            EventRegistrationPreference.objects.filter(pk=recent_pref.pk).exists()
        )
        # The registration row itself is never touched by the sweep.
        self.assertTrue(
            EventRegistration.objects.filter(pk=old_pref.registration_id).exists()
        )

    def test_retention_dry_run_deletes_nothing(self):
        from crush_lu.models import EventRegistrationPreference

        self._preference_on_event_days_ago(40, "old@example.com")
        call_command("gdpr_retention_cleanup")
        self.assertEqual(EventRegistrationPreference.objects.count(), 1)
