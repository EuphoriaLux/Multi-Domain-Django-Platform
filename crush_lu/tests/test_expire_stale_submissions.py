"""
Tests for expiring stale pre-pivot coach-review submissions.

The verification pivot replaced the coach-review queue with self-serve
verification (LuxID or in-person at an event). Submissions left 'pending' or
'recontact_coach' from the old flow are closed out (status='expired') by the
expire_stale_submissions command; every user-facing surface must then treat
the user exactly like a fresh post-pivot pending signup — the "Verify your
identity" hero, no dead-end coach messaging, no navbar "needs action" flag.
"""

from datetime import date, datetime, timedelta, timezone as dt_tz
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.context_processors import crush_user_context
from crush_lu.models import CrushProfile
from crush_lu.models.profiles import (
    CrushCoach,
    ProfileSubmission,
    ScreeningSlot,
    UserDataConsent,
)

User = get_user_model()

# Comfortably before the default PIVOT_SWAP_AT cutoff (2026-07-11 21:10 UTC),
# so "days_ago" arithmetic in the tests is unambiguous either side of it.
STALE_DAYS = 60


def make_profile(username, verification_status="pending"):
    user = User.objects.create_user(
        username=f"{username}@example.com",
        email=f"{username}@example.com",
        password="pass123",
        first_name=username.capitalize(),
    )
    # The consent row is auto-created by a signal; the consent middleware
    # gates the dashboard until it is granted.
    UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1993, 3, 15),
        gender="F",
        location="Luxembourg City",
        verification_status=verification_status,
        is_active=True,
    )
    return user, profile


def make_submission(profile, status, days_ago=STALE_DAYS, **fields):
    """Create a submission back-dated by ``days_ago`` (submitted_at is
    auto_now_add, so it must be rewritten with a queryset update)."""
    submission = ProfileSubmission.objects.create(
        profile=profile, status=status, **fields
    )
    ProfileSubmission.objects.filter(pk=submission.pk).update(
        submitted_at=timezone.now() - timedelta(days=days_ago)
    )
    submission.refresh_from_db()
    return submission


def run_command(*args):
    out = StringIO()
    call_command("expire_stale_submissions", *args, stdout=out)
    return out.getvalue()


