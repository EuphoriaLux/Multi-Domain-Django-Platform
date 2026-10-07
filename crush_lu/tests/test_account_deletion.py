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
            ACCOUNT_ERASURE_ANONYMIZE, ACCOUNT_ERASURE_PURGE,
            ACCOUNT_ERASURE_RETAINED,
        )

        user_model = get_user_model()
        purged = {name for name, _fields in ACCOUNT_ERASURE_PURGE}
        anonymized = {name for name, _field in ACCOUNT_ERASURE_ANONYMIZE}
        classified = purged | anonymized | set(ACCOUNT_ERASURE_RETAINED)
        self.assertFalse(purged & set(ACCOUNT_ERASURE_RETAINED))
        unclassified = []
        for model in apps.get_app_config('crush_lu').get_models():
            if model._meta.proxy:
                continue
            owns_user_row = any(
                f.is_relation
                and f.related_model is user_model
                for f in model._meta.concrete_fields
            )
            if owns_user_row and model.__name__ not in classified:
                unclassified.append(model.__name__)
        self.assertEqual(
            unclassified, [],
            'Classify these in ACCOUNT_ERASURE_PURGE or ACCOUNT_ERASURE_RETAINED '
            '(crush_lu/views_account.py)',
        )

    def test_every_other_app_user_fk_is_handled_or_deferred(self):
        """Full-account deletion covers other installed apps too."""
        from django.apps import apps
        from django.contrib.auth import get_user_model

        from crush_lu.views_account import (
            ACCOUNT_ERASURE_ANONYMIZE_OTHER_APPS,
            ACCOUNT_ERASURE_PURGE_OTHER_APPS,
        )

        handled = {
            (app, name) for app, name, _f in ACCOUNT_ERASURE_PURGE_OTHER_APPS
        } | {(app, name) for app, name, _f in ACCOUNT_ERASURE_ANONYMIZE_OTHER_APPS}
        deferred = {
            ("hub", "SocialPost"),
            ("hub", "WhatsAppMessage"),
            ("token_blacklist", "OutstandingToken"),
            ("hub", "PartnerOnboardingStep"),
            ("onboarding", "OnboardingSession"),
            ("arborist", "ArboristLead"),
            ("arborist", "LeadEvent"),
        }
        user_model = get_user_model()
        unclassified = []
        for model in apps.get_models():
            label = model._meta.app_label
            if label in ("crush_lu", "auth", "admin", "sessions",
                         "contenttypes", "account", "socialaccount"):
                continue
            if model._meta.proxy:
                continue
            if any(
                f.is_relation and f.related_model is user_model
                for f in model._meta.concrete_fields
            ) and (label, model.__name__) not in handled | deferred:
                unclassified.append(f"{label}.{model.__name__}")
        self.assertEqual(unclassified, [])

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_nullable_links_and_send_logs_are_anonymized_in_place(self, _s):
        from crush_lu.models import (
            Campaign, CampaignRecipient, CrushSpark, Newsletter,
            NewsletterRecipient, ReferralAttribution,
        )
        from crush_lu.models.referrals import ReferralCode
        from crush_lu.views import delete_full_account
        from hub.models import WhatsAppMessage

        newsletter = Newsletter.objects.create(
            subject='S', body_html='x', audience='all_users',
        )
        NewsletterRecipient.objects.create(
            newsletter=newsletter, user=self.user, email='del@example.com',
            status='sent', error_message='bounce for del@example.com',
        )
        campaign = Campaign.objects.create(
            name='c', channels=['whatsapp'], audience='all_users',
        )
        wa = WhatsAppMessage.objects.create(
            user=self.other, recipient='+352621000000', template_name='t',
            language='en', parameters={'1': 'Del', '2': 'del@example.com'},
            status='sent',
        )
        CampaignRecipient.objects.create(
            campaign=campaign, channel='whatsapp', user=self.user,
            status='sent', whatsapp_message=wa,
        )
        code = ReferralCode.objects.create(referrer=self.other.crushprofile)
        ReferralAttribution.objects.create(
            referral_code=code, referrer=self.other.crushprofile,
            referred_user=self.user, ip_address='1.2.3.4',
            user_agent='UA', landing_path='/x', session_key='sk',
        )

        delete_full_account(self.user)

        nr = NewsletterRecipient.objects.get(newsletter=newsletter)
        self.assertEqual((nr.email, nr.error_message, nr.status), ('', '', 'sent'))
        wa.refresh_from_db()
        self.assertEqual((wa.recipient, wa.parameters), ('', {}))
        self.assertEqual(
            CampaignRecipient.objects.get(campaign=campaign).status, 'sent'
        )
        attribution = ReferralAttribution.objects.get()
        self.assertIsNone(attribution.referred_user)
        self.assertEqual(
            (attribution.ip_address, attribution.user_agent), ('', '')
        )

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_full_deletion_purges_other_app_profiles_profile_only_does_not(self, _s):
        from crush_lu.views import delete_crushlu_profile_only, delete_full_account
        from entreprinder.models import EntrepreneurProfile
        from hub.models import HubProfile

        for user in (self.user, self.other):
            HubProfile.objects.create(user=user, organization='Acme')
            EntrepreneurProfile.objects.create(user=user)
        delete_crushlu_profile_only(self.user)
        self.assertTrue(HubProfile.objects.filter(user=self.user).exists())
        delete_full_account(self.user)
        self.assertFalse(HubProfile.objects.filter(user=self.user).exists())
        self.assertFalse(EntrepreneurProfile.objects.filter(user=self.user).exists())
        self.assertTrue(HubProfile.objects.filter(user=self.other).exists())

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_purged_models_files_outside_user_folder_are_deleted(self, _s):
        """JourneyGift QR codes live in public storage, not users/<id>/ (#1183)."""
        from django.core.files.base import ContentFile
        from django.core.files.storage import InMemoryStorage

        from crush_lu.models import JourneyGift
        from crush_lu.views import delete_full_account

        storage = InMemoryStorage()
        qr_name = storage.save('journey_gifts/qr/ABC123.png', ContentFile(b'png'))
        other_name = storage.save('journey_gifts/qr/KEEP.png', ContentFile(b'png'))
        gift = JourneyGift.objects.create(
            sender=self.user, recipient_name='R',
            date_first_met=date(2024, 1, 1), location_first_met='Lux',
        )
        keep = JourneyGift.objects.create(
            sender=self.other, recipient_name='R',
            date_first_met=date(2024, 1, 1), location_first_met='Lux',
        )
        JourneyGift.objects.filter(pk=gift.pk).update(qr_code_image=qr_name)
        JourneyGift.objects.filter(pk=keep.pk).update(qr_code_image=other_name)
        # A missing file must not abort the purge.
        JourneyGift.objects.create(
            sender=self.user, recipient_name='R2', qr_code_image='gone/missing.png',
            date_first_met=date(2024, 1, 1), location_first_met='Lux',
        )

        field = JourneyGift._meta.get_field('qr_code_image')
        with patch.object(field, 'storage', storage):
            delete_full_account(self.user)

        self.assertFalse(storage.exists(qr_name))
        self.assertTrue(storage.exists(other_name))
        self.assertFalse(JourneyGift.objects.filter(sender=self.user).exists())
        self.assertTrue(JourneyGift.objects.filter(pk=keep.pk).exists())
