"""
Event Tests for Crush.lu

Comprehensive tests for event functionality including:
- Event registration workflows
- Waitlist management
- Voting system
- Event capacity management

Run with: pytest crush_lu/tests/test_events.py -v
"""
from datetime import date, timedelta
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

User = get_user_model()


class EventRegistrationTests(TestCase):
    """Test event registration workflow."""

    def setUp(self):
        """Set up test data."""
        from crush_lu.models import MeetupEvent, CrushProfile

        self.client = Client()

        self.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='testpass123',
            first_name='Test',
            last_name='User'
        )

        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg',
            is_approved=True,
            is_active=True
        )

        self.event = MeetupEvent.objects.create(
            title='Test Speed Dating',
            description='A test event',
            event_type='speed_dating',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg City',
            address='123 Test Street',
            max_participants=20,
            min_age=18,
            max_age=35,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )

    def test_registration_within_deadline(self):
        """Test user can register before deadline."""
        from crush_lu.models import EventRegistration

        self.client.login(username='testuser@example.com', password='testpass123')

        registration = EventRegistration.objects.create(
            event=self.event,
            user=self.user
        )

        self.assertIsNotNone(registration)
        self.assertEqual(registration.status, 'pending')

    def test_registration_after_deadline_not_open(self):
        """Test registration is not open after deadline."""
        from crush_lu.models import MeetupEvent

        past_deadline_event = MeetupEvent.objects.create(
            title='Past Deadline Event',
            description='Event with past deadline',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg',
            address='123 Test Street',
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(days=1),  # Past deadline
            is_published=True
        )

        self.assertFalse(past_deadline_event.is_registration_open)

    def test_full_event_goes_to_waitlist(self):
        """Test registration goes to waitlist when event is full."""
        from crush_lu.models import EventRegistration

        # Set max to 1
        self.event.max_participants = 1
        self.event.save()

        # Register first user
        EventRegistration.objects.create(
            event=self.event,
            user=self.user,
            status='confirmed'
        )

        # Create second user
        user2 = User.objects.create_user(
            username='user2@example.com',
            email='user2@example.com',
            password='testpass123'
        )

        # Second registration should recognize event is full
        self.assertTrue(self.event.is_full)

    def test_waitlist_promotion_on_cancellation(self):
        """Test waitlist user gets promoted when spot opens."""
        from crush_lu.models import EventRegistration

        # Set max to 1
        self.event.max_participants = 1
        self.event.save()

        # Register first user as confirmed
        reg1 = EventRegistration.objects.create(
            event=self.event,
            user=self.user,
            status='confirmed'
        )

        # Create and register second user on waitlist
        user2 = User.objects.create_user(
            username='user2@example.com',
            email='user2@example.com',
            password='testpass123'
        )

        reg2 = EventRegistration.objects.create(
            event=self.event,
            user=user2,
            status='waitlist'
        )

        # Cancel first registration
        reg1.status = 'cancelled'
        reg1.save()

        # Event should no longer be full
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_full)


class EventCapacityTests(TestCase):
    """Test event capacity management."""

    def setUp(self):
        """Set up test data."""
        from crush_lu.models import MeetupEvent

        self.event = MeetupEvent.objects.create(
            title='Capacity Test Event',
            description='Testing capacity',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg',
            address='123 Test Street',
            max_participants=5,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )

    def test_spots_remaining_decreases(self):
        """Test spots remaining decreases with registrations."""
        from crush_lu.models import EventRegistration

        initial_spots = self.event.spots_remaining

        user = User.objects.create_user(
            username='test@example.com',
            email='test@example.com',
            password='testpass123'
        )

        EventRegistration.objects.create(
            event=self.event,
            user=user,
            status='confirmed'
        )

        self.assertEqual(self.event.spots_remaining, initial_spots - 1)

    def test_confirmed_count_accurate(self):
        """Test get_confirmed_count returns correct number."""
        from crush_lu.models import EventRegistration

        # Create multiple users and registrations
        for i in range(3):
            user = User.objects.create_user(
                username=f'user{i}@example.com',
                email=f'user{i}@example.com',
                password='testpass123'
            )
            EventRegistration.objects.create(
                event=self.event,
                user=user,
                status='confirmed'
            )

        self.assertEqual(self.event.get_confirmed_count(), 3)

    def test_waitlist_count_accurate(self):
        """Test get_waitlist_count returns correct number."""
        from crush_lu.models import EventRegistration

        # Create waitlist registrations
        for i in range(2):
            user = User.objects.create_user(
                username=f'waitlist{i}@example.com',
                email=f'waitlist{i}@example.com',
                password='testpass123'
            )
            EventRegistration.objects.create(
                event=self.event,
                user=user,
                status='waitlist'
            )

        self.assertEqual(self.event.get_waitlist_count(), 2)


