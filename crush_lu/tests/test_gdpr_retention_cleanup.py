"""Tests for the gdpr_retention_cleanup management command.

Covers the dry-run default (nothing deleted) and --apply (only rows past
their retention window are deleted; newer rows survive).

Run with: pytest crush_lu/tests/test_gdpr_retention_cleanup.py -v
"""
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from crush_lu.models import CrushProfile
from crush_lu.models.phone_otp import PhoneOTP
from crush_lu.models.profiles import CallAttempt, DailyUserActivity

User = get_user_model()


def make_user(email):
    user = User.objects.create_user(
        username=email, email=email, password="x", first_name="Member",
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth="1995-01-01",
        gender="M",
        location="Luxembourg",
        is_approved=True,
    )
    return user


class GdprRetentionCommandTests(TestCase):
    def setUp(self):
        self.user = make_user("retention@example.com")
        self.profile = self.user.crushprofile
        now = timezone.now()

        # PhoneOTP: one old (40d), one fresh.
        self.old_otp = PhoneOTP.objects.create(
            user=self.user,
            phone_number="+352111111111",
            code_hash="old-hash",
            expires_at=now - timedelta(days=40),
        )
        PhoneOTP.objects.filter(pk=self.old_otp.pk).update(
            created_at=now - timedelta(days=40)
        )
        self.new_otp = PhoneOTP.objects.create(
            user=self.user,
            phone_number="+352111111111",
            code_hash="new-hash",
            expires_at=now + timedelta(minutes=10),
        )

        # CallAttempt: one old (400d), one fresh.
        self.old_call = CallAttempt.objects.create(
            profile=self.profile, result="failed",
        )
        CallAttempt.objects.filter(pk=self.old_call.pk).update(
            attempt_date=now - timedelta(days=400)
        )
        self.new_call = CallAttempt.objects.create(
            profile=self.profile, result="success",
        )

        # DailyUserActivity: one old (120d), one fresh.
        today = timezone.localdate()
        self.old_activity = DailyUserActivity.objects.create(
            user=self.user, activity_date=today - timedelta(days=120),
        )
        self.new_activity = DailyUserActivity.objects.create(
            user=self.user, activity_date=today,
        )

    def test_dry_run_deletes_nothing(self):
        out = StringIO()
        call_command("gdpr_retention_cleanup", stdout=out)

        self.assertIn("DRY-RUN", out.getvalue())
        self.assertTrue(PhoneOTP.objects.filter(pk=self.old_otp.pk).exists())
        self.assertTrue(CallAttempt.objects.filter(pk=self.old_call.pk).exists())
        self.assertTrue(
            DailyUserActivity.objects.filter(pk=self.old_activity.pk).exists()
        )

    def test_apply_deletes_only_expired_rows(self):
        out = StringIO()
        call_command("gdpr_retention_cleanup", **{"apply": True}, stdout=out)

        # Old rows gone.
        self.assertFalse(PhoneOTP.objects.filter(pk=self.old_otp.pk).exists())
        self.assertFalse(CallAttempt.objects.filter(pk=self.old_call.pk).exists())
        self.assertFalse(
            DailyUserActivity.objects.filter(pk=self.old_activity.pk).exists()
        )
        # Fresh rows survive.
        self.assertTrue(PhoneOTP.objects.filter(pk=self.new_otp.pk).exists())
        self.assertTrue(CallAttempt.objects.filter(pk=self.new_call.pk).exists())
        self.assertTrue(
            DailyUserActivity.objects.filter(pk=self.new_activity.pk).exists()
        )

    def test_apply_respects_window_override(self):
        """--phone-otp-days 0 is a 0-day window: every OTP row predates the
        command's 'now', so all are purged, while the non-overridden
        categories (CallAttempt at its 365d default) are left untouched."""
        call_command(
            "gdpr_retention_cleanup",
            **{"apply": True, "phone_otp_days": 0},
            stdout=StringIO(),
        )
        # A 0-day window deletes all OTPs, fresh included (created < now).
        # (Under the pre-fix `opt or default` bug this fell back to 30d and
        # the fresh OTP survived — so this doubles as a regression guard.)
        self.assertFalse(PhoneOTP.objects.filter(pk=self.old_otp.pk).exists())
        self.assertFalse(PhoneOTP.objects.filter(pk=self.new_otp.pk).exists())
        # The OTP-only override left CallAttempt untouched.
        self.assertTrue(CallAttempt.objects.filter(pk=self.new_call.pk).exists())

    def test_zero_day_cli_override_is_honored(self):
        """A CLI 0-day window must purge by 'now', not fall back to the
        default. Regression: `options[...] or window` swallowed 0 and used
        the 365d default, silently leaving data an operator asked to purge.

        A 5-day-old CallAttempt survives the 365d default but must be
        deleted under a 0-day window — the discriminating case the existing
        override test can't catch (its rows are either 'now' or 400d old,
        which behave identically at 0d and 365d).
        """
        now = timezone.now()
        mid_call = CallAttempt.objects.create(
            profile=self.profile, result="failed",
        )
        CallAttempt.objects.filter(pk=mid_call.pk).update(
            attempt_date=now - timedelta(days=5)
        )

        call_command(
            "gdpr_retention_cleanup",
            **{"apply": True, "call_attempt_days": 0},
            stdout=StringIO(),
        )

        self.assertFalse(CallAttempt.objects.filter(pk=mid_call.pk).exists())
        self.assertFalse(CallAttempt.objects.filter(pk=self.old_call.pk).exists())
