"""
Unit tests for Crush Connect 7-Day Cycle, Temporary Chat, Coffee Dates,
Pair Exclusions, and Safety Reports (Epic 13 / Slice 1).
"""

from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.connect_phase import (
    cycle_access_open,
    is_event_verified_member,
    receiver_access_open,
)
from crush_lu.models.crush_connect_cycle import (
    ConnectWeekSession,
    ConnectCycleCard,
    ConnectWeeklyRequest,
    ConnectTemporaryChat,
    ConnectChatMessage,
    ConnectCoffeeDate,
    ConnectPairExclusion,
    ConnectReport,
)
from crush_lu.models.profiles import CrushProfile, CrushCoach
from crush_lu.models.events import MeetupEvent, EventRegistration
from hub.models import Location


class ConnectCycleModelTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username="alice", email="alice@test.lu", first_name="Alice")
        self.user2 = User.objects.create_user(username="bob", email="bob@test.lu", first_name="Bob")
        self.user3 = User.objects.create_user(username="claire", email="claire@test.lu", first_name="Claire")

        self.profile1 = CrushProfile.objects.create(
            user=self.user1,
            verification_status="verified",
            verification_method="coach_event",
            gender="woman",
        )
        self.profile2 = CrushProfile.objects.create(
            user=self.user2,
            verification_status="verified",
            verification_method="luxid",
            gender="man",
        )

        self.location = Location.objects.create(
            name="Café de Paris",
            address="Place d'Armes 1",
            city="Luxembourg",
            max_capacity=50,
            partnership_stage="Active",
        )

    def test_connect_week_session_lifecycle(self):
        session = ConnectWeekSession.objects.create(user=self.user1)
        self.assertEqual(session.current_day_number, 1)
        self.assertEqual(session.status, ConnectWeekSession.Status.ACTIVE)
        self.assertTrue(session.is_active)
        self.assertFalse(session.is_review_open)
        self.assertFalse(session.is_review_active)

        # Open 24h review
        session.open_weekly_review(highlight_user=self.user2)
        session.refresh_from_db()
        self.assertEqual(session.status, ConnectWeekSession.Status.REVIEW_OPEN)
        self.assertTrue(session.is_review_open)
        self.assertTrue(session.is_review_active)
        self.assertEqual(session.compatibility_highlight_user, self.user2)
        self.assertIsNotNone(session.review_expires_at)
        self.assertTrue(session.review_expires_at > timezone.now())

    def test_connect_cycle_card_creation_and_constraints(self):
        session = ConnectWeekSession.objects.create(user=self.user1)

        card1 = ConnectCycleCard.objects.create(
            session=session,
            day_number=1,
            card_index=1,
            target_user=self.user2,
            generated_date=timezone.localdate(),
            answers_json={"q1": "yes", "energy_match": "high"},
            is_completed=True,
            completed_at=timezone.now(),
        )
        self.assertEqual(card1.session, session)
        self.assertTrue(card1.is_completed)

        # Unique constraint on (session, day_number, card_index)
        with self.assertRaises(Exception):
            ConnectCycleCard.objects.create(
                session=session,
                day_number=1,
                card_index=1,
                target_user=self.user3,
                generated_date=timezone.localdate(),
            )

    def test_connect_weekly_request_expiration(self):
        session = ConnectWeekSession.objects.create(user=self.user1)
        request = ConnectWeeklyRequest.objects.create(
            session=session,
            requester=self.user1,
            recipient=self.user2,
        )
        self.assertEqual(request.status, ConnectWeeklyRequest.Status.PENDING)
        self.assertIsNotNone(request.expires_at)
        self.assertFalse(request.is_expired)

        # Expire request artificially
        request.expires_at = timezone.now() - timedelta(minutes=5)
        self.assertTrue(request.is_expired)

    def test_connect_temporary_chat_and_coffee_date_flow(self):
        session = ConnectWeekSession.objects.create(user=self.user1)
        req = ConnectWeeklyRequest.objects.create(
            session=session,
            requester=self.user1,
            recipient=self.user2,
            status=ConnectWeeklyRequest.Status.ACCEPTED,
        )

        chat = ConnectTemporaryChat.objects.create(
            request=req,
            participant_1=self.user1,
            participant_2=self.user2,
        )
        self.assertEqual(chat.status, ConnectTemporaryChat.Status.ACTIVE)
        self.assertEqual(chat.get_other_participant(self.user1), self.user2)
        self.assertEqual(chat.get_other_participant(self.user2), self.user1)
        self.assertGreater(chat.expires_at, timezone.now() + timedelta(days=6))

        # Send messages
        msg = ConnectChatMessage.objects.create(
            chat=chat,
            sender=self.user1,
            message="Hi Bob! Coffee this Thursday?",
        )
        self.assertEqual(chat.messages.count(), 1)
        self.assertIn("Hi Bob", msg.message)

        # Propose Coffee Date
        coffee_date = ConnectCoffeeDate.objects.create(
            chat=chat,
            proposer=self.user1,
            venue_location=self.location,
            proposed_date=timezone.localdate() + timedelta(days=2),
            proposed_time_slot="18:30",
        )
        self.assertEqual(coffee_date.status, ConnectCoffeeDate.Status.PROPOSED)

        # Alice confirms after meeting
        coffee_date.confirm_by_user(self.user1)
        coffee_date.refresh_from_db()
        self.assertIsNotNone(coffee_date.participant_1_confirmed_at)
        self.assertIsNone(coffee_date.meeting_confirmed_at)

        # Bob confirms
        coffee_date.confirm_by_user(self.user2)
        coffee_date.refresh_from_db()
        chat.refresh_from_db()

        self.assertEqual(coffee_date.status, ConnectCoffeeDate.Status.CONFIRMED_BY_BOTH)
        self.assertIsNotNone(coffee_date.meeting_confirmed_at)
        self.assertEqual(chat.status, ConnectTemporaryChat.Status.MEETING_CONFIRMED)
        # Verify chat is extended for 3 days post-meeting
        self.assertAlmostEqual(
            (chat.expires_at - timezone.now()).total_seconds(),
            timedelta(days=3).total_seconds(),
            delta=30,
        )

    def test_connect_pair_exclusion_symmetry(self):
        self.assertFalse(ConnectPairExclusion.are_excluded(self.user1, self.user2))
        self.assertFalse(ConnectPairExclusion.are_excluded(self.user2, self.user1))

        ConnectPairExclusion.exclude_pair(
            self.user1,
            self.user2,
            reason=ConnectPairExclusion.Reason.MEMBER_BLOCKED,
        )

        self.assertTrue(ConnectPairExclusion.are_excluded(self.user1, self.user2))
        self.assertTrue(ConnectPairExclusion.are_excluded(self.user2, self.user1))

    def test_connect_report_filing(self):
        report = ConnectReport.objects.create(
            reporter=self.user1,
            reported_user=self.user2,
            reason="inappropriate_language",
            details="Sent unsolicited text in chat.",
        )
        self.assertEqual(report.status, ConnectReport.Status.PENDING)
        self.assertEqual(self.user1.connect_reports_filed.count(), 1)
        self.assertEqual(self.user2.connect_reports_received.count(), 1)


