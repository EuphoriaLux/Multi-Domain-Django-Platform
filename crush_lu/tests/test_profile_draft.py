"""
Tests for the profile-wizard draft system (auto-save / restore) and the
wizard resume position.

Covers:
  - save_draft / get_draft never persist or return csrfmiddlewaretoken
    (a restored stale token would 403 every save after re-login)
  - delete_photo_draft clears the stored photo + draft URL (wizard photos
    auto-upload, so "remove" must delete server-side too)
  - wizard_step resume position (event_languages gap, completed → Review)
"""

import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.contrib.sites.models import Site
from django.utils import timezone

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


def _make_logged_in_client():
    client = Client()
    user = User.objects.create_user(
        username="draft@example.com",
        email="draft@example.com",
        password="pass-pass-pass",
        first_name="Dana",
    )
    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save(update_fields=["crushlu_consent_given"])
    client.login(username="draft@example.com", password="pass-pass-pass")
    return client, user


@override_settings(**CRUSH_LU_URL_SETTINGS)
class DraftCsrfSanitizationTests(_SiteMixin, TestCase):
    """draft_data must never store or return the CSRF token."""

    def setUp(self):
        self.client, self.user = _make_logged_in_client()

    def test_save_draft_strips_csrf_token(self):
        response = self.client.post(
            "/api/profile/draft/save/",
            data=json.dumps(
                {
                    "step": 1,
                    "data": {
                        "gender": "F",
                        "csrfmiddlewaretoken": "stale-token-123",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        profile = CrushProfile.objects.get(user=self.user)
        self.assertEqual(profile.draft_data["step1"].get("gender"), "F")
        self.assertNotIn("csrfmiddlewaretoken", profile.draft_data["step1"])

    def test_get_draft_strips_csrf_token_from_poisoned_draft(self):
        """Drafts written by older clients may already contain a token —
        get_draft must not hand it back in either `draft` or `merged`."""
        CrushProfile.objects.create(
            user=self.user,
            draft_data={
                "step1": {
                    "gender": "M",
                    "csrfmiddlewaretoken": "stale-token-123",
                }
            },
            last_draft_saved=timezone.now(),
        )

        response = self.client.get("/api/profile/draft/get/")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]

        self.assertNotIn("csrfmiddlewaretoken", data["merged"])
        self.assertNotIn("csrfmiddlewaretoken", data["draft"].get("step1", {}))
        # Real content survives sanitization
        self.assertEqual(data["merged"].get("gender"), "M")

    def test_get_draft_merges_draft_over_profile(self):
        """Draft input (newer, unsaved) wins over saved profile fields."""
        CrushProfile.objects.create(
            user=self.user,
            bio="old saved bio",
            draft_data={"step2": {"bio": "newer unsaved bio"}},
            last_draft_saved=timezone.now(),
        )

        response = self.client.get("/api/profile/draft/get/")
        self.assertEqual(response.json()["data"]["merged"]["bio"], "newer unsaved bio")

    def test_empty_value_overwrites_a_prior_draft_value(self):
        """Saves are merged per-key (draft_data[step].update). A cleared trait
        field must be sent as "" so it overwrites — otherwise a deselected
        quality/defect (whose <button> chip clears the x-bound hidden input)
        would survive in the draft and resurrect on resume. The wizard's
        gatherCurrentStepData sends qualities_ids/defects_ids even when empty;
        this pins the server half of that contract."""

        def save(value):
            return self.client.post(
                "/api/profile/draft/save/",
                data=json.dumps({"step": 2, "data": {"qualities_ids": value}}),
                content_type="application/json",
            )

        self.assertEqual(save("2,13").status_code, 200)
        self.assertEqual(save("").status_code, 200)  # deselected all

        profile = CrushProfile.objects.get(user=self.user)
        self.assertEqual(profile.draft_data["step2"]["qualities_ids"], "")


@override_settings(**CRUSH_LU_URL_SETTINGS)
class DeletePhotoDraftTests(_SiteMixin, TestCase):
    """The wizard's remove-photo button must delete server-side."""

    def setUp(self):
        self.client, self.user = _make_logged_in_client()
        self.profile = CrushProfile.objects.create(user=self.user)
        # Bypass save() so no storage interaction happens during setup.
        CrushProfile.objects.filter(pk=self.profile.pk).update(
            photo_1="users/1/photos/test.jpg",
            draft_data={"step3": {"photo_1_url": "https://example/test.jpg"}},
        )

    def test_delete_clears_photo_and_draft_url(self):
        response = self.client.post(
            "/api/profile/draft/delete-photo/", {"photo_number": "1"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

        self.profile.refresh_from_db()
        self.assertFalse(self.profile.photo_1)
        self.assertNotIn("photo_1_url", self.profile.draft_data.get("step3", {}))

    def test_invalid_photo_number_rejected(self):
        response = self.client.post(
            "/api/profile/draft/delete-photo/", {"photo_number": "9"}
        )
        self.assertEqual(response.status_code, 400)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.photo_1)

    def test_no_profile_returns_404(self):
        self.profile.delete()
        response = self.client.post(
            "/api/profile/draft/delete-photo/", {"photo_number": "1"}
        )
        self.assertEqual(response.status_code, 404)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class SaveStep1VerifiedPhoneTests(_SiteMixin, TestCase):
    """A verified phone is locked — a step-1 save with a different number
    keeps the stored number AND the verified flag (save_profile_step1 must
    not pretend to reset what CrushProfile.save() restores anyway)."""

    def setUp(self):
        self.client, self.user = _make_logged_in_client()
        self.profile = CrushProfile.objects.create(
            user=self.user,
            phone_number="+352621000000",
            phone_verified=True,
            phone_verified_at=timezone.now(),
        )

    def test_changed_number_is_ignored_and_verification_kept(self):
        response = self.client.post(
            "/api/profile/save-step1/",
            data=json.dumps(
                {
                    "phone_number": "+352621999999",
                    "date_of_birth": "1993-03-15",
                    "gender": "F",
                    "location": "canton-luxembourg",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.phone_number, "+352621000000")
        self.assertTrue(self.profile.phone_verified)
        self.assertIsNotNone(self.profile.phone_verified_at)
        # The rest of step 1 still saved normally
        self.assertEqual(self.profile.gender, "F")
        self.assertEqual(self.profile.location, "canton-luxembourg")


@override_settings(**CRUSH_LU_URL_SETTINGS)
class WizardResumePositionTests(_SiteMixin, TestCase):
    """Returning users must land on the sub-step they actually need."""

    def setUp(self):
        self.client, self.user = _make_logged_in_client()

    def _profile(self, **overrides):
        from datetime import date

        defaults = dict(
            user=self.user,
            welcome_seen_at=timezone.now(),
            coach_intro_seen_at=timezone.now(),
            phone_verified=True,
            phone_number="+352621000000",
            date_of_birth=date(1993, 3, 15),
            gender="F",
            location="canton-luxembourg",
            verification_status="incomplete",
        )
        defaults.update(overrides)
        profile = CrushProfile.objects.create(**defaults)
        return profile

    def test_missing_event_languages_resumes_on_step3(self):
        profile = self._profile(event_languages=[])
        CrushProfile.objects.filter(pk=profile.pk).update(
            photo_1="users/1/photos/test.jpg"
        )
        profile.refresh_from_db()
        self.assertEqual(profile.wizard_step, 3)

    def test_missing_photo_resumes_on_step3(self):
        profile = self._profile(event_languages=["en", "fr"])
        self.assertIsNone(profile.wizard_step)

    def test_complete_profile_resumes_on_review(self):
        """Everything filled but not submitted → resume on Review (4), not
        back at Basic Info."""
        profile = self._profile(event_languages=["en"])
        CrushProfile.objects.filter(pk=profile.pk).update(
            photo_1="users/1/photos/test.jpg"
        )
        profile.refresh_from_db()
        self.assertIsNone(profile.wizard_step)

        response = self.client.get("/create-profile/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_step"], 4)

    def test_incomplete_basics_resumes_on_step1(self):
        profile = self._profile(gender="")
        self.assertEqual(profile.wizard_step, 1)
