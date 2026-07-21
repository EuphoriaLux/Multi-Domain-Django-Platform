"""
Tests for profile completion reminder emails (send_profile_reminders).

Covers the eligibility windows in get_users_needing_reminder, the
24h -> 72h -> 7d chain requirement, the skip rules (unsubscribed,
already pending/verified), and the sender identity: batch runs pass
request=None, and send_profile_incomplete_reminder must pin
domain='crush.lu' so the email is sent from the Crush.lu sender instead
of the powerup.lu fallback.
"""

from datetime import date, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.email_helpers import (
    get_users_needing_reminder,
    send_profile_incomplete_reminder,
)
from crush_lu.models import CrushProfile, EmailPreference, ProfileReminder

User = get_user_model()


def make_incomplete_user(
    username,
    created_hours_ago,
    completion_status="step2",
    verification_status="incomplete",
    preferred_language="en",
):
    user = User.objects.create_user(
        username=f"{username}@example.com",
        email=f"{username}@example.com",
        password="pass123",
        first_name=username.capitalize(),
    )
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1993, 3, 15),
        gender="F",
        location="Luxembourg City",
        completion_status=completion_status,
        verification_status=verification_status,
        preferred_language=preferred_language,
    )
    # created_at is auto_now_add — backdate it via queryset update
    CrushProfile.objects.filter(pk=profile.pk).update(
        created_at=timezone.now() - timedelta(hours=created_hours_ago)
    )
    return user


class GetUsersNeedingReminderTests(TestCase):
    def test_24h_window_covers_24_to_72h_incomplete_profiles(self):
        # 48h-wide window: a signup missed by one daily tick (send-limit
        # spike or a transient email failure) is retried on the next run
        # instead of ageing out. 24h < created <= 72h is eligible.
        early = make_incomplete_user("inwindow", created_hours_ago=30)
        late = make_incomplete_user("stillinwindow", created_hours_ago=60)
        make_incomplete_user("tooearly", created_hours_ago=10)
        make_incomplete_user("toolate", created_hours_ago=80)

        eligible = get_users_needing_reminder("24h")
        self.assertEqual(set(eligible), {early, late})

    def test_24h_excludes_users_already_reminded(self):
        user = make_incomplete_user("reminded", created_hours_ago=30)
        ProfileReminder.objects.create(user=user, reminder_type="24h")

        self.assertNotIn(user, get_users_needing_reminder("24h"))

    def test_72h_requires_prior_24h_reminder(self):
        user = make_incomplete_user("chain72", created_hours_ago=80)

        self.assertNotIn(user, get_users_needing_reminder("72h"))

        ProfileReminder.objects.create(user=user, reminder_type="24h")
        self.assertIn(user, get_users_needing_reminder("72h"))

    def test_7d_requires_prior_72h_reminder(self):
        user = make_incomplete_user("chain7d", created_hours_ago=180)

        self.assertNotIn(user, get_users_needing_reminder("7d"))

        ProfileReminder.objects.create(user=user, reminder_type="72h")
        self.assertIn(user, get_users_needing_reminder("7d"))


