"""
Connection Tests for Crush.lu

Comprehensive tests for connection system including:
- Connection request/response workflow
- Mutual matching logic
- Direct messaging between connections
- Privacy settings in connections

Run with: pytest crush_lu/tests/test_connections.py -v
"""
from datetime import date, timedelta
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


class ConnectionRequestTests(TestCase):
    """Test connection request workflow."""

    def setUp(self):
        """Set up test data with two users who attended the same event."""
        from crush_lu.models import MeetupEvent, EventRegistration, CrushProfile

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

        # Create profiles
        for user, gender in [(self.user1, 'M'), (self.user2, 'F')]:
            CrushProfile.objects.create(
                user=user,
                date_of_birth=date(1995, 5, 15),
                gender=gender,
                location='Luxembourg',
                is_approved=True,
                is_active=True
            )

        # Create a past event where they met
        self.event = MeetupEvent.objects.create(
            title='Connection Test Event',
            description='Event where they met',
            event_type='mixer',
            date_time=timezone.now() - timedelta(hours=2),  # Past event
            location='Luxembourg',
            address='123 Test Street',
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(days=3),
            is_published=True
        )

        # Both attended the event
        for user in [self.user1, self.user2]:
            EventRegistration.objects.create(
                event=self.event,
                user=user,
                status='attended'
            )

    def test_connection_request_creates_pending_status(self):
        """Test connection request creates pending status."""
        from crush_lu.models import EventConnection

        connection = EventConnection.objects.create(
            event=self.event,
            requester=self.user1,
            recipient=self.user2
        )

        self.assertEqual(connection.status, 'pending')

    def test_accepted_connection_status(self):
        """Test connection can be accepted."""
        from crush_lu.models import EventConnection

        # User1 requests connection
        connection = EventConnection.objects.create(
            event=self.event,
            requester=self.user1,
            recipient=self.user2,
            status='pending'
        )

        # User2 accepts
        connection.status = 'accepted'
        connection.responded_at = timezone.now()
        connection.save()

        connection.refresh_from_db()
        self.assertEqual(connection.status, 'accepted')
        self.assertIsNotNone(connection.responded_at)

    def test_declined_connection_status(self):
        """Test declined connection has correct status."""
        from crush_lu.models import EventConnection

        connection = EventConnection.objects.create(
            event=self.event,
            requester=self.user1,
            recipient=self.user2,
            status='pending'
        )

        # User2 declines
        connection.status = 'declined'
        connection.responded_at = timezone.now()
        connection.save()

        connection.refresh_from_db()
        self.assertEqual(connection.status, 'declined')

    def test_duplicate_connection_prevented(self):
        """Test duplicate connection request is prevented."""
        from crush_lu.models import EventConnection
        from django.db import IntegrityError

        EventConnection.objects.create(
            event=self.event,
            requester=self.user1,
            recipient=self.user2
        )

        # Attempting to create duplicate should fail due to unique_together
        with self.assertRaises(IntegrityError):
            EventConnection.objects.create(
                event=self.event,
                requester=self.user1,
                recipient=self.user2
            )

    def test_is_mutual_property(self):
        """Test is_mutual property detects reverse connection."""
        from crush_lu.models import EventConnection

        # User1 requests connection to User2
        connection1 = EventConnection.objects.create(
            event=self.event,
            requester=self.user1,
            recipient=self.user2
        )

        # Initially not mutual
        self.assertFalse(connection1.is_mutual)

        # User2 also requests connection to User1
        EventConnection.objects.create(
            event=self.event,
            requester=self.user2,
            recipient=self.user1
        )

        # Now should be mutual
        self.assertTrue(connection1.is_mutual)