class ConnectPhaseGatingTests(TestCase):
    def setUp(self):
        self.event_user = User.objects.create_user(username="attendee", email="att@test.lu")
        self.luxid_user = User.objects.create_user(username="luxid_only", email="lux@test.lu")
        self.unverified_user = User.objects.create_user(username="unverified", email="unv@test.lu")

        CrushProfile.objects.create(
            user=self.event_user,
            verification_status="verified",
            verification_method="coach_event",
        )
        CrushProfile.objects.create(
            user=self.luxid_user,
            verification_status="verified",
            verification_method="luxid",
        )
        CrushProfile.objects.create(
            user=self.unverified_user,
            verification_status="unverified",
        )

    @override_settings(CRUSH_CONNECT_CANDIDATE_OPEN=True, CRUSH_CONNECT_LAUNCHED=False)
    def test_cycle_access_in_beta(self):
        # Event verified user gets 7-Day Cycle access in beta
        self.assertTrue(cycle_access_open(self.event_user))

        # LuxID-only user is in candidate pool, not cycle-eligible in beta
        self.assertFalse(cycle_access_open(self.luxid_user))

        # Unverified user has no cycle access
        self.assertFalse(cycle_access_open(self.unverified_user))

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_cycle_access_at_full_launch(self):
        self.assertTrue(cycle_access_open(self.event_user))
        self.assertTrue(cycle_access_open(self.luxid_user))

    @override_settings(CRUSH_CONNECT_CANDIDATE_OPEN=True, CRUSH_CONNECT_LAUNCHED=False)
    def test_receiver_access_stays_staff_and_selected_tester_only_in_beta(self):
        """Today's Drop (the already-live feature) must NOT widen just because
        the 7-Day Cycle's gate does — regression guard for the bug where
        ``cycle_access_open``'s event-verified fallback leaked into
        ``receiver_access_open`` (caught by
        ``test_beta_premium_non_tester_treated_as_candidate``)."""
        self.assertFalse(receiver_access_open(self.event_user))
        self.assertFalse(receiver_access_open(self.luxid_user))
        self.assertFalse(receiver_access_open(self.unverified_user))
