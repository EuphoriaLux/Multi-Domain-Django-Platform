"""
Crush-admin Action Center, dashboard counters and profile proxies, keyed on
the profile's verification state.

Since the July 2026 verification pivot a member is verified by LuxID or at an
event door, and neither path creates a ProfileSubmission, so every admin
counter keyed on ``ProfileSubmission(status="pending")`` read zero while
hundreds of members waited. These tests pin the re-keyed counts to
``CrushProfile``, check that every Action Center tile opens a changelist of
exactly its own count, and pin the legacy coach-review proxies to the
member's *latest* submission.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.admin import crush_admin_site
from crush_lu.admin.profiles import (
    PendingReviewProfile,
    RecontactCoachProfile,
    RejectedProfile,
    RevisionNeededProfile,
)
from crush_lu.admin.verification_queues import pending_action_counts
from crush_lu.models import (
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    ProfileSubmission,
)
from crush_lu.models.connections import EventConnection

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}


class VerificationFixturesMixin:
    def _profile(self, username, *, status="pending", is_active=True):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="pw12345678",
        )
        return CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1992, 3, 4),
            gender="F",
            location="Luxembourg",
            verification_status=status,
            is_approved=status == "verified",
            is_active=is_active,
        )

    def _event(self, title, start, **fields):
        fields = {"is_published": True, **fields}
        return MeetupEvent.objects.create(
            title=title,
            description="Action Center fixture event",
            event_type="mixer",
            date_time=start,
            duration_minutes=120,
            location="Luxembourg",
            address="1 Test Street",
            max_participants=20,
            registration_deadline=start - timedelta(hours=1),
            **fields,
        )

    def _register(self, profile, event, status="confirmed"):
        return EventRegistration.objects.create(
            event=event, user=profile.user, status=status
        )

    def _submission(self, profile, status, *, days_ago):
        """A submission made ``days_ago`` days ago. ``submitted_at`` is
        ``auto_now_add``, so the date is stamped after the insert."""
        submission = ProfileSubmission.objects.create(profile=profile, status=status)
        ProfileSubmission.objects.filter(pk=submission.pk).update(
            submitted_at=timezone.now() - timedelta(days=days_ago)
        )
        return submission

    def _door_cohort(self):
        """Pending members on both sides of the door split, plus members the
        Action Center must never count. Returns the (booked, unbooked) pks."""
        now = timezone.now()
        upcoming = self._event("Upcoming door", now + timedelta(days=5))
        # Started half an hour ago and runs for two: its door is open now.
        running = self._event("Running door", now - timedelta(minutes=30))
        # Being unpublished does not close an event's door.
        unpublished = self._event(
            "Unpublished door", now + timedelta(days=6), is_published=False
        )
        ended = self._event("Ended door", now - timedelta(days=2))
        cancelled = self._event(
            "Cancelled door", now + timedelta(days=5), is_cancelled=True
        )

        booked = set()
        for username, event, status in (
            ("seat", upcoming, "confirmed"),
            ("paying", upcoming, "pending"),
            ("waitlisted", upcoming, "waitlist"),
            ("at_the_door", running, "confirmed"),
            ("unpublished_seat", unpublished, "confirmed"),
        ):
            profile = self._profile(username)
            self._register(profile, event, status)
            booked.add(profile.pk)

        unbooked = {self._profile("no_booking").pk}
        for username, event, status in (
            ("ended_seat", ended, "attended"),
            ("cancelled_event", cancelled, "confirmed"),
            ("cancelled_seat", upcoming, "cancelled"),
            ("applied_only", upcoming, "applied"),
        ):
            profile = self._profile(username)
            self._register(profile, event, status)
            unbooked.add(profile.pk)

        # Never counted: not awaiting verification, or deactivated.
        self._register(self._profile("verified_seat", status="verified"), upcoming)
        self._profile("incomplete", status="incomplete")
        self._profile("rejected", status="rejected")
        self._register(self._profile("inactive", is_active=False), upcoming)

        return booked, unbooked


class PendingActionCountsTests(VerificationFixturesMixin, TestCase):
    """The counts both Action Centers read."""

    def setUp(self):
        self.booked, self.unbooked = self._door_cohort()

    def test_pending_members_split_on_whether_a_door_can_still_verify_them(self):
        counts = pending_action_counts()

        self.assertEqual(counts["booked"], len(self.booked))
        self.assertEqual(counts["unbooked"], len(self.unbooked))
        self.assertEqual(counts["total_pending"], len(self.booked | self.unbooked))

    def test_members_without_a_submission_are_counted(self):
        """The pivot regression: nobody here has a submission, yet all wait."""
        self.assertFalse(ProfileSubmission.objects.exists())

        self.assertEqual(
            pending_action_counts()["total_pending"], len(self.booked | self.unbooked)
        )

    def test_a_pending_submission_alone_counts_nothing(self):
        """The old keying counted this row, but the member is already verified."""
        verified = self._profile("verified_by_luxid", status="verified")
        self._submission(verified, "pending", days_ago=1)

        counts = pending_action_counts()

        self.assertEqual(counts["total_pending"], len(self.booked | self.unbooked))
        self.assertEqual(counts["legacy_reviews"], 0)


class LegacyReviewCountTests(VerificationFixturesMixin, TestCase):
    """``legacy_reviews``: the member's latest submission awaits a coach."""

    def test_resubmitted_legacy_member_is_counted(self):
        member = self._profile("resubmitted")
        self._submission(member, "revision", days_ago=40)
        self._submission(member, "pending", days_ago=1)

        self.assertEqual(pending_action_counts()["legacy_reviews"], 1)

    def test_rows_behind_a_newer_one_are_history(self):
        expired = self._profile("expired_latest")
        self._submission(expired, "pending", days_ago=60)
        self._submission(expired, "expired", days_ago=30)
        moved_on = self._profile("moved_on", status="incomplete")
        self._submission(moved_on, "pending", days_ago=10)
        self._submission(moved_on, "revision", days_ago=5)

        self.assertEqual(pending_action_counts()["legacy_reviews"], 0)

    def test_verified_and_deactivated_members_are_not_counted(self):
        verified = self._profile("verified_open_row", status="verified")
        self._submission(verified, "pending", days_ago=1)
        inactive = self._profile("inactive_open_row", is_active=False)
        self._submission(inactive, "pending", days_ago=1)

        self.assertEqual(pending_action_counts()["legacy_reviews"], 0)


