"""
Signal Handler Tests for Crush.lu

Tests signal handlers for:
- Default activity option creation on event save
- Coach staff status management
- Event registration capacity enforcement
"""
import pytest
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta

from crush_lu.models import (
    MeetupEvent,
    EventActivityOption,
    EventRegistration,
    CrushCoach,
)

User = get_user_model()


class TestCreateDefaultActivityOptions(TestCase):
    """Test the create_default_activity_options signal handler."""

    def _create_event(self, title='Test Event'):
        return MeetupEvent.objects.create(
            title=title, description='A test',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg', address='123 Test St',
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )

    def test_new_event_gets_6_default_options(self):
        """A newly created event should automatically get 6 activity options."""
        event = self._create_event()
        options = EventActivityOption.objects.filter(event=event)
        self.assertEqual(options.count(), 6)

    def test_default_options_have_correct_types(self):
        """The 6 default options should include 3 presentation_style and 3 speed_dating_twist."""
        event = self._create_event('Type Test')
        options = EventActivityOption.objects.filter(event=event)
        self.assertEqual(options.filter(activity_type='presentation_style').count(), 3)
        self.assertEqual(options.filter(activity_type='speed_dating_twist').count(), 3)

    def test_saving_existing_event_does_not_create_duplicates(self):
        """Saving an existing event should NOT create duplicate activity options."""
        event = self._create_event('Dupe Test')
        self.assertEqual(EventActivityOption.objects.filter(event=event).count(), 6)

        event.title = 'Updated Title'
        event.save()
        self.assertEqual(EventActivityOption.objects.filter(event=event).count(), 6)


class TestCoachStaffStatusSignal(TestCase):
    """Test the manage_coach_staff_status signal handler."""

    def test_active_coach_gets_staff_status(self):
        """Creating an active coach should grant is_staff=True."""
        user = User.objects.create_user(
            username='newcoach@test.com', email='newcoach@test.com',
            password='testpass123'
        )
        self.assertFalse(user.is_staff)

        CrushCoach.objects.create(
            user=user, bio='Test coach',
            specializations='General', is_active=True,
            max_active_reviews=10
        )

        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_inactive_coach_loses_staff_status(self):
        """Deactivating a coach should revoke is_staff=True."""
        user = User.objects.create_user(
            username='deactivate@test.com', email='deactivate@test.com',
            password='testpass123'
        )

        coach = CrushCoach.objects.create(
            user=user, bio='Test coach',
            specializations='General', is_active=True,
            max_active_reviews=10
        )

        user.refresh_from_db()
        self.assertTrue(user.is_staff)

        coach.is_active = False
        coach.save()

        user.refresh_from_db()
        self.assertFalse(user.is_staff)

    def test_superuser_not_affected_by_coach_deactivation(self):
        """Superuser's staff status should not be modified by coach signals."""
        user = User.objects.create_superuser(
            username='supercoach@test.com', email='supercoach@test.com',
            password='testpass123'
        )

        coach = CrushCoach.objects.create(
            user=user, bio='Super coach',
            specializations='All', is_active=True,
            max_active_reviews=10
        )

        coach.is_active = False
        coach.save()

        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_coach_staff_status_idempotent(self):
        """Saving an already-active coach should not cause errors."""
        user = User.objects.create_user(
            username='idempotent@test.com', email='idempotent@test.com',
            password='testpass123'
        )

        coach = CrushCoach.objects.create(
            user=user, bio='Coach',
            specializations='General', is_active=True,
            max_active_reviews=10
        )

        user.refresh_from_db()
        self.assertTrue(user.is_staff)

        # Save again without changes
        coach.save()
        user.refresh_from_db()
        self.assertTrue(user.is_staff)


class TestEventCapacity(TestCase):
    """Test event registration capacity enforcement."""

    def test_event_full_detection(self):
        """When an event is full, is_full should be True."""
        event = MeetupEvent.objects.create(
            title='Small Event', description='Only 2 spots',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg', address='123 Test St',
            max_participants=2,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )

        for i in range(2):
            user = User.objects.create_user(
                username=f'fill{i}@test.com', email=f'fill{i}@test.com',
                password='testpass123'
            )
            EventRegistration.objects.create(
                event=event, user=user, status='confirmed'
            )

        self.assertTrue(event.is_full)
        self.assertFalse(event.is_registration_open)

    def test_capacity_count_excludes_cancelled(self):
        """Cancelled registrations should not count toward capacity."""
        event = MeetupEvent.objects.create(
            title='Cancel Test', description='Test cancelled',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg', address='123 Test St',
            max_participants=2,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )

        user1 = User.objects.create_user(
            username='cancel1@test.com', email='cancel1@test.com',
            password='testpass123'
        )
        user2 = User.objects.create_user(
            username='cancel2@test.com', email='cancel2@test.com',
            password='testpass123'
        )

        EventRegistration.objects.create(event=event, user=user1, status='confirmed')
        EventRegistration.objects.create(event=event, user=user2, status='cancelled')

        self.assertEqual(event.get_confirmed_count(), 1)
        self.assertFalse(event.is_full)

    def test_waitlist_count_tracked_separately(self):
        """Waitlist registrations should be counted separately."""
        event = MeetupEvent.objects.create(
            title='Waitlist Test', description='Test',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg', address='123 Test St',
            max_participants=1,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )

        user1 = User.objects.create_user(
            username='wl1@test.com', email='wl1@test.com',
            password='testpass123'
        )
        user2 = User.objects.create_user(
            username='wl2@test.com', email='wl2@test.com',
            password='testpass123'
        )

        EventRegistration.objects.create(event=event, user=user1, status='confirmed')
        EventRegistration.objects.create(event=event, user=user2, status='waitlist')

        self.assertEqual(event.get_confirmed_count(), 1)
        self.assertEqual(event.get_waitlist_count(), 1)
        self.assertTrue(event.is_full)
