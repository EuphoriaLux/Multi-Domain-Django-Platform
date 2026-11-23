"""Verification nudges for submitted ("pending") members.

Covers the day 1 / 3 / 7 reminder eligibility, the one-off legacy catch-up,
the send path (email content, dedup, skip rules), the daily command, and the
"get verified" page listing the next entry events.
"""

from datetime import date, timedelta
from io import StringIO

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone

from crush_lu.models import (
    CrushProfile,
    EmailPreference,
    MeetupEvent,
    ProfileReminder,
    ProfileSubmission,
)
from crush_lu.models.profiles import UserDataConsent
from crush_lu.services import verification_nudges as nudges

User = get_user_model()


def make_pending_user(username, submitted_hours_ago=30, lang="en", **profile_kwargs):
    user = User.objects.create_user(
        username=f"{username}@example.com",
        email=f"{username}@example.com",
        password="pass123",
        first_name=username.capitalize(),
    )
    defaults = dict(
        date_of_birth=date(1991, 5, 2),
        gender="F",
        location="Luxembourg City",
        completion_status="submitted",
        verification_status="pending",
        phone_verified=True,
        preferred_language=lang,
        submitted_for_verification_at=(
            None
            if submitted_hours_ago is None
            else timezone.now() - timedelta(hours=submitted_hours_ago)
        ),
    )
    defaults.update(profile_kwargs)
    CrushProfile.objects.create(user=user, **defaults)
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return user


def make_entry_event(days_ahead=5, **kwargs):
    defaults = dict(
        title="Entry Mixer",
        description="An evening for new members.",
        event_type="mixer",
        date_time=timezone.now() + timedelta(days=days_ahead),
        location="Luxembourg City",
        address="1 Rue de la Gare, Luxembourg",
        max_participants=20,
        registration_deadline=timezone.now() + timedelta(days=days_ahead - 1),
        registration_fee=0,
        is_published=True,
        profile_requirement="completed",
    )
    defaults.update(kwargs)
    return MeetupEvent.objects.create(**defaults)


def record(user, reminder_type, hours_ago):
    row = ProfileReminder.objects.create(user=user, reminder_type=reminder_type)
    ProfileReminder.objects.filter(pk=row.pk).update(
        sent_at=timezone.now() - timedelta(hours=hours_ago)
    )


class EligibilityTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_v24h_window(self):
        due = make_pending_user("due", submitted_hours_ago=30)
        make_pending_user("early", submitted_hours_ago=10)
        make_pending_user("stale", submitted_hours_ago=200)
        self.assertEqual(
            set(nudges.get_users_needing_verification_reminder("v24h")), {due}
        )

    def test_skip_rules(self):
        make_pending_user("verified", verification_status="verified")
        make_pending_user("incomplete", verification_status="incomplete")
        no_consent = make_pending_user("noconsent")
        UserDataConsent.objects.filter(user=no_consent).update(
            crushlu_consent_given=False
        )
        banned = make_pending_user("banned")
        UserDataConsent.objects.filter(user=banned).update(crushlu_banned=True)
        luxid = make_pending_user("luxid")
        SocialAccount.objects.create(user=luxid, provider="luxid", uid="lux-1")
        coach_path = make_pending_user("coachpath")
        ProfileSubmission.objects.create(
            profile=coach_path.crushprofile, status="pending"
        )
        make_pending_user("inactive", is_active=False)
        self.assertEqual(
            list(nudges.get_users_needing_verification_reminder("v24h")), []
        )

    def test_v72h_needs_v24h_sent_48h_earlier(self):
        spaced = make_pending_user("spaced", submitted_hours_ago=100)
        record(spaced, "v24h", hours_ago=60)
        too_soon = make_pending_user("toosoon", submitted_hours_ago=100)
        record(too_soon, "v24h", hours_ago=20)
        make_pending_user("nofirst", submitted_hours_ago=100)
        self.assertEqual(
            set(nudges.get_users_needing_verification_reminder("v72h")), {spaced}
        )

    def test_legacy_only_without_timestamp_and_never_in_sequence(self):
        legacy = make_pending_user("legacy", submitted_hours_ago=None)
        make_pending_user("new", submitted_hours_ago=30)
        self.assertEqual(list(nudges.get_legacy_pending_users()), [legacy])
        self.assertNotIn(legacy, nudges.get_users_needing_verification_reminder("v24h"))

        record(legacy, "vlegacy", hours_ago=1)
        self.assertEqual(list(nudges.get_legacy_pending_users()), [])


class SendTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()

    def test_sends_with_events_and_records_once(self):
        user = make_pending_user("anna")
        event = make_entry_event()
        make_entry_event(title="Members only", profile_requirement="approved")

        self.assertTrue(nudges.send_verification_reminder(user, "v24h"))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["anna@example.com"])
        self.assertEqual(message.subject, "One last step: get verified on Crush.lu")
        html = message.alternatives[0][0]
        self.assertIn("/en/id/verify/", html)
        self.assertIn(f"/en/events/{event.id}/", html)
        self.assertIn("Entry Mixer", html)
        self.assertNotIn("Members only", html)
        self.assertTrue(
            ProfileReminder.objects.filter(user=user, reminder_type="v24h").exists()
        )

        self.assertFalse(nudges.send_verification_reminder(user, "v24h"))
        self.assertEqual(len(mail.outbox), 1)

    def test_german_member_gets_german_email(self):
        user = make_pending_user("lena", lang="de")
        nudges.send_verification_reminder(user, "v24h")
        self.assertIn("verifizieren", mail.outbox[0].subject)
        self.assertIn("/de/id/verify/", mail.outbox[0].alternatives[0][0])

    def test_skips_verified_and_unsubscribed(self):
        verified = make_pending_user("ver", verification_status="verified")
        self.assertFalse(nudges.send_verification_reminder(verified, "v24h"))
        unsub = make_pending_user("unsub")
        prefs = EmailPreference.get_or_create_for_user(unsub)
        prefs.unsubscribed_all = True
        prefs.save()
        self.assertFalse(nudges.send_verification_reminder(unsub, "v24h"))
        self.assertEqual(mail.outbox, [])


class CommandTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()

    def test_daily_run_sends_sequence_but_not_legacy(self):
        due = make_pending_user("due", submitted_hours_ago=30)
        make_pending_user("legacy", submitted_hours_ago=None)
        out = StringIO()
        call_command("send_verification_reminders", stdout=out)
        self.assertEqual([m.to for m in mail.outbox], [[due.email]])
        self.assertIn("sent 1", out.getvalue())

    def test_one_reminder_per_run(self):
        # Eligible for v24h now; v72h must wait for a later run.
        user = make_pending_user("late", submitted_hours_ago=100)
        call_command("send_verification_reminders", stdout=StringIO())
        call_command("send_verification_reminders", stdout=StringIO())
        self.assertEqual(
            list(
                ProfileReminder.objects.filter(user=user).values_list(
                    "reminder_type", flat=True
                )
            ),
            ["v24h"],
        )

    def test_legacy_dry_run_sends_nothing(self):
        make_pending_user("legacy", submitted_hours_ago=None)
        out = StringIO()
        call_command(
            "send_verification_reminders", legacy=True, dry_run=True, stdout=out
        )
        self.assertIn("vlegacy", out.getvalue())
        self.assertEqual(mail.outbox, [])
        self.assertFalse(ProfileReminder.objects.exists())

    def test_legacy_run_sends_catch_up(self):
        legacy = make_pending_user("legacy", submitted_hours_ago=None)
        call_command("send_verification_reminders", legacy=True, stdout=StringIO())
        self.assertEqual([m.to for m in mail.outbox], [[legacy.email]])
        self.assertTrue(
            ProfileReminder.objects.filter(
                user=legacy, reminder_type="vlegacy"
            ).exists()
        )


class EntryEventsTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_upcoming_entry_events_filters(self):
        first = make_entry_event(days_ahead=3)
        second = make_entry_event(days_ahead=6)
        make_entry_event(days_ahead=9)
        make_entry_event(title="Approved", profile_requirement="approved")
        make_entry_event(title="Private", is_private_invitation=True)
        make_entry_event(title="Cancelled", is_cancelled=True)
        make_entry_event(title="Draft", is_published=False)
        make_entry_event(
            title="Closed",
            registration_deadline=timezone.now() - timedelta(hours=1),
        )
        self.assertEqual(nudges.upcoming_entry_events(), [first, second])

    def test_get_verified_page_lists_entry_events(self):
        user = make_pending_user("page")
        event = make_entry_event()
        client = Client()
        client.force_login(user)
        response = client.get("/en/id/verify/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("data-entry-events", html)
        self.assertIn(f'href="/en/events/{event.id}/"', html)
        self.assertIn("/en/events/?entry=1", html)