class ConnectionMessagingTests(TestCase):
    """Test messaging between connected users."""

    def setUp(self):
        """Set up connected users for messaging tests."""
        from crush_lu.models import MeetupEvent, EventConnection, CrushProfile

        self.user1 = User.objects.create_user(
            username='sender@example.com',
            email='sender@example.com',
            password='testpass123',
            first_name='Sender',
            last_name='User'
        )

        self.user2 = User.objects.create_user(
            username='receiver@example.com',
            email='receiver@example.com',
            password='testpass123',
            first_name='Receiver',
            last_name='User'
        )

        for user, gender in [(self.user1, 'M'), (self.user2, 'F')]:
            CrushProfile.objects.create(
                user=user,
                date_of_birth=date(1995, 5, 15),
                gender=gender,
                location='Luxembourg',
                is_approved=True
            )

        self.event = MeetupEvent.objects.create(
            title='Messaging Test Event',
            description='Event for messaging test',
            event_type='mixer',
            date_time=timezone.now() - timedelta(days=1),
            location='Luxembourg',
            address='123 Test Street',
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(days=3),
            is_published=True
        )

        # Create accepted connection
        self.connection = EventConnection.objects.create(
            event=self.event,
            requester=self.user1,
            recipient=self.user2,
            status='accepted',
            responded_at=timezone.now()
        )

    def test_message_between_connected_users(self):
        """Test users can send messages when connected."""
        from crush_lu.models import ConnectionMessage

        message = ConnectionMessage.objects.create(
            connection=self.connection,
            sender=self.user1,
            message='Hello! Nice meeting you at the event.'
        )

        self.assertIsNotNone(message)
        self.assertEqual(message.sender, self.user1)
        self.assertIsNone(message.read_at)

    def test_message_marked_read(self):
        """Test message can be marked as read."""
        from crush_lu.models import ConnectionMessage

        message = ConnectionMessage.objects.create(
            connection=self.connection,
            sender=self.user1,
            message='Hello!'
        )

        self.assertIsNone(message.read_at)

        message.read_at = timezone.now()
        message.save()

        message.refresh_from_db()
        self.assertIsNotNone(message.read_at)

    def test_message_ordering(self):
        """Test messages are ordered by sent time."""
        from crush_lu.models import ConnectionMessage

        msg1 = ConnectionMessage.objects.create(
            connection=self.connection,
            sender=self.user1,
            message='First message'
        )

        msg2 = ConnectionMessage.objects.create(
            connection=self.connection,
            sender=self.user2,
            message='Second message'
        )

        messages = ConnectionMessage.objects.filter(
            connection=self.connection
        ).order_by('sent_at')

        self.assertEqual(list(messages), [msg1, msg2])


class ConnectionPrivacyTests(TestCase):
    """Test privacy settings in connections."""

    def setUp(self):
        """Set up users with different privacy settings."""
        from crush_lu.models import MeetupEvent, EventConnection, CrushProfile

        self.user_private = User.objects.create_user(
            username='private@example.com',
            email='private@example.com',
            password='testpass123',
            first_name='Private',
            last_name='User'
        )

        self.user_public = User.objects.create_user(
            username='public@example.com',
            email='public@example.com',
            password='testpass123',
            first_name='Public',
            last_name='Person'
        )

        # Private user with privacy settings
        self.profile_private = CrushProfile.objects.create(
            user=self.user_private,
            date_of_birth=date(1995, 5, 15),
            gender='F',
            location='Luxembourg',
            is_approved=True,
            show_full_name=False,  # Privacy enabled
            show_exact_age=False,
            blur_photos=True
        )

        # Public user without privacy restrictions
        self.profile_public = CrushProfile.objects.create(
            user=self.user_public,
            date_of_birth=date(1993, 3, 20),
            gender='M',
            location='Luxembourg',
            is_approved=True,
            show_full_name=True,
            show_exact_age=True,
            blur_photos=False
        )

        self.event = MeetupEvent.objects.create(
            title='Privacy Test Event',
            description='Testing privacy',
            event_type='mixer',
            date_time=timezone.now() - timedelta(days=1),
            location='Luxembourg',
            address='123 Test Street',
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(days=3),
            is_published=True
        )

        self.connection = EventConnection.objects.create(
            event=self.event,
            requester=self.user_private,
            recipient=self.user_public,
            status='accepted',
            responded_at=timezone.now()
        )

    def test_private_user_display_name(self):
        """Test private user shows first name only."""
        self.assertEqual(self.profile_private.display_name, 'Private')

    def test_public_user_display_name(self):
        """Test public user shows full name."""
        self.assertEqual(self.profile_public.display_name, 'Public Person')

    def test_private_user_age_range(self):
        """Test private user shows age range."""
        # Age range instead of exact age
        self.assertIn('-', self.profile_private.age_range)

    def test_blur_photos_setting(self):
        """Test blur_photos setting is respected."""
        self.assertTrue(self.profile_private.blur_photos)
        self.assertFalse(self.profile_public.blur_photos)


