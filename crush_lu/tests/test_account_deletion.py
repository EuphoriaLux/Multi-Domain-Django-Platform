"""
Account deletion tests for Crush.lu.

Run with: pytest crush_lu/tests/test_account_deletion.py -v
"""
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase, RequestFactory
from django.utils import timezone


class AccountDeletionTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from crush_lu.models import MeetupEvent, CrushProfile

        self.factory = RequestFactory()
        self.User = get_user_model()

        self.user = self.User.objects.create_user(
            username='deleteme@example.com',
            email='deleteme@example.com',
            password='testpass123',
            first_name='Delete',
            last_name='Me'
        )

        self.other_user = self.User.objects.create_user(
            username='other@example.com',
            email='other@example.com',
            password='testpass123',
            first_name='Other',
            last_name='User'
        )

        self.event = MeetupEvent.objects.create(
            title='Deletion Test Event',
            description='Event for deletion tests',
            event_type='mixer',
            date_time=timezone.now() - timedelta(hours=2),
            location='Luxembourg',
            address='123 Test Street',
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(days=3),
            is_published=True
        )

        for user, gender in [(self.user, 'M'), (self.other_user, 'F')]:
            CrushProfile.objects.create(
                user=user,
                date_of_birth=date(1995, 5, 15),
                gender=gender,
                location='Luxembourg',
                is_approved=True,
                is_active=True
            )

    @patch('crush_lu.views.delete_user_storage', return_value=(True, 0))
    def test_delete_user_data_removes_connections_and_messages(self, _mock_storage):
        from crush_lu.models import EventConnection, ConnectionMessage
        from crush_lu.views import delete_user_data

        connection = EventConnection.objects.create(
            event=self.event,
            requester=self.user,
            recipient=self.other_user
        )
        ConnectionMessage.objects.create(
            connection=connection,
            sender=self.user,
            message='hello'
        )

        delete_user_data(self.user, confirmation_code='test-code')

        self.assertFalse(EventConnection.objects.exists())
        self.assertFalse(ConnectionMessage.objects.exists())

    def test_crush_user_context_handles_deleted_profile(self):
        from crush_lu.context_processors import crush_user_context
        from crush_lu.models import CrushProfile

        profile = self.user.crushprofile
        profile.delete()

        request = self.factory.get('/fake-path/')
        request.user = self.user

        context = crush_user_context(request)

        self.assertNotIn('profile_completion_status', context)

