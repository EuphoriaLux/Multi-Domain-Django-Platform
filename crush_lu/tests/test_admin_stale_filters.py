"""
Crush-admin filters, recent-activity lists and the coach dashboard card that
still read ProfileSubmission after the July 2026 verification pivot.

LuxID and event-door verification create no submission, and submitting a
profile only moves it to ``pending``. So these surfaces read zero, listed
every member who joined since the pivot, or filtered a status value the model
never had. Each test pins a surface to what it reads now: the profile's
verification state, the member's latest submission, ``assigned_coach``, or a
reciprocal EventConnection row.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventConnection,
    EventRegistration,
    MeetupEvent,
    ProfileSubmission,
)
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}

PROFILES = "crush_admin:crush_lu_crushprofile_changelist"
CONNECTIONS = "crush_admin:crush_lu_eventconnection_changelist"


def make_event(title, start, **fields):
    fields = {"is_published": True, **fields}
    return MeetupEvent.objects.create(
        title=title,
        description="Stale admin filter fixture event",
        event_type="mixer",
        date_time=start,
        duration_minutes=120,
        location="Luxembourg",
        address="1 Test Street",
        max_participants=20,
        registration_deadline=start - timedelta(hours=1),
        **fields,
    )


class AdminFixturesMixin:
    def setUp(self):
        # Recycled SQLite primary keys share one rate-limit counter.
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        self.superuser = User.objects.create_superuser(
            username="stale_filters_admin",
            email="stale-filters-admin@example.com",
            password="pw12345678",
        )
        self.client.force_login(self.superuser)
        # A login can create an incomplete profile for the account. The
        # admin's own would then land in every "incomplete" expectation.
        CrushProfile.objects.filter(user=self.superuser).delete()

    def _profile(self, username, *, status="pending", is_active=True, **fields):
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
            **fields,
        )

    def _submission(self, profile, status, *, days_ago, **fields):
        """A submission made ``days_ago`` days ago. ``submitted_at`` is
        ``auto_now_add``, so the date is stamped after the insert."""
        submission = ProfileSubmission.objects.create(
            profile=profile, status=status, **fields
        )
        ProfileSubmission.objects.filter(pk=submission.pk).update(
            submitted_at=timezone.now() - timedelta(days=days_ago)
        )
        return submission

    def _listed(self, changelist, query=""):
        """The primary keys a changelist lists for ``query``, and its count."""
        response = self.client.get(f"{reverse(changelist)}?{query}")
        self.assertEqual(response.status_code, 200)
        listed = response.context["cl"]
        return set(listed.queryset.values_list("pk", flat=True)), listed.result_count


@override_settings(**CRUSH_LU_URL_SETTINGS)
class MutualConnectionFilterTests(AdminFixturesMixin, TestCase):
    """"Mutual" is a reciprocal row at the same event: the definition of the
    changelist's own "Mutual" column. It filtered a 'mutual' status that
    EventConnection never had."""

    def setUp(self):
        super().setUp()
        now = timezone.now()
        mixer = make_event("Mixer", now - timedelta(days=3))
        quiz = make_event("Quiz", now - timedelta(days=10))
        ana, ben, cam, dee = (
            self._profile(name, status="verified").user
            for name in ("ana", "ben", "cam", "dee")
        )

        def connect(requester, recipient, event, status, **fields):
            return EventConnection.objects.create(
                requester=requester,
                recipient=recipient,
                event=event,
                status=status,
                **fields,
            ).pk

        crush = {"flow": EventConnection.FLOW_CRUSH}
        self.rows = {
            # Both asked at the same event, whatever the status.
            "ana_ben": connect(ana, ben, mixer, "shared"),
            "ben_ana": connect(ben, ana, mixer, "pending"),
            # Two private crush leads on each other: the column ticks both.
            "cam_dee": connect(cam, dee, mixer, "pending", **crush),
            "dee_cam": connect(dee, cam, mixer, "coach_reviewing", **crush),
            "ana_cam": connect(ana, cam, mixer, "accepted"),
            # The way back was asked at another event.
            "ben_dee": connect(ben, dee, mixer, "pending"),
            "dee_ben": connect(dee, ben, quiz, "declined"),
        }

    def _rows(self, *names):
        return {self.rows[name] for name in names}

    def test_mutual_lists_exactly_the_rows_the_mutual_column_ticks(self):
        listed, count = self._listed(CONNECTIONS, "connection_type=mutual")

        self.assertEqual(listed, self._rows("ana_ben", "ben_ana", "cam_dee", "dee_cam"))
        self.assertEqual(count, 4)
        for connection in EventConnection.objects.all():
            with self.subTest(connection=connection.pk):
                self.assertEqual(connection.is_mutual, connection.pk in listed)

    def test_one_way_is_every_other_row(self):
        listed, count = self._listed(CONNECTIONS, "connection_type=one_way")

        self.assertEqual(listed, self._rows("ana_cam", "ben_dee", "dee_ben"))
        self.assertEqual(count, 3)

    def test_pending_response_still_filters_the_status(self):
        listed, _ = self._listed(CONNECTIONS, "connection_type=pending")

        self.assertEqual(listed, self._rows("ben_ana", "cam_dee", "ben_dee"))

    def test_mutual_combines_with_the_message_filter(self):
        """`HasMessagesFilter` groups by a Count; the reciprocal annotation
        must survive that GROUP BY."""
        listed, _ = self._listed(CONNECTIONS, "connection_type=mutual&has_messages=no")

        self.assertEqual(listed, self._rows("ana_ben", "ben_ana", "cam_dee", "dee_cam"))


@override_settings(**CRUSH_LU_URL_SETTINGS)
class SubmissionHistoryFilterTests(AdminFixturesMixin, TestCase):
    """`ProfileSubmissionDetailFilter`: every option reads the member's
    latest submission, or the profile for members who never submitted."""

    def _never_submitted_cohort(self):
        never = self._profile("still_in_the_wizard", status="incomplete")
        # Submitted once, then sent back by a coach.
        sent_back = self._profile("sent_back", status="incomplete")
        self._submission(sent_back, "revision", days_ago=5, revision_round=1)
        # Joined after the pivot: submitted, so no row, and waiting or done.
        self._profile("post_pivot_pending")
        self._profile("verified_by_luxid", status="verified")
        self._profile("rejected_at_the_door", status="rejected")
        return never

    def test_never_submitted_is_an_incomplete_profile_with_no_row(self):
        never = self._never_submitted_cohort()

        listed, count = self._listed(PROFILES, "submission_history=never_submitted")

        self.assertEqual(listed, {never.pk})
        self.assertEqual(count, 1)

    def test_the_quick_filter_count_is_the_filter(self):
        """The changelist's "Never Submitted" link carries this count."""
        self._never_submitted_cohort()

        response = self.client.get(reverse(PROFILES))

        self.assertEqual(response.context["filter_counts"]["never_submitted"], 1)
        self.assertContains(response, 'href="?submission_history=never_submitted"')

    def test_revision_requested_is_a_latest_revision_row(self):
        waiting = self._profile("waiting_on_member", status="incomplete")
        self._submission(waiting, "revision", days_ago=5, revision_round=1)
        # Resubmitted: the same row went back to pending.
        back = self._profile("resubmitted")
        self._submission(back, "pending", days_ago=1, revision_round=1)
        older = self._profile("revision_behind_a_newer_row")
        self._submission(older, "revision", days_ago=40)
        self._submission(older, "pending", days_ago=2)
        verified = self._profile("verified_since", status="verified")
        self._submission(verified, "revision", days_ago=20)
        expired = self._profile("revision_expired", status="incomplete")
        self._submission(expired, "revision", days_ago=60)
        self._submission(expired, "expired", days_ago=30)

        listed, _ = self._listed(PROFILES, "submission_history=revision_pending")
        segment, _ = self._listed(
            "crush_admin:crush_lu_revisionneededprofile_changelist"
        )

        self.assertEqual(listed, {waiting.pk})
        # The filter and the Revision Needed segment are one definition.
        self.assertEqual(listed, segment)

    def test_previously_rejected_is_a_latest_rejected_row(self):
        rejected = self._profile("rejected", status="rejected")
        self._submission(rejected, "rejected", days_ago=12)
        # Rejected, then verified by an admin anyway: still history.
        overturned = self._profile("overturned", status="verified")
        self._submission(overturned, "rejected", days_ago=30)
        # A rejection behind a newer row no longer says where they stand.
        moved_on = self._profile("moved_on", status="verified")
        self._submission(moved_on, "rejected", days_ago=90)
        self._submission(moved_on, "approved", days_ago=3)
        # Rejected with no submission at all: not submission history.
        self._profile("rejected_without_a_row", status="rejected")

        listed, _ = self._listed(PROFILES, "submission_history=rejected")

        self.assertEqual(listed, {rejected.pk, overturned.pk})

    def test_resubmitted_is_a_revised_latest_row_that_came_back(self):
        # One row throughout: revised, then re-queued by the resubmission.
        back = self._profile("back_for_review")
        self._submission(back, "pending", days_ago=1, revision_round=1)
        approved = self._profile("approved_after_revision", status="verified")
        self._submission(approved, "approved", days_ago=3, revision_round=2)
        # Asked to revise and not back yet.
        waiting = self._profile("waiting", status="incomplete")
        self._submission(waiting, "revision", days_ago=5, revision_round=1)
        # Two rows, never revised: what the old two-row rule listed.
        two_rows = self._profile("expired_then_verified", status="verified")
        self._submission(two_rows, "expired", days_ago=60)
        self._submission(two_rows, "approved", days_ago=2)
        # Closed by the pivot cleanup mid-revision.
        closed = self._profile("closed_mid_revision")
        self._submission(closed, "expired", days_ago=30, revision_round=1)

        listed, _ = self._listed(PROFILES, "submission_history=resubmitted")

        self.assertEqual(listed, {back.pk, approved.pk})