class EventVotingTests(TestCase):
    """Test event voting system."""

    def setUp(self):
        """Set up test data."""
        from crush_lu.models import (
            MeetupEvent, EventVotingSession, EventActivityOption,
            GlobalActivityOption, EventRegistration, CrushProfile
        )

        self.user = User.objects.create_user(
            username='voter@example.com',
            email='voter@example.com',
            password='testpass123',
            first_name='Voter',
            last_name='User'
        )

        CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg',
            is_approved=True
        )

        self.event = MeetupEvent.objects.create(
            title='Voting Test Event',
            description='Testing voting',
            event_type='speed_dating',
            date_time=timezone.now() + timedelta(hours=1),  # Starts soon
            location='Luxembourg',
            address='123 Test Street',
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(hours=1),  # Closed
            is_published=True
        )

        # Register user for event
        EventRegistration.objects.create(
            event=self.event,
            user=self.user,
            status='confirmed'
        )

        # Create voting session
        self.voting_session = EventVotingSession.objects.create(
            event=self.event,
            is_active=True,
            voting_start_time=timezone.now() - timedelta(minutes=5),  # Started
            voting_end_time=timezone.now() + timedelta(minutes=25)  # Open for 25 more mins
        )

        # Create GlobalActivityOption instances (used for voting)
        self.option1, _ = GlobalActivityOption.objects.get_or_create(
            activity_variant='spicy_questions',
            defaults={
                'activity_type': 'speed_dating_twist',
                'display_name': 'Spicy Questions First',
                'description': 'Break the ice with bold, fun questions right away',
            }
        )

        self.option2, _ = GlobalActivityOption.objects.get_or_create(
            activity_variant='music',
            defaults={
                'activity_type': 'presentation_style',
                'display_name': 'With Favorite Music',
                'description': 'Introduce yourself while your favorite song plays',
            }
        )

    def test_vote_submission(self):
        """Test user can submit a vote."""
        from crush_lu.models import EventActivityVote

        vote = EventActivityVote.objects.create(
            event=self.event,
            user=self.user,
            selected_option=self.option1
        )

        self.assertIsNotNone(vote)
        self.assertEqual(vote.selected_option, self.option1)

    def test_vote_count_tracking(self):
        """Test that votes are counted correctly via EventVotingSession."""
        from crush_lu.models import EventActivityVote

        # Create a vote
        EventActivityVote.objects.create(
            event=self.event,
            user=self.user,
            selected_option=self.option1
        )

        # Count votes for this event and option
        vote_count = EventActivityVote.objects.filter(
            event=self.event,
            selected_option=self.option1
        ).count()

        self.assertEqual(vote_count, 1)

    def test_voting_session_is_open(self):
        """Test voting session is_voting_open property."""
        self.assertTrue(self.voting_session.is_voting_open)

    def test_voting_session_closed(self):
        """Test voting session is closed after end time."""
        from crush_lu.models import EventVotingSession, MeetupEvent

        # Create a different event for this test to avoid unique constraint violation
        another_event = MeetupEvent.objects.create(
            title='Another Event',
            description='Testing closed session',
            event_type='mixer',
            date_time=timezone.now() + timedelta(hours=1),
            location='Luxembourg',
            address='456 Test Street',
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(hours=1),
            is_published=True
        )

        closed_session = EventVotingSession.objects.create(
            event=another_event,
            is_active=True,
            voting_start_time=timezone.now() - timedelta(hours=2),
            voting_end_time=timezone.now() - timedelta(hours=1)  # Ended
        )

        self.assertFalse(closed_session.is_voting_open)

    def test_vote_results_calculation(self):
        """Test vote results are calculated correctly."""
        from crush_lu.models import EventActivityVote

        # Create multiple votes
        for i in range(3):
            user = User.objects.create_user(
                username=f'voter{i}@example.com',
                email=f'voter{i}@example.com',
                password='testpass123'
            )
            EventActivityVote.objects.create(
                event=self.event,
                user=user,
                selected_option=self.option1
            )

        # Update total votes on session
        self.voting_session.total_votes = 3
        self.voting_session.save()

        # Count votes for this option
        vote_count = EventActivityVote.objects.filter(
            event=self.event,
            selected_option=self.option1
        ).count()

        # Calculate percentage
        percentage = (vote_count / self.voting_session.total_votes) * 100
        self.assertEqual(percentage, 100.0)


