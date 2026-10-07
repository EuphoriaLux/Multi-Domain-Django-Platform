"""SLA fallback sweep: claim under lock, send after, undo on failed send (#1196)."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.api_admin_hybrid import revert_fallback_offer
from crush_lu.models import CrushCoach, CrushProfile, EmailPreference, ProfileSubmission
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()

SEND = "crush_lu.email_helpers.send_domain_email"
URL = "/api/admin/hybrid-coach-sla-sweep/"


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    ADMIN_API_KEY="k-admin",
    HYBRID_COACH_SYSTEM_ENABLED=True,
)
class SlaSweepTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )

    def setUp(self):
        cache.clear()
        coach_user = User.objects.create_user(
            "coach@example.com", "coach@example.com", "pw12345678"
        )
        self.coach = CrushCoach.objects.create(
            user=coach_user, is_active=True, hybrid_features_enabled=True
        )

    def _submission(self, name):
        user = User.objects.create_user(
            f"{name}@example.com", f"{name}@example.com", "pw12345678"
        )
        UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
        profile = CrushProfile.objects.create(
            user=user,
            gender="M",
            location="Luxembourg",
            verification_status="pending",
            photo_1="users/1/photos/a.jpg",
        )
        submission = ProfileSubmission.objects.create(
            profile=profile, status="pending", coach=self.coach
        )
        # A signal stamps a future deadline on create; breach it.
        ProfileSubmission.objects.filter(pk=submission.pk).update(
            sla_deadline=timezone.now() - timedelta(hours=1)
        )
        submission.refresh_from_db()
        return submission

    def _sweep(self):
        return self.client.post(
            URL, HTTP_HOST="crush.lu", HTTP_AUTHORIZATION="Bearer k-admin"
        )

    def test_successful_send_keeps_the_offer(self):
        sub = self._submission("ok")

        with patch(SEND, return_value=1) as send:
            body = self._sweep().json()

        sub.refresh_from_db()
        self.assertEqual((body["processed"], body["failed"]), (1, 0))
        self.assertIsNotNone(sub.fallback_offered_at)
        self.assertIsNotNone(sub.booking_token)
        self.assertFalse(send.call_args.kwargs["fail_silently"])

    def test_offer_is_committed_before_the_email_is_sent(self):
        sub = self._submission("order")
        seen = {}

        def fake_send(*args, **kwargs):
            seen["offered"] = ProfileSubmission.objects.get(
                pk=sub.pk
            ).fallback_offered_at
            return 1

        with patch(SEND, side_effect=fake_send):
            self._sweep()

        self.assertIsNotNone(seen["offered"])

    def test_suppressed_send_does_not_mark_the_offer_done(self):
        sub = self._submission("zero")

        with patch(SEND, return_value=0):
            body = self._sweep().json()

        sub.refresh_from_db()
        self.assertEqual((body["processed"], body["failed"]), (0, 1))
        self.assertIsNone(sub.fallback_offered_at)
        self.assertIsNone(sub.booking_token)
        self.assertIsNone(sub.booking_token_expires_at)
        self.assertEqual(sub.system_actions[-1]["type"], "fallback_email_failed")

    def test_raising_send_does_not_mark_the_offer_done(self):
        sub = self._submission("boom")

        with patch(SEND, side_effect=RuntimeError("graph down")):
            body = self._sweep().json()

        sub.refresh_from_db()
        self.assertEqual((body["processed"], body["failed"]), (0, 1))
        self.assertIsNone(sub.fallback_offered_at)

    def test_failed_submission_backs_off_then_is_retried(self):
        sub = self._submission("retry")
        with patch(SEND, return_value=0):
            self._sweep()

        # Immediately after a failure it is not hammered again ...
        with patch(SEND, return_value=1) as send:
            self._sweep()
        send.assert_not_called()

        # ... but once the backoff has elapsed the next sweep retries it.
        ProfileSubmission.objects.filter(pk=sub.pk).update(
            fallback_offer_claimed_at=timezone.now() - timedelta(hours=2)
        )
        with patch(SEND, return_value=1) as send:
            body = self._sweep().json()

        sub.refresh_from_db()
        self.assertEqual(body["processed"], 1)
        self.assertEqual(send.call_count, 1)
        self.assertIsNotNone(sub.fallback_offered_at)
        self.assertIsNotNone(sub.fallback_offer_sent_at)

    def test_unsubscribed_member_is_marked_offered_without_a_send(self):
        sub = self._submission("unsub")
        prefs = EmailPreference.get_or_create_for_user(sub.profile.user)
        prefs.unsubscribed_all = True
        prefs.save()

        with patch(SEND, return_value=1) as send:
            body = self._sweep().json()

        sub.refresh_from_db()
        send.assert_not_called()
        self.assertEqual(
            (body["processed"], body["failed"], body["skipped"]), (0, 0, 1)
        )
        self.assertIsNotNone(sub.fallback_offered_at)

    def test_one_failure_does_not_block_the_others(self):
        first = self._submission("first")
        second = self._submission("second")

        with patch(SEND, side_effect=[0, 1]):
            body = self._sweep().json()

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((body["processed"], body["failed"]), (1, 1))
        self.assertEqual(
            sorted([bool(first.fallback_offered_at), bool(second.fallback_offered_at)]),
            [False, True],
        )

    def test_suppressed_address_is_terminal_not_retried_forever(self):
        from crush_lu.models import EmailSuppression

        sub = self._submission("suppressed")
        EmailSuppression.objects.create(email=sub.profile.user.email)

        with patch(SEND, return_value=0) as send:
            body = self._sweep().json()
            again = self._sweep().json()

        sub.refresh_from_db()
        send.assert_not_called()
        self.assertEqual((body["failed"], body["skipped"]), (0, 1))
        self.assertEqual(again["skipped"] + again["failed"], 0)
        self.assertIsNotNone(sub.fallback_offered_at)
        self.assertIsNotNone(sub.fallback_offer_sent_at)

    def test_suppressed_addresses_cannot_starve_newer_submissions(self):
        from crush_lu.models import EmailSuppression

        for i in range(3):
            old = self._submission(f"starve{i}")
            EmailSuppression.objects.create(email=old.profile.user.email)
        fresh = self._submission("fresh")

        with (
            patch("crush_lu.api_admin_hybrid.SLA_SWEEP_CLAIM_CHUNK", 2),
            patch(SEND, return_value=1),
        ):
            body = self._sweep().json()

        fresh.refresh_from_db()
        self.assertEqual((body["processed"], body["skipped"]), (1, 3))
        self.assertIsNotNone(fresh.fallback_offered_at)

    def test_one_call_drains_more_than_a_single_chunk(self):
        subs = [self._submission(f"drain{i}") for i in range(5)]

        with (
            patch("crush_lu.api_admin_hybrid.SLA_SWEEP_CLAIM_CHUNK", 2),
            patch(SEND, return_value=1),
        ):
            body = self._sweep().json()

        self.assertEqual(body["processed"], 5)
        for sub in subs:
            sub.refresh_from_db()
            self.assertIsNotNone(sub.fallback_offer_sent_at)

    def test_exhausted_time_budget_defers_and_undoes_unsent_offers(self):
        import crush_lu.api_admin_hybrid as module

        subs = [self._submission(f"budget{i}") for i in range(3)]

        def spend_budget(*args, **kwargs):
            module.SLA_SWEEP_SEND_BUDGET_SECONDS = -1
            return 1

        with (
            patch.object(module, "SLA_SWEEP_SEND_BUDGET_SECONDS", 100),
            patch.object(module, "SLA_SWEEP_CLAIM_CHUNK", 3),
            patch(SEND, side_effect=spend_budget),
        ):
            body = self._sweep().json()

        offered = [
            ProfileSubmission.objects.get(pk=s.pk).fallback_offered_at is not None
            for s in subs
        ]
        self.assertEqual((body["processed"], body["deferred"]), (1, 2))
        self.assertEqual(sorted(offered), [False, False, True])

    def test_failed_send_keeps_the_offer_when_the_member_already_booked(self):
        from crush_lu.models import ScreeningSlot

        sub = self._submission("booked")
        with patch(SEND, return_value=1):
            self._sweep()
        sub.refresh_from_db()
        token = sub.booking_token
        start = timezone.now() + timedelta(days=1)
        ScreeningSlot.objects.create(
            coach=self.coach,
            submission=sub,
            start_at=start,
            end_at=start + timedelta(minutes=30),
            status="booked",
        )

        self.assertFalse(revert_fallback_offer(sub.pk, token, reason="x"))

        sub.refresh_from_db()
        self.assertEqual(sub.booking_token, token)
        self.assertIsNotNone(sub.fallback_offered_at)

    def test_booked_submission_is_not_reclaimed_by_the_sweep(self):
        from crush_lu.models import ScreeningSlot

        sub = self._submission("booked2")
        start = timezone.now() + timedelta(days=1)
        ScreeningSlot.objects.create(
            coach=self.coach,
            submission=sub,
            start_at=start,
            end_at=start + timedelta(minutes=30),
            status="booked",
        )

        with patch(SEND, return_value=1) as send:
            self._sweep()

        send.assert_not_called()

    def test_stale_claim_from_a_dead_worker_is_recovered(self):
        sub = self._submission("stale")
        with patch(SEND, return_value=1):
            self._sweep()
        sub.refresh_from_db()
        token = sub.booking_token
        # Simulate a crash between commit and send: claimed, never sent.
        ProfileSubmission.objects.filter(pk=sub.pk).update(
            fallback_offer_sent_at=None,
            fallback_offer_claimed_at=timezone.now() - timedelta(hours=1),
        )

        with patch(SEND, return_value=1) as send:
            body = self._sweep().json()

        sub.refresh_from_db()
        self.assertEqual(body["processed"], 1)
        send.assert_called_once()
        self.assertEqual(sub.booking_token, token)
        self.assertIsNotNone(sub.fallback_offer_sent_at)
        self.assertEqual(sub.system_actions[-1]["type"], "fallback_claim_recovered")

    def test_fresh_unsent_claim_is_inside_its_lease(self):
        sub = self._submission("lease")
        with patch(SEND, return_value=1):
            self._sweep()
        ProfileSubmission.objects.filter(pk=sub.pk).update(
            fallback_offer_sent_at=None,
            fallback_offer_claimed_at=timezone.now() - timedelta(minutes=1),
        )

        with patch(SEND, return_value=1) as send:
            self._sweep()

        send.assert_not_called()

    def test_failed_recovery_keeps_the_original_token(self):
        sub = self._submission("recfail")
        with patch(SEND, return_value=1):
            self._sweep()
        sub.refresh_from_db()
        token = sub.booking_token
        ProfileSubmission.objects.filter(pk=sub.pk).update(
            fallback_offer_sent_at=None,
            fallback_offer_claimed_at=timezone.now() - timedelta(hours=1),
        )

        with patch(SEND, return_value=0):
            body = self._sweep().json()

        sub.refresh_from_db()
        self.assertEqual(body["failed"], 1)
        self.assertEqual(sub.booking_token, token)

    def test_revert_ignores_an_offer_that_changed_since_the_attempt(self):
        import uuid

        sub = self._submission("changed")
        with patch(SEND, return_value=1):
            self._sweep()
        sub.refresh_from_db()
        token = sub.booking_token

        self.assertFalse(revert_fallback_offer(sub.pk, uuid.uuid4(), reason="x"))
        sub.refresh_from_db()
        self.assertEqual(sub.booking_token, token)
        self.assertTrue(revert_fallback_offer(sub.pk, token, reason="x"))