@override_settings(**CRUSH_LU_URL_SETTINGS)
class CoachAssignmentFilterTests(AdminFixturesMixin, TestCase):
    """`CoachAssignmentFilter` reads ``assigned_coach``, the "Assigned Coach"
    column beside it, not the coaches on submission rows."""

    def setUp(self):
        super().setUp()
        coach_user = User.objects.create_user(
            username="coach_cam", email="coach-cam@example.com", password="pw12345678"
        )
        coach = CrushCoach.objects.create(
            user=coach_user, is_active=True, max_active_reviews=10
        )
        # Joined after the pivot: a permanent coach and no submission row.
        self.assigned = self._profile("assigned", status="verified", assigned_coach=coach)
        # Reviewed before the pivot, never given a permanent coach.
        self.reviewed = self._profile("reviewed_only", status="incomplete")
        self._submission(self.reviewed, "revision", days_ago=40, coach=coach)
        self.nobody = self._profile("nobody")

    def test_has_coach_is_a_permanently_assigned_coach(self):
        listed, _ = self._listed(PROFILES, "coach_assignment=has_coach")

        self.assertEqual(listed, {self.assigned.pk})

    def test_no_coach_is_every_other_member(self):
        listed, _ = self._listed(PROFILES, "coach_assignment=no_coach")

        self.assertEqual(listed, {self.reviewed.pk, self.nobody.pk})

    def test_not_submitted_is_no_longer_offered(self):
        """Its members are under Submission History's "Never Submitted"."""
        response = self.client.get(reverse(PROFILES))

        self.assertContains(response, "coach_assignment=has_coach")
        self.assertNotContains(response, "coach_assignment=not_submitted")


