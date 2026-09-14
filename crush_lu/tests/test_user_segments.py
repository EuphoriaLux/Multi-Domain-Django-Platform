"""
Crush-admin "User segments": segments keyed on the profile's verification state.

Since the July 2026 verification pivot a member is verified self-serve by LuxID
or in person at an event door, and neither path creates a ProfileSubmission —
the coach-review queue these segments used to read is empty for everyone who
joined since. These tests pin the re-keyed segments to ``CrushProfile``
(``verification_status`` / ``approved_at``), the legacy coach-review
sub-states to the member's *latest* submission, and the drill-down, CSV export
and audience resolvers behind every segment card.
"""

import csv
import io
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.admin.custom_sms import _profiles_from_segment, find_segment
from crush_lu.admin.user_segments import get_segment_definitions
from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    ProfileSubmission,
)
from crush_lu.models.connections import ConnectionMessage, EventConnection
from crush_lu.models.profiles import UserDataConsent
from crush_lu.newsletter_service import _get_segment_users
from crush_lu.services.profile_verification import claim_profile_verification

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}
SEGMENTS_URL = "/crush-admin/user-segments/"


def _segment(key):
    """One segment's definition, without paying every segment's COUNT."""
    for category in get_segment_definitions(include_counts=False).values():
        for segment in category["segments"]:
            if segment["key"] == key:
                return segment
    raise AssertionError(f"segment {key!r} is not defined")


def _members(key):
    """Primary keys of the profiles a segment resolves to."""
    return set(_segment(key)["queryset"].values_list("pk", flat=True))


class SegmentFixturesMixin:
    def _profile(self, email, *, status="pending", method="", approved_at=None):
        user = User.objects.create_user(
            username=email, email=email, password="pw12345678"
        )
        return CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1992, 3, 4),
            gender="F",
            location="Luxembourg",
            verification_status=status,
            is_approved=status == "verified",
            verification_method=method,
            approved_at=approved_at,
        )

    def _event(self, title, start):
        return MeetupEvent.objects.create(
            title=title,
            description="Segment fixture event",
            event_type="mixer",
            date_time=start,
            duration_minutes=120,
            location="Luxembourg",
            address="1 Test Street",
            max_participants=20,
            registration_deadline=start - timedelta(hours=1),
            is_published=True,
        )

    def _register(self, profile, event, status):
        return EventRegistration.objects.create(
            event=event, user=profile.user, status=status
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class RecentlyApprovedSegmentTests(SegmentFixturesMixin, TestCase):
    """Keyed on ``approved_at``, which every verification path stamps."""

    def setUp(self):
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )

    def test_luxid_verified_member_without_submission_counts(self):
        from crush_lu.signals import _execute_luxid_direct_verify

        profile = self._profile("luxid@example.com")

        _execute_luxid_direct_verify(
            profile.user, profile, submission=None, request=None
        )

        profile.refresh_from_db()
        self.assertEqual(profile.verification_method, "luxid")
        self.assertFalse(ProfileSubmission.objects.filter(profile=profile).exists())
        self.assertIn(profile.pk, _members("lifecycle_recently_approved"))
        # The card's number, not only its queryset, sees the member.
        card = next(
            segment
            for segment in get_segment_definitions()["lifecycle"]["segments"]
            if segment["key"] == "lifecycle_recently_approved"
        )
        self.assertEqual(card["count"], 1)

    def test_event_door_verified_member_without_submission_counts(self):
        profile = self._profile("door@example.com")

        claimed = claim_profile_verification(
            profile, method="coach_event", approved_at=timezone.now()
        )

        self.assertTrue(claimed)
        self.assertFalse(ProfileSubmission.objects.filter(profile=profile).exists())
        self.assertIn(profile.pk, _members("lifecycle_recently_approved"))

    def test_approval_older_than_seven_days_is_excluded(self):
        profile = self._profile(
            "old@example.com",
            status="verified",
            method="luxid",
            approved_at=timezone.now() - timedelta(days=8),
        )

        self.assertNotIn(profile.pk, _members("lifecycle_recently_approved"))

    def test_recently_reviewed_submission_alone_does_not_count(self):
        """The old keying: a submission reviewed this week, an approval long past."""
        profile = self._profile(
            "legacy@example.com",
            status="verified",
            method="legacy",
            approved_at=timezone.now() - timedelta(days=200),
        )
        ProfileSubmission.objects.create(
            profile=profile, status="approved", reviewed_at=timezone.now()
        )

        self.assertNotIn(profile.pk, _members("lifecycle_recently_approved"))