class AttendeeListTests(TestCase):
    """Test attendee list visibility and access."""

    def setUp(self):
        """Set up event with multiple attendees."""
        from crush_lu.models import MeetupEvent, EventRegistration, CrushProfile

        self.event = MeetupEvent.objects.create(
            title='Attendee List Test',
            description='Testing attendee list',
            event_type='mixer',
            date_time=timezone.now() - timedelta(days=1),  # Past event
            location='Luxembourg',
            address='123 Test Street',
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(days=3),
            is_published=True
        )

        # Create multiple attendees
        self.attendees = []
        for i in range(5):
            user = User.objects.create_user(
                username=f'attendee{i}@example.com',
                email=f'attendee{i}@example.com',
                password='testpass123',
                first_name=f'Attendee{i}',
                last_name='User'
            )

            CrushProfile.objects.create(
                user=user,
                date_of_birth=date(1995, 5, 15),
                gender='M' if i % 2 == 0 else 'F',
                location='Luxembourg',
                is_approved=True
            )

            EventRegistration.objects.create(
                event=self.event,
                user=user,
                status='attended'
            )

            self.attendees.append(user)

    def test_attendee_list_only_shows_attended(self):
        """Test attendee list only shows users who attended."""
        from crush_lu.models import EventRegistration

        # Get attended registrations
        attended = EventRegistration.objects.filter(
            event=self.event,
            status='attended'
        )

        self.assertEqual(attended.count(), 5)

    def test_non_attendee_not_in_list(self):
        """Test users who didn't attend are not in list."""
        from crush_lu.models import EventRegistration

        # Create a user who cancelled
        cancelled_user = User.objects.create_user(
            username='cancelled@example.com',
            email='cancelled@example.com',
            password='testpass123'
        )

        EventRegistration.objects.create(
            event=self.event,
            user=cancelled_user,
            status='cancelled'
        )

        # Should not appear in attended list
        attended = EventRegistration.objects.filter(
            event=self.event,
            status='attended'
        )

        self.assertEqual(attended.count(), 5)  # Still 5


class ConnectionNotificationTests(TestCase):
    """Test connection-related notifications."""

    def setUp(self):
        """Set up users for notification tests."""
        from crush_lu.models import MeetupEvent, CrushProfile

        self.user1 = User.objects.create_user(
            username='notif1@example.com',
            email='notif1@example.com',
            password='testpass123',
            first_name='Notif1',
            last_name='User'
        )

        self.user2 = User.objects.create_user(
            username='notif2@example.com',
            email='notif2@example.com',
            password='testpass123',
            first_name='Notif2',
            last_name='User'
        )

        for user, gender in [(self.user1, 'M'), (self.user2, 'F')]:
            CrushProfile.objects.create(
                user=user,
                date_of_birth=date(1995, 5, 15),
                gender=gender,
                location='Luxembourg',
                is_approved=True
            )

        self.event = MeetupEvent.objects.create(
            title='Notification Test Event',
            description='For notification testing',
            event_type='mixer',
            date_time=timezone.now() - timedelta(days=1),
            location='Luxembourg',
            address='123 Test Street',
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(days=3),
            is_published=True
        )

    def test_unread_message_count(self):
        """Test unread message count is accurate."""
        from crush_lu.models import EventConnection, ConnectionMessage

        connection = EventConnection.objects.create(
            event=self.event,
            requester=self.user1,
            recipient=self.user2,
            status='accepted',
            responded_at=timezone.now()
        )

        # Create unread messages (read_at is None)
        for i in range(3):
            ConnectionMessage.objects.create(
                connection=connection,
                sender=self.user1,
                message=f'Message {i}'
                # read_at defaults to None (unread)
            )

        # Count unread messages for user2 (receiver)
        unread_count = ConnectionMessage.objects.filter(
            connection=connection,
            read_at__isnull=True
        ).exclude(sender=self.user2).count()

        self.assertEqual(unread_count, 3)
