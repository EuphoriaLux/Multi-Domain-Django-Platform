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

# A fixed timestamp comfortably before the default PIVOT_SWAP_AT cutoff
# (2026-07-11 21:10 UTC). Fixed rather than now-relative so the "stale"
# fixtures stay on the pre-pivot side of the fixed cutoff no matter when the
# suite runs.
STALE_SUBMITTED_AT = datetime(2026, 5, 1, 12, 0, tzinfo=dt_tz.utc)


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


def make_submission(profile, status, days_ago=None, **fields):
    """Create a submission back-dated to the fixed pre-pivot
    STALE_SUBMITTED_AT, or by ``days_ago`` from now when given
    (submitted_at is auto_now_add, so it must be rewritten with a
    queryset update)."""
    submission = ProfileSubmission.objects.create(
        profile=profile, status=status, **fields
    )
    submitted_at = (
        timezone.now() - timedelta(days=days_ago)
        if days_ago is not None
        else STALE_SUBMITTED_AT
    )
    ProfileSubmission.objects.filter(pk=submission.pk).update(
        submitted_at=submitted_at
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
        self.assertIn("skipped (active/future booked slot): 1", output)

    def test_skips_submission_with_booked_slot_in_progress(self):
        """A slot stays 'booked' until the coach completes it — a call
        happening right now (start_at past, end_at future) must protect the
        submission from being expired mid-call."""
        _, profile = make_profile("midcall")
        submission = make_submission(profile, "pending")
        coach_user = User.objects.create_user(
            username="coach5@example.com", email="coach5@example.com", password="x"
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        ScreeningSlot.objects.create(
            coach=coach,
            submission=submission,
            status="booked",
            start_at=timezone.now() - timedelta(minutes=10),
            end_at=timezone.now() + timedelta(minutes=20),
        )

        output = run_command()

        submission.refresh_from_db()
        self.assertEqual(submission.status, "pending")
        self.assertIn("skipped (active/future booked slot): 1", output)

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
        self.assertIn("skipped (active/future booked slot): 0", output)

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

    def test_skips_submission_with_completed_screening_call(self):
        """A completed screening call means the row sits on the admin's
        'Ready to Approve' path — expiring it would discard the call."""
        _, profile = make_profile("calldone")
        submission = make_submission(profile, "pending", review_call_completed=True)

        output = run_command()

        submission.refresh_from_db()
        self.assertEqual(submission.status, "pending")
        self.assertIn("skipped (completed screening call): 1", output)


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

    def test_admin_action_skips_booked_slot_in_progress(self):
        """Parity with the command: a booked slot that started but hasn't
        ended protects the row from the bulk expire action too."""
        from django.contrib import admin as django_admin
        from django.contrib.messages.storage.fallback import FallbackStorage

        from crush_lu.admin.profiles import ProfileSubmissionAdmin

        _, profile = make_profile("adminmidcall")
        submission = make_submission(profile, "pending")
        coach_user = User.objects.create_user(
            username="coach6@example.com", email="coach6@example.com", password="x"
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        ScreeningSlot.objects.create(
            coach=coach,
            submission=submission,
            status="booked",
            start_at=timezone.now() - timedelta(minutes=10),
            end_at=timezone.now() + timedelta(minutes=20),
        )

        admin_user = User.objects.create_superuser(
            username="admin5@example.com", email="admin5@example.com", password="x"
        )
        factory = RequestFactory()
        request = factory.post("/admin/")
        request.user = admin_user
        request.session = self.client.session
        request._messages = FallbackStorage(request)

        model_admin = ProfileSubmissionAdmin(ProfileSubmission, django_admin.site)
        model_admin.bulk_expire_to_self_serve(
            request, ProfileSubmission.objects.filter(pk=submission.pk)
        )

        submission.refresh_from_db()
        self.assertEqual(submission.status, "pending")

    def test_admin_action_skips_completed_screening_call(self):
        """Parity with the command: a completed screening call protects the
        row from the bulk expire action too."""
        from django.contrib import admin as django_admin
        from django.contrib.messages.storage.fallback import FallbackStorage

        from crush_lu.admin.profiles import ProfileSubmissionAdmin

        _, profile = make_profile("admincalldone")
        call_done = make_submission(profile, "pending", review_call_completed=True)

        admin_user = User.objects.create_superuser(
            username="admin4@example.com", email="admin4@example.com", password="x"
        )
        factory = RequestFactory()
        request = factory.post("/admin/")
        request.user = admin_user
        request.session = self.client.session
        request._messages = FallbackStorage(request)

        model_admin = ProfileSubmissionAdmin(ProfileSubmission, django_admin.site)
        model_admin.bulk_expire_to_self_serve(
            request, ProfileSubmission.objects.filter(pk=call_done.pk)
        )

        call_done.refresh_from_db()
        self.assertEqual(call_done.status, "pending")


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


class ProfileSubmissionAdminFormTests(TestCase):
    """Direct admin status edits (change form + list_editable) must never
    offer or accept 'expired' — only bulk_expire_to_self_serve may set it,
    because it applies the safety guards and writes the audit entry."""

    def _admin_request(self, suffix):
        admin_user = User.objects.create_superuser(
            username=f"admin{suffix}@example.com",
            email=f"admin{suffix}@example.com",
            password="x",
        )
        request = RequestFactory().post("/admin/")
        request.user = admin_user
        return request

    def test_expired_choice_hidden_for_active_rows(self):
        from crush_lu.admin.profiles import ProfileSubmissionAdminForm

        _, profile = make_profile("adminform")
        submission = make_submission(profile, "pending")
        form = ProfileSubmissionAdminForm(instance=submission)
        self.assertNotIn(
            "expired", [value for value, label in form.fields["status"].choices]
        )

    def test_expired_choice_kept_for_already_expired_rows(self):
        from crush_lu.admin.profiles import ProfileSubmissionAdminForm

        _, profile = make_profile("adminformexp")
        submission = make_submission(profile, "expired")
        form = ProfileSubmissionAdminForm(instance=submission)
        self.assertIn(
            "expired", [value for value, label in form.fields["status"].choices]
        )

    def test_change_form_uses_guarded_form(self):
        from django.contrib import admin as django_admin

        from crush_lu.admin.profiles import (
            ProfileSubmissionAdmin,
            ProfileSubmissionAdminForm,
        )

        model_admin = ProfileSubmissionAdmin(ProfileSubmission, django_admin.site)
        form_class = model_admin.get_form(self._admin_request("changeform"))
        self.assertTrue(issubclass(form_class, ProfileSubmissionAdminForm))

    def test_changelist_edit_to_expired_is_rejected(self):
        """list_editable saves go through get_changelist_form, which ignores
        ModelAdmin.form — the override must hand it the guarded form."""
        from django.contrib import admin as django_admin

        from crush_lu.admin.profiles import ProfileSubmissionAdmin

        _, profile = make_profile("listeditrow")
        submission = make_submission(profile, "pending")

        model_admin = ProfileSubmissionAdmin(ProfileSubmission, django_admin.site)
        form_class = model_admin.get_changelist_form(
            self._admin_request("listedit"),
            fields=["status", "review_call_completed"],
        )
        form = form_class({"status": "expired"}, instance=submission)

        self.assertFalse(form.is_valid())
        self.assertIn("status", form.errors)
        submission.refresh_from_db()
        self.assertEqual(submission.status, "pending")

    def test_changelist_keeps_already_expired_row_saveable(self):
        """An already-expired row keeps its value in the select so a
        changelist save of other rows doesn't force it out of 'expired'."""
        from django.contrib import admin as django_admin

        from crush_lu.admin.profiles import ProfileSubmissionAdmin

        _, profile = make_profile("expkeeprow")
        submission = make_submission(profile, "expired")

        model_admin = ProfileSubmissionAdmin(ProfileSubmission, django_admin.site)
        form_class = model_admin.get_changelist_form(
            self._admin_request("expkeep"),
            fields=["status", "review_call_completed"],
        )
        form = form_class({"status": "expired"}, instance=submission)

        self.assertTrue(form.is_valid(), form.errors)

    def test_expired_row_offers_only_expired(self):
        """The select on an expired row must not offer reactivation targets."""
        from crush_lu.admin.profiles import ProfileSubmissionAdminForm

        _, profile = make_profile("expiredonlychoice")
        submission = make_submission(profile, "expired")
        form = ProfileSubmissionAdminForm(instance=submission)
        self.assertEqual(
            [value for value, label in form.fields["status"].choices], ["expired"]
        )

    def test_expired_row_cannot_be_reactivated_directly(self):
        """Saving an expired row as pending/approved/... must be rejected —
        reactivation would bypass the audit trail; the recovery path is the
        member's own resubmission (a fresh row)."""
        from django.contrib import admin as django_admin

        from crush_lu.admin.profiles import ProfileSubmissionAdmin

        _, profile = make_profile("reactivaterow")
        submission = make_submission(profile, "expired")

        model_admin = ProfileSubmissionAdmin(ProfileSubmission, django_admin.site)
        form_class = model_admin.get_changelist_form(
            self._admin_request("reactivate"),
            fields=["status", "review_call_completed"],
        )
        for target in ("pending", "approved", "revision"):
            form = form_class({"status": target}, instance=submission)
            self.assertFalse(form.is_valid(), f"'{target}' must be rejected")
            self.assertIn("status", form.errors)
        submission.refresh_from_db()
        self.assertEqual(submission.status, "expired")


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CoachReviewExpiredSubmissionTests(TestCase):
    """A stale /coach/review/<id>/ link must not let a coach re-open an
    expired row and pull the user back into the legacy review flow."""

    def setUp(self):
        self.coach_user = User.objects.create_user(
            username="reviewcoach@example.com",
            email="reviewcoach@example.com",
            password="pass12345",
            first_name="Cam",
        )
        UserDataConsent.objects.filter(user=self.coach_user).update(
            crushlu_consent_given=True
        )
        self.coach = CrushCoach.objects.create(user=self.coach_user, is_active=True)
        _, profile = make_profile("expiredreview")
        self.submission = make_submission(profile, "expired", coach=self.coach)

    def test_get_redirects_away(self):
        self.client.force_login(self.coach_user)
        response = self.client.get(
            reverse("crush_lu:coach_review_profile", args=[self.submission.id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("crush_lu:coach_profiles"))

    def test_post_cannot_reopen_expired_row(self):
        self.client.force_login(self.coach_user)
        response = self.client.post(
            reverse("crush_lu:coach_review_profile", args=[self.submission.id]),
            data={"status": "approved", "coach_notes": "", "feedback_to_user": ""},
        )
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, "expired")
        self.assertIsNone(self.submission.reviewed_at)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ExpiredLatestSubmissionFallbackTests(TestCase):
    """When the latest row is expired, user-facing surfaces must treat the
    profile as having no submission — not fall back to an older non-expired
    row (e.g. an old 'revision' skipped by the cleanup), which would
    resurrect legacy coach messaging for exactly the users whose review
    was closed out."""

    def setUp(self):
        self.user, self.profile = make_profile("fallback")
        # Older revision row, skipped by the cleanup command...
        self.old_revision = make_submission(self.profile, "revision")
        ProfileSubmission.objects.filter(pk=self.old_revision.pk).update(
            submitted_at=STALE_SUBMITTED_AT - timedelta(days=30)
        )
        # ...and the newer row that the cleanup expired.
        self.expired = make_submission(self.profile, "expired")

    def test_helper_returns_none_when_latest_is_expired(self):
        self.assertIsNone(ProfileSubmission.latest_for_profile(self.profile))

    def test_helper_returns_latest_row_when_not_expired(self):
        newer = make_submission(self.profile, "pending", days_ago=0)
        self.assertEqual(
            ProfileSubmission.latest_for_profile(self.profile).pk, newer.pk
        )

    def test_profile_submitted_does_not_resurrect_older_row(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("crush_lu:profile_submitted"))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["submission"])
        self.assertNotContains(response, "Coach Needs to Speak With You")

    def test_navbar_context_does_not_resurrect_older_row(self):
        request = RequestFactory().get("/en/dashboard/")
        request.user = self.user
        context = crush_user_context(request)

        self.assertNotIn("profile_submission", context)
        self.assertNotIn("profile_needs_action", context)

    def test_dashboard_does_not_resurrect_older_row(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("crush_lu:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["submission"])

    def test_api_submission_status_404s_when_latest_is_expired(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("api_submission_status"))

        self.assertEqual(response.status_code, 404)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ExpiredLatestResubmitGuardTests(TestCase):
    """The submit endpoints must honor the expired-latest invariant: when the
    newest row is expired, a POST must NOT requeue an older revision/recontact
    row back to pending — the user stays in the self-serve flow."""

    def setUp(self):
        self.user, self.profile = make_profile("resubguard")
        # The journey guards in complete_profile_submission require a verified
        # phone, an acked coach intro, and a verified email address.
        CrushProfile.objects.filter(pk=self.profile.pk).update(
            phone_number="+352123456700",
            phone_verified=True,
            coach_intro_seen_at=timezone.now(),
        )
        self.profile.refresh_from_db()
        from allauth.account.models import EmailAddress

        EmailAddress.objects.update_or_create(
            user=self.user,
            email=self.user.email,
            defaults={"verified": True, "primary": True},
        )
        # Older legacy revision row, skipped by the cleanup...
        self.old_revision = make_submission(self.profile, "revision")
        ProfileSubmission.objects.filter(pk=self.old_revision.pk).update(
            submitted_at=STALE_SUBMITTED_AT - timedelta(days=30)
        )
        # ...and the newer row that the cleanup expired.
        self.expired = make_submission(self.profile, "expired")

    def test_api_complete_does_not_requeue_older_revision_row(self):
        from unittest.mock import patch

        self.client.force_login(self.user)
        with patch(
            "crush_lu.models.CrushProfile.get_missing_fields", return_value=[]
        ), patch(
            "crush_lu.views_profile.broadcast_new_submission_to_channel"
        ) as broadcast:
            self.client.post(
                reverse("api_complete_profile_submission"),
                content_type="application/json",
                data="{}",
            )

        self.old_revision.refresh_from_db()
        self.expired.refresh_from_db()
        # The older row must stay a closed-out legacy revision, not re-enter
        # the coach channel; the expired row stays terminal.
        self.assertEqual(self.old_revision.status, "revision")
        self.assertEqual(self.expired.status, "expired")
        broadcast.assert_not_called()

    def test_api_complete_still_requeues_live_revision_row(self):
        """Control: without an expired latest row, a revision resubmit still
        returns to the verification channel as pending."""
        from unittest.mock import patch

        self.expired.delete()
        self.client.force_login(self.user)
        with patch(
            "crush_lu.models.CrushProfile.get_missing_fields", return_value=[]
        ), patch("crush_lu.views_profile.broadcast_new_submission_to_channel"):
            self.client.post(
                reverse("api_complete_profile_submission"),
                content_type="application/json",
                data="{}",
            )

        self.old_revision.refresh_from_db()
        self.assertEqual(self.old_revision.status, "pending")
        self.assertIsNone(self.old_revision.coach_id)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class SmsInviteExpiredCohortTests(TestCase):
    """Users whose latest submission is expired behave like users with no
    submission, so they must appear in the unverified-event SMS invite pool
    (they were previously in neither the pending-submissions bucket nor the
    no-submission bucket)."""

    def setUp(self):
        from crush_lu.models import MeetupEvent

        coach_user = User.objects.create_user(
            username="smscoach@example.com",
            email="smscoach@example.com",
            password="pass12345",
            first_name="Cam",
        )
        UserDataConsent.objects.filter(user=coach_user).update(
            crushlu_consent_given=True
        )
        CrushCoach.objects.create(user=coach_user, is_active=True)
        self.coach_user = coach_user

        self.event = MeetupEvent.objects.create(
            title="Unverified Mixer",
            description="Test event",
            event_type="mixer",
            location="Luxembourg City",
            address="1 Test Street",
            date_time=timezone.now() + timedelta(days=7),
            registration_deadline=timezone.now() + timedelta(days=6),
            profile_requirement="unverified",
        )

    def _make_invitable_profile(self, name):
        user, profile = make_profile(name)
        CrushProfile.objects.filter(pk=profile.pk).update(
            phone_number=f"+3526{abs(hash(name)) % 10**8:08d}",
            phone_verified=True,
        )
        profile.refresh_from_db()
        return user, profile

    def _pool_profile_ids(self):
        self.client.force_login(self.coach_user)
        response = self.client.get(
            reverse("crush_lu:coach_event_sms_invite", args=[self.event.id])
        )
        self.assertEqual(response.status_code, 200)
        return {
            row["profile"].id for row in response.context["unsubmitted_profiles"]
        }

    def test_expired_only_profile_is_invitable(self):
        _, expired_only = self._make_invitable_profile("smsexpired")
        make_submission(expired_only, "expired")

        _, no_submission = self._make_invitable_profile("smsfresh")

        _, old_rev_expired = self._make_invitable_profile("smsoldrev")
        old_rev = make_submission(old_rev_expired, "revision")
        ProfileSubmission.objects.filter(pk=old_rev.pk).update(
            submitted_at=STALE_SUBMITTED_AT - timedelta(days=30)
        )
        make_submission(old_rev_expired, "expired")

        pool_ids = self._pool_profile_ids()

        self.assertIn(expired_only.id, pool_ids)
        self.assertIn(no_submission.id, pool_ids)
        # Latest expired + older revision row still counts as "no submission".
        self.assertIn(old_rev_expired.id, pool_ids)

    def test_live_submission_profile_stays_out_of_no_submission_pool(self):
        """Control: a live pending submission keeps the profile in the
        submissions bucket, not the no-submission pool."""
        _, pending_profile = self._make_invitable_profile("smspending")
        make_submission(pending_profile, "pending", days_ago=0)

        pool_ids = self._pool_profile_ids()

        self.assertNotIn(pending_profile.id, pool_ids)

    def test_expired_profile_keeps_sent_invite_state(self):
        """An invite logged against the submission while it was still pending
        must survive the row's expiry — the profile lands in the
        no-submission pool marked already_sent, not as a fresh target."""
        from crush_lu.models import CallAttempt

        _, invited = self._make_invitable_profile("smsalreadysent")
        expired_sub = make_submission(invited, "expired")
        CallAttempt.objects.create(
            submission=expired_sub,
            event=self.event,
            result="event_invite_sms",
        )

        self.client.force_login(self.coach_user)
        response = self.client.get(
            reverse("crush_lu:coach_event_sms_invite", args=[self.event.id])
        )
        self.assertEqual(response.status_code, 200)
        rows = {
            row["profile"].id: row
            for row in response.context["unsubmitted_profiles"]
        }
        self.assertIn(invited.id, rows)
        self.assertTrue(rows[invited.id]["already_sent"])

    def test_approved_profile_with_expired_latest_is_not_invitable(self):
        """A cleanup user who has since verified (e.g. via LuxID) must stay
        out of the unverified pool — event_register rejects is_approved
        users for unverified events, so inviting them is a dead end."""
        _, verified_since = self._make_invitable_profile("smsverified")
        make_submission(verified_since, "expired")
        CrushProfile.objects.filter(pk=verified_since.pk).update(
            is_approved=True, verification_status="verified"
        )

        _, approved_no_sub = self._make_invitable_profile("smsapproved")
        CrushProfile.objects.filter(pk=approved_no_sub.pk).update(
            is_approved=True, verification_status="verified"
        )

        pool_ids = self._pool_profile_ids()

        self.assertNotIn(verified_since.id, pool_ids)
        self.assertNotIn(approved_no_sub.id, pool_ids)


class LegacyBulkActionExpiredGuardTests(TestCase):
    """The legacy bulk actions (approve/reject/revision) must skip expired
    rows — they would otherwise resurrect a closed-out user into a legacy
    state and flip their profile without any audit or recovery path."""

    def _run_action(self, action_name, submission):
        from django.contrib import admin as django_admin
        from django.contrib.messages.storage.fallback import FallbackStorage

        from crush_lu.admin.profiles import ProfileSubmissionAdmin

        admin_user = User.objects.create_superuser(
            username=f"{action_name}@example.com",
            email=f"{action_name}@example.com",
            password="x",
        )
        request = RequestFactory().post("/admin/")
        request.user = admin_user
        request.session = self.client.session
        request._messages = FallbackStorage(request)

        model_admin = ProfileSubmissionAdmin(ProfileSubmission, django_admin.site)
        getattr(model_admin, action_name)(
            request, ProfileSubmission.objects.filter(pk=submission.pk)
        )

    def test_bulk_reject_skips_expired(self):
        _, profile = make_profile("bulkrejectexp")
        submission = make_submission(profile, "expired")

        self._run_action("bulk_reject_profiles", submission)

        submission.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(submission.status, "expired")
        self.assertEqual(profile.verification_status, "pending")

    def test_bulk_revision_skips_expired(self):
        _, profile = make_profile("bulkrevexp")
        submission = make_submission(profile, "expired")

        self._run_action("bulk_request_revision", submission)

        submission.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(submission.status, "expired")
        self.assertEqual(profile.verification_status, "pending")

    def test_bulk_approve_skips_expired_even_with_completed_call(self):
        """An expired row with review_call_completed=True must not be
        approvable — the row is terminal and the profile must not flip."""
        _, profile = make_profile("bulkapproveexp")
        submission = make_submission(profile, "expired", review_call_completed=True)

        self._run_action("bulk_approve_profiles", submission)

        submission.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(submission.status, "expired")
        self.assertFalse(profile.is_approved)
        self.assertEqual(profile.verification_status, "pending")
