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

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_delete_full_account_removes_connections_and_messages(self, _mock_storage):
        from crush_lu.models import EventConnection, ConnectionMessage
        from crush_lu.views import delete_full_account

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

        delete_full_account(self.user)

        self.assertFalse(EventConnection.objects.exists())
        self.assertFalse(ConnectionMessage.objects.exists())

    def test_crush_user_context_handles_deleted_profile(self):
        from crush_lu.context_processors import crush_user_context

        profile = self.user.crushprofile
        profile.delete()

        request = self.factory.get('/fake-path/')
        request.user = self.user

        context = crush_user_context(request)

        self.assertNotIn('profile_completion_status', context)



class AccountErasureCompletenessTests(TestCase):
    """#1183: User-keyed personal rows must not outlive account deletion."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from crush_lu.models import CrushProfile

        User = get_user_model()
        self.user = User.objects.create_user(
            username='del@example.com', email='del@example.com',
            password='x', first_name='Del', last_name='Me',
        )
        self.other = User.objects.create_user(
            username='o@example.com', email='o@example.com', password='x',
        )
        for user in (self.user, self.other):
            CrushProfile.objects.create(
                user=user, date_of_birth=date(1995, 5, 15), gender='M',
                location='Luxembourg', is_approved=True, is_active=True,
            )

    def _seed(self):
        from crush_lu.models import (
            IOSAppDevice, Notification, PhoneOTP, PushSubscription,
            PWADeviceInstallation, UserBlock, UserReport,
        )
        from crush_lu.models.crush_connect_cycle import (
            ConnectChatMessage, ConnectTemporaryChat, ConnectWeekSession,
            ConnectWeeklyRequest,
        )

        PushSubscription.objects.create(
            user=self.user, endpoint='https://push.example/abc',
            p256dh_key='k', auth_key='a',
        )
        PhoneOTP.objects.create(
            user=self.user, phone_number='+352621000000', code_hash='h',
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        PWADeviceInstallation.objects.create(
            user=self.user, device_fingerprint='fp', device_category='mobile',
        )
        Notification.objects.create(
            user=self.user, notification_type='x', title='hello',
        )
        IOSAppDevice.objects.create(user=self.user, device_token='tok')
        UserBlock.objects.create(blocker=self.user, blocked=self.other)
        UserReport.objects.create(
            reporter=self.user, reported_user=self.other, reason='spam',
        )
        session = ConnectWeekSession.objects.create(user=self.user)
        request = ConnectWeeklyRequest.objects.create(
            session=session, requester=self.user, recipient=self.other,
            expires_at=timezone.now() + timedelta(days=1),
        )
        chat = ConnectTemporaryChat.objects.create(
            request=request, participant_1=self.user, participant_2=self.other,
            expires_at=timezone.now() + timedelta(days=1),
        )
        ConnectChatMessage.objects.create(
            chat=chat, sender=self.user, message='private text',
        )
        ConnectChatMessage.objects.create(
            chat=chat, sender=self.other, message='their reply',
        )

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_full_deletion_purges_user_keyed_personal_rows(self, _storage):
        from crush_lu.models import (
            IOSAppDevice, Notification, PhoneOTP, PushSubscription,
            PWADeviceInstallation, UserBlock, UserReport,
        )
        from crush_lu.models.crush_connect_cycle import (
            ConnectChatMessage, ConnectTemporaryChat,
        )
        from crush_lu.views import delete_full_account

        self._seed()
        delete_full_account(self.user)

        for model in (
            PushSubscription, PhoneOTP, PWADeviceInstallation, Notification,
            IOSAppDevice, ConnectChatMessage, ConnectTemporaryChat,
        ):
            self.assertEqual(model.objects.count(), 0, model.__name__)
        # Moderation/safety records are retained pending a product decision.
        self.assertEqual(UserBlock.objects.count(), 1)
        self.assertEqual(UserReport.objects.count(), 1)

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_erasure_does_not_touch_other_members_data(self, _storage):
        from crush_lu.models import PushSubscription
        from crush_lu.views import delete_full_account

        PushSubscription.objects.create(
            user=self.other, endpoint='https://push.example/keep',
            p256dh_key='k', auth_key='a',
        )
        delete_full_account(self.user)
        self.assertEqual(PushSubscription.objects.filter(user=self.other).count(), 1)

    def test_every_user_fk_model_is_purged_or_explicitly_retained(self):
        """A new table keyed on User must be classified, not silently kept."""
        from django.apps import apps
        from django.contrib.auth import get_user_model

        from crush_lu.views_account import (
            ACCOUNT_ERASURE_PURGE, ACCOUNT_ERASURE_RETAINED,
        )

        user_model = get_user_model()
        purged = {name for name, _fields in ACCOUNT_ERASURE_PURGE}
        classified = purged | set(ACCOUNT_ERASURE_RETAINED)
        self.assertFalse(purged & set(ACCOUNT_ERASURE_RETAINED))
        unclassified = []
        for model in apps.get_app_config('crush_lu').get_models():
            if model._meta.proxy:
                continue
            owns_user_row = any(
                f.is_relation
                and f.related_model is user_model
                and not f.null
                for f in model._meta.concrete_fields
            )
            if owns_user_row and model.__name__ not in classified:
                unclassified.append(model.__name__)
        self.assertEqual(
            unclassified, [],
            'Classify these in ACCOUNT_ERASURE_PURGE or ACCOUNT_ERASURE_RETAINED '
            '(crush_lu/views_account.py)',
        )
