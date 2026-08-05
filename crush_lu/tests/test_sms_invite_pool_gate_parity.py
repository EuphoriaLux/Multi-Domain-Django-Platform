"""The coach SMS invite pool must not list members `event_register` refuses.

Every invite costs a real SMS, and a member who taps one and gets bounced is a
worse outcome than never being asked. Two filters had drifted from the gate:

- ``sub_age_q`` admitted profiles with **no date of birth** even on
  age-restricted events, while ``event_register`` refuses ``profile.age is None``
  outright for those events.
- The ``unverified`` and ``none`` branches admitted profiles declaring **no
  languages** (``event_languages`` empty or NULL), while
  ``user_meets_language_requirement`` refuses exactly those.

Both were reachable on every event live on 2026-08-05 — all six were both
age-restricted and language-restricted.

These tests assert against the *rendered page*, not the queryset: the pool is
only ever observed through it, and a green helper with a broken page is the
failure mode this app has hit before.

**Backend note.** The language half of the pool uses
``event_languages__contains`` on a ``JSONField``. That lookup is unsupported on
SQLite — it raises ``NotSupportedError``, it does not merely differ — so the
coach SMS invite page 500s on SQLite for *any* event with a language
requirement. That is pre-existing behaviour, not something this fix introduced,
and it is invisible in production because prod runs PostgreSQL. CI runs SQLite,
so the language-dependent tests below are skipped there and only the DOB half is
enforced automatically. Run this module against PostgreSQL to cover both.
"""

import unittest
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.utils import timezone

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}

# Phone numbers double as identifiers: the page renders them into `sms:` hrefs,
# so presence/absence of the number is presence/absence in the pool.
PHONE_ELIGIBLE = "+352691000001"
PHONE_NO_DOB = "+352691000002"
PHONE_NO_LANGS = "+352691000003"