class PendingVerificationSegmentTests(SegmentFixturesMixin, TestCase):
    """Pending members, split on whether an event door can still verify them."""

    def setUp(self):
        now = timezone.now()
        upcoming = self._event("Upcoming door", now + timedelta(days=5))
        other_upcoming = self._event("Another upcoming door", now + timedelta(days=9))
        ended = self._event("Ended door", now - timedelta(days=2))
        cancelled = self._event("Cancelled door", now + timedelta(days=5))

        self.confirmed = self._profile("confirmed@example.com")
        self._register(self.confirmed, upcoming, "confirmed")
        self.waitlisted = self._profile("waitlisted@example.com")
        self._register(self.waitlisted, upcoming, "waitlist")
        # A registration that no longer holds a place is no door to verify at.
        self.withdrew = self._profile("withdrew@example.com")
        self._register(self.withdrew, other_upcoming, "cancelled")
        self.past_only = self._profile("past@example.com")
        self._register(self.past_only, ended, "confirmed")
        self.cancelled_event = self._profile("cancelled-event@example.com")
        self._register(self.cancelled_event, cancelled, "confirmed")
        # Cancelling flips the flag and leaves registrations alone.
        MeetupEvent.objects.filter(pk=cancelled.pk).update(is_cancelled=True)
        self.not_registered = self._profile("none@example.com")

        # Not pending: in neither card, however they are booked.
        self.verified = self._profile(
            "verified@example.com", status="verified", method="luxid", approved_at=now
        )
        self._register(self.verified, upcoming, "confirmed")
        self.incomplete = self._profile("incomplete@example.com", status="incomplete")
        self._register(self.incomplete, upcoming, "confirmed")

    def test_split_on_a_door_that_can_still_verify_them(self):
        self.assertEqual(
            _members("pending_booked"), {self.confirmed.pk, self.waitlisted.pk}
        )
        self.assertEqual(
            _members("pending_unbooked"),
            {
                self.withdrew.pk,
                self.past_only.pk,
                self.cancelled_event.pk,
                self.not_registered.pk,
            },
        )

    def test_cards_partition_the_pending_cohort(self):
        booked = _members("pending_booked")
        unbooked = _members("pending_unbooked")

        self.assertFalse(booked & unbooked)
        self.assertEqual(booked | unbooked, _members("unverified_pending_review"))

    def test_keyed_on_the_profile_not_the_submission_table(self):
        # No pending member above has a submission — the old cards read zero
        # for every one of them. A stale pending row on a member who is already
        # verified must not pull them back into the funnel either.
        self.assertFalse(ProfileSubmission.objects.exists())
        ProfileSubmission.objects.create(profile=self.verified, status="pending")

        pending = _members("pending_booked") | _members("pending_unbooked")

        self.assertNotIn(self.verified.pk, pending)
        self.assertEqual(len(pending), 6)

    def test_no_live_event_leaves_every_pending_member_unbooked(self):
        # A lull with no event anyone could be verified at: the empty event
        # list must read as "no seat", not collapse either card.
        MeetupEvent.objects.update(is_cancelled=True)

        self.assertEqual(_members("pending_booked"), set())
        self.assertEqual(
            _members("pending_unbooked"), _members("unverified_pending_review")
        )
        self.assertEqual(len(_members("pending_unbooked")), 6)

    def test_retired_review_keys_resolve_to_nobody(self):
        """``pending_urgent`` / ``pending_normal`` counted coach-review rows.

        They are retired, not repointed: a saved newsletter, campaign or SMS
        batch still naming one must keep reaching nobody rather than jump to
        the whole pending cohort with copy written for a review queue.
        """
        definitions = get_segment_definitions(include_counts=False)
        for key in ("pending_urgent", "pending_normal"):
            with self.subTest(key=key):
                self.assertEqual(find_segment(key, definitions), (None, None))
                self.assertFalse(_get_segment_users(key).exists())


