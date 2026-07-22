"""
Deep verification of the post-signup multi-step profile builder.

Three guarantees the onboarding flow must hold, asserted end-to-end against
the real AJAX endpoints (not mocks):

  1. SAVE — every step writes its fields straight to the CrushProfile the
     moment its endpoint is called (each step is independently persisted).
  2. SYNC / NO-CLOBBER — saving a later step never wipes an earlier step's
     data; the profile accumulates across steps. Invalid input is preserved
     in draft_data instead of being lost.
  3. RESUME — a user who stops mid-flow comes back to the exact sub-step they
     need, with their already-saved values pre-filled on the form.

These lock in the behaviour in views_profile.save_profile_step1/2/3,
save_profile_preferences, CrushProfile.wizard_step and the create_profile
GET resume branch.
"""

import json
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from allauth.account.models import EmailAddress

from crush_lu.models import CrushProfile
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}


class _SiteMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )


def _logged_in_user(username="builder@example.com"):
    """A consent-granted, email-verified user + logged-in client.

    Email is verified so the submission gate never deflects the builder to
    the /accounts/email/ page, and consent is granted so the consent
    middleware doesn't intercept onboarding URLs.
    """
    client = Client()
    user = User.objects.create_user(
        username=username,
        email=username,
        password="pass-pass-pass",
        first_name="Bo",
    )
    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save(update_fields=["crushlu_consent_given"])
    EmailAddress.objects.update_or_create(
        user=user, email=user.email, defaults={"verified": True, "primary": True}
    )
    client.login(username=username, password="pass-pass-pass")
    return client, user


