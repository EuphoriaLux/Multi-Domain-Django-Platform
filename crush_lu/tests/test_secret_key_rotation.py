"""
Tests for Task 5.4b (SECRET_KEY rotation).

Proves the mechanism, not a real rotation: a signature/hash created under an
"old" key (simulated via override_settings) must still verify once SECRET_KEY
changes to a "new" value, as long as the old value is listed in
SECRET_KEY_FALLBACKS — and must NOT verify once that fallback is removed.

Covers:
- The real door check-in path (`event_checkin_api`), which signs with a bare
  `django.core.signing.Signer()` — this is the load-bearing case: an
  in-flight event ticket must survive a SECRET_KEY rotation.
- `IOSNativeAuthCode`, whose hand-rolled hash needed an explicit fix (it does
  not use django.core.signing, so it gets no automatic fallback support).

Run with: python manage.py test crush_lu.tests.test_secret_key_rotation
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.signing import BadSignature, Signer
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.models.ios_app import IOSNativeAuthCode, _hash_native_auth_code
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()

OLD_KEY = "old-secret-key-simulated-for-rotation-test"
NEW_KEY = "new-secret-key-simulated-for-rotation-test"


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CheckinTokenRotationTests(TestCase):
    """`event_checkin_api` must accept a ticket signed under the previous
    SECRET_KEY once that key is listed in SECRET_KEY_FALLBACKS, and must
    reject it once the fallback is dropped.

    ROOT_URLCONF is overridden to azureproject.urls_crush because
    event_checkin_api is only wired there, not on the default
    azureproject.urls — same reason crush_lu/tests/test_checkin_door_actions.py
    uses `pytest.mark.urls("azureproject.urls_crush")`; override_settings is
    used here instead so these tests also run under `manage.py test`.
    """

    def setUp(self):
        self.client = Client()
        self.event = MeetupEvent.objects.create(
            title="Rotation Test Event",
            description="d",
            event_type="mixer",
            location="Luxembourg",
            address="1 St",
            canton="Luxembourg",
            date_time=timezone.now() + timedelta(hours=1),
            duration_minutes=120,
            max_participants=10,
            registration_deadline=timezone.now() + timedelta(minutes=30),
            is_published=True,
        )
        self.user = User.objects.create_user(
            username="rotation@test.com",
            email="rotation@test.com",
            password="p",
        )
        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.registration = EventRegistration.objects.create(
            event=self.event, user=self.user, status="confirmed"
        )
        # Simulate a ticket that was generated and stored *before* the
        # rotation: signed with what will become the "old" key.
        old_token = Signer(key=OLD_KEY).sign(
            f"{self.registration.pk}:{self.event.pk}"
        )
        self.registration.checkin_token = old_token
        self.registration.save(update_fields=["checkin_token"])
        self.url = reverse(
            "event_checkin_api",
            kwargs={"registration_id": self.registration.pk, "token": old_token},
        )

    @override_settings(SECRET_KEY=NEW_KEY, SECRET_KEY_FALLBACKS=[OLD_KEY])
    def test_old_ticket_still_scans_when_old_key_is_a_fallback(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "attended")

    @override_settings(SECRET_KEY=NEW_KEY, SECRET_KEY_FALLBACKS=[])
    def test_old_ticket_is_rejected_once_fallback_is_dropped(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "confirmed")

    @override_settings(SECRET_KEY=NEW_KEY, SECRET_KEY_FALLBACKS=[OLD_KEY])
    def test_a_freshly_generated_token_signs_with_the_new_key_not_a_fallback(self):
        """New tokens are minted with settings.SECRET_KEY (the primary key)
        even while a fallback is configured — fallbacks are for verifying
        old signatures, never for creating new ones."""
        from crush_lu.views_ticket import _generate_checkin_token

        fresh_registration = EventRegistration.objects.create(
            event=self.event,
            user=User.objects.create_user(
                username="rotation2@test.com",
                email="rotation2@test.com",
                password="p",
            ),
            status="confirmed",
        )
        token = _generate_checkin_token(fresh_registration)

        # Signed with NEW_KEY: verifying against NEW_KEY alone must succeed...
        self.assertEqual(
            Signer(key=NEW_KEY).unsign(token),
            f"{fresh_registration.pk}:{fresh_registration.event_id}",
        )
        # ...and verifying against OLD_KEY alone (no fallback) must fail.
        with self.assertRaises(BadSignature):
            Signer(key=OLD_KEY).unsign(token)


class IOSNativeAuthCodeRotationTests(TestCase):
    """IOSNativeAuthCode.consume() must accept a code issued under the
    previous SECRET_KEY once that key is a configured fallback."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="ios-rotation@test.com",
            email="ios-rotation@test.com",
            password="p",
        )

    def _issue_under_old_key(self, code="plain-code-value"):
        # Mirrors IOSNativeAuthCode.issue(), but pins the hash to OLD_KEY to
        # simulate "issued just before the rotation".
        return IOSNativeAuthCode.objects.create(
            user=self.user,
            code_hash=_hash_native_auth_code(code, secret_key=OLD_KEY),
            redirect_uri="crushlu://auth",
            expires_at=timezone.now() + timedelta(minutes=5),
        )

    @override_settings(SECRET_KEY=NEW_KEY, SECRET_KEY_FALLBACKS=[OLD_KEY])
    def test_code_issued_under_old_key_is_consumable_with_fallback_configured(self):
        code = "plain-code-value"
        self._issue_under_old_key(code)

        consumed_user = IOSNativeAuthCode.consume(code)

        self.assertEqual(consumed_user, self.user)

    @override_settings(SECRET_KEY=NEW_KEY, SECRET_KEY_FALLBACKS=[])
    def test_code_issued_under_old_key_is_rejected_without_fallback(self):
        code = "plain-code-value"
        self._issue_under_old_key(code)

        consumed_user = IOSNativeAuthCode.consume(code)

        self.assertIsNone(consumed_user)

    def test_issue_and_consume_round_trip_under_a_single_key(self):
        """Sanity check: the ordinary, non-rotation path still works."""
        code = IOSNativeAuthCode.issue(self.user, redirect_uri="crushlu://auth")

        consumed_user = IOSNativeAuthCode.consume(code)

        self.assertEqual(consumed_user, self.user)
        # Single-use: a second consume of the same code must fail.
        self.assertIsNone(IOSNativeAuthCode.consume(code))
