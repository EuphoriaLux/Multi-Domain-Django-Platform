"""2026-11 bot wave, part 2: logins to unverified accounts re-sent the
confirmation email over and over.

* ACCOUNT_RATE_LIMITS caps confirmation emails at 3 per address per day (on
  top of allauth's 1 per 3 minutes).
* ``purge_unverified_signups`` deletes accounts that never got past email
  confirmation (dry-run by default).
"""

from __future__ import annotations

import time
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from allauth.account.internal.flows.email_verification import (
    consume_email_verification_rate_limit,
)
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from crush_lu.models import CrushProfile, EventRegistration, MeetupEvent

User = get_user_model()

LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-unverified-signup-purge",
    }
}


@override_settings(CACHES=LOCMEM)
class ConfirmationEmailDailyCapTests(TestCase):
    def setUp(self):
        cache.clear()
        self.rf = RequestFactory()

    def _send_allowed(self):
        request = self.rf.post("/", REMOTE_ADDR="203.0.113.7")
        return consume_email_verification_rate_limit(request, "victim@example.com")

    def test_three_per_day_then_skipped(self):
        start = time.time()
        allowed = []
        for i in range(5):
            # Ten minutes apart: past the 3-minute cooldown every time.
            with patch(
                "allauth.core.internal.ratelimit.time.time",
                return_value=start + i * 600,
            ):
                allowed.append(self._send_allowed())
        self.assertEqual(allowed, [True, True, True, False, False])

    def test_cooldown_still_applies(self):
        self.assertTrue(self._send_allowed())
        self.assertFalse(self._send_allowed())


class PurgeUnverifiedSignupsTests(TestCase):
    SINCE = "2026-11-19T17:00:00Z"

    def _user(self, email, joined=None, verified=False, **extra):
        user = User.objects.create_user(
            username=email, email=email, password="pw-12345!", **extra
        )
        User.objects.filter(pk=user.pk).update(
            date_joined=joined or timezone.now(), last_login=None
        )
        EmailAddress.objects.create(
            user=user, email=email, verified=verified, primary=True
        )
        return user

    def _run(self, *args):
        out = StringIO()
        call_command(
            "purge_unverified_signups", "--since", self.SINCE, *args, stdout=out
        )
        return out.getvalue()

    def test_dry_run_reports_but_keeps(self):
        self._user("bot1@gmail.com")
        self._user("bot2@yahoo.com")
        output = self._run()
        self.assertIn("2 unverified signup(s)", output)
        self.assertIn("gmail.com", output)
        self.assertIn("b***@gmail.com", output)
        self.assertNotIn("bot1@gmail.com", output)
        self.assertIn("Dry run", output)
        self.assertEqual(User.objects.count(), 2)

    def test_apply_deletes_only_unverified_profileless_signups(self):
        junk = self._user("bot@gmail.com")
        verified = self._user("real@gmail.com", verified=True)
        old = self._user("old@gmail.com", joined=timezone.now() - timedelta(days=400))
        staff = self._user("staff@gmail.com", is_staff=True)
        logged_in = self._user("li@gmail.com")
        User.objects.filter(pk=logged_in.pk).update(last_login=timezone.now())
        with_profile = self._user("profile@gmail.com")
        CrushProfile.objects.create(user=with_profile)
        social = self._user("social@gmail.com")
        SocialAccount.objects.create(user=social, provider="google", uid="1")
        guest = self._user("guest@gmail.com")
        event = MeetupEvent.objects.create(
            title="Mixer",
            description="x",
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=7),
            location="Luxembourg",
            address="1 Test Street",
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=5),
        )
        EventRegistration.objects.create(event=event, user=guest)

        output = self._run("--apply")

        self.assertIn("Deleted 1 account(s).", output)
        self.assertFalse(User.objects.filter(pk=junk.pk).exists())
        for kept in (verified, old, staff, logged_in, with_profile, social, guest):
            self.assertTrue(User.objects.filter(pk=kept.pk).exists(), kept.email)

    def test_until_bounds_the_window(self):
        self._user("bot@gmail.com")
        output = self._run("--until", "2026-11-19T18:00:00Z")
        self.assertIn("0 unverified signup(s)", output)