requires_json_contains = unittest.skipUnless(
    connection.features.supports_json_field_contains,
    "JSONField __contains is unsupported on this backend (raises "
    "NotSupportedError on SQLite), so the invite pool's language filter cannot "
    "run here at all. Production is PostgreSQL; run this module against it.",
)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class SmsInvitePoolGateParityTests(TestCase):
    """The invite pool is `eligibility AND contactable`, never looser."""

    def setUp(self):
        from crush_lu.models import CrushCoach, CrushProfile, MeetupEvent
        from crush_lu.models.profiles import UserDataConsent
        from crush_lu.models.site_config import CrushSiteConfig

        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )

        self.coach_user = User.objects.create_user(
            username="coach@pool.test",
            email="coach@pool.test",
            password="coachpass",
            first_name="Sophie",
        )
        UserDataConsent.objects.filter(user=self.coach_user).update(
            crushlu_consent_given=True
        )
        self.coach = CrushCoach.objects.create(
            user=self.coach_user, is_active=True, max_active_reviews=10
        )

        # Age-restricted like every event live on 2026-08-05 (25-38, 40-65,
        # 45-65). Languages are added per-test so the DOB cases stay runnable on
        # SQLite — see the backend note in the module docstring.
        self.event = MeetupEvent.objects.create(
            title="Pool Parity Speed Dating",
            description="desc",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=7),
            location="Luxembourg",
            address="1 Test St",
            max_participants=20,
            min_age=25,
            max_age=38,
            registration_deadline=timezone.now() + timedelta(days=5),
            registration_fee=0,
            is_published=True,
            profile_requirement="unverified",
        )
        self.event.coaches.add(self.coach)
        CrushSiteConfig.get_config()

        self.CrushProfile = CrushProfile
        self.client = Client()
        self.client.login(username="coach@pool.test", password="coachpass")

    def _require_language(self, codes=("en",)):
        self.event.languages = list(codes)
        self.event.save(update_fields=["languages"])

    def _member(self, name, phone, *, dob, languages):
        """A contactable, unverified member — the pool's target cohort."""
        from crush_lu.models.profiles import UserDataConsent

        user = User.objects.create_user(
            username=f"{name}@pool.test",
            email=f"{name}@pool.test",
            password="pass",
            first_name=name.title(),
        )
        UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
        return self.CrushProfile.objects.create(
            user=user,
            date_of_birth=dob,
            gender="F",
            location="Luxembourg",
            phone_number=phone,
            phone_verified=True,
            event_languages=languages,
        )

    def _page(self):
        response = self.client.get(
            f"/de/coach/events/{self.event.id}/sms-invite/",
            HTTP_ACCEPT_LANGUAGE="de",
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    # --- the control: a member the gate would admit must still be invited ----

    def test_eligible_member_is_still_listed(self):
        """Guard against over-filtering: the fix must not empty the pool."""
        self._member(
            "eligible", PHONE_ELIGIBLE, dob=date(1995, 5, 15), languages=["en"]
        )
        self.assertIn(
            PHONE_ELIGIBLE,
            self._page(),
            "an in-range member vanished from the invite pool — the filters are "
            "now stricter than the gate",
        )

    # --- defect 1: missing date of birth on an age-restricted event ---------

    def test_profile_without_dob_is_not_invited(self):
        """The main pool was already strict here; this pins it against regression."""
        self._member("nodob", PHONE_NO_DOB, dob=None, languages=["en"])
        self.assertNotIn(
            PHONE_NO_DOB,
            self._page(),
            "invited a member with no date_of_birth to an age-restricted event; "
            "event_register refuses profile.age is None, so the SMS is wasted",
        )

    def test_submission_without_dob_is_not_invited(self):
        """The submission pool is the one that drifted — it kept `isnull=True`."""
        from crush_lu.models.profiles import ProfileSubmission

        profile = self._member("subnodob", PHONE_NO_DOB, dob=None, languages=["en"])
        ProfileSubmission.objects.create(
            profile=profile, coach=self.coach, status="pending"
        )
        self.assertNotIn(
            PHONE_NO_DOB,
            self._page(),
            "the pending-submission pool invited a member with no date_of_birth "
            "to an age-restricted event",
        )

    # --- defect 2: no declared languages on a language-restricted event -----

    @requires_json_contains
    def test_profile_without_languages_is_not_invited(self):
        self._require_language()
        self._member("nolangs", PHONE_NO_LANGS, dob=date(1995, 5, 15), languages=[])
        self.assertNotIn(
            PHONE_NO_LANGS,
            self._page(),
            "invited a member declaring no languages to a language-restricted "
            "event; user_meets_language_requirement refuses exactly those",
        )

    @requires_json_contains
    def test_profile_without_languages_is_not_invited_on_open_event(self):
        """`none` waives the profile requirement, not the language one."""
        self._require_language()
        self.event.profile_requirement = "none"
        self.event.save(update_fields=["profile_requirement"])
        self._member("nolangs", PHONE_NO_LANGS, dob=date(1995, 5, 15), languages=[])
        self.assertNotIn(
            PHONE_NO_LANGS,
            self._page(),
            "the `none` branch invited a member declaring no languages to a "
            "language-restricted event",
        )

    @requires_json_contains
    def test_language_matching_member_is_still_listed(self):
        """The language control: strict matching must not empty the pool."""
        self._require_language()
        self._member(
            "eligible", PHONE_ELIGIBLE, dob=date(1995, 5, 15), languages=["en"]
        )
        self.assertIn(
            PHONE_ELIGIBLE,
            self._page(),
            "a language-matching member vanished once strict lang_q was applied",
        )

    # --- the guard rail: unrestricted events must stay permissive -----------

    def test_unrestricted_event_still_invites_profiles_without_dob(self):
        """A default 18/99 event is never age-checked, so NULL DOB is fine.

        This is why the fix is conditional on `has_age_filter` rather than a
        straight deletion of the isnull leg.
        """
        self.event.min_age = 18
        self.event.max_age = 99
        self.event.save(update_fields=["min_age", "max_age"])
        self._member("nodob", PHONE_NO_DOB, dob=None, languages=[])
        self.assertIn(
            PHONE_NO_DOB,
            self._page(),
            "an unrestricted event stopped inviting a member with no DOB — "
            "event_register does not age-check those events, so this "
            "over-filters and loses real reach",
        )
