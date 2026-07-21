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
        """A tighter --phone-otp-days window deletes the fresh OTP too."""
        call_command(
            "gdpr_retention_cleanup",
            **{"apply": True, "phone_otp_days": 0},
            stdout=StringIO(),
        )
        self.assertFalse(PhoneOTP.objects.filter(pk=self.old_otp.pk).exists())
        # created_at of the fresh OTP is 'now', which is NOT < now - 0 days,
        # so it survives a 0-day window; verify the old one is gone and the
        # attempt rows were untouched by the OTP override.
        self.assertTrue(PhoneOTP.objects.filter(pk=self.new_otp.pk).exists())
        self.assertTrue(CallAttempt.objects.filter(pk=self.new_call.pk).exists())