@override_settings(**CRUSH_LU_URL_SETTINGS)
class RecentPendingProfilesTests(AdminFixturesMixin, TestCase):
    """The index's Today's Focus tab and the analytics dashboard's table list
    the members most recently updated while awaiting verification. They
    listed pending ProfileSubmissions, which nobody creates any more."""

    def setUp(self):
        super().setUp()
        now = timezone.now()
        pending = []
        for number in range(12):
            profile = self._profile(f"pending_{number}")
            # The higher the number, the more recent the update.
            CrushProfile.objects.filter(pk=profile.pk).update(
                updated_at=now - timedelta(hours=100 - number)
            )
            pending.append(profile.pk)
        self.newest_first = pending[::-1]
        door = make_event("Upcoming door", now + timedelta(days=4))
        EventRegistration.objects.create(
            event=door,
            user=CrushProfile.objects.get(pk=self.newest_first[0]).user,
            status="confirmed",
        )
        # Updated just now, but not awaiting verification.
        self._profile("verified", status="verified")
        self._profile("incomplete", status="incomplete")
        self._profile("deactivated", is_active=False)
        # An open submission alone does not make a member pending. The old
        # lists showed this row, with a link to review it.
        self.open_row = self._submission(
            self._profile("verified_open_row", status="verified"), "pending", days_ago=0
        )

    def _assert_no_submission_link(self, response):
        # Not a bare "/profilesubmission/": the nav sidebar links that list.
        review = reverse(
            "crush_admin:crush_lu_profilesubmission_change", args=[self.open_row.pk]
        )
        self.assertNotContains(response, f'href="{review}"')

    def test_index_lists_the_five_most_recently_updated_pending_members(self):
        response = self.client.get(reverse("crush_admin:index"))

        listed = [profile.pk for profile in response.context["recent_pending_profiles"]]
        self.assertEqual(listed, self.newest_first[:5])
        for pk in listed:
            change = reverse("crush_admin:crush_lu_crushprofile_change", args=[pk])
            self.assertContains(response, f'href="{change}"')
        self.assertNotIn("recent_submissions", response.context)
        self._assert_no_submission_link(response)

    def test_index_rows_carry_the_door_split(self):
        response = self.client.get(reverse("crush_admin:index"))

        flags = {
            profile.pk: profile.has_door_seat
            for profile in response.context["recent_pending_profiles"]
        }
        self.assertEqual(
            flags, {pk: pk == self.newest_first[0] for pk in self.newest_first[:5]}
        )
        self.assertContains(response, "&bull; booked on an event", count=1)
        self.assertContains(response, "&bull; not booked on an event", count=4)

    def test_dashboard_lists_the_ten_most_recently_updated_pending_members(self):
        response = self.client.get(reverse("crush_admin_dashboard"))

        listed = [profile.pk for profile in response.context["recent_pending_profiles"]]
        self.assertEqual(listed, self.newest_first[:10])
        self.assertNotIn("recent_submissions", response.context)
        self._assert_no_submission_link(response)
        self.assertContains(response, "Recently Updated Pending Profiles")
        for pk in listed:
            change = reverse("crush_admin:crush_lu_crushprofile_change", args=[pk])
            self.assertContains(response, f'href="{change}"')