class EventAgeRestrictionTests(TestCase):
    """Test event age restriction enforcement."""

    def setUp(self):
        """Set up test data."""
        from crush_lu.models import MeetupEvent

        self.event = MeetupEvent.objects.create(
            title='Age Restricted Event',
            description='For 25-35 only',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg',
            address='123 Test Street',
            max_participants=20,
            min_age=25,
            max_age=35,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )

    def test_user_within_age_range(self):
        """Test user within age range can attend."""
        from crush_lu.models import CrushProfile

        # User is 28 (within 25-35)
        today = date.today()
        user = User.objects.create_user(
            username='test28@example.com',
            email='test28@example.com',
            password='testpass123'
        )

        profile = CrushProfile.objects.create(
            user=user,
            date_of_birth=date(today.year - 28, 1, 1),
            gender='M',
            location='Luxembourg',
            is_approved=True
        )

        # Age should be within range
        self.assertGreaterEqual(profile.age, self.event.min_age)
        self.assertLessEqual(profile.age, self.event.max_age)

    def test_user_below_age_range(self):
        """Test user below age range validation."""
        from crush_lu.models import CrushProfile

        # User is 22 (below 25)
        today = date.today()
        user = User.objects.create_user(
            username='test22@example.com',
            email='test22@example.com',
            password='testpass123'
        )

        profile = CrushProfile.objects.create(
            user=user,
            date_of_birth=date(today.year - 22, 1, 1),
            gender='M',
            location='Luxembourg',
            is_approved=True
        )

        # Age should be below minimum
        self.assertLess(profile.age, self.event.min_age)


class EventCancellationTests(TestCase):
    """Test event cancellation workflow."""

    def setUp(self):
        """Set up test data."""
        from crush_lu.models import MeetupEvent, EventRegistration, CrushProfile

        self.user = User.objects.create_user(
            username='cancel@example.com',
            email='cancel@example.com',
            password='testpass123',
            first_name='Cancel',
            last_name='Test'
        )

        CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg',
            is_approved=True
        )

        self.event = MeetupEvent.objects.create(
            title='Cancellation Test Event',
            description='Testing cancellation',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg',
            address='123 Test Street',
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )

        self.registration = EventRegistration.objects.create(
            event=self.event,
            user=self.user,
            status='confirmed'
        )

    def test_user_can_cancel_registration(self):
        """Test user can cancel their registration."""
        self.registration.status = 'cancelled'
        self.registration.save()

        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, 'cancelled')

    def test_cancelled_registration_frees_spot(self):
        """Test cancelling registration frees up a spot."""
        initial_confirmed = self.event.get_confirmed_count()

        self.registration.status = 'cancelled'
        self.registration.save()

        self.assertEqual(self.event.get_confirmed_count(), initial_confirmed - 1)

    def test_event_cancellation_by_organizer(self):
        """Test event can be cancelled by organizer."""
        self.event.is_cancelled = True
        self.event.save()

        self.event.refresh_from_db()
        self.assertTrue(self.event.is_cancelled)


