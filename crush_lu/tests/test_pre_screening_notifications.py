"""Tests for Phase 5 — email + push invite/reminder/push flows."""
from __future__ import annotations

from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models import CrushCoach, CrushProfile, ProfileSubmission
from crush_lu.models.profiles import UserDataConsent
from crush_lu.pre_screening_notifications import (
    candidates_for_invite,
    candidates_for_reminder,
    send_pre_screening_invite_email,
    send_pre_screening_user_push,
)

User = get_user_model()


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    PRE_SCREENING_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class PreScreeningInviteEmailTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.user = User.objects.create_user(
            username="p5@example.com",
            email="p5@example.com",
            password="pw",
            first_name="Alex",
        )
        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg City",
            bio="b",
            phone_number="+352661234569",
            event_languages=["en"],
            is_approved=False,
            preferred_language="en",
        )
        self.submission = ProfileSubmission.objects.create(
            profile=self.profile, status="pending"
        )

    def test_invite_email_sent_once(self):
        self.assertTrue(send_pre_screening_invite_email(self.submission))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("3 minutes", mail.outbox[0].subject)
        # Idempotent — second call is a no-op thanks to cache dedup.
        self.assertFalse(send_pre_screening_invite_email(self.submission))
        self.assertEqual(len(mail.outbox), 1)

    def test_invite_skipped_when_pre_screening_already_submitted(self):
        self.submission.pre_screening_submitted_at = timezone.now()
        self.submission.save()
        self.assertFalse(send_pre_screening_invite_email(self.submission))
        self.assertEqual(len(mail.outbox), 0)

    def test_invite_skipped_when_submission_no_longer_pending(self):
        self.submission.status = "approved"
        self.submission.save()
        self.assertFalse(send_pre_screening_invite_email(self.submission))
        self.assertEqual(len(mail.outbox), 0)

    def test_reminder_email_is_separate_from_invite(self):
        self.assertTrue(send_pre_screening_invite_email(self.submission, reminder=False))
        self.assertTrue(send_pre_screening_invite_email(self.submission, reminder=True))
        self.assertEqual(len(mail.outbox), 2)
        subjects = {m.subject for m in mail.outbox}
        self.assertEqual(len(subjects), 2)  # two distinct subjects

    def test_third_reminder_never_fires(self):
        send_pre_screening_invite_email(self.submission, reminder=True)
        self.assertEqual(len(mail.outbox), 1)
        # Second reminder is idempotent-no-op
        self.assertFalse(send_pre_screening_invite_email(self.submission, reminder=True))
        self.assertEqual(len(mail.outbox), 1)


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    PRE_SCREENING_ENABLED=True,
)
class PreScreeningCandidateQueryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="p5b@example.com",
            email="p5b@example.com",
            password="pw",
            first_name="Alex",
        )
        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg City",
            bio="b",
            phone_number="+352661234570",
            event_languages=["en"],
            is_approved=False,
        )

    def _make_submission(self, *, hours_ago: float, submitted: bool = False,
                        status: str = "pending") -> ProfileSubmission:
        sub = ProfileSubmission.objects.create(
            profile=self.profile, status=status
        )
        ProfileSubmission.objects.filter(pk=sub.pk).update(
            submitted_at=timezone.now() - timedelta(hours=hours_ago)
        )
        sub.refresh_from_db()
        if submitted:
            sub.pre_screening_submitted_at = timezone.now()
            sub.save(update_fields=["pre_screening_submitted_at"])
        return sub

    def test_invite_candidate_requires_one_hour_age(self):
        fresh = self._make_submission(hours_ago=0.5)
        aged = self._make_submission(hours_ago=2)
        ids = [s.id for s in candidates_for_invite()]
        self.assertIn(aged.id, ids)
        self.assertNotIn(fresh.id, ids)

    def test_reminder_candidate_requires_twenty_four_hours(self):
        # Override existing submissions so only the ones we want remain eligible.
        day_old = self._make_submission(hours_ago=25)
        half_day = self._make_submission(hours_ago=12)
        ids = [s.id for s in candidates_for_reminder()]
        self.assertIn(day_old.id, ids)
        self.assertNotIn(half_day.id, ids)

    def test_candidate_queries_exclude_already_submitted(self):
        done = self._make_submission(hours_ago=2, submitted=True)
        pending = self._make_submission(hours_ago=2)
        invite_ids = [s.id for s in candidates_for_invite()]
        self.assertIn(pending.id, invite_ids)
        self.assertNotIn(done.id, invite_ids)


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    PRE_SCREENING_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class PreScreeningCommandTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.user = User.objects.create_user(
            username="cmd@example.com", email="cmd@example.com", password="pw",
            first_name="Sam",
        )
        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg City",
            bio="b",
            phone_number="+352661234571",
            event_languages=["en"],
            is_approved=False,
        )
        self.submission = ProfileSubmission.objects.create(
            profile=self.profile, status="pending"
        )
        ProfileSubmission.objects.filter(pk=self.submission.pk).update(
            submitted_at=timezone.now() - timedelta(hours=2)
        )

    def test_command_sends_invite(self):
        out = StringIO()
        call_command("send_pre_screening_invites", "--only=invite", stdout=out)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("sent=", out.getvalue())

    def test_command_dry_run_sends_nothing(self):
        call_command("send_pre_screening_invites", "--dry-run")
        self.assertEqual(len(mail.outbox), 0)


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    PRE_SCREENING_ENABLED=False,
)
class PreScreeningCommandFlagOffTests(TestCase):
    def test_command_bails_when_flag_off(self):
        out = StringIO()
        call_command("send_pre_screening_invites", stdout=out)
        self.assertIn("PRE_SCREENING_ENABLED is off", out.getvalue())


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    PRE_SCREENING_ENABLED=True,
)
class PreScreeningPushTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="push@example.com", email="push@example.com", password="pw"
        )
        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg City",
            bio="b",
            phone_number="+352661234572",
            event_languages=["en"],
            is_approved=False,
        )
        self.submission = ProfileSubmission.objects.create(
            profile=self.profile, status="pending"
        )

    @patch("crush_lu.push_notifications.send_push_notification")
    def test_push_sends_once(self, mock_send):
        mock_send.return_value = {"success": 1, "failed": 0}
        self.assertTrue(send_pre_screening_user_push(self.submission))
        self.assertEqual(mock_send.call_count, 1)
        # Second call dedupes via cache.
        self.assertFalse(send_pre_screening_user_push(self.submission))
        self.assertEqual(mock_send.call_count, 1)

    @patch("crush_lu.push_notifications.send_push_notification")
    def test_push_skipped_after_submission(self, mock_send):
        self.submission.pre_screening_submitted_at = timezone.now()
        self.submission.save()
        self.assertFalse(send_pre_screening_user_push(self.submission))
        mock_send.assert_not_called()
