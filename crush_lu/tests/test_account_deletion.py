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
            Campaign, CampaignRecipient, Newsletter,
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

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_severed_links_also_blank_identifying_payload(self, _s):
        from crush_lu.models import (
            EventInvitation, JourneyGift, MeetupEvent, SpecialUserExperience,
        )
        from crush_lu.views import delete_full_account

        event = MeetupEvent.objects.create(
            title='E', description='d', event_type='mixer',
            date_time=timezone.now() + timedelta(days=5), location='L',
            address='A', max_participants=10,
            registration_deadline=timezone.now() + timedelta(days=3),
        )
        invitation = EventInvitation.objects.create(
            event=event, guest_email='g@example.com', guest_first_name='Gus',
            guest_last_name='Guest', created_user=self.user,
            coach_notes='note about Gus',
        )
        special = SpecialUserExperience.objects.create(
            first_name='Gus', last_name='Guest', linked_user=self.user,
        )
        gift = JourneyGift.objects.create(
            sender=self.other, recipient_name='Gus', recipient_email='g@example.com',
            claimed_by=self.user, date_first_met=date(2024, 1, 1),
            location_first_met='Lux',
        )

        delete_full_account(self.user)

        invitation.refresh_from_db()
        special.refresh_from_db()
        gift.refresh_from_db()
        self.assertIsNone(invitation.created_user)
        self.assertEqual(
            (invitation.guest_email, invitation.guest_first_name,
             invitation.guest_last_name, invitation.coach_notes),
            ('', '', '', ''),
        )
        self.assertIsNone(special.linked_user)
        self.assertNotEqual(special.first_name, 'Gus')
        self.assertNotEqual(special.last_name, 'Guest')
        self.assertIsNone(gift.claimed_by)
        self.assertEqual((gift.recipient_name, gift.recipient_email), ('', ''))

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_other_app_files_are_deleted_from_their_storage(self, _s):
        from django.core.files.base import ContentFile
        from django.core.files.storage import InMemoryStorage

        from crush_lu.views import delete_full_account
        from delegations.models import DelegationProfile

        storage = InMemoryStorage()
        name = storage.save('delegations/me.jpg', ContentFile(b'img'))
        DelegationProfile.objects.create(user=self.user, profile_photo=name)
        field = DelegationProfile._meta.get_field('profile_photo')
        with patch.object(field, 'storage', storage):
            delete_full_account(self.user)
        self.assertFalse(storage.exists(name))
        self.assertFalse(DelegationProfile.objects.filter(user=self.user).exists())

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_consent_is_revoked_before_anything_is_purged(self, _s):
        from crush_lu import views_account
        from crush_lu.email_helpers import can_send_email
        from crush_lu.models import EmailPreference
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )
        EmailPreference.get_or_create_for_user(self.user)
        seen = {}
        real = views_account._anonymize_send_logs

        def spy(user):
            consent = UserDataConsent.objects.get(user=user)
            seen.setdefault('state', (
                consent.crushlu_consent_given, consent.crushlu_banned,
                EmailPreference.objects.filter(user=user).exists(),
            ))
            return real(user)

        with patch.object(views_account, '_anonymize_send_logs', side_effect=spy):
            views_account.delete_crushlu_profile_only(self.user)
        self.assertEqual(seen['state'], (False, True, True))
        # Purged prefs are not resurrected for the banned account.
        self.assertFalse(can_send_email(self.user, 'newsletter'))
        self.assertFalse(EmailPreference.objects.filter(user=self.user).exists())

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_user_m2m_memberships_and_quiz_rows_are_erased(self, _s):
        from crush_lu.models import MeetupEvent, QuizEvent, QuizTable
        from crush_lu.models import QuizTableMembership
        from crush_lu.views import delete_full_account
        from hub.models import HubResource

        event = MeetupEvent.objects.create(
            title='Q', description='d', event_type='mixer',
            date_time=timezone.now() + timedelta(days=5), location='L',
            address='A', max_participants=10,
            registration_deadline=timezone.now() + timedelta(days=3),
        )
        event.invited_users.add(self.user, self.other)
        resource = HubResource.objects.create(title='R')
        resource.audience.add(self.user, self.other)
        quiz = QuizEvent.objects.create(event=event, created_by=self.other)
        table = QuizTable.objects.create(quiz=quiz, table_number=1)
        QuizTableMembership.objects.create(table=table, user=self.user)
        QuizTableMembership.objects.create(table=table, user=self.other)

        delete_full_account(self.user)

        self.assertEqual(list(event.invited_users.all()), [self.other])
        self.assertEqual(list(resource.audience.all()), [self.other])
        self.assertEqual(
            list(QuizTableMembership.objects.values_list('user', flat=True)),
            [self.other.pk],
        )

    def test_every_user_m2m_relation_is_handled(self):
        from django.apps import apps
        from django.contrib.auth import get_user_model

        from crush_lu.views_account import (
            ACCOUNT_ERASURE_M2M_CRUSH, ACCOUNT_ERASURE_M2M_OTHER_APPS,
            ACCOUNT_ERASURE_M2M_VIA_PURGED_THROUGH,
        )

        handled = (
            set(ACCOUNT_ERASURE_M2M_CRUSH)
            | set(ACCOUNT_ERASURE_M2M_OTHER_APPS)
            | set(ACCOUNT_ERASURE_M2M_VIA_PURGED_THROUGH)
        )
        user_model = get_user_model()
        unhandled = [
            f'{m._meta.app_label}.{m.__name__}.{f.name}'
            for m in apps.get_models()
            for f in m._meta.local_many_to_many
            if f.related_model is user_model
            and (m._meta.app_label, m.__name__, f.name) not in handled
        ]
        self.assertEqual(unhandled, [])

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_two_erased_special_experiences_do_not_collide(self, _s):
        from django.contrib.auth import get_user_model
        from crush_lu.models import CrushProfile, SpecialUserExperience
        from crush_lu.views import delete_full_account

        third = get_user_model().objects.create_user(
            username='third@example.com', email='third@example.com', password='x',
        )
        CrushProfile.objects.create(
            user=third, date_of_birth=date(1995, 5, 15), gender='F',
            location='Luxembourg', is_approved=True, is_active=True,
        )
        for user, first in ((self.user, 'Ann'), (third, 'Bob')):
            SpecialUserExperience.objects.create(
                first_name=first, last_name='Same', linked_user=user,
            )
        delete_full_account(self.user)
        delete_full_account(third)
        rows = list(SpecialUserExperience.objects.all())
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r.linked_user_id is None for r in rows))
        self.assertEqual(len({(r.first_name, r.last_name) for r in rows}), 2)
        self.assertNotIn('Ann', {r.first_name for r in rows})

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_cache_attempt_photo_is_deleted_when_link_is_severed(self, _s):
        from django.core.files.base import ContentFile
        from django.core.files.storage import InMemoryStorage

        from crush_lu.models import CacheChallengeAttempt
        from crush_lu.views import delete_full_account

        station_attempt, challenge = self._setup_cache()
        storage = InMemoryStorage()
        name = storage.save('cache/answer.jpg', ContentFile(b'img'))
        attempt = CacheChallengeAttempt.objects.create(
            station_attempt=station_attempt, challenge=challenge,
            answered_by=self.user, photo=name, last_answer='secret',
        )
        field = CacheChallengeAttempt._meta.get_field('photo')
        with patch.object(field, 'storage', storage):
            delete_full_account(self.user)
        attempt.refresh_from_db()
        self.assertFalse(storage.exists(name))
        self.assertFalse(attempt.photo)
        self.assertEqual(attempt.last_answer, '')
        self.assertIsNone(attempt.answered_by)

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_coach_record_is_anonymised_in_place(self, _s):
        from django.core.files.base import ContentFile
        from django.core.files.storage import InMemoryStorage

        from crush_lu.models import CoachPushSubscription, CrushCoach
        from crush_lu.views import delete_full_account

        storage = InMemoryStorage()
        name = storage.save('coaches/1/p.jpg', ContentFile(b'img'))
        coach = CrushCoach.objects.create(
            user=self.user, bio='about me', specializations='x',
            phone_number='+352621111111', photo=name, spoken_languages=['en'],
        )
        CoachPushSubscription.objects.create(
            coach=coach, endpoint='https://p.example/c', p256dh_key='k',
            auth_key='a',
        )
        field = CrushCoach._meta.get_field('photo')
        with patch.object(field, 'storage', storage):
            delete_full_account(self.user)
        coach.refresh_from_db()  # row kept for referential integrity
        self.assertEqual((coach.bio, coach.phone_number, coach.specializations),
                         ('', '', ''))
        self.assertFalse(coach.photo)
        self.assertFalse(coach.is_active)
        self.assertFalse(storage.exists(name))
        self.assertFalse(CoachPushSubscription.objects.filter(coach=coach).exists())

    def _setup_cache(self):
        from crush_lu.models import (
            CacheChallenge, CacheHunt, CacheStation, CacheStationAttempt,
            CacheTeam, MeetupEvent,
        )

        event = MeetupEvent.objects.create(
            title='H', description='d', event_type='mixer',
            date_time=timezone.now() + timedelta(days=5), location='L',
            address='A', max_participants=10,
            registration_deadline=timezone.now() + timedelta(days=3),
        )
        hunt = CacheHunt.objects.create(
            event=event, title='Hunt', created_by=self.other,
        )
        station = CacheStation.objects.create(hunt=hunt, order=1, name='S')
        challenge = CacheChallenge.objects.create(station=station)
        team = CacheTeam.objects.create(hunt=hunt, name='T', join_code='JOIN01')
        station_attempt = CacheStationAttempt.objects.create(
            team=team, station=station,
        )
        return station_attempt, challenge

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_whatsapp_inbound_messages_are_anonymised_by_phone(self, _s):
        from crush_lu.models import CrushProfile, PhoneOTP
        from crush_lu.views import delete_full_account
        from hub.models import WhatsAppInboundMessage

        CrushProfile.objects.filter(user=self.user).update(
            phone_number='+352 621 111 111', phone_verified=True
        )
        PhoneOTP.objects.create(
            user=self.user, phone_number='+352621222222', code_hash='h',
            expires_at=timezone.now() + timedelta(minutes=5), consumed=True,
        )
        # A consumed row can just be a code superseded by a resend, and an OTP
        # can be requested for someone else's number: neither proves ownership.
        PhoneOTP.objects.create(
            user=self.user, phone_number='+352699999999', code_hash='h',
            expires_at=timezone.now() + timedelta(minutes=5), consumed=False,
        )

        def inbound(wa_id, number):
            return WhatsAppInboundMessage.objects.create(
                wa_message_id=wa_id, from_number=number, contact_name='Del Me',
                text='hello', payload={'x': 1}, received_at=timezone.now(),
            )

        mine = inbound('wamid.1', '352621111111')
        superseded = inbound('wamid.2', '+352621222222')  # consumed != verified
        theirs = inbound('wamid.3', '352699999999')  # only an unverified OTP

        delete_full_account(self.user)

        mine.refresh_from_db()
        self.assertEqual(
            (mine.from_number, mine.contact_name, mine.text, mine.payload),
            ('erased', '', '', {}),
        )
        for row in (superseded, theirs):
            row.refresh_from_db()
            self.assertEqual((row.contact_name, row.text), ('Del Me', 'hello'))

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_erased_user_pk_is_removed_from_manual_sms_batches(self, _s):
        from crush_lu.models.custom_sms import CustomSmsBatch
        from crush_lu.views import delete_crushlu_profile_only, delete_full_account

        batch = CustomSmsBatch.objects.create(
            manual_user_ids=[self.user.pk, self.other.pk],
        )
        keep = CustomSmsBatch.objects.create(manual_user_ids=[self.other.pk])
        delete_crushlu_profile_only(self.user)
        batch.refresh_from_db()
        self.assertEqual(batch.manual_user_ids, [self.other.pk])
        delete_full_account(self.user)  # second path stays idempotent
        keep.refresh_from_db()
        self.assertEqual(keep.manual_user_ids, [self.other.pk])

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_final_sweep_cleans_receipts_written_during_the_purge(self, _s):
        """A receipt written by an in-flight sender mid-purge is blanked."""
        from crush_lu import views_account
        from crush_lu.models import Newsletter, NewsletterRecipient

        newsletter = Newsletter.objects.create(
            subject='S', body_html='x', audience='all_users',
        )
        real = views_account._remove_user_from_id_lists

        def late_receipt(user):
            NewsletterRecipient.objects.update_or_create(
                newsletter=newsletter, user=user,
                defaults={'email': 'del@example.com', 'status': 'sent'},
            )
            return real(user)

        with patch.object(
            views_account, '_remove_user_from_id_lists', side_effect=late_receipt
        ):
            views_account.delete_crushlu_profile_only(self.user)
        row = NewsletterRecipient.objects.get(newsletter=newsletter)
        self.assertEqual((row.status, row.email), ('sent', ''))


