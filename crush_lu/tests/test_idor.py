"""
IDOR (Insecure Direct Object Reference) Tests for Crush.lu

Tests that users cannot access or modify resources belonging to other users
by manipulating IDs in URLs.
"""
import pytest
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from datetime import date, timedelta
from django.utils import timezone

from crush_lu.models import (
    CrushProfile,
    MeetupEvent,
    EventRegistration,
    EventConnection,
)

User = get_user_model()


class SiteTestMixin:
    """Mixin to create Site object for tests."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(id=1, defaults={'domain': 'localhost', 'name': 'localhost'})


@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class TestConnectionIDOR(SiteTestMixin, TestCase):
    """Test that users cannot access/modify other users' connections."""

    def setUp(self):
        self.user_a = User.objects.create_user(
            username='usera@test.com', email='usera@test.com',
            password='testpass123', first_name='Alice', last_name='A'
        )
        self.user_b = User.objects.create_user(
            username='userb@test.com', email='userb@test.com',
            password='testpass123', first_name='Bob', last_name='B'
        )
        self.user_c = User.objects.create_user(
            username='userc@test.com', email='userc@test.com',
            password='testpass123', first_name='Charlie', last_name='C'
        )

        for user, gender in [(self.user_a, 'F'), (self.user_b, 'M'), (self.user_c, 'M')]:
            CrushProfile.objects.create(
                user=user, date_of_birth=date(1995, 1, 1),
                gender=gender, location='Luxembourg City',
                is_approved=True, is_active=True
            )

        self.event = MeetupEvent.objects.create(
            title='Test Event', description='Test',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg', address='123 Test St',
            max_participants=20, registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )

        for user in [self.user_a, self.user_b, self.user_c]:
            EventRegistration.objects.create(
                event=self.event, user=user, status='attended'
            )

        self.connection = EventConnection.objects.create(
            requester=self.user_b, recipient=self.user_a,
            event=self.event, status='pending'
        )

    def test_user_c_cannot_respond_to_connection_between_a_and_b(self):
        """User C (unrelated) tries to accept a connection addressed to User A."""
        self.client.login(username='userc@test.com', password='testpass123')
        url = reverse('crush_lu:respond_connection', kwargs={
            'connection_id': self.connection.id, 'action': 'accept'
        })
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)

    def test_requester_cannot_respond_to_own_request(self):
        """User B (requester) tries to accept their own connection request."""
        self.client.login(username='userb@test.com', password='testpass123')
        url = reverse('crush_lu:respond_connection', kwargs={
            'connection_id': self.connection.id, 'action': 'accept'
        })
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)

    def test_recipient_can_accept_connection(self):
        """User A (recipient) CAN accept the connection."""
        self.client.login(username='usera@test.com', password='testpass123')
        url = reverse('crush_lu:respond_connection', kwargs={
            'connection_id': self.connection.id, 'action': 'accept'
        })
        response = self.client.post(url)
        # Should redirect after accepting (302)
        self.assertEqual(response.status_code, 302)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status, 'accepted')

    def test_user_c_cannot_view_connection_detail(self):
        """User C cannot view the detail page of a connection between A and B."""
        self.connection.status = 'accepted'
        self.connection.save()

        self.client.login(username='userc@test.com', password='testpass123')
        url = reverse('crush_lu:connection_detail', kwargs={
            'connection_id': self.connection.id
        })
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_requester_can_view_connection_detail(self):
        """User B (requester) CAN view the connection detail."""
        self.connection.status = 'accepted'
        self.connection.save()

        self.client.login(username='userb@test.com', password='testpass123')
        url = reverse('crush_lu:connection_detail', kwargs={
            'connection_id': self.connection.id
        })
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_recipient_can_view_connection_detail(self):
        """User A (recipient) CAN view the connection detail."""
        self.connection.status = 'accepted'
        self.connection.save()

        self.client.login(username='usera@test.com', password='testpass123')
        url = reverse('crush_lu:connection_detail', kwargs={
            'connection_id': self.connection.id
        })
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_user_c_cannot_send_message_on_connection(self):
        """User C cannot send messages on a connection between A and B."""
        self.connection.status = 'accepted'
        self.connection.save()

        self.client.login(username='userc@test.com', password='testpass123')
        url = reverse('crush_lu:connection_detail', kwargs={
            'connection_id': self.connection.id
        })
        response = self.client.post(url, {'message': 'Sneaky message!'})
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_user_redirected(self):
        """Unauthenticated users should be redirected to login."""
        url = reverse('crush_lu:connection_detail', kwargs={
            'connection_id': self.connection.id
        })
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)


@override_settings(ROOT_URLCONF='azureproject.urls_crush')
class TestEventAttendeeIDOR(SiteTestMixin, TestCase):
    """Test that only event attendees can view other attendees."""

    def setUp(self):
        self.event = MeetupEvent.objects.create(
            title='Test Event', description='Test',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=7),
            location='Luxembourg', address='123 Test St',
            max_participants=20, registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True
        )
        self.attendee = User.objects.create_user(
            username='attendee@test.com', email='attendee@test.com',
            password='testpass123'
        )
        CrushProfile.objects.create(
            user=self.attendee, date_of_birth=date(1995, 1, 1),
            gender='M', location='Luxembourg', is_approved=True, is_active=True
        )
        EventRegistration.objects.create(
            event=self.event, user=self.attendee, status='attended'
        )

    def test_non_attendee_cannot_view_attendees(self):
        """A user not registered for the event cannot see attendees."""
        outsider = User.objects.create_user(
            username='outsider@test.com', email='outsider@test.com',
            password='testpass123'
        )
        CrushProfile.objects.create(
            user=outsider, date_of_birth=date(1995, 1, 1),
            gender='M', location='Luxembourg', is_approved=True, is_active=True
        )

        self.client.login(username='outsider@test.com', password='testpass123')
        url = reverse('crush_lu:event_attendees', kwargs={'event_id': self.event.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_attendee_can_view_attendees(self):
        """A user who attended the event CAN see attendees."""
        self.client.login(username='attendee@test.com', password='testpass123')
        url = reverse('crush_lu:event_attendees', kwargs={'event_id': self.event.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