class ExpireStaleSubmissionsCommandTests(TestCase):
    def test_expires_stale_pending_and_recontact(self):
        _, p1 = make_profile("pending")
        _, p2 = make_profile("recontact")
        pending = make_submission(p1, "pending")
        recontact = make_submission(p2, "recontact_coach")

        output = run_command()

        pending.refresh_from_db()
        recontact.refresh_from_db()
        self.assertEqual(pending.status, "expired")
        self.assertEqual(recontact.status, "expired")
        self.assertIn("Expired 2 submission(s)", output)

        # Audit trail records the transition and its previous status.
        entry = recontact.system_actions[-1]
        self.assertEqual(entry["type"], "expired_to_self_serve")
        self.assertEqual(entry["details"]["previous_status"], "recontact_coach")
        # Profiles are untouched — the users stay in the pending funnel.
        p1.refresh_from_db()
        self.assertEqual(p1.verification_status, "pending")

    def test_skips_terminal_statuses_and_recent_submissions(self):
        _, p1 = make_profile("approved", verification_status="verified")
        _, p2 = make_profile("revision")
        _, p3 = make_profile("recent")
        approved = make_submission(p1, "approved")
        revision = make_submission(p2, "revision")
        # Submitted today — after the 2026-07-11 default cutoff.
        recent = make_submission(p3, "pending", days_ago=0)

        run_command()

        for submission in (approved, revision, recent):
            submission.refresh_from_db()
        self.assertEqual(approved.status, "approved")
        self.assertEqual(revision.status, "revision")
        self.assertEqual(recent.status, "pending")

    def test_skips_paused_submissions(self):
        _, profile = make_profile("paused")
        paused = make_submission(profile, "recontact_coach", is_paused=True)

        output = run_command()

        paused.refresh_from_db()
        self.assertEqual(paused.status, "recontact_coach")
        self.assertIn("skipped (paused): 1", output)

    def test_skips_submission_with_future_booked_slot(self):
        _, profile = make_profile("booked")
        submission = make_submission(profile, "pending")
        coach_user = User.objects.create_user(
            username="coach@example.com", email="coach@example.com", password="x"
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        ScreeningSlot.objects.create(
            coach=coach,
            submission=submission,
            status="booked",
            start_at=timezone.now() + timedelta(days=2),
            end_at=timezone.now() + timedelta(days=2, minutes=30),
        )

        output = run_command()

        submission.refresh_from_db()
        self.assertEqual(submission.status, "pending")
        self.assertIn("skipped (future booked slot): 1", output)

    def test_swap_day_submission_before_the_swap_is_included(self):
        """The default cutoff is the swap moment (21:10 UTC), not local
        midnight — submissions from earlier on swap day were still created
        under the old coach-review flow and must be cleaned up too."""
        _, p1 = make_profile("swapmorning")
        _, p2 = make_profile("swapevening")
        morning = make_submission(p1, "pending")
        evening = make_submission(p2, "pending")
        ProfileSubmission.objects.filter(pk=morning.pk).update(
            submitted_at=datetime(2026, 7, 11, 12, 0, tzinfo=dt_tz.utc)
        )
        ProfileSubmission.objects.filter(pk=evening.pk).update(
            submitted_at=datetime(2026, 7, 11, 22, 0, tzinfo=dt_tz.utc)
        )

        run_command()

        morning.refresh_from_db()
        evening.refresh_from_db()
        self.assertEqual(morning.status, "expired")
        self.assertEqual(evening.status, "pending")

    def test_past_booked_slot_does_not_block_expiry(self):
        _, profile = make_profile("pastslot")
        submission = make_submission(profile, "pending")
        coach_user = User.objects.create_user(
            username="coach2@example.com", email="coach2@example.com", password="x"
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        ScreeningSlot.objects.create(
            coach=coach,
            submission=submission,
            status="booked",
            start_at=timezone.now() - timedelta(days=30),
            end_at=timezone.now() - timedelta(days=30) + timedelta(minutes=30),
        )

        run_command()

        submission.refresh_from_db()
        self.assertEqual(submission.status, "expired")

    def test_unrelated_future_slot_does_not_shield_submission(self):
        """Regression: exclude() matches multiple conditions on a multi-valued
        relation against possibly *different* rows — a past booked slot plus a
        separate future non-booked slot must not shield the submission (and
        must not be silently dropped without appearing in the skip count)."""
        _, profile = make_profile("splitslots")
        submission = make_submission(profile, "pending")
        coach_user = User.objects.create_user(
            username="coach3@example.com", email="coach3@example.com", password="x"
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        ScreeningSlot.objects.create(
            coach=coach,
            submission=submission,
            status="booked",
            start_at=timezone.now() - timedelta(days=30),
            end_at=timezone.now() - timedelta(days=30) + timedelta(minutes=30),
        )
        ScreeningSlot.objects.create(
            coach=coach,
            submission=submission,
            status="available",
            start_at=timezone.now() + timedelta(days=2),
            end_at=timezone.now() + timedelta(days=2, minutes=30),
        )

        output = run_command()

        submission.refresh_from_db()
        self.assertEqual(submission.status, "expired")
        self.assertIn("skipped (future booked slot): 0", output)

    def test_dry_run_changes_nothing(self):
        _, profile = make_profile("dryrun")
        submission = make_submission(profile, "recontact_coach")

        output = run_command("--dry-run")

        submission.refresh_from_db()
        self.assertEqual(submission.status, "recontact_coach")
        self.assertIn("Dry run", output)
        self.assertIn("recontact_coach: 1", output)

    def test_custom_cutoff(self):
        _, profile = make_profile("cutoff")
        submission = make_submission(profile, "pending", days_ago=5)
        cutoff = (timezone.now() - timedelta(days=2)).date().isoformat()

        run_command("--before", cutoff)

        submission.refresh_from_db()
        self.assertEqual(submission.status, "expired")

    def test_custom_cutoff_accepts_datetime(self):
        _, profile = make_profile("dtcutoff")
        submission = make_submission(profile, "pending", days_ago=5)
        cutoff = (timezone.now() - timedelta(days=2)).isoformat()

        run_command("--before", cutoff)

        submission.refresh_from_db()
        self.assertEqual(submission.status, "expired")


# api_submission_status is defined in urls_crush.py at root level
# (language-neutral), so reversing it needs the crush urlconf explicitly.
@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ExpiredSubmissionUserExperienceTests(TestCase):
    """An expired submission must render exactly like no submission."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user, self.profile = make_profile("expired")
        self.submission = make_submission(self.profile, "expired")

    def test_navbar_context_ignores_expired_submission(self):
        request = self.factory.get("/en/dashboard/")
        request.user = self.user
        context = crush_user_context(request)

        self.assertNotIn("profile_submission", context)
        self.assertNotIn("profile_needs_action", context)

    def test_profile_submitted_renders_self_serve_hero(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("crush_lu:profile_submitted"))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["submission"])
        self.assertNotContains(response, "Coach Needs to Speak With You")

    def test_dashboard_treats_expired_as_no_submission(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("crush_lu:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["submission"])

    def test_api_submission_status_404s_for_expired_only(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("api_submission_status"))

        self.assertEqual(response.status_code, 404)

    def test_recontact_still_renders_old_messaging_until_expired(self):
        """Control: a live recontact submission keeps its coach messaging —
        only the expired transition switches the user to self-serve."""
        other_user, other_profile = make_profile("live")
        make_submission(other_profile, "recontact_coach")
        self.client.force_login(other_user)

        response = self.client.get(reverse("crush_lu:profile_submitted"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Coach Needs to Speak With You")


class ExpiredSubmissionResubmitTests(TestCase):
    """The edge-case resubmission path must not resurrect an expired row."""

    def test_get_or_create_pattern_creates_fresh_submission(self):
        _, profile = make_profile("resubmit")
        expired = make_submission(profile, "expired")

        # Same lookup the resubmission path in views.py uses.
        submission, created = ProfileSubmission.objects.exclude(
            status="expired"
        ).get_or_create(profile=profile, defaults={"status": "pending", "coach": None})

        self.assertTrue(created)
        self.assertNotEqual(submission.pk, expired.pk)
        self.assertEqual(submission.status, "pending")


class BulkExpireAdminActionTests(TestCase):
    def test_admin_action_expires_only_active_review_statuses(self):
        from django.contrib import admin as django_admin
        from django.contrib.messages.storage.fallback import FallbackStorage

        from crush_lu.admin.profiles import ProfileSubmissionAdmin

        _, p1 = make_profile("adminstale")
        _, p2 = make_profile("adminapproved", verification_status="verified")
        stale = make_submission(p1, "recontact_coach")
        approved = make_submission(p2, "approved")

        admin_user = User.objects.create_superuser(
            username="admin@example.com", email="admin@example.com", password="x"
        )
        factory = RequestFactory()
        request = factory.post("/admin/")
        request.user = admin_user
        request.session = self.client.session
        request._messages = FallbackStorage(request)

        model_admin = ProfileSubmissionAdmin(ProfileSubmission, django_admin.site)
        model_admin.bulk_expire_to_self_serve(
            request, ProfileSubmission.objects.filter(pk__in=[stale.pk, approved.pk])
        )

        stale.refresh_from_db()
        approved.refresh_from_db()
        self.assertEqual(stale.status, "expired")
        self.assertEqual(approved.status, "approved")
        self.assertEqual(stale.system_actions[-1]["type"], "expired_to_self_serve")
        self.assertEqual(stale.system_actions[-1]["actor"], f"admin:{admin_user.pk}")

    def test_admin_action_skips_paused_and_future_booked(self):
        """The admin action applies the same safety guards as the command:
        paused submissions and future booked screening calls are preserved."""
        from django.contrib import admin as django_admin
        from django.contrib.messages.storage.fallback import FallbackStorage

        from crush_lu.admin.profiles import ProfileSubmissionAdmin

        _, p1 = make_profile("adminpaused")
        _, p2 = make_profile("adminbooked")
        paused = make_submission(p1, "pending", is_paused=True)
        booked = make_submission(p2, "recontact_coach")
        coach_user = User.objects.create_user(
            username="coach4@example.com", email="coach4@example.com", password="x"
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        ScreeningSlot.objects.create(
            coach=coach,
            submission=booked,
            status="booked",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, minutes=30),
        )

        admin_user = User.objects.create_superuser(
            username="admin2@example.com", email="admin2@example.com", password="x"
        )
        factory = RequestFactory()
        request = factory.post("/admin/")
        request.user = admin_user
        request.session = self.client.session
        request._messages = FallbackStorage(request)

        model_admin = ProfileSubmissionAdmin(ProfileSubmission, django_admin.site)
        model_admin.bulk_expire_to_self_serve(
            request, ProfileSubmission.objects.filter(pk__in=[paused.pk, booked.pk])
        )

        paused.refresh_from_db()
        booked.refresh_from_db()
        self.assertEqual(paused.status, "pending")
        self.assertEqual(booked.status, "recontact_coach")


class ProfileReviewFormTests(TestCase):
    """Coaches must never be offered the system-only 'expired' status."""

    def test_expired_not_offered_and_rejected(self):
        from crush_lu.forms import ProfileReviewForm

        form = ProfileReviewForm()
        self.assertNotIn(
            "expired", [value for value, label in form.fields["status"].choices]
        )

        _, profile = make_profile("coachform")
        submission = make_submission(profile, "pending")
        form = ProfileReviewForm(
            data={"status": "expired", "coach_notes": "", "feedback_to_user": ""},
            instance=submission,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("status", form.errors)