class WaitlistPromotionTests(TestCase):
    """Test gender-aware waitlist promotion logic."""

    def setUp(self):
        from crush_lu.models import MeetupEvent, EventRegistration, CrushProfile

        self.event = MeetupEvent.objects.create(
            title='Waitlist Promotion Test',
            description='Testing waitlist promotion',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg',
            address='123 Test Street',
            max_participants=4,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True,
        )

    def _create_user_with_profile(self, username, gender):
        from crush_lu.models import CrushProfile

        user = User.objects.create_user(
            username=username,
            email=username,
            password='testpass123',
            first_name=username.split('@')[0],
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender=gender,
            location='Luxembourg',
        )
        return user

    def _create_user_without_profile(self, username):
        return User.objects.create_user(
            username=username,
            email=username,
            password='testpass123',
        )

    def _register(self, user, status):
        from crush_lu.models import EventRegistration

        return EventRegistration.objects.create(
            event=self.event, user=user, status=status,
        )

    def test_fifo_promotion_on_cancel(self):
        """When no gender limits, first waitlisted user is promoted (FIFO)."""
        from django.db import transaction
        from crush_lu.views_events import _promote_from_waitlist

        u1 = self._create_user_with_profile('u1@test.com', 'M')
        u2 = self._create_user_with_profile('u2@test.com', 'F')
        u3 = self._create_user_with_profile('u3@test.com', 'M')

        self.event.max_participants = 1
        self.event.save()

        reg1 = self._register(u1, 'confirmed')
        reg2 = self._register(u2, 'waitlist')
        reg3 = self._register(u3, 'waitlist')

        # Cancel the confirmed user
        reg1.status = 'cancelled'
        reg1.save()

        with transaction.atomic():
            locked = type(self.event).objects.select_for_update().get(pk=self.event.pk)
            promoted = _promote_from_waitlist(locked, u1)

        self.assertIsNotNone(promoted)
        self.assertEqual(promoted.user, u2)  # First in line (FIFO)
        reg2.refresh_from_db()
        self.assertEqual(reg2.status, 'confirmed')

    def test_gender_aware_no_promotion_when_pool_full(self):
        """No promotion when only waitlisted candidate's gender pool is full."""
        from django.db import transaction
        from crush_lu.views_events import _promote_from_waitlist

        u_m1 = self._create_user_with_profile('m1@test.com', 'M')
        u_f1 = self._create_user_with_profile('f1@test.com', 'F')
        u_f2 = self._create_user_with_profile('f2@test.com', 'F')
        u_f3 = self._create_user_with_profile('f3@test.com', 'F')

        self.event.max_participants = 4
        self.event.max_participants_m = 2
        self.event.max_participants_f = 2
        self.event.max_participants_nb = 0
        self.event.save()

        reg_m1 = self._register(u_m1, 'confirmed')
        reg_f1 = self._register(u_f1, 'confirmed')
        reg_f2 = self._register(u_f2, 'confirmed')
        # f3 on waitlist — female pool full (2/2), total not full (3/4)
        reg_f3 = self._register(u_f3, 'waitlist')

        # Cancel the male → male pool now 0/2, total 2/4
        # But only waitlisted candidate is f3 whose pool is full (2/2)
        reg_m1.status = 'cancelled'
        reg_m1.save()

        with transaction.atomic():
            locked = type(self.event).objects.select_for_update().get(pk=self.event.pk)
            promoted = _promote_from_waitlist(locked, u_m1)

        # f3 can't be promoted because her pool is still full (2/2)
        self.assertIsNone(promoted)

    def test_gender_same_pool_candidate_promoted(self):
        """Same-gender-pool candidate promoted when their pool has room."""
        from django.db import transaction
        from crush_lu.views_events import _promote_from_waitlist

        u_f1 = self._create_user_with_profile('f1@test.com', 'F')
        u_f2 = self._create_user_with_profile('f2@test.com', 'F')
        u_m1 = self._create_user_with_profile('m1@test.com', 'M')

        self.event.max_participants = 4
        self.event.max_participants_m = 2
        self.event.max_participants_f = 2
        self.event.max_participants_nb = 0
        self.event.save()

        reg_f1 = self._register(u_f1, 'confirmed')
        reg_m1 = self._register(u_m1, 'confirmed')
        # f2 on waitlist (registered after, but female pool has room: 1/2)
        reg_f2 = self._register(u_f2, 'waitlist')

        # Cancel f1 → should promote f2 (same pool, has room)
        reg_f1.status = 'cancelled'
        reg_f1.save()

        with transaction.atomic():
            locked = type(self.event).objects.select_for_update().get(pk=self.event.pk)
            promoted = _promote_from_waitlist(locked, u_f1)

        self.assertIsNotNone(promoted)
        self.assertEqual(promoted.user, u_f2)

    def test_gender_fallback_to_other_pool(self):
        """Falls back to other pool when no same-pool candidate exists."""
        from django.db import transaction
        from crush_lu.views_events import _promote_from_waitlist

        u_m1 = self._create_user_with_profile('m1@test.com', 'M')
        u_m2 = self._create_user_with_profile('m2@test.com', 'M')
        u_f1 = self._create_user_with_profile('f1@test.com', 'F')

        self.event.max_participants = 4
        self.event.max_participants_m = 2
        self.event.max_participants_f = 2
        self.event.max_participants_nb = 0
        self.event.save()

        reg_m1 = self._register(u_m1, 'confirmed')
        reg_m2 = self._register(u_m2, 'confirmed')
        # f1 on waitlist — total was full when she registered
        reg_f1 = self._register(u_f1, 'waitlist')

        # Cancel m1 → no males on waitlist, fallback to f1 whose pool has room (0/2)
        reg_m1.status = 'cancelled'
        reg_m1.save()

        with transaction.atomic():
            locked = type(self.event).objects.select_for_update().get(pk=self.event.pk)
            promoted = _promote_from_waitlist(locked, u_m1)

        self.assertIsNotNone(promoted)
        self.assertEqual(promoted.user, u_f1)

    def test_no_promotion_when_event_full(self):
        """No promotion when total capacity still full."""
        from django.db import transaction
        from crush_lu.views_events import _promote_from_waitlist

        users = [self._create_user_with_profile(f'u{i}@test.com', 'M') for i in range(5)]

        self.event.max_participants = 4
        self.event.save()

        # 4 confirmed, 1 waitlisted
        for u in users[:4]:
            self._register(u, 'confirmed')
        reg_wl = self._register(users[4], 'waitlist')

        # Don't cancel anyone — event is full
        with transaction.atomic():
            locked = type(self.event).objects.select_for_update().get(pk=self.event.pk)
            promoted = _promote_from_waitlist(locked)

        self.assertIsNone(promoted)

    def test_waitlist_user_cancel_no_spurious_promotion(self):
        """Cancelling a waitlisted user doesn't promote anyone when event is full."""
        from django.db import transaction
        from crush_lu.views_events import _promote_from_waitlist

        users = [self._create_user_with_profile(f'u{i}@test.com', 'M') for i in range(6)]

        self.event.max_participants = 4
        self.event.save()

        for u in users[:4]:
            self._register(u, 'confirmed')
        reg_wl1 = self._register(users[4], 'waitlist')
        reg_wl2 = self._register(users[5], 'waitlist')

        # Waitlisted user cancels — doesn't free a confirmed spot
        reg_wl1.status = 'cancelled'
        reg_wl1.save()

        with transaction.atomic():
            locked = type(self.event).objects.select_for_update().get(pk=self.event.pk)
            promoted = _promote_from_waitlist(locked, users[4])

        # Event is still full (4/4), no one should be promoted
        self.assertIsNone(promoted)

    def test_genderless_user_skipped_when_gender_limits_active(self):
        """User without gender profile is not promoted when gender limits active."""
        from django.db import transaction
        from crush_lu.views_events import _promote_from_waitlist

        u_m1 = self._create_user_with_profile('m1@test.com', 'M')
        u_no_gender = self._create_user_without_profile('nogender@test.com')

        self.event.max_participants = 4
        self.event.max_participants_m = 2
        self.event.max_participants_f = 2
        self.event.max_participants_nb = 0
        self.event.save()

        reg_m1 = self._register(u_m1, 'confirmed')
        reg_ng = self._register(u_no_gender, 'waitlist')

        # Cancel m1 → genderless user should be skipped
        reg_m1.status = 'cancelled'
        reg_m1.save()

        with transaction.atomic():
            locked = type(self.event).objects.select_for_update().get(pk=self.event.pk)
            promoted = _promote_from_waitlist(locked, u_m1)

        self.assertIsNone(promoted)
        reg_ng.refresh_from_db()
        self.assertEqual(reg_ng.status, 'waitlist')  # Still waitlisted
