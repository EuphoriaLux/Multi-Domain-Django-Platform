"""
"My Crush!" Phase B — lead model tests.

Spec: docs/superpowers/specs/2026-07-21-crush-my-crush-post-event-flow.md

Covers (Phase B scope only):
- flow discriminator default + no backfill semantics (§7, §13)
- additive migration state (§7)
- call-tracking fields, "call by" SLA timestamp (§6/O8, §7)
- routing tiers: assigned coach -> event.coaches selection policy -> pool,
  including deactivated coaches falling through (§5/§7, §13 stale-coach)
- coach action queue queryset excluding legacy rows and non-actionable leads

Deliberately NOT covered here (later phases): member declaration flow and
privacy matrix (Phase C), coach UI/notifications/reminder sweep (Phase D).

Run with: pytest crush_lu/tests/test_crush_leads.py -v
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventConnection,
    MeetupEvent,
    ProfileSubmission,
)
from crush_lu.services.crush_leads import coach_action_queue, reminder_due

User = get_user_model()


def make_user(username, gender='M'):
    user = User.objects.create_user(
        username=username,
        email=username,
        password='testpass123',
    )
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender=gender,
        location='Luxembourg',
        is_approved=True,
        is_active=True,
    )
    return user, profile


def make_coach(username, is_active=True):
    user = User.objects.create_user(
        username=username,
        email=username,
        password='coachpass123',
    )
    return CrushCoach.objects.create(
        user=user,
        bio='Test coach',
        is_active=is_active,
    )


class CrushLeadBaseTestCase(TestCase):
    def setUp(self):
        self.requester, self.requester_profile = make_user('requester@example.com', 'M')
        self.recipient, _ = make_user('recipient@example.com', 'F')
        self.event = MeetupEvent.objects.create(
            title='Crush Lead Test Event',
            description='Event for crush lead tests',
            event_type='mixer',
            date_time=timezone.now() - timedelta(hours=2),
            location='Luxembourg',
            address='1 Test Street',
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(days=3),
            is_published=True,
        )

    def make_connection(self, **kwargs):
        defaults = {
            'event': self.event,
            'requester': self.requester,
            'recipient': self.recipient,
        }
        defaults.update(kwargs)
        return EventConnection.objects.create(**defaults)


class FlowDiscriminatorTests(CrushLeadBaseTestCase):
    """§7: durable flow discriminator; historical rows never become leads."""

    def test_flow_defaults_to_legacy(self):
        connection = self.make_connection()
        self.assertEqual(connection.flow, EventConnection.FLOW_LEGACY)

    def test_call_tracking_fields_nullable(self):
        connection = self.make_connection(flow=EventConnection.FLOW_CRUSH)
        self.assertIsNone(connection.coach_call_scheduled_at)
        self.assertIsNone(connection.coach_call_completed_at)
        self.assertIsNone(connection.call_outcome)
        self.assertIsNone(connection.reminder_sent_at)

    def test_historical_pending_row_never_in_crush_queue(self):
        """Pre-launch pending rows keep flow=legacy and stay out of the queue."""
        coach = make_coach('coach-queue@example.com')
        legacy = self.make_connection(assigned_coach=coach, status='pending')

        self.assertEqual(legacy.flow, EventConnection.FLOW_LEGACY)
        self.assertFalse(
            EventConnection.objects.crush_leads().filter(pk=legacy.pk).exists()
        )
        self.assertFalse(
            EventConnection.objects.open_crush_leads().filter(pk=legacy.pk).exists()
        )
        self.assertEqual(list(coach_action_queue(coach)), [])

    def test_no_pending_model_changes_after_migration(self):
        """The Phase B migration captures the full model state (additive)."""
        # Exits non-zero (SystemExit) if makemigrations would create changes.
        call_command('makemigrations', 'crush_lu', check=True, dry_run=True, verbosity=0)


class CallBySlaTests(CrushLeadBaseTestCase):
    """§6/O8: "call by" timestamp based on the 48h SLA default."""

    def test_call_by_is_requested_at_plus_48h(self):
        connection = self.make_connection(flow=EventConnection.FLOW_CRUSH)
        declared_at = timezone.now() - timedelta(hours=3)
        EventConnection.objects.filter(pk=connection.pk).update(requested_at=declared_at)
        connection.refresh_from_db()

        self.assertEqual(connection.call_by, declared_at + timedelta(hours=48))

    def test_call_by_is_none_for_legacy_rows(self):
        connection = self.make_connection()
        self.assertIsNone(connection.call_by)

    def test_reminder_due_after_24h_only_once(self):
        """reminder_sent_at is the idempotency record for the 24h reminder."""
        connection = self.make_connection(flow=EventConnection.FLOW_CRUSH)
        old = timezone.now() - timedelta(hours=25)
        EventConnection.objects.filter(pk=connection.pk).update(requested_at=old)
        connection.refresh_from_db()
        self.assertTrue(reminder_due(connection))

        connection.reminder_sent_at = timezone.now()
        self.assertFalse(reminder_due(connection))

    def test_reminder_never_due_for_completed_or_declined(self):
        connection = self.make_connection(flow=EventConnection.FLOW_CRUSH)
        old = timezone.now() - timedelta(hours=25)
        EventConnection.objects.filter(pk=connection.pk).update(requested_at=old)
        connection.refresh_from_db()

        connection.coach_call_completed_at = timezone.now()
        self.assertFalse(reminder_due(connection))

        connection.coach_call_completed_at = None
        connection.status = 'declined'
        self.assertFalse(reminder_due(connection))

    def test_reminder_not_due_when_call_scheduled(self):
        """A scheduled call counts as touched — no 24h untouched reminder."""
        connection = self.make_connection(flow=EventConnection.FLOW_CRUSH)
        old = timezone.now() - timedelta(hours=25)
        EventConnection.objects.filter(pk=connection.pk).update(requested_at=old)
        connection.refresh_from_db()

        connection.coach_call_scheduled_at = timezone.now() + timedelta(hours=12)
        self.assertFalse(reminder_due(connection))


class RoutingTierTests(CrushLeadBaseTestCase):
    """
    §5/§7: assigned coach -> event.coaches selection policy -> active pool.
    Every tier filters is_active; stale assignments fall through (§13).
    """

    def setUp(self):
        super().setUp()
        self.assigned_coach = make_coach('coach-assigned@example.com')
        self.event_coach_1 = make_coach('coach-event-1@example.com')
        self.event_coach_2 = make_coach('coach-event-2@example.com')

    def approve_with_coach(self, coach):
        return ProfileSubmission.objects.create(
            profile=self.requester_profile,
            coach=coach,
            status='approved',
        )

    def test_tier1_active_assigned_coach_wins(self):
        self.approve_with_coach(self.assigned_coach)
        self.event.coaches.add(self.event_coach_1)

        connection = self.make_connection()
        connection.assign_coach()

        self.assertEqual(connection.assigned_coach, self.assigned_coach)

    def test_tier1_deactivated_assigned_coach_falls_through(self):
        self.approve_with_coach(self.assigned_coach)
        self.assigned_coach.is_active = False
        self.assigned_coach.save()
        self.event.coaches.add(self.event_coach_1)

        connection = self.make_connection()
        connection.assign_coach()

        self.assertEqual(connection.assigned_coach, self.event_coach_1)

    def test_permanent_profile_coach_when_no_approved_submission(self):
        """CrushProfile.assigned_coach (set at first event/premium) routes the
        lead when there is no approved ProfileSubmission (Codex P1)."""
        self.requester_profile.assigned_coach = self.assigned_coach
        self.requester_profile.save()
        self.event.coaches.add(self.event_coach_1)

        connection = self.make_connection()
        connection.assign_coach()

        self.assertEqual(connection.assigned_coach, self.assigned_coach)

    def test_deactivated_permanent_profile_coach_falls_through(self):
        self.requester_profile.assigned_coach = self.assigned_coach
        self.requester_profile.save()
        self.assigned_coach.is_active = False
        self.assigned_coach.save()
        self.event.coaches.add(self.event_coach_1)

        connection = self.make_connection()
        connection.assign_coach()

        self.assertEqual(connection.assigned_coach, self.event_coach_1)

    def test_tier2_event_coach_when_no_assigned(self):
        self.event.coaches.add(self.event_coach_1)

        connection = self.make_connection()
        connection.assign_coach()

        self.assertEqual(connection.assigned_coach, self.event_coach_1)

    def test_tier2_deactivated_event_coach_falls_through_to_active_one(self):
        inactive_event_coach = make_coach('coach-event-inactive@example.com', is_active=False)
        self.event.coaches.add(inactive_event_coach, self.event_coach_1)

        connection = self.make_connection()
        connection.assign_coach()

        self.assertEqual(connection.assigned_coach, self.event_coach_1)

    def test_tier2_all_event_coaches_inactive_falls_to_pool(self):
        inactive_event_coach = make_coach('coach-event-inactive2@example.com', is_active=False)
        self.event.coaches.add(inactive_event_coach)

        connection = self.make_connection()
        connection.assign_coach()

        # Pool fallback: first active coach by id — never the inactive one.
        self.assertIsNotNone(connection.assigned_coach)
        self.assertTrue(connection.assigned_coach.is_active)
        self.assertNotEqual(connection.assigned_coach, inactive_event_coach)

    def test_tier2_selection_policy_least_loaded(self):
        """Least-loaded by open crush leads, else first by id."""
        self.event.coaches.add(self.event_coach_1, self.event_coach_2)

        # event_coach_1 (lower id) already carries one open crush lead.
        open_lead = self.make_connection(
            flow=EventConnection.FLOW_CRUSH,
            assigned_coach=self.event_coach_1,
        )
        self.assertTrue(
            EventConnection.objects.open_crush_leads().filter(pk=open_lead.pk).exists()
        )

        connection = self.make_connection(
            recipient=make_user('recipient-routing@example.com', 'F')[0],
        )
        connection.assign_coach()

        self.assertEqual(connection.assigned_coach, self.event_coach_2)

    def test_tier2_selection_policy_tie_picks_first_by_id(self):
        self.event.coaches.add(self.event_coach_1, self.event_coach_2)

        connection = self.make_connection()
        connection.assign_coach()

        self.assertEqual(connection.assigned_coach, self.event_coach_1)

    def test_tier2_load_count_ignores_completed_and_legacy(self):
        """Completed calls and legacy rows don't count toward coach load."""
        self.event.coaches.add(self.event_coach_1, self.event_coach_2)
        self.make_connection(
            flow=EventConnection.FLOW_CRUSH,
            assigned_coach=self.event_coach_1,
            coach_call_completed_at=timezone.now(),
        )
        self.make_connection(
            assigned_coach=self.event_coach_1,  # legacy row — never counted
            recipient=make_user('recipient-legacy@example.com', 'F')[0],
        )

        connection = self.make_connection(
            recipient=make_user('recipient-fresh@example.com', 'F')[0],
        )
        connection.assign_coach()

        # No *open crush* load on coach 1 -> tie -> first by id.
        self.assertEqual(connection.assigned_coach, self.event_coach_1)

    def test_tier3_pool_fallback_first_active_coach_by_id(self):
        connection = self.make_connection()
        connection.assign_coach()

        expected = CrushCoach.objects.filter(is_active=True).order_by('id').first()
        self.assertEqual(connection.assigned_coach, expected)


