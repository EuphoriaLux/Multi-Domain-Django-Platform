"""SLA fallback sweep: claim under lock, send after, undo on failed send (#1196)."""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.api_admin_hybrid import _lock_sweep_chunk, revert_fallback_offer
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

    def test_stale_lease_recovery_is_claimed_before_older_fresh_breaches(self):
        from crush_lu.api_admin_hybrid import _sweep_candidates

        # A coach-initiated claim has a future deadline; breached work is older.
        breached = [self._submission(f"breach{i}") for i in range(3)]
        recovery = self._submission("recover")
        ProfileSubmission.objects.filter(pk=recovery.pk).update(
            sla_deadline=timezone.now() + timedelta(days=1),
            fallback_offered_at=timezone.now() - timedelta(hours=2),
            booking_token=uuid.uuid4(),
            booking_token_expires_at=timezone.now() + timedelta(days=2),
            fallback_offer_claimed_at=timezone.now() - timedelta(hours=1),
            fallback_offer_sent_at=None,
        )

        ordered = list(
            _sweep_candidates(timezone.now())
            .order_by("sweep_priority", "sla_deadline", "pk")
            .values_list("pk", flat=True)
        )

        self.assertEqual(ordered[0], recovery.pk)
        self.assertCountEqual(ordered[1:], [s.pk for s in breached])

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

    def test_submission_that_moved_on_after_claim_gets_no_email(self):
        import crush_lu.api_admin_hybrid as module

        for label, change in (
            ("paused", {"is_paused": True}),
            ("approved", {"status": "approved"}),
            ("completed", {"review_call_completed": True}),
        ):
            sub = self._submission(f"moved{label}")
            original = module.mark_fallback_offered

            def claim_then_change(s, *args, _change=change, _pk=sub.pk, **kwargs):
                result = original(s, *args, **kwargs)
                # A coach acts between the committed claim and the send.
                if s.pk == _pk:
                    ProfileSubmission.objects.filter(pk=_pk).update(**_change)
                return result

            with (
                patch.object(module, "mark_fallback_offered", claim_then_change),
                patch(SEND, return_value=1) as send,
            ):
                body = self._sweep().json()

            sub.refresh_from_db()
            send.assert_not_called()
            self.assertEqual(body["processed"], 0, label)
            self.assertIsNone(sub.fallback_offered_at, label)
            self.assertIsNone(sub.booking_token, label)

    def test_last_send_start_plus_worst_case_fits_the_caller_timeout(self):
        import re
        from pathlib import Path

        import crush_lu.api_admin_hybrid as module

        source = (
            Path(__file__).resolve().parents[2]
            / "azure-functions"
            / "hybrid-maintenance"
            / "function_app.py"
        ).read_text(encoding="utf-8")
        caller_timeout = int(re.search(r"timeout: int = (\d+)", source).group(1))

        # The documented caller timeout the constants assume is the real one.
        self.assertEqual(module.SLA_SWEEP_CALLER_TIMEOUT_SECONDS, caller_timeout)
        # No send may START so late that its worst case (30 s Graph send + up
        # to 20 s cold token) outlives the caller.
        self.assertLessEqual(
            module.SLA_SWEEP_SEND_BUDGET_SECONDS
            + module.SLA_SWEEP_WORST_CASE_SEND_SECONDS,
            caller_timeout - module.SLA_SWEEP_SAFETY_MARGIN_SECONDS,
        )

    def test_reclaim_with_an_expired_token_mints_a_fresh_one(self):
        sub = self._submission("expired")
        with patch(SEND, return_value=1):
            self._sweep()
        sub.refresh_from_db()
        old_token = sub.booking_token
        ProfileSubmission.objects.filter(pk=sub.pk).update(
            fallback_offer_sent_at=None,
            fallback_offer_claimed_at=timezone.now() - timedelta(hours=1),
            booking_token_expires_at=timezone.now() - timedelta(days=1),
        )

        with patch(SEND, return_value=1):
            self._sweep()

        sub.refresh_from_db()
        self.assertNotEqual(sub.booking_token, old_token)
        self.assertGreater(sub.booking_token_expires_at, timezone.now())

    def test_reclaim_keeps_a_still_valid_token(self):
        sub = self._submission("valid")
        with patch(SEND, return_value=1):
            self._sweep()
        sub.refresh_from_db()
        token = sub.booking_token
        ProfileSubmission.objects.filter(pk=sub.pk).update(
            fallback_offer_sent_at=None,
            fallback_offer_claimed_at=timezone.now() - timedelta(hours=1),
        )

        with patch(SEND, return_value=1):
            self._sweep()

        sub.refresh_from_db()
        self.assertEqual(sub.booking_token, token)

    def test_ambiguous_transport_failure_keeps_the_token_and_retries_same_link(self):
        import requests

        sub = self._submission("ambiguous")
        with patch(SEND, side_effect=requests.exceptions.ReadTimeout("slow")):
            body = self._sweep().json()

        sub.refresh_from_db()
        token = sub.booking_token
        self.assertEqual(
            (body["uncertain"], body["failed"], body["processed"]), (1, 0, 0)
        )
        self.assertIsNotNone(token)
        self.assertIsNone(sub.fallback_offer_sent_at)

        # Inside the lease nothing is re-sent; after it the SAME token is reused.
        with patch(SEND, return_value=1) as send:
            self._sweep()
        send.assert_not_called()
        ProfileSubmission.objects.filter(pk=sub.pk).update(
            fallback_offer_claimed_at=timezone.now() - timedelta(hours=1)
        )
        with patch(SEND, return_value=1) as send:
            body = self._sweep().json()
        sub.refresh_from_db()
        self.assertEqual(body["processed"], 1)
        self.assertEqual(sub.booking_token, token)
        self.assertIsNotNone(sub.fallback_offer_sent_at)

    def test_definite_http_failure_still_clears_the_token(self):
        sub = self._submission("definite")
        with patch(
            SEND, side_effect=Exception("Failed to send email via Graph API: HTTP 400")
        ):
            body = self._sweep().json()

        sub.refresh_from_db()
        self.assertEqual((body["failed"], body["uncertain"]), (1, 0))
        self.assertIsNone(sub.booking_token)

    def test_connect_timeout_is_a_definite_failure(self):
        import requests

        sub = self._submission("connecttimeout")
        with patch(SEND, side_effect=requests.exceptions.ConnectTimeout("nope")):
            body = self._sweep().json()

        sub.refresh_from_db()
        self.assertEqual((body["failed"], body["uncertain"]), (1, 0))
        self.assertIsNone(sub.booking_token)

    def _coach_offer(self, sub):
        UserDataConsent.objects.filter(user=self.coach.user).update(
            crushlu_consent_given=True
        )
        self.client.force_login(self.coach.user)
        return self.client.post(
            f"/en/coach/review/{sub.pk}/offer-booking/", HTTP_HOST="crush.lu"
        )

    def test_ambiguous_manual_offer_is_retried_by_the_sweep_before_the_sla(self):
        import requests

        sub = self._submission("manual")
        # Not breached, and the coach is outside the hybrid gate: only lease
        # recovery may pick this up.
        ProfileSubmission.objects.filter(pk=sub.pk).update(
            sla_deadline=timezone.now() + timedelta(hours=24)
        )
        with patch(SEND, side_effect=requests.exceptions.ReadTimeout("slow")):
            response = self._coach_offer(sub)
        self.assertEqual(response.status_code, 200)
        sub.refresh_from_db()
        token = sub.booking_token
        self.assertIsNotNone(token)
        self.assertIsNone(sub.fallback_offer_sent_at)

        CrushCoach.objects.filter(pk=self.coach.pk).update(
            hybrid_features_enabled=False
        )
        ProfileSubmission.objects.filter(pk=sub.pk).update(
            fallback_offer_claimed_at=timezone.now() - timedelta(hours=1)
        )
        with patch(SEND, return_value=1) as send:
            body = self._sweep().json()

        sub.refresh_from_db()
        self.assertEqual(body["processed"], 1)
        send.assert_called_once()
        self.assertEqual(sub.booking_token, token)
        self.assertIsNotNone(sub.fallback_offer_sent_at)

    def test_coach_repost_resends_an_unsent_claim_but_not_a_sent_one(self):
        import requests

        sub = self._submission("repost")
        with patch(SEND, side_effect=requests.exceptions.ReadTimeout("slow")):
            self._coach_offer(sub)
        sub.refresh_from_db()
        token = sub.booking_token

        with patch(SEND, return_value=1) as send:
            self._coach_offer(sub)
        send.assert_called_once()
        sub.refresh_from_db()
        self.assertEqual(sub.booking_token, token)
        self.assertIsNotNone(sub.fallback_offer_sent_at)

        with patch(SEND, return_value=1) as send:
            self._coach_offer(sub)
        send.assert_not_called()

    def test_failed_resend_of_an_unsent_claim_keeps_its_token(self):
        import requests

        sub = self._submission("resendfail")
        with patch(SEND, side_effect=requests.exceptions.ReadTimeout("slow")):
            self._coach_offer(sub)
        sub.refresh_from_db()
        token = sub.booking_token

        with patch(SEND, return_value=0):
            response = self._coach_offer(sub)

        sub.refresh_from_db()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(sub.booking_token, token)

    def test_sla_tick_command_recovers_a_stale_unsent_claim(self):
        from io import StringIO

        from django.core.management import call_command

        sub = self._submission("tick")
        with patch(SEND, return_value=1):
            self._sweep()
        sub.refresh_from_db()
        token = sub.booking_token
        # The coach is outside the hybrid gate and the SLA is not breached: only
        # the shared lease-recovery predicate may pick this up.
        CrushCoach.objects.filter(pk=self.coach.pk).update(
            hybrid_features_enabled=False
        )
        ProfileSubmission.objects.filter(pk=sub.pk).update(
            sla_deadline=timezone.now() + timedelta(hours=5),
            fallback_offer_sent_at=None,
            fallback_offer_claimed_at=timezone.now() - timedelta(hours=1),
        )

        out = StringIO()
        with patch(SEND, return_value=1) as send:
            call_command("sla_tick", stdout=out)

        sub.refresh_from_db()
        send.assert_called_once()
        self.assertEqual(sub.booking_token, token)
        self.assertIsNotNone(sub.fallback_offer_sent_at)
        self.assertIn("processed=1", out.getvalue())

    def test_lock_step_drops_a_row_claimed_after_it_was_picked(self):
        """A sweep that picked a row before another sweep claimed it must not
        lock it again: it would read as a recovered claim and re-send the mail.
        (SQLite cannot reproduce the timing; this pins the re-check itself.)"""
        picked = self._submission("raced")
        untouched = self._submission("free")
        now = timezone.now()
        ProfileSubmission.objects.filter(pk=picked.pk).update(
            fallback_offered_at=now,
            booking_token=uuid.uuid4(),
            booking_token_expires_at=now + timedelta(days=30),
            fallback_offer_claimed_at=now,
        )

        locked = _lock_sweep_chunk(now, set(), picked_pks=[picked.pk, untouched.pk])

        self.assertEqual(locked, [untouched.pk])