@override_settings(**CRUSH_LU_URL_SETTINGS)
class CoachDashboardAwaitingVerificationTests(TestCase):
    """The coach dashboard's card counts the pending chip of the "Unverified
    profiles" page it opens. It counted the coach's pending submissions."""

    def setUp(self):
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        self.coach = CrushCoach.objects.create(
            user=self._user("coach@example.com"), is_active=True, max_active_reviews=10
        )
        self.client.force_login(self.coach.user)

    def _user(self, email, *, consent=True, **fields):
        user = User.objects.create_user(
            username=email, email=email, password="pw12345678", **fields
        )
        # The consent row is created by a signal; the page lists only members
        # who accepted the Crush.lu profile layer.
        UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=consent)
        return user

    def _profile(self, email, *, status="pending", consent=True, **fields):
        user_active = fields.pop("user_active", True)
        return CrushProfile.objects.create(
            user=self._user(email, consent=consent, is_active=user_active),
            date_of_birth=date(1990, 1, 1),
            gender="M",
            location="Luxembourg",
            verification_status=status,
            is_approved=status == "verified",
            **fields,
        )

    def test_card_counts_the_pending_chip_it_opens(self):
        self._profile("waiting@example.com")
        # Joined before the pivot; the cleanup closed their row.
        expired_row = self._profile("expired-row@example.com")
        ProfileSubmission.objects.create(profile=expired_row, status="expired")
        # Never counted.
        self._profile("no-consent@example.com", consent=False)
        banned = self._profile("banned@example.com")
        UserDataConsent.objects.filter(user=banned.user).update(crushlu_banned=True)
        self._profile("deactivated@example.com", is_active=False)
        self._profile("closed-account@example.com", user_active=False)
        self._profile("verified@example.com", status="verified")
        self._profile("incomplete@example.com", status="incomplete")
        self._profile("rejected@example.com", status="rejected")

        dashboard = self.client.get(reverse("crush_lu:coach_dashboard"))
        unverified = self.client.get(
            reverse("crush_lu:coach_unverified_profiles"), {"status": "pending"}
        )

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.context["awaiting_verification_count"], 2)
        self.assertEqual(unverified.context["total_count"], 2)
        chip = f'href="{reverse("crush_lu:coach_unverified_profiles")}?status=pending"'
        self.assertContains(dashboard, chip)
        self.assertContains(dashboard, "Awaiting Verification")
        self.assertNotIn("pending_reviews", dashboard.context)

    def test_profiles_badge_counts_what_the_profiles_page_lists_first(self):
        other = CrushCoach.objects.create(
            user=self._user("coach2@example.com"), is_active=True, max_active_reviews=10
        )
        mine = self._profile("mine@example.com")
        ProfileSubmission.objects.create(profile=mine, coach=self.coach, status="pending")
        theirs = self._profile("theirs@example.com")
        ProfileSubmission.objects.create(profile=theirs, coach=other, status="pending")
        done = self._profile("done@example.com", status="verified")
        ProfileSubmission.objects.create(profile=done, coach=self.coach, status="approved")

        dashboard = self.client.get(reverse("crush_lu:coach_dashboard"))
        profiles_page = self.client.get(reverse("crush_lu:coach_profiles"))

        self.assertEqual(dashboard.context["pending_submissions_count"], 1)
        self.assertEqual(len(profiles_page.context["pending_submissions"]), 1)