@override_settings(**CRUSH_LU_URL_SETTINGS)
class LegacyCoachReviewSegmentTests(SegmentFixturesMixin, TestCase):
    """Revision / Recontact keep a live writer, for the pre-pivot cohort only.

    ``submit_profile`` re-queues a member's legacy revision/recontact row and
    a coach can review it again through ``coach_review_profile`` — so both
    segments stay, read from the member's latest submission.
    """

    def setUp(self):
        cache.clear()
        self.coach_user = User.objects.create_user(
            username="segment-coach@example.com",
            email="segment-coach@example.com",
            password="pw12345678",
        )
        UserDataConsent.objects.filter(user=self.coach_user).update(
            crushlu_consent_given=True
        )
        self.coach = CrushCoach.objects.create(
            user=self.coach_user, bio="Segment coach", is_active=True
        )
        self.profile = self._profile("requeued@example.com")
        UserDataConsent.objects.filter(user=self.profile.user).update(
            crushlu_consent_given=True
        )
        # A pre-pivot submission, re-queued by `submit_profile` and claimed.
        self.submission = ProfileSubmission.objects.create(
            profile=self.profile, coach=self.coach, status="pending"
        )
        self.client.force_login(self.coach_user)

    def _review(self, status):
        return self.client.post(
            reverse("crush_lu:coach_review_profile", args=[self.submission.pk]),
            data={
                "status": status,
                "coach_notes": "Legacy review.",
                "feedback_to_user": "Please update your bio.",
            },
        )

    @patch("crush_lu.views_coach.notify_profile_revision")
    def test_coach_revision_lands_in_revision_segment(self, _notify):
        self.assertEqual(self._review("revision").status_code, 302)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "incomplete")
        self.assertIn(self.profile.pk, _members("unverified_revision"))
        self.assertNotIn(self.profile.pk, _members("unverified_recontact"))

    @patch("crush_lu.notification_service.notify_profile_recontact")
    def test_coach_recontact_lands_in_recontact_segment(self, _notify):
        self.assertEqual(self._review("recontact_coach").status_code, 302)

        self.profile.refresh_from_db()
        # The recontact branch leaves the profile pending, which the old
        # `verification_status="incomplete"` filter could never match.
        self.assertEqual(self.profile.verification_status, "pending")
        self.assertIn(self.profile.pk, _members("unverified_recontact"))
        self.assertNotIn(self.profile.pk, _members("unverified_revision"))

    def test_only_the_latest_submission_counts(self):
        now = timezone.now()
        # The pivot cleanup closed this member's story after the revision.
        closed = self._profile("closed@example.com", status="incomplete")
        old_revision = ProfileSubmission.objects.create(
            profile=closed, status="revision"
        )
        ProfileSubmission.objects.filter(pk=old_revision.pk).update(
            submitted_at=now - timedelta(days=60)
        )
        ProfileSubmission.objects.create(profile=closed, status="expired")
        # Verified by LuxID after the coach asked for changes: nothing to chase.
        verified = self._profile(
            "verified-after@example.com",
            status="verified",
            method="luxid",
            approved_at=now,
        )
        ProfileSubmission.objects.create(profile=verified, status="revision")

        revision = _members("unverified_revision")

        self.assertNotIn(closed.pk, revision)
        self.assertNotIn(verified.pk, revision)