class NewsletterReceiptErasureTests(TestCase):
    """Receipts never keep the address after deletion (#1184 race)."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from crush_lu.models import CrushProfile, Newsletter

        self.user = get_user_model().objects.create_user(
            username='nr@example.com', email='nr@example.com', password='x',
        )
        CrushProfile.objects.create(
            user=self.user, date_of_birth=date(1995, 5, 15), gender='M',
            location='Luxembourg', is_approved=True, is_active=True,
        )
        self.newsletter = Newsletter.objects.create(
            subject='S', body_html='x', audience='all_users',
        )

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_receipt_written_before_deletion_is_blanked(self, _s):
        from crush_lu.models import NewsletterRecipient
        from crush_lu.models.profiles import UserDataConsent
        from crush_lu.newsletter_service import write_receipt
        from crush_lu.views import delete_crushlu_profile_only

        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )
        write_receipt(self.newsletter, self.user, {'status': 'sent'})
        self.assertEqual(
            NewsletterRecipient.objects.get().email, 'nr@example.com'
        )
        delete_crushlu_profile_only(self.user)
        row = NewsletterRecipient.objects.get()
        self.assertEqual((row.status, row.email), ('sent', ''))

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_receipt_written_after_revocation_is_stored_blank(self, _s):
        from crush_lu.models import NewsletterRecipient
        from crush_lu.newsletter_service import write_receipt
        from crush_lu.views import delete_crushlu_profile_only

        delete_crushlu_profile_only(self.user)
        write_receipt(self.newsletter, self.user, {'status': 'sent'})
        self.assertEqual(NewsletterRecipient.objects.get().email, '')


class ConsentAndCoachErasureTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from crush_lu.models import CrushProfile

        self.user = get_user_model().objects.create_user(
            username='cc@example.com', email='cc@example.com', password='x',
        )
        CrushProfile.objects.create(
            user=self.user, date_of_birth=date(1995, 5, 15), gender='M',
            location='Luxembourg', is_approved=True, is_active=True,
        )

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_full_deletion_sanitises_identity_layer_consent(self, _s):
        from crush_lu.models.profiles import UserDataConsent
        from crush_lu.views import delete_full_account

        UserDataConsent.objects.filter(user=self.user).update(
            powerup_consent_given=True, powerup_consent_date=timezone.now(),
            powerup_consent_ip='1.2.3.4', marketing_consent=True,
            marketing_consent_date=timezone.now(),
            crushlu_consent_given=True, crushlu_consent_ip='1.2.3.4',
        )
        delete_full_account(self.user)
        consent = UserDataConsent.objects.get(user=self.user)
        self.assertFalse(consent.powerup_consent_given)
        self.assertIsNone(consent.powerup_consent_date)
        self.assertIsNone(consent.powerup_consent_ip)
        self.assertFalse(consent.marketing_consent)
        self.assertIsNone(consent.marketing_consent_date)
        self.assertFalse(consent.crushlu_consent_given)
        self.assertIsNone(consent.crushlu_consent_ip)
        self.assertTrue(consent.crushlu_banned)  # minimal audit record kept

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_profile_only_deletion_keeps_identity_layer_consent(self, _s):
        from crush_lu.models.profiles import UserDataConsent
        from crush_lu.views import delete_crushlu_profile_only

        UserDataConsent.objects.filter(user=self.user).update(
            powerup_consent_given=True, powerup_consent_ip='1.2.3.4',
        )
        from django.contrib.auth import get_user_model

        fresh = get_user_model().objects.get(pk=self.user.pk)
        delete_crushlu_profile_only(fresh)
        consent = UserDataConsent.objects.get(user=self.user)
        self.assertTrue(consent.powerup_consent_given)
        self.assertEqual(consent.powerup_consent_ip, '1.2.3.4')

    def test_every_consent_and_coach_field_is_classified(self):
        from crush_lu.models import CrushCoach
        from crush_lu.models.profiles import UserDataConsent
        from crush_lu.views_account import (
            ACCOUNT_ERASURE_COACH_RESET, ACCOUNT_ERASURE_COACH_RETAINED,
            ACCOUNT_ERASURE_CONSENT_RETAINED, ACCOUNT_ERASURE_CONSENT_SANITIZED,
        )

        consent_fields = {f.name for f in UserDataConsent._meta.concrete_fields}
        classified = set(ACCOUNT_ERASURE_CONSENT_SANITIZED) | set(
            ACCOUNT_ERASURE_CONSENT_RETAINED
        )
        self.assertEqual(consent_fields - classified, set())
        self.assertEqual(classified - consent_fields, set())
        coach_fields = {f.name for f in CrushCoach._meta.concrete_fields}
        classified = set(ACCOUNT_ERASURE_COACH_RESET) | set(
            ACCOUNT_ERASURE_COACH_RETAINED
        )
        self.assertEqual(coach_fields - classified, set())
        self.assertEqual(classified - coach_fields, set())

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_coach_scheduling_state_is_reset(self, _s):
        from crush_lu.models import CrushCoach
        from crush_lu.views import delete_full_account

        coach = CrushCoach.objects.create(
            user=self.user, bio='x', working_mode='scheduled', is_away=True,
            away_until=timezone.now() + timedelta(days=3),
        )
        delete_full_account(self.user)
        coach.refresh_from_db()
        self.assertEqual(coach.working_mode, 'spontaneous')
        self.assertFalse(coach.is_away)
        self.assertIsNone(coach.away_until)


class Round8ErasureTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from crush_lu.models import CrushProfile

        self.user = get_user_model().objects.create_user(
            username='r8@example.com', email='r8@example.com', password='x',
        )
        CrushProfile.objects.create(
            user=self.user, date_of_birth=date(1995, 5, 15), gender='M',
            location='Luxembourg', is_approved=True, is_active=True,
        )

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_refund_reminder_survives_profile_only_deletion_not_full(self, _s):
        from crush_lu.email_helpers import can_send_email
        from crush_lu.views import delete_crushlu_profile_only, delete_full_account

        delete_crushlu_profile_only(self.user)
        # Banned for marketing, but the refund-right notice still goes out.
        self.assertTrue(can_send_email(self.user, 'crush_credit_expiry'))
        self.assertFalse(can_send_email(self.user, 'newsletter'))
        self.assertFalse(can_send_email(self.user, 'event_reminders'))
        delete_full_account(self.user)
        self.assertFalse(can_send_email(self.user, 'crush_credit_expiry'))

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_email_delivery_records_are_sanitised_suppression_stays_effective(self, _s):
        from crush_lu.models import EmailBounceEvent, EmailSuppression
        from crush_lu.views import delete_full_account

        EmailSuppression.objects.create(email='r8@example.com', diagnostic='550 r8@example.com')
        EmailSuppression.objects.create(email='other@example.com', diagnostic='keep')
        EmailBounceEvent.objects.create(
            source_message_id='m1', recipient='R8@example.com',
            subject='Undeliverable: hi r8', diagnostic='550 r8@example.com',
        )
        delete_full_account(self.user)
        mine = EmailSuppression.objects.get(email='r8@example.com')
        self.assertEqual(mine.diagnostic, '')
        self.assertTrue(mine.is_active)  # still protects against re-sending
        self.assertEqual(EmailSuppression.objects.get(email='other@example.com').diagnostic, 'keep')
        event = EmailBounceEvent.objects.get()
        self.assertEqual((event.recipient, event.subject, event.diagnostic), ('', '', ''))
        self.assertEqual(event.classification, 'unknown')

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_whatsapp_message_linked_after_erasure_is_sanitised(self, _s):
        """A send in flight during deletion must not attach phone/params."""
        from crush_lu.models import Campaign
        from crush_lu.services.campaigns import CHANNEL_ADAPTERS
        from hub.models import WhatsAppMessage

        campaign = Campaign.objects.create(
            name='c', channels=['whatsapp'], audience='all_users',
        )
        message = WhatsAppMessage.objects.create(
            user=self.user, recipient='+352621000001', template_name='t',
            language='en', parameters={'1': 'r8@example.com'}, status='sent',
            status_history=[{'error_message': '+352621000001'}],
        )
        # Consent is gone (deletion committed) when the send returns.
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=False, crushlu_banned=True
        )
        CHANNEL_ADAPTERS['whatsapp']._record(
            campaign, self.user, 'sent', message=message, error='boom +352621000001'
        )
        message.refresh_from_db()
        self.assertEqual((message.recipient, message.parameters, message.status_history), ('', {}, []))
        from crush_lu.models import CampaignRecipient

        self.assertEqual(CampaignRecipient.objects.get().error_message, '')

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_oauth_state_rows_are_deleted_and_scalar_ids_are_audited(self, _s):
        from django.apps import apps
        from django.db import models as dj_models

        from crush_lu.models import OAuthState
        from crush_lu.views import delete_full_account
        from crush_lu.views_account import ACCOUNT_ERASURE_SCALAR_USER_IDS

        OAuthState.objects.create(
            state_id='s1', state_data='{}', auth_user_id=self.user.pk,
            expires_at=timezone.now() + timedelta(minutes=5),
            ip_address='1.2.3.4', user_agent='UA',
        )
        OAuthState.objects.create(
            state_id='s2', state_data='{}', auth_user_id=self.user.pk + 99,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        delete_full_account(self.user)
        self.assertEqual(list(OAuthState.objects.values_list('state_id', flat=True)), ['s2'])

        handled = {(a, m, f) for a, m, f in ACCOUNT_ERASURE_SCALAR_USER_IDS}
        scalar = {
            (m._meta.app_label, m.__name__, f.name)
            for m in apps.get_models()
            for f in m._meta.concrete_fields
            if not f.is_relation
            and isinstance(f, (dj_models.IntegerField, dj_models.CharField))
            and (f.name.endswith('user_id') or f.name == 'auth_user_id')
        }
        self.assertEqual(scalar - handled, set())

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_special_experience_journey_graph_and_files_are_erased(self, _s):
        from django.core.files.base import ContentFile
        from django.core.files.storage import InMemoryStorage

        from crush_lu.models import (
            AdventCalendar, JourneyConfiguration, SpecialUserExperience,
        )
        from crush_lu.views import delete_full_account

        storage = InMemoryStorage()
        bg = storage.save('advent_backgrounds/bg.jpg', ContentFile(b'img'))
        special = SpecialUserExperience.objects.create(
            first_name='Ann', last_name='Z', linked_user=self.user,
            custom_welcome_title='For Ann', custom_landing_url='https://x/ann',
        )
        journey = JourneyConfiguration.objects.create(
            special_experience=special, journey_name='Ann journey',
            final_message='Dear Ann',
        )
        AdventCalendar.objects.create(
            journey=journey, year=2026, start_date=date(2026, 12, 1),
            end_date=date(2026, 12, 24), calendar_title='Ann advent',
            background_image=bg,
        )
        field = AdventCalendar._meta.get_field('background_image')
        with patch.object(field, 'storage', storage):
            delete_full_account(self.user)
        special.refresh_from_db()
        self.assertEqual((special.custom_welcome_title, special.custom_landing_url), ('', ''))
        self.assertFalse(JourneyConfiguration.objects.exists())
        self.assertFalse(AdventCalendar.objects.exists())
        self.assertFalse(storage.exists(bg))


class Round9ErasureTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from crush_lu.models import CrushProfile

        User = get_user_model()
        self.user = User.objects.create_user(
            username='r9@example.com', email='r9@example.com', password='x',
        )
        self.sender = User.objects.create_user(
            username='sender9@example.com', email='sender9@example.com', password='x',
        )
        for u in (self.user, self.sender):
            CrushProfile.objects.create(
                user=u, date_of_birth=date(1995, 5, 15), gender='M',
                location='Luxembourg', is_approved=True, is_active=True,
            )

    @patch('crush_lu.storage.delete_user_storage', return_value=(True, 0))
    def test_claimed_gift_media_and_personal_details_are_erased(self, _s):
        from django.core.files.base import ContentFile
        from django.core.files.storage import InMemoryStorage

        from crush_lu.models import JourneyGift
        from crush_lu.views import delete_full_account

        storage = InMemoryStorage()
        photo = storage.save('journey_gifts/c1.jpg', ContentFile(b'x'))
        audio = storage.save('journey_gifts/a.mp3', ContentFile(b'x'))
        gift = JourneyGift.objects.create(
            sender=self.sender, recipient_name='R9', claimed_by=self.user,
            sender_message='From the sender', date_first_met=date(2024, 3, 3),
            location_first_met='Cafe Rue X', chapter1_image=photo,
            chapter4_audio=audio,
        )
        with patch.object(
            JourneyGift._meta.get_field('chapter1_image'), 'storage', storage
        ), patch.object(
            JourneyGift._meta.get_field('chapter4_audio'), 'storage', storage
        ):
            delete_full_account(self.user)
        gift.refresh_from_db()
        self.assertFalse(storage.exists(photo))
        self.assertFalse(storage.exists(audio))
        self.assertEqual((gift.chapter1_image.name, gift.chapter4_audio.name), ('', ''))
        self.assertEqual((gift.location_first_met, gift.recipient_name), ('', ''))
        self.assertEqual(gift.date_first_met, gift.created_at.date())
        self.assertEqual(gift.sender_message, 'From the sender')
        self.assertIsNone(gift.claimed_by)

    def test_whatsapp_link_is_serialised_with_the_consent_lock(self):
        """Revoked between the recheck and the link => stored message is blank."""
        from crush_lu.models import Campaign, CampaignRecipient
        from crush_lu.models.profiles import UserDataConsent
        from crush_lu.services import campaigns
        from hub.models import WhatsAppMessage

        campaign = Campaign.objects.create(
            name='c', channels=['whatsapp'], audience='all_users',
        )
        message = WhatsAppMessage.objects.create(
            user=self.sender, recipient='+352621000009', template_name='t',
            language='en', parameters={'1': 'r9'}, status='sent',
        )
        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )
        real = campaigns.newsletter_service.locked_consent_holds

        def revoke_then_lock(user):
            UserDataConsent.objects.filter(user=user).update(
                crushlu_consent_given=False, crushlu_banned=True
            )
            return real(user)

        with patch.object(
            campaigns.newsletter_service, 'locked_consent_holds',
            side_effect=revoke_then_lock,
        ):
            campaigns.CHANNEL_ADAPTERS['whatsapp']._record(
                campaign, self.user, 'sent', message=message
            )
        message.refresh_from_db()
        self.assertEqual((message.recipient, message.parameters), ('', {}))
        self.assertEqual(CampaignRecipient.objects.get().whatsapp_message_id, message.pk)