class EventDoorsTests(TestCase):
    """The door helpers live in `services.event_doors`, and the coach page
    and the Action Center both read them from there."""

    def test_live_or_future_events_are_the_open_doors(self):
        from crush_lu.services.event_doors import live_or_future_event_ids

        now = timezone.now()
        upcoming = make_event("Upcoming", now + timedelta(days=5))
        # Started half an hour ago and runs for two hours.
        running = make_event("Running", now - timedelta(minutes=30))
        unpublished = make_event(
            "Unpublished", now + timedelta(days=6), is_published=False
        )
        make_event("Ended", now - timedelta(days=2))
        make_event("Cancelled", now + timedelta(days=5), is_cancelled=True)

        self.assertEqual(
            set(live_or_future_event_ids(now)),
            {upcoming.pk, running.pk, unpublished.pk},
        )

    def test_one_definition(self):
        from crush_lu import views_coach
        from crush_lu.admin import verification_queues
        from crush_lu.services import event_doors

        self.assertIs(
            views_coach._live_or_future_event_ids,
            event_doors.live_or_future_event_ids,
        )
        self.assertIs(
            verification_queues.live_or_future_event_ids,
            event_doors.live_or_future_event_ids,
        )
        self.assertIs(
            views_coach.DOOR_VISIBLE_REGISTRATION_STATUSES,
            event_doors.DOOR_VISIBLE_REGISTRATION_STATUSES,
        )