class ConnectionActivitySegmentTests(SegmentFixturesMixin, TestCase):
    """The connection cards read the live post-event connection tables."""

    def test_reverse_names_belong_to_event_connection(self):
        requester = EventConnection._meta.get_field("requester")
        recipient = EventConnection._meta.get_field("recipient")

        self.assertEqual(
            requester.remote_field.related_name, "connection_requests_sent"
        )
        self.assertEqual(
            recipient.remote_field.related_name, "connection_requests_received"
        )
        self.assertIs(
            User._meta.get_field("connection_requests_sent").related_model,
            EventConnection,
        )
        self.assertIs(
            User._meta.get_field("connectionmessage").related_model, ConnectionMessage
        )

    def test_cards_follow_event_connection_rows(self):
        now = timezone.now()
        event = self._event("Past mixer", now - timedelta(days=3))
        requester = self._profile(
            "requester@example.com",
            status="verified",
            method="coach_event",
            approved_at=now,
        )
        recipient = self._profile(
            "recipient@example.com",
            status="verified",
            method="coach_event",
            approved_at=now,
        )
        loner = self._profile(
            "loner@example.com", status="verified", method="luxid", approved_at=now
        )
        connection = EventConnection.objects.create(
            requester=requester.user,
            recipient=recipient.user,
            event=event,
            status="accepted",
        )
        ConnectionMessage.objects.create(
            connection=connection, sender=requester.user, message="Hello!"
        )

        self.assertEqual(_members("conn_has_accepted"), {requester.pk, recipient.pk})
        self.assertEqual(_members("conn_none"), {loner.pk})
        self.assertEqual(_members("conn_has_messaged"), {requester.pk})


@override_settings(**CRUSH_LU_URL_SETTINGS)
class SegmentPageTests(SegmentFixturesMixin, TestCase):
    """Dashboard totals, drill-down, CSV export and audience resolvers."""

    def setUp(self):
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        now = timezone.now()
        upcoming = self._event("Upcoming door", now + timedelta(days=5))

        self.approved = self._profile(
            "recent@example.com", status="verified", method="luxid", approved_at=now
        )
        self.booked = self._profile("booked@example.com")
        self._register(self.booked, upcoming, "confirmed")
        self.unbooked = self._profile("unbooked@example.com")
        self.revision = self._profile("revision@example.com", status="incomplete")
        ProfileSubmission.objects.create(profile=self.revision, status="revision")
        self.recontact = self._profile("recontact@example.com")
        ProfileSubmission.objects.create(
            profile=self.recontact, status="recontact_coach"
        )
        self.rejected = self._profile("rejected@example.com", status="rejected")

        self.expected = {
            "lifecycle_recently_approved": {self.approved},
            "pending_booked": {self.booked},
            "pending_unbooked": {self.unbooked, self.recontact},
            "unverified_revision": {self.revision},
            "unverified_recontact": {self.recontact},
        }

        admin = User.objects.create_superuser(
            "root@example.com", "root@example.com", "pw12345678"
        )
        self.client.force_login(admin)

    def test_dashboard_totals(self):
        response = self.client.get(SEGMENTS_URL)

        self.assertEqual(response.status_code, 200)
        # booked + unbooked + recontact are the pending cohort.
        self.assertEqual(response.context["total_pending"], 3)
        # 1 incomplete + 3 pending + 1 rejected: the Revision and Recontact
        # cards are sub-states of those, not extra members.
        self.assertEqual(response.context["total_unverified"], 5)
        self.assertContains(response, "Pending Verification")
        self.assertNotContains(response, "Pending Coach Review")
        self.assertNotIn("pending_reviews", response.context["segments"])

    def test_drill_down_and_csv_export_list_the_rekeyed_members(self):
        for key, profiles in self.expected.items():
            emails = {profile.user.email for profile in profiles}
            with self.subTest(key=key):
                response = self.client.get(f"{SEGMENTS_URL}{key}/")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["is_profile_segment"])
                self.assertEqual(
                    {item.user.email for item in response.context["users"]}, emails
                )

                export = self.client.get(f"{SEGMENTS_URL}{key}/?export=csv")
                self.assertEqual(export.status_code, 200)
                rows = list(csv.reader(io.StringIO(export.content.decode())))
                self.assertEqual(rows[0][0], "Email")
                self.assertEqual({row[0] for row in rows[1:]}, emails)
                self.assertEqual(len(rows) - 1, len(emails))

    def test_audience_resolvers_accept_the_rekeyed_querysets(self):
        """Newsletters/campaigns and Custom SMS map the same members."""
        for key, profiles in self.expected.items():
            with self.subTest(key=key):
                self.assertEqual(
                    set(_get_segment_users(key).values_list("pk", flat=True)),
                    {profile.user_id for profile in profiles},
                )
                self.assertEqual(
                    set(
                        _profiles_from_segment(_segment(key)["queryset"]).values_list(
                            "pk", flat=True
                        )
                    ),
                    {profile.pk for profile in profiles},
                )