def _post_json(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


# ---------------------------------------------------------------------------
# 1. SAVE — each step persists immediately
# ---------------------------------------------------------------------------


@override_settings(**CRUSH_LU_URL_SETTINGS)
class StepSavesImmediatelyTests(_SiteMixin, TestCase):
    def setUp(self):
        self.client, self.user = _logged_in_user()

    def test_step1_persists_basic_info_and_clears_its_draft(self):
        resp = _post_json(
            self.client,
            "/api/profile/save-step1/",
            {
                "phone_number": "+352621000000",
                "date_of_birth": "1993-03-15",
                "gender": "F",
                "location": "canton-luxembourg",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

        profile = CrushProfile.objects.get(user=self.user)
        self.assertEqual(profile.date_of_birth, date(1993, 3, 15))
        self.assertEqual(profile.gender, "F")
        self.assertEqual(profile.location, "canton-luxembourg")
        self.assertEqual(profile.verification_status, "incomplete")
        # Draft scratch space for step1 is cleared once it is officially saved.
        self.assertNotIn("step1", profile.draft_data or {})

    def test_step1_saves_without_location(self):
        """Location is optional since fast-track event verification — an
        omitted location must not 400 (model, API and form all agree)."""
        resp = _post_json(
            self.client,
            "/api/profile/save-step1/",
            {
                "phone_number": "+352621000000",
                "date_of_birth": "1993-03-15",
                "gender": "F",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        profile = CrushProfile.objects.get(user=self.user)
        self.assertEqual(profile.location, "")

    def test_step2_no_longer_writes_bio_and_interests(self):
        """The free-text bio/interests write path is retired (spec §6.2): a
        direct save-step2 POST carrying bio/interests writes neither field. The
        structured Event Identity fields are persisted instead."""
        from crush_lu.models import Interest

        # Step 2 is gated on a verified phone.
        CrushProfile.objects.create(
            user=self.user,
            phone_number="+352621000000",
            phone_verified=True,
            phone_verified_at=timezone.now(),
        )
        yoga = Interest.objects.get(slug="yoga")
        resp = _post_json(
            self.client,
            "/api/profile/save-step2/",
            {
                "bio": "I like long walks",
                "interests": "hiking, films",
                "interests_new": [yoga.pk],
                "ask_me_about": [yoga.pk],
                "event_vibe": "quiet_corner",
            },
        )
        self.assertEqual(resp.status_code, 200)

        profile = CrushProfile.objects.get(user=self.user)
        # Legacy free-text columns are never written by the new step 2.
        self.assertEqual(profile.bio, "")
        self.assertEqual(profile.interests, "")
        # Structured Event Identity fields are saved.
        self.assertEqual(
            list(profile.interests_new.values_list("slug", flat=True)), ["yoga"]
        )
        self.assertEqual(profile.ask_me_about, [yoga.pk])
        self.assertEqual(profile.event_vibe, "quiet_corner")

    def test_step2_blocked_without_verified_phone(self):
        CrushProfile.objects.create(user=self.user)  # phone NOT verified
        resp = _post_json(
            self.client, "/api/profile/save-step2/", {"bio": "x", "interests": "y"}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(resp.json().get("phone_verification_required"))

    def test_step3_persists_event_languages_and_privacy(self):
        CrushProfile.objects.create(
            user=self.user,
            phone_number="+352621000000",
            phone_verified=True,
            phone_verified_at=timezone.now(),
        )
        resp = self.client.post(
            "/api/profile/save-step3/",
            {
                "event_languages": ["en", "fr"],
                "show_full_name": "on",
                "show_exact_age": "on",
            },
        )
        self.assertEqual(resp.status_code, 200)

        profile = CrushProfile.objects.get(user=self.user)
        self.assertEqual(sorted(profile.event_languages), ["en", "fr"])
        self.assertTrue(profile.show_full_name)
        self.assertTrue(profile.show_exact_age)

    def test_preferences_persist_with_update_fields(self):
        CrushProfile.objects.create(
            user=self.user,
            phone_number="+352621000000",
            phone_verified=True,
        )
        resp = _post_json(
            self.client,
            "/api/profile/save-preferences/",
            {
                "preferred_genders": ["M", "F"],
                "preferred_age_min": 25,
                "preferred_age_max": 40,
            },
        )
        self.assertEqual(resp.status_code, 200)

        profile = CrushProfile.objects.get(user=self.user)
        self.assertEqual(sorted(profile.preferred_genders), ["F", "M"])
        self.assertEqual(profile.preferred_age_min, 25)
        self.assertEqual(profile.preferred_age_max, 40)


# ---------------------------------------------------------------------------
# 2. SYNC / NO-CLOBBER — steps accumulate, invalid input is not lost
# ---------------------------------------------------------------------------


@override_settings(**CRUSH_LU_URL_SETTINGS)
class StepsAccumulateTests(_SiteMixin, TestCase):
    def setUp(self):
        self.client, self.user = _logged_in_user()

    def test_saving_step2_keeps_step1_data(self):
        # Step 1 first.
        _post_json(
            self.client,
            "/api/profile/save-step1/",
            {
                "phone_number": "+352621000000",
                "date_of_birth": "1990-01-01",
                "gender": "M",
                "location": "canton-luxembourg",
            },
        )
        # Verify the phone so step 2's gate passes (mirrors the journey:
        # phone is verified at step 2 before the builder's About section).
        CrushProfile.objects.filter(user=self.user).update(
            phone_verified=True, phone_verified_at=timezone.now()
        )
        # Now step 2 (structured Event Identity fields).
        from crush_lu.models import Interest

        yoga = Interest.objects.get(slug="yoga")
        _post_json(
            self.client,
            "/api/profile/save-step2/",
            {"interests_new": [yoga.pk], "event_vibe": "quiet_corner"},
        )

        profile = CrushProfile.objects.get(user=self.user)
        # Step 1 fields survived the step 2 save.
        self.assertEqual(profile.date_of_birth, date(1990, 1, 1))
        self.assertEqual(profile.gender, "M")
        self.assertEqual(profile.location, "canton-luxembourg")
        # Step 2 Event Identity fields are present too.
        self.assertEqual(
            list(profile.interests_new.values_list("slug", flat=True)), ["yoga"]
        )
        self.assertEqual(profile.event_vibe, "quiet_corner")

    def test_invalid_step1_age_preserves_input_in_draft(self):
        """A sub-18 date is rejected, but the user's typed values are kept in
        draft_data so the wizard can repopulate them — no silent data loss."""
        resp = _post_json(
            self.client,
            "/api/profile/save-step1/",
            {
                "phone_number": "+352621000000",
                "date_of_birth": "2015-01-01",  # under 18
                "gender": "F",
                "location": "canton-luxembourg",
            },
        )
        self.assertEqual(resp.status_code, 400)

        profile = CrushProfile.objects.get(user=self.user)
        draft = (profile.draft_data or {}).get("step1", {})
        self.assertEqual(draft.get("gender"), "F")
        self.assertEqual(draft.get("location"), "canton-luxembourg")
        self.assertEqual(draft.get("date_of_birth"), "2015-01-01")
        # The bad value was NOT promoted onto the real field.
        self.assertIsNone(profile.date_of_birth)


# ---------------------------------------------------------------------------
# 3. RESUME — come back to the right sub-step with data pre-filled
# ---------------------------------------------------------------------------


@override_settings(**CRUSH_LU_URL_SETTINGS)
class ResumeAfterStoppingTests(_SiteMixin, TestCase):
    def setUp(self):
        self.client, self.user = _logged_in_user()
        # Put the user at journey step 4 (the builder) so /create-profile/
        # renders instead of bouncing to the smart-resume entry.
        CrushProfile.objects.create(
            user=self.user,
            welcome_seen_at=timezone.now(),
            coach_intro_seen_at=timezone.now(),
            phone_verified=True,
            phone_number="+352621000000",
            verification_status="incomplete",
        )

    def test_fresh_builder_starts_on_basic_info(self):
        resp = self.client.get("/create-profile/", follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["current_step"], 1)

    def test_stop_after_basics_resumes_on_photos_with_data_prefilled(self):
        # Fill Basic Info via the real endpoint, then "leave".
        _post_json(
            self.client,
            "/api/profile/save-step1/",
            {
                "phone_number": "+352621000000",
                "date_of_birth": "1988-07-09",
                "gender": "NB",
                "location": "canton-luxembourg",
            },
        )

        # Come back: should resume on the Photos sub-step (3), because basics
        # are complete but photo_1 + event_languages are still missing.
        resp = self.client.get("/create-profile/", follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["current_step"], 3)

        # The already-saved values are bound to the form for pre-fill.
        instance = resp.context["form"].instance
        self.assertEqual(instance.gender, "NB")
        self.assertEqual(instance.location, "canton-luxembourg")
        self.assertEqual(instance.date_of_birth, date(1988, 7, 9))

    def test_completed_builder_resumes_on_review(self):
        # Basics + photo + event languages all present → Review (4).
        _post_json(
            self.client,
            "/api/profile/save-step1/",
            {
                "phone_number": "+352621000000",
                "date_of_birth": "1988-07-09",
                "gender": "NB",
                "location": "canton-luxembourg",
            },
        )
        CrushProfile.objects.filter(user=self.user).update(
            photo_1="users/1/photos/test.jpg", event_languages=["en"]
        )

        resp = self.client.get("/create-profile/", follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["current_step"], 4)

    def test_onboarding_entry_routes_a_builder_user_to_create_profile(self):
        """Journey-level resume: hitting /onboarding/ mid-builder lands on the
        create-profile step, not back at welcome. (Use the language-prefixed
        path so LocaleMiddleware doesn't add its own redirect hop first.)"""
        resp = self.client.get("/en/onboarding/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("create-profile", resp["Location"])
