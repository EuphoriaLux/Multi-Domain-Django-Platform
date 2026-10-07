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
            seen['state'] = (
                consent.crushlu_consent_given, consent.crushlu_banned,
                EmailPreference.objects.filter(user=user).exists(),
            )
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
            phone_number='+352 621 111 111'
        )
        PhoneOTP.objects.create(
            user=self.user, phone_number='+352621222222', code_hash='h',
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        def inbound(wa_id, number):
            return WhatsAppInboundMessage.objects.create(
                wa_message_id=wa_id, from_number=number, contact_name='Del Me',
                text='hello', payload={'x': 1}, received_at=timezone.now(),
            )

        mine = inbound('wamid.1', '352621111111')
        also_mine = inbound('wamid.2', '+352621222222')
        theirs = inbound('wamid.3', '352699999999')

        delete_full_account(self.user)

        for row in (mine, also_mine):
            row.refresh_from_db()
            self.assertEqual(
                (row.from_number, row.contact_name, row.text, row.payload),
                ('erased', '', '', {}),
            )
        theirs.refresh_from_db()
        self.assertEqual((theirs.contact_name, theirs.text), ('Del Me', 'hello'))
