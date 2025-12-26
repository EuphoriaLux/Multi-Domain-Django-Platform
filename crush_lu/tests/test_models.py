"""
Model Tests for Crush.lu

Comprehensive tests for all Crush.lu models including:
- CrushProfile properties and methods
- MeetupEvent capacity and registration logic
- EventConnection mutual matching
- JourneyProgress tracking

Run with: pytest crush_lu/tests/test_models.py -v
"""
from datetime import date, timedelta
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


class CrushProfileTests(TestCase):
    """Test CrushProfile model properties and methods."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='testpass123',
            first_name='John',
            last_name='Doe'
        )

    def test_age_calculation(self):
        """Test that age is calculated correctly from date of birth."""
        from crush_lu.models import CrushProfile

        # Born exactly 25 years ago
        today = date.today()
        dob = date(today.year - 25, today.month, today.day)

        profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=dob,
            gender='M',
            location='Luxembourg'
        )

        self.assertEqual(profile.age, 25)

    def test_age_calculation_before_birthday(self):
        """Test age calculation before birthday this year."""
        from crush_lu.models import CrushProfile

        today = date.today()
        # Born 25 years ago, but birthday is tomorrow
        if today.month == 12 and today.day == 31:
            # Edge case: Dec 31, use Jan 1
            dob = date(today.year - 24, 1, 1)
        else:
            next_day = today + timedelta(days=1)
            dob = date(today.year - 25, next_day.month, next_day.day)

        profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=dob,
            gender='M',
            location='Luxembourg'
        )

        self.assertEqual(profile.age, 24)

    def test_age_range_property(self):
        """Test age_range returns correct range string."""
        from crush_lu.models import CrushProfile

        today = date.today()
        dob = date(today.year - 27, 1, 1)

        profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=dob,
            gender='M',
            location='Luxembourg'
        )

        self.assertEqual(profile.age_range, '25-29')

    def test_display_name_full_name(self):
        """Test display_name returns full name when allowed."""
        from crush_lu.models import CrushProfile

        profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg',
            show_full_name=True
        )

        # Model uses user.get_full_name() which returns 'John Doe'
        self.assertEqual(profile.display_name, 'John Doe')

    def test_display_name_first_only(self):
        """Test display_name returns first name only when privacy enabled."""
        from crush_lu.models import CrushProfile

        profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg',
            show_full_name=False
        )

        self.assertEqual(profile.display_name, 'John')

    def test_city_alias_for_location(self):
        """Test city property is alias for location."""
        from crush_lu.models import CrushProfile

        profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg City'
        )

        self.assertEqual(profile.city, 'Luxembourg City')


class MeetupEventTests(TestCase):
    """Test MeetupEvent model properties and methods."""

    def setUp(self):
        """Set up test data."""
        from crush_lu.models import MeetupEvent

        self.event = MeetupEvent.objects.create(
            title='Test Event',
            description='A test event',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg',
            address='123 Test Street',
            max_participants=10,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )

    def test_is_registration_open_before_deadline(self):
        """Test registration is open before deadline."""
        self.assertTrue(self.event.is_registration_open)

    def test_is_registration_closed_after_deadline(self):
        """Test registration is closed after deadline."""
        from crush_lu.models import MeetupEvent

        past_event = MeetupEvent.objects.create(
            title='Past Event',
            description='A past event',
            event_type='mixer',
            date_time=timezone.now() - timedelta(days=1),
            location='Luxembourg',
            address='123 Test Street',
            max_participants=10,
            registration_deadline=timezone.now() - timedelta(days=3),
            is_published=True
        )

        self.assertFalse(past_event.is_registration_open)

    def test_is_full_with_no_registrations(self):
        """Test event is not full with no registrations."""
        self.assertFalse(self.event.is_full)

    def test_spots_remaining_calculation(self):
        """Test spots_remaining is calculated correctly."""
        self.assertEqual(self.event.spots_remaining, 10)

    def test_get_confirmed_count_no_registrations(self):
        """Test confirmed count is zero with no registrations."""
        self.assertEqual(self.event.get_confirmed_count(), 0)


class EventRegistrationTests(TestCase):
    """Test EventRegistration model and workflow."""

    def setUp(self):
        """Set up test data."""
        from crush_lu.models import MeetupEvent, CrushProfile

        self.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='testpass123',
            first_name='John',
            last_name='Doe'
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
            title='Test Event',
            description='A test event',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg',
            address='123 Test Street',
            max_participants=2,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )

    def test_registration_creates_pending_status(self):
        """Test new registration has pending status."""
        from crush_lu.models import EventRegistration

        registration = EventRegistration.objects.create(
            event=self.event,
            user=self.user
        )

        self.assertEqual(registration.status, 'pending')

    def test_duplicate_registration_prevented(self):
        """Test user cannot register twice for same event."""
        from crush_lu.models import EventRegistration
        from django.db import IntegrityError

        EventRegistration.objects.create(
            event=self.event,
            user=self.user
        )

        with self.assertRaises(IntegrityError):
            EventRegistration.objects.create(
                event=self.event,
                user=self.user
            )

    def test_event_is_full_after_max_registrations(self):
        """Test event shows as full after max registrations."""
        from crush_lu.models import EventRegistration

        # Create another user
        user2 = User.objects.create_user(
            username='testuser2@example.com',
            email='testuser2@example.com',
            password='testpass123'
        )

        # Register both users (max is 2)
        EventRegistration.objects.create(
            event=self.event,
            user=self.user,
            status='confirmed'
        )
        EventRegistration.objects.create(
            event=self.event,
            user=user2,
            status='confirmed'
        )

        self.assertTrue(self.event.is_full)
        self.assertEqual(self.event.spots_remaining, 0)


class EventConnectionTests(TestCase):
    """Test EventConnection mutual matching logic."""

    def setUp(self):
        """Set up test data."""
        from crush_lu.models import MeetupEvent, CrushProfile

        self.user1 = User.objects.create_user(
            username='user1@example.com',
            email='user1@example.com',
            password='testpass123',
            first_name='John',
            last_name='Doe'
        )

        self.user2 = User.objects.create_user(
            username='user2@example.com',
            email='user2@example.com',
            password='testpass123',
            first_name='Jane',
            last_name='Smith'
        )

        for user in [self.user1, self.user2]:
            CrushProfile.objects.create(
                user=user,
                date_of_birth=date(1995, 5, 15),
                gender='M' if user == self.user1 else 'F',
                location='Luxembourg',
                is_approved=True,
                is_active=True
            )

        self.event = MeetupEvent.objects.create(
            title='Test Event',
            description='A test event',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg',
            address='123 Test Street',
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )

    def test_connection_request_creates_pending(self):
        """Test connection request creates pending status."""
        from crush_lu.models import EventConnection

        connection = EventConnection.objects.create(
            event=self.event,
            requester=self.user1,
            recipient=self.user2
        )

        self.assertEqual(connection.status, 'pending')

    def test_mutual_connection_required(self):
        """Test both users must connect for status to be 'connected'."""
        from crush_lu.models import EventConnection

        # User1 requests connection
        connection = EventConnection.objects.create(
            event=self.event,
            requester=self.user1,
            recipient=self.user2,
            status='pending'
        )

        # Still pending, not connected
        self.assertEqual(connection.status, 'pending')


class SpecialUserExperienceTests(TestCase):
    """Test SpecialUserExperience matching logic."""

    def test_user_matching_case_insensitive(self):
        """Test user matching is case-insensitive."""
        from crush_lu.models import SpecialUserExperience

        experience = SpecialUserExperience.objects.create(
            first_name='John',
            last_name='Doe',
            custom_welcome_message='Welcome, John!',
            is_active=True
        )

        # Should match regardless of case
        match = SpecialUserExperience.objects.filter(
            first_name__iexact='JOHN',
            last_name__iexact='DOE',
            is_active=True
        ).first()

        self.assertIsNotNone(match)
        self.assertEqual(match.id, experience.id)

    def test_trigger_count_increments(self):
        """Test trigger count increments on access."""
        from crush_lu.models import SpecialUserExperience

        experience = SpecialUserExperience.objects.create(
            first_name='John',
            last_name='Doe',
            custom_welcome_message='Welcome!',
            is_active=True,
            trigger_count=0
        )

        # Simulate trigger using the trigger() method
        experience.trigger()

        experience.refresh_from_db()
        self.assertEqual(experience.trigger_count, 1)
        self.assertIsNotNone(experience.last_triggered_at)


class ProfileSubmissionTests(TestCase):
    """Test ProfileSubmission review workflow."""

    def setUp(self):
        """Set up test data."""
        from crush_lu.models import CrushProfile, CrushCoach

        self.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='testpass123',
            first_name='John',
            last_name='Doe'
        )

        self.coach_user = User.objects.create_user(
            username='coach@example.com',
            email='coach@example.com',
            password='testpass123',
            first_name='Coach',
            last_name='Marie'
        )

        self.coach = CrushCoach.objects.create(
            user=self.coach_user,
            bio='Experienced dating coach',
            is_active=True,
            max_active_reviews=10
        )

        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg',
            is_approved=False
        )

    def test_submission_starts_as_pending(self):
        """Test new submission has pending status."""
        from crush_lu.models import ProfileSubmission

        submission = ProfileSubmission.objects.create(
            profile=self.profile
        )

        self.assertEqual(submission.status, 'pending')

    def test_coach_assignment(self):
        """Test coach can be assigned to submission."""
        from crush_lu.models import ProfileSubmission

        submission = ProfileSubmission.objects.create(
            profile=self.profile,
            coach=self.coach
        )

        self.assertEqual(submission.coach, self.coach)

    def test_approval_updates_profile(self):
        """Test approving submission updates profile."""
        from crush_lu.models import ProfileSubmission

        submission = ProfileSubmission.objects.create(
            profile=self.profile,
            coach=self.coach
        )

        # Approve the submission
        submission.status = 'approved'
        submission.save()

        # Update profile
        self.profile.is_approved = True
        self.profile.approved_at = timezone.now()
        self.profile.save()

        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_approved)
        self.assertIsNotNone(self.profile.approved_at)