class SendProfileIncompleteReminderTests(TestCase):
    def test_batch_send_uses_crush_sender_and_records_reminder(self):
        user = make_incomplete_user("batch", created_hours_ago=30)

        result = send_profile_incomplete_reminder(user, "24h", request=None)

        self.assertTrue(result)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        # request=None must NOT fall back to the powerup.lu sender config
        self.assertTrue(
            email.from_email.endswith("@crush.lu"),
            f"expected a crush.lu sender, got {email.from_email!r}",
        )
        self.assertEqual(email.recipients(), [user.email])
        self.assertIn("https://crush.lu/", email.body)
        self.assertTrue(
            ProfileReminder.objects.filter(user=user, reminder_type="24h").exists()
        )

    def test_de_72h_heading_is_grammatical(self):
        """The 72h heading concatenates "Your profile is" + a fragment; the DE
        fragment must read as a complement of "ist" ("noch nicht erstellt"),
        not as a second conjugated verb ("ist warten/wartet darauf ...")."""
        user = make_incomplete_user(
            "german",
            created_hours_ago=80,
            completion_status="not_started",
            preferred_language="de",
        )
        ProfileReminder.objects.create(user=user, reminder_type="24h")

        self.assertTrue(send_profile_incomplete_reminder(user, "72h", request=None))
        body = mail.outbox[0].body
        self.assertIn("Dein Profil ist noch nicht erstellt", body)
        self.assertNotIn("ist warten", body)
        self.assertNotIn("ist wartet", body)

    def test_existing_reminder_row_blocks_duplicate(self):
        """The admin panel calls this helper directly, so it must skip a
        reminder a concurrent path already recorded rather than re-send it
        (Codex P2)."""
        user = make_incomplete_user("dupe", created_hours_ago=30)
        ProfileReminder.objects.create(user=user, reminder_type="24h")

        result = send_profile_incomplete_reminder(user, "24h", request=None)

        self.assertFalse(result)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(
            ProfileReminder.objects.filter(
                user=user, reminder_type="24h"
            ).count(),
            1,
        )

    def test_in_flight_claim_blocks_duplicate(self):
        """A per-user claim held by a concurrent send closes the
        send-before-insert window so the second path doesn't also deliver."""
        from django.core.cache import cache

        user = make_incomplete_user("inflight", created_hours_ago=30)
        key = f"profile_reminder_claim:{user.id}:24h"
        cache.add(key, "1", 300)  # simulate an in-progress send from another path
        try:
            result = send_profile_incomplete_reminder(user, "24h", request=None)
        finally:
            cache.delete(key)

        self.assertFalse(result)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(ProfileReminder.objects.filter(user=user).exists())

    def test_failed_send_leaves_no_row_and_releases_claim(self):
        """A failed delivery records no reminder (so the user is retried, not
        dropped) and frees the claim."""
        from unittest.mock import patch
        from django.core.cache import cache

        user = make_incomplete_user("failsend", created_hours_ago=30)
        key = f"profile_reminder_claim:{user.id}:24h"

        with patch("crush_lu.email_helpers.send_domain_email", return_value=0):
            result = send_profile_incomplete_reminder(user, "24h", request=None)

        self.assertFalse(result)
        self.assertFalse(ProfileReminder.objects.filter(user=user).exists())
        # Claim was released: a genuine retry can re-acquire it.
        self.assertTrue(cache.add(key, "1", 1))
        cache.delete(key)

    def test_unsubscribed_user_is_skipped(self):
        user = make_incomplete_user("optout", created_hours_ago=30)
        prefs = EmailPreference.get_or_create_for_user(user)
        prefs.email_profile_updates = False
        prefs.save()

        result = send_profile_incomplete_reminder(user, "24h", request=None)

        self.assertFalse(result)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(ProfileReminder.objects.filter(user=user).exists())

    def test_pending_and_verified_profiles_are_skipped(self):
        for i, status in enumerate(("pending", "verified")):
            user = make_incomplete_user(
                f"already{i}", created_hours_ago=30, verification_status=status
            )
            self.assertFalse(
                send_profile_incomplete_reminder(user, "24h", request=None)
            )
        self.assertEqual(len(mail.outbox), 0)


class SendProfileRemindersCommandTests(TestCase):
    def test_dry_run_sends_nothing(self):
        make_incomplete_user("dryrun", created_hours_ago=30)

        out = StringIO()
        call_command("send_profile_reminders", "--dry-run", stdout=out)

        self.assertIn("[DRY RUN]", out.getvalue())
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(ProfileReminder.objects.count(), 0)

    def test_limit_caps_emails_per_run(self):
        make_incomplete_user("limita", created_hours_ago=30)
        make_incomplete_user("limitb", created_hours_ago=30)

        out = StringIO()
        call_command("send_profile_reminders", "--limit=1", stdout=out)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(ProfileReminder.objects.count(), 1)

    @override_settings(
        PROFILE_REMINDER_TIMING={
            # Deliberately overlapping windows so one user sits in both the
            # 24h and 72h stages at once — the boundary case Codex flagged.
            "24h": {"min_hours": 24, "max_hours": 100},
            "72h": {"min_hours": 50, "max_hours": 120},
            "7d": {"min_hours": 168, "max_hours": 216},
        }
    )
    def test_no_double_send_across_adjacent_stages_in_one_run(self):
        """A user eligible for two adjacent stages in the same sweep must get
        only the earlier stage's email — the later stage is evaluated first,
        before this run creates the prerequisite row (Codex P2)."""
        user = make_incomplete_user("overlap", created_hours_ago=60)

        call_command("send_profile_reminders", stdout=StringIO())

        types = set(
            ProfileReminder.objects.filter(user=user).values_list(
                "reminder_type", flat=True
            )
        )
        self.assertEqual(types, {"24h"})
        self.assertEqual(len(mail.outbox), 1)

    def test_concurrent_sweep_is_skipped(self):
        """A second sweep started while one holds the lock must no-op, not
        re-send to the same users before the first records its rows (Codex P2)."""
        from crush_lu.management.commands.send_profile_reminders import (
            SWEEP_LOCK_KEY,
        )
        from django.core.cache import cache

        make_incomplete_user("locked", created_hours_ago=30)
        cache.add(SWEEP_LOCK_KEY, "held", 600)  # simulate an in-progress sweep
        try:
            out = StringIO()
            call_command("send_profile_reminders", stdout=out)
            self.assertIn("in progress", out.getvalue().lower())
            self.assertEqual(len(mail.outbox), 0)
            self.assertEqual(ProfileReminder.objects.count(), 0)
        finally:
            cache.delete(SWEEP_LOCK_KEY)