class AdminClientMixin:
    def _login_superuser(self):
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        self.superuser = User.objects.create_superuser(
            username="action_center_admin",
            email="action-center-admin@example.com",
            password="pw12345678",
        )
        self.client.force_login(self.superuser)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class ActionCenterIndexTests(AdminClientMixin, VerificationFixturesMixin, TestCase):
    def setUp(self):
        self.booked, self.unbooked = self._door_cohort()
        # A pre-pivot recontact the member resubmitted: pending, no booking.
        self.legacy = self._profile("legacy_resubmission")
        self._submission(self.legacy, "recontact_coach", days_ago=90)
        self._submission(self.legacy, "pending", days_ago=2)
        self.unbooked.add(self.legacy.pk)
        self._login_superuser()

    def _tiles(self):
        """Each tile's link, as the template writes it, and who it must list."""
        profiles = reverse("crush_admin:crush_lu_crushprofile_changelist")
        pending = f"{profiles}?verification_status__exact=pending&is_active__exact=1"
        legacy = reverse("crush_admin:crush_lu_pendingreviewprofile_changelist")
        return {
            "unbooked": (f"{pending}&door_booking=unbooked", self.unbooked),
            "booked": (f"{pending}&door_booking=booked", self.booked),
            "total_pending": (pending, self.booked | self.unbooked),
            "legacy_reviews": (f"{legacy}?is_active__exact=1", {self.legacy.pk}),
        }

    def test_index_shows_the_shared_counts(self):
        response = self.client.get(reverse("crush_admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["pending_actions"], pending_action_counts())

    def test_every_tile_opens_a_list_of_exactly_its_count(self):
        index = self.client.get(reverse("crush_admin:index"))
        counts = index.context["pending_actions"]

        for key, (url, expected) in self._tiles().items():
            with self.subTest(tile=key):
                self.assertContains(index, f'href="{url}"')
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                changelist = response.context["cl"]
                self.assertEqual(
                    set(changelist.queryset.values_list("pk", flat=True)), expected
                )
                self.assertEqual(changelist.result_count, counts[key])

    def test_submission_queue_links_are_gone(self):
        response = self.client.get(reverse("crush_admin:index"))
        submissions = reverse("crush_admin:crush_lu_profilesubmission_changelist")

        self.assertNotContains(response, f'href="{submissions}?')

    def test_connections_stat_counts_completed_introductions(self):
        event = self._event("Past mixer", timezone.now() - timedelta(days=3))
        recipient = self._profile("recipient", status="verified")
        for number, status in enumerate(
            ("shared", "accepted", "coach_approved", "pending")
        ):
            requester = self._profile(f"requester_{number}", status="verified")
            EventConnection.objects.create(
                requester=requester.user,
                recipient=recipient.user,
                event=event,
                status=status,
            )

        response = self.client.get(reverse("crush_admin:index"))

        self.assertEqual(response.context["shared_connections"], 1)
        connections = reverse("crush_admin:crush_lu_eventconnection_changelist")
        self.assertContains(response, f'href="{connections}?status__exact=shared"')
        self.assertNotContains(response, "status__exact=mutual")


@override_settings(**CRUSH_LU_URL_SETTINGS)
class DashboardActionCenterTests(
    AdminClientMixin, VerificationFixturesMixin, TestCase
):
    def setUp(self):
        self.booked, self.unbooked = self._door_cohort()
        self._login_superuser()

    def test_dashboard_shares_the_index_counts(self):
        dashboard = self.client.get(reverse("crush_admin_dashboard"))
        index = self.client.get(reverse("crush_admin:index"))

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(
            dashboard.context["pending_actions"], index.context["pending_actions"]
        )
        self.assertNotIn("pending_reviews", dashboard.context)

    def test_pending_verification_links_to_the_pending_profiles(self):
        response = self.client.get(reverse("crush_admin_dashboard"))
        profiles = reverse("crush_admin:crush_lu_crushprofile_changelist")
        pending = f"{profiles}?verification_status__exact=pending&is_active__exact=1"

        self.assertContains(response, f'href="{pending}"')
        self.assertContains(response, f'href="{pending}&door_booking=unbooked"')
        self.assertContains(response, f'href="{pending}&door_booking=booked"')
        self.assertContains(
            response, f"Pending Verification ({len(self.booked | self.unbooked)})"
        )
        self.assertNotContains(response, "Pending Reviews (")


class ProfileProxyAdminTests(VerificationFixturesMixin, TestCase):
    """The legacy coach-review proxies read the latest submission; Rejected
    reads the profile."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="proxy_admin",
            email="proxy-admin@example.com",
            password="pw12345678",
        )

    def _listed(self, proxy):
        """Primary keys a proxy's changelist lists for a superuser, sorted but
        not de-duplicated, so a member listed twice shows up twice."""
        request = RequestFactory().get("/crush-admin/")
        request.user = self.superuser
        queryset = crush_admin_site._registry[proxy].get_queryset(request)
        return sorted(queryset.values_list("pk", flat=True))

    def test_pending_review_is_a_latest_submission_awaiting_a_coach(self):
        queued = self._profile("queued")
        self._submission(queued, "revision", days_ago=40)
        self._submission(queued, "pending", days_ago=1)
        # Two open rows: listed once, not once per row.
        doubled = self._profile("doubled")
        self._submission(doubled, "pending", days_ago=9)
        self._submission(doubled, "pending", days_ago=3)
        moved_on = self._profile("moved_on", status="incomplete")
        self._submission(moved_on, "pending", days_ago=10)
        self._submission(moved_on, "revision", days_ago=5)
        expired = self._profile("expired_latest")
        self._submission(expired, "pending", days_ago=60)
        self._submission(expired, "expired", days_ago=30)
        verified = self._profile("verified_open_row", status="verified")
        self._submission(verified, "pending", days_ago=1)
        self._profile("pending_without_submission")

        self.assertEqual(
            self._listed(PendingReviewProfile), sorted([queued.pk, doubled.pk])
        )

    def test_revision_needed_reads_the_latest_submission(self):
        waiting = self._profile("waiting_on_member", status="incomplete")
        self._submission(waiting, "pending", days_ago=10)
        self._submission(waiting, "revision", days_ago=5)
        resubmitted = self._profile("resubmitted")
        self._submission(resubmitted, "revision", days_ago=40)
        self._submission(resubmitted, "pending", days_ago=1)
        verified = self._profile("verified_since", status="verified")
        self._submission(verified, "revision", days_ago=20)
        expired = self._profile("revision_expired", status="incomplete")
        self._submission(expired, "revision", days_ago=60)
        self._submission(expired, "expired", days_ago=30)

        self.assertEqual(self._listed(RevisionNeededProfile), [waiting.pk])

    def test_recontact_coach_reads_the_latest_submission(self):
        # A coach's recontact leaves the profile pending, not incomplete.
        recontact = self._profile("recontact")
        self._submission(recontact, "recontact_coach", days_ago=3)
        resubmitted = self._profile("recontact_resubmitted")
        self._submission(resubmitted, "recontact_coach", days_ago=30)
        self._submission(resubmitted, "pending", days_ago=2)
        verified = self._profile("recontact_verified", status="verified")
        self._submission(verified, "recontact_coach", days_ago=3)

        self.assertEqual(self._listed(RecontactCoachProfile), [recontact.pk])

    def test_rejected_reads_the_profile_decision(self):
        rejected_directly = self._profile("rejected_directly", status="rejected")
        rejected_in_review = self._profile("rejected_in_review", status="rejected")
        self._submission(rejected_in_review, "rejected", days_ago=12)
        verified_since = self._profile("verified_since", status="verified")
        self._submission(verified_since, "rejected", days_ago=100)

        self.assertEqual(
            self._listed(RejectedProfile),
            sorted([rejected_directly.pk, rejected_in_review.pk]),
        )