class CoachActionQueueTests(CrushLeadBaseTestCase):
    """§5/§7: coach action queue — open crush leads with a "call by" SLA."""

    def setUp(self):
        super().setUp()
        self.coach = make_coach('coach-action@example.com')
        self.other_coach = make_coach('coach-other@example.com')

    def test_queue_lists_open_crush_leads_for_coach_oldest_first(self):
        first = self.make_connection(
            flow=EventConnection.FLOW_CRUSH, assigned_coach=self.coach,
        )
        second = self.make_connection(
            flow=EventConnection.FLOW_CRUSH, assigned_coach=self.coach,
            recipient=make_user('recipient2@example.com', 'F')[0],
        )
        self.assertEqual(
            list(coach_action_queue(self.coach)),
            [first, second],
        )
        for lead in coach_action_queue(self.coach):
            self.assertEqual(lead.call_by, lead.requested_at + timedelta(hours=48))

    def test_queue_excludes_legacy_rows(self):
        self.make_connection(assigned_coach=self.coach, status='pending')
        self.assertEqual(list(coach_action_queue(self.coach)), [])

    def test_queue_excludes_non_actionable_statuses(self):
        for status in ('declined', 'shared'):
            self.make_connection(
                flow=EventConnection.FLOW_CRUSH,
                assigned_coach=self.coach,
                status=status,
                recipient=make_user(f'recipient-{status}@example.com', 'F')[0],
            )
        self.assertEqual(list(coach_action_queue(self.coach)), [])

    def test_queue_excludes_completed_calls(self):
        self.make_connection(
            flow=EventConnection.FLOW_CRUSH,
            assigned_coach=self.coach,
            coach_call_completed_at=timezone.now(),
        )
        self.assertEqual(list(coach_action_queue(self.coach)), [])

    def test_queue_excludes_other_coaches_leads(self):
        self.make_connection(
            flow=EventConnection.FLOW_CRUSH, assigned_coach=self.other_coach,
        )
        self.assertEqual(list(coach_action_queue(self.coach)), [])
