"""Phase C UI / write-path tests for the Event Identity redesign.

Covers the member-facing contract (spec §13, §14):

  * the create-profile wizard step 2 completes without bio/interests;
  * the legacy ``/api/profile/save-step2/`` route can no longer write
    bio/interests;
  * the merged "Your Event Identity" edit card renders (card list + section);
  * the edit-profile / create-profile templates do not leak a member's legacy
    free-text bio/interests.

The ``event_identity`` autosave round-trip and the retired ``about`` section are
covered in ``test_profiles.ProfileSettingsAutosaveTests``; the form validators
in ``test_event_identity``.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.urls import reverse
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


def _member(username, **profile_kwargs):
    """A consent-granted, email-verified user + logged-in client + profile."""
    client = Client()
    user = User.objects.create_user(
        username=username, email=username, password="pass-pass-pass", first_name="Bo"
    )
    EmailAddress.objects.create(user=user, email=username, verified=True, primary=True)
    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save()
    defaults = dict(
        date_of_birth=timezone.now().date() - timedelta(days=30 * 365),
        gender="F",
        location="canton-luxembourg",
        phone_number="+35212345678",
        phone_verified=True,
        phone_verified_at=timezone.now(),
        event_languages=["en"],
    )
    defaults.update(profile_kwargs)
    profile = CrushProfile.objects.create(user=user, **defaults)
    client.login(username=username, password="pass-pass-pass")
    return client, user, profile


@override_settings(**CRUSH_LU_URL_SETTINGS)
class WizardStep2Tests(_SiteMixin, TestCase):
    def test_step2_completes_without_bio_or_interests(self):
        """An empty step-2 submission (no bio, no interests, no structured
        selections) still succeeds — nothing is required (spec O2)."""
        client, _user, profile = _member("empty2@example.com")
        resp = client.post(
            "/api/profile/save-step2/",
            data="{}",
            content_type="application/json",
            HTTP_HOST="crush.lu",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        profile.refresh_from_db()
        self.assertEqual(profile.bio, "")
        self.assertEqual(profile.interests, "")

    def test_save_step2_ignores_bio_and_interests_payload(self):
        """Even when a client posts bio/interests to the legacy route, they are
        never written (spec §6.2)."""
        client, _user, profile = _member("leak2@example.com", bio="", interests="")
        resp = client.post(
            "/api/profile/save-step2/",
            data='{"bio": "sneaky bio", "interests": "sneaky, interests"}',
            content_type="application/json",
            HTTP_HOST="crush.lu",
        )
        self.assertEqual(resp.status_code, 200)
        profile.refresh_from_db()
        self.assertEqual(profile.bio, "")
        self.assertEqual(profile.interests, "")


@override_settings(**CRUSH_LU_URL_SETTINGS)
class EditProfileEventIdentityCardTests(_SiteMixin, TestCase):
    def _approved(self, username, **kw):
        return _member(
            username,
            is_approved=True,
            verification_status="verified",
            bio="SECRET_LEGACY_BIO",
            interests="SECRET_LEGACY_INTERESTS",
            **kw,
        )

    def test_card_list_shows_single_event_identity_card(self):
        client, _user, _profile = self._approved("card@example.com")
        resp = client.get(reverse("crush_lu:edit_profile"), HTTP_HOST="crush.lu")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Your Event Identity", body)
        self.assertIn("section=event_identity", body)
        # The three merged cards no longer exist as separate destinations.
        # (Trailing quote avoids matching the unrelated ?section=about_crushlu.)
        self.assertNotIn('?section=about"', body)
        self.assertNotIn('?section=traits"', body)
        self.assertNotIn('?section=event_languages"', body)
        # Legacy free-text bio/interests never render on the member card list.
        self.assertNotIn("SECRET_LEGACY_BIO", body)
        self.assertNotIn("SECRET_LEGACY_INTERESTS", body)

    def test_event_identity_section_renders_form(self):
        client, _user, _profile = self._approved("section@example.com")
        resp = client.get(
            reverse("crush_lu:edit_profile") + "?section=event_identity",
            HTTP_HOST="crush.lu",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        # The autosaving Event Identity form is present.
        self.assertIn('data-section="event_identity"', body)
        self.assertIn('name="interests_new"', body)
        self.assertIn('name="ask_me_about"', body)
        self.assertIn('name="event_vibe"', body)
        self.assertIn('name="event_languages"', body)
        # No legacy free-text leak on the section either.
        self.assertNotIn("SECRET_LEGACY_BIO", body)
        self.assertNotIn("SECRET_LEGACY_INTERESTS", body)

    def test_retired_sections_fall_through_to_card_list(self):
        """A stale ?section=about / traits / event_languages link no longer has
        a handler — it falls through to the default card list (200, single
        Event Identity card), never a legacy bio editor."""
        client, _user, _profile = self._approved("stale@example.com")
        for stale in ("about", "traits", "event_languages"):
            resp = client.get(
                reverse("crush_lu:edit_profile") + f"?section={stale}",
                HTTP_HOST="crush.lu",
            )
            self.assertEqual(resp.status_code, 200, stale)
            body = resp.content.decode()
            self.assertNotIn("SECRET_LEGACY_BIO", body)
            self.assertIn("Your Event Identity", body)
