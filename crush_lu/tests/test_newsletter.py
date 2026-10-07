"""
Newsletter System Tests for Crush.lu

Tests for:
- Audience filtering (all_users, all_profiles, approved_profiles, segment)
- Email preference opt-out
- Master unsubscribe
- Dry run mode
- Limit cap
- Resumability (re-run skips sent users)
- Unsubscribe URL rendering
- Management command

Run with: pytest crush_lu/tests/test_newsletter.py -v
"""
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from crush_lu.models import CrushProfile, EmailPreference
from crush_lu.models.profiles import UserDataConsent
from crush_lu.models.newsletter import Newsletter, NewsletterRecipient
from crush_lu.newsletter_service import get_newsletter_recipients, send_newsletter

User = get_user_model()


def make_profile(consented=True, **kwargs):
    """Create a CrushProfile; consented by default.

    Newsletter/campaign audiences require ``crushlu_consent_given`` (#1184);
    the post-save signal creates the consent row with it False, so fixtures
    flip it unless a test passes ``consented=False``.
    """
    profile = CrushProfile.objects.create(**kwargs)
    UserDataConsent.objects.update_or_create(
        user=profile.user, defaults={"crushlu_consent_given": consented},
    )
    return profile


class NewsletterConsentGateTests(TestCase):
    """#1184: a lazily-created profile without Crush.lu consent is never mailed."""

    def setUp(self):
        self.unconsented = User.objects.create_user(
            username='nc@example.com', email='nc@example.com', password='x',
        )
        make_profile(
            consented=False, user=self.unconsented,
            date_of_birth='1995-01-01', gender='M', location='Luxembourg',
            is_approved=True, verification_status='verified',
        )
        self.consented = User.objects.create_user(
            username='c@example.com', email='c@example.com', password='x',
        )
        make_profile(
            user=self.consented,
            date_of_birth='1995-01-01', gender='F', location='Luxembourg',
            is_approved=True, verification_status='verified',
        )

    def test_unconsented_profile_excluded_from_every_audience(self):
        for audience in ('all_users', 'all_profiles', 'approved_profiles'):
            nl = Newsletter.objects.create(
                subject='S', body_html='<p>x</p>', audience=audience,
            )
            recipients = get_newsletter_recipients(nl)
            self.assertNotIn(self.unconsented, recipients, audience)
            self.assertIn(self.consented, recipients, audience)

    def test_user_without_consent_row_excluded(self):
        UserDataConsent.objects.filter(user=self.consented).delete()
        nl = Newsletter.objects.create(
            subject='S', body_html='<p>x</p>', audience='all_users',
        )
        self.assertNotIn(self.consented, get_newsletter_recipients(nl))

    def test_campaign_audience_applies_the_same_gate(self):
        from crush_lu.services.campaigns import (
            CHANNEL_ADAPTERS, create_campaign, resolve_campaign_audience,
        )
        campaign = create_campaign(
            name='c', channels=['email'], audience='all_users',
        )
        self.assertNotIn(self.unconsented, resolve_campaign_audience(campaign))
        self.assertIn(self.consented, resolve_campaign_audience(campaign))
        eligible = CHANNEL_ADAPTERS['email'].eligible_users(campaign)
        self.assertNotIn(self.unconsented, eligible)
        self.assertIn(self.consented, eligible)


class NewsletterAudienceTests(TestCase):
    """Test that audience filtering returns the correct users."""

    def setUp(self):
        # User with no profile
        self.user_no_profile = User.objects.create_user(
            username='noprofile@example.com',
            email='noprofile@example.com',
            password='testpass123',
            first_name='No',
            last_name='Profile',
        )

        # User with unapproved profile
        self.user_unapproved = User.objects.create_user(
            username='unapproved@example.com',
            email='unapproved@example.com',
            password='testpass123',
            first_name='Un',
            last_name='Approved',
        )
        make_profile(
            user=self.user_unapproved,
            date_of_birth='1995-01-01',
            gender='M',
            location='Luxembourg',
            is_approved=False,
        )

        # User with approved profile
        self.user_approved = User.objects.create_user(
            username='approved@example.com',
            email='approved@example.com',
            password='testpass123',
            first_name='App',
            last_name='Roved',
        )
        make_profile(
            user=self.user_approved,
            date_of_birth='1995-01-01',
            gender='F',
            location='Luxembourg',
            is_approved=True,
        )

    def test_all_users_audience(self):
        """all_users should only include users with a CrushProfile."""
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Hi</p>', audience='all_users',
        )
        recipients = get_newsletter_recipients(newsletter)
        # user_no_profile is excluded (no CrushProfile)
        self.assertEqual(recipients.count(), 2)
        self.assertNotIn(self.user_no_profile, recipients)

    def test_all_profiles_audience(self):
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Hi</p>', audience='all_profiles',
        )
        recipients = get_newsletter_recipients(newsletter)
        self.assertEqual(recipients.count(), 2)
        self.assertNotIn(self.user_no_profile, recipients)

    def test_approved_profiles_audience(self):
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Hi</p>', audience='approved_profiles',
        )
        recipients = get_newsletter_recipients(newsletter)
        self.assertEqual(recipients.count(), 1)
        self.assertIn(self.user_approved, recipients)

    def test_newsletter_preference_opt_out(self):
        """Users who opted out of newsletter emails should be excluded."""
        EmailPreference.objects.update_or_create(
            user=self.user_approved,
            defaults={'email_newsletter': False},
        )
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Hi</p>', audience='all_users',
        )
        recipients = get_newsletter_recipients(newsletter)
        self.assertNotIn(self.user_approved, recipients)
        # user_no_profile excluded (no CrushProfile), user_approved opted out
        self.assertEqual(recipients.count(), 1)

    def test_unsubscribed_all_excluded(self):
        """Users with unsubscribed_all=True should be excluded."""
        EmailPreference.objects.update_or_create(
            user=self.user_no_profile,
            defaults={'unsubscribed_all': True},
        )
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Hi</p>', audience='all_users',
        )
        recipients = get_newsletter_recipients(newsletter)
        self.assertNotIn(self.user_no_profile, recipients)

    def test_users_without_preference_record_included(self):
        """Users without an EmailPreference record should be included (default=True)."""
        # No EmailPreference created for any user
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Hi</p>', audience='all_users',
        )
        recipients = get_newsletter_recipients(newsletter)
        # user_no_profile excluded (no CrushProfile), 2 users with profiles included
        self.assertEqual(recipients.count(), 2)


class NewsletterSendTests(TestCase):
    """Test the send_newsletter function."""

    def setUp(self):
        self.user1 = User.objects.create_user(
            username='user1@example.com',
            email='user1@example.com',
            password='testpass123',
            first_name='Alice',
        )
        make_profile(
            user=self.user1, date_of_birth='1995-01-01',
            gender='F', location='Luxembourg',
        )
        self.user2 = User.objects.create_user(
            username='user2@example.com',
            email='user2@example.com',
            password='testpass123',
            first_name='Bob',
        )
        make_profile(
            user=self.user2, date_of_birth='1995-01-01',
            gender='M', location='Luxembourg',
        )
        self.newsletter = Newsletter.objects.create(
            subject='Test Newsletter',
            body_html='<p>Hello world!</p>',
            audience='all_users',
        )

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_send_creates_recipient_records(self, mock_send):
        results = send_newsletter(self.newsletter)

        self.assertEqual(results['sent'], 2)
        self.assertEqual(results['failed'], 0)
        self.assertEqual(NewsletterRecipient.objects.filter(
            newsletter=self.newsletter, status='sent'
        ).count(), 2)

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_send_updates_newsletter_stats(self, mock_send):
        send_newsletter(self.newsletter)

        self.newsletter.refresh_from_db()
        self.assertEqual(self.newsletter.status, 'sent')
        self.assertEqual(self.newsletter.total_sent, 2)
        self.assertIsNotNone(self.newsletter.sent_at)

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=0)
    def test_suppressed_deliveries_are_recorded_as_skipped(self, mock_send):
        results = send_newsletter(self.newsletter)

        self.assertEqual(results['sent'], 0)
        self.assertEqual(results['skipped'], 2)
        self.assertEqual(
            NewsletterRecipient.objects.filter(
                newsletter=self.newsletter, status='skipped'
            ).count(),
            2,
        )
        self.assertFalse(
            NewsletterRecipient.objects.filter(
                newsletter=self.newsletter, status='sent'
            ).exists()
        )

    def test_dry_run_creates_no_records(self):
        results = send_newsletter(self.newsletter, dry_run=True)

        self.assertEqual(results['sent'], 0)
        self.assertEqual(NewsletterRecipient.objects.count(), 0)
        self.newsletter.refresh_from_db()
        self.assertEqual(self.newsletter.status, 'draft')

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_limit_caps_sends(self, mock_send):
        results = send_newsletter(self.newsletter, limit=1)

        self.assertEqual(results['sent'], 1)
        self.assertEqual(mock_send.call_count, 1)

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_resumability_skips_sent(self, mock_send):
        """Re-running a newsletter should skip already-sent users."""
        # First run: send to 1 user
        send_newsletter(self.newsletter, limit=1)
        self.assertEqual(mock_send.call_count, 1)

        # Reset newsletter status to allow re-send
        self.newsletter.status = 'sending'
        self.newsletter.save(update_fields=['status'])

        # Second run: should only send to the remaining user
        results = send_newsletter(self.newsletter)
        self.assertEqual(results['sent'], 1)
        self.assertEqual(mock_send.call_count, 2)  # total across both runs

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_opted_out_user_excluded_from_recipients(self, mock_send):
        """Users who opted out of newsletters are excluded from the recipient list."""
        EmailPreference.objects.update_or_create(
            user=self.user1,
            defaults={'email_newsletter': False},
        )

        results = send_newsletter(self.newsletter)
        # user1 excluded at queryset level, only user2 sent
        self.assertEqual(results['sent'], 1)
        self.assertEqual(mock_send.call_count, 1)

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', side_effect=Exception("SMTP error"))
    def test_failed_send_recorded(self, mock_send):
        results = send_newsletter(self.newsletter)

        self.assertEqual(results['failed'], 2)
        self.newsletter.refresh_from_db()
        self.assertEqual(self.newsletter.status, 'failed')
        self.assertEqual(self.newsletter.total_failed, 2)

    def test_cannot_send_already_sent_newsletter(self):
        self.newsletter.status = 'sent'
        self.newsletter.save()

        with self.assertRaises(ValueError):
            send_newsletter(self.newsletter)

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_sends_from_love_at_crush_lu(self, mock_send):
        send_newsletter(self.newsletter, limit=1)

        call_kwargs = mock_send.call_args[1]
        self.assertEqual(call_kwargs['from_email'], 'love@crush.lu')
        self.assertEqual(call_kwargs['domain'], 'crush.lu')


class NewsletterEmailRenderTests(TestCase):
    """Test that the newsletter email template renders correctly."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='render@example.com',
            email='render@example.com',
            password='testpass123',
            first_name='Render',
        )
        make_profile(
            user=self.user, date_of_birth='1995-01-01',
            gender='F', location='Luxembourg',
        )

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_unsubscribe_url_in_email(self, mock_send):
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Content</p>', audience='all_users',
        )
        send_newsletter(newsletter)

        call_kwargs = mock_send.call_args[1]
        html = call_kwargs['html_message']
        self.assertIn('unsubscribe', html.lower())

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_body_html_rendered(self, mock_send):
        newsletter = Newsletter.objects.create(
            subject='Test',
            body_html='<p>Special newsletter content here</p>',
            audience='all_users',
        )
        send_newsletter(newsletter)

        call_kwargs = mock_send.call_args[1]
        html = call_kwargs['html_message']
        self.assertIn('Special newsletter content here', html)

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_plain_text_fallback_auto_generated(self, mock_send):
        """When body_text is blank, plain text is auto-stripped from HTML."""
        newsletter = Newsletter.objects.create(
            subject='Test',
            body_html='<p>Auto stripped</p>',
            body_text='',
            audience='all_users',
        )
        send_newsletter(newsletter)

        call_kwargs = mock_send.call_args[1]
        plain = call_kwargs['message']
        self.assertNotIn('<p>', plain)

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_custom_plain_text_used(self, mock_send):
        """When body_text is provided, it should be used as the plain text."""
        newsletter = Newsletter.objects.create(
            subject='Test',
            body_html='<p>HTML version</p>',
            body_text='Custom plain text version',
            audience='all_users',
        )
        send_newsletter(newsletter)

        call_kwargs = mock_send.call_args[1]
        self.assertEqual(call_kwargs['message'], 'Custom plain text version')


class NewsletterManagementCommandTests(TestCase):
    """Test the send_newsletter management command."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='cmd@example.com',
            email='cmd@example.com',
            password='testpass123',
            first_name='Cmd',
        )
        make_profile(
            user=self.user, date_of_birth='1995-01-01',
            gender='M', location='Luxembourg',
        )

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_send_by_newsletter_id(self, mock_send):
        newsletter = Newsletter.objects.create(
            subject='CLI Test', body_html='<p>Hi</p>', audience='all_users',
        )
        out = StringIO()
        call_command('send_newsletter', newsletter_id=newsletter.pk, stdout=out)

        newsletter.refresh_from_db()
        self.assertEqual(newsletter.status, 'sent')

    def test_dry_run_flag(self):
        newsletter = Newsletter.objects.create(
            subject='Dry Run', body_html='<p>Hi</p>', audience='all_users',
        )
        out = StringIO()
        call_command(
            'send_newsletter', newsletter_id=newsletter.pk,
            dry_run=True, stdout=out,
        )

        newsletter.refresh_from_db()
        self.assertEqual(newsletter.status, 'draft')

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_limit_flag(self, mock_send):
        # Create 3 users (all with CrushProfiles)
        user2 = User.objects.create_user(
            username='cmd2@example.com', email='cmd2@example.com', password='pass',
        )
        make_profile(
            user=user2, date_of_birth='1995-01-01',
            gender='F', location='Luxembourg',
        )
        user3 = User.objects.create_user(
            username='cmd3@example.com', email='cmd3@example.com', password='pass',
        )
        make_profile(
            user=user3, date_of_birth='1995-01-01',
            gender='M', location='Luxembourg',
        )
        newsletter = Newsletter.objects.create(
            subject='Limit', body_html='<p>Hi</p>', audience='all_users',
        )
        out = StringIO()
        call_command(
            'send_newsletter', newsletter_id=newsletter.pk,
            limit=2, stdout=out,
        )
        self.assertEqual(mock_send.call_count, 2)

    def test_list_segments(self):
        out = StringIO()
        call_command('send_newsletter', list_segments=True, stdout=out)
        output = out.getvalue()
        self.assertIn('Profile Completion', output)

    def test_missing_args_raises_error(self):
        """Command should error if neither --newsletter-id nor --subject/--body-file given."""
        out = StringIO()
        with self.assertRaises(Exception):
            call_command('send_newsletter', stdout=out)


class NewsletterModelTests(TestCase):
    """Test Newsletter model basics."""

    def test_newsletter_str(self):
        newsletter = Newsletter.objects.create(
            subject='My Newsletter', body_html='<p>test</p>',
        )
        self.assertIn('My Newsletter', str(newsletter))
        # Status label is translated; check the raw status value is draft
        self.assertEqual(newsletter.status, 'draft')

    def test_recipient_str(self):
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>test</p>',
        )
        user = User.objects.create_user(
            username='str@example.com', email='str@example.com', password='pass',
        )
        recipient = NewsletterRecipient.objects.create(
            newsletter=newsletter, user=user, email='str@example.com',
        )
        self.assertIn('str@example.com', str(recipient))

    def test_unique_together_constraint(self):
        """Cannot create two recipient records for the same user+newsletter."""
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>test</p>',
        )
        user = User.objects.create_user(
            username='dup@example.com', email='dup@example.com', password='pass',
        )
        NewsletterRecipient.objects.create(
            newsletter=newsletter, user=user, email='dup@example.com',
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            NewsletterRecipient.objects.create(
                newsletter=newsletter, user=user, email='dup@example.com',
            )


class NewsletterOptOutControlsTests(TestCase):
    """#1185: the marketing controls drive the flag newsletters really read."""

    def setUp(self):
        from django.core.cache import cache
        from django.test import Client

        cache.clear()
        self.user = User.objects.create_user(
            username='ctl@example.com', email='ctl@example.com', password='x',
        )
        make_profile(
            user=self.user, date_of_birth='1995-01-01', gender='F',
            location='Luxembourg', is_approved=True,
        )
        self.client = Client(HTTP_HOST='crush.lu')
        self.newsletter = Newsletter.objects.create(
            subject='S', body_html='<p>x</p>', audience='all_users',
        )

    def _post_pref(self, key, value):
        import json

        self.client.force_login(self.user)
        return self.client.post(
            '/api/email/preferences/',
            data=json.dumps({'key': key, 'value': value}),
            content_type='application/json',
        )

    def test_marketing_toggle_off_stops_newsletters(self):
        self.assertIn(self.user, get_newsletter_recipients(self.newsletter))
        self.assertEqual(self._post_pref('email_marketing', False).status_code, 200)
        self.assertNotIn(self.user, get_newsletter_recipients(self.newsletter))

    def test_marketing_toggle_on_reenables_newsletters(self):
        self._post_pref('email_marketing', False)
        self._post_pref('email_marketing', True)
        self.assertIn(self.user, get_newsletter_recipients(self.newsletter))

    def test_newsletter_key_is_accepted_by_the_preferences_api(self):
        self.assertEqual(self._post_pref('email_newsletter', False).status_code, 200)
        self.assertNotIn(self.user, get_newsletter_recipients(self.newsletter))

    def test_unsubscribe_marketing_only_clears_newsletter_flag_too(self):
        prefs = EmailPreference.get_or_create_for_user(self.user)
        prefs.email_marketing = True
        prefs.save()
        response = self.client.post(
            f'/en/unsubscribe/{prefs.unsubscribe_token}/',
            {'action': 'unsubscribe_marketing'},
        )
        self.assertEqual(response.status_code, 200)
        prefs.refresh_from_db()
        self.assertFalse(prefs.email_marketing)
        self.assertFalse(prefs.email_newsletter)
        self.assertNotIn(self.user, get_newsletter_recipients(self.newsletter))
        # Other (service) preferences are untouched.
        self.assertTrue(prefs.email_event_reminders)

    def test_marketing_unsubscribe_button_shown_when_only_newsletter_on(self):
        prefs = EmailPreference.get_or_create_for_user(self.user)
        self.assertFalse(prefs.email_marketing)
        self.assertTrue(prefs.email_newsletter)
        response = self.client.get(f'/en/unsubscribe/{prefs.unsubscribe_token}/')
        self.assertContains(response, 'value="unsubscribe_marketing"')

    def test_resubscribe_keeps_the_marketing_choice_consistent(self):
        """Resubscribe must not turn newsletters back on behind an OFF toggle."""
        prefs = EmailPreference.get_or_create_for_user(self.user)
        self._post_pref('email_marketing', False)
        self.client.post(
            f'/en/unsubscribe/{prefs.unsubscribe_token}/',
            {'action': 'unsubscribe_all'},
        )
        self.client.post(
            f'/en/unsubscribe/{prefs.unsubscribe_token}/',
            {'action': 'resubscribe'},
        )
        prefs.refresh_from_db()
        self.assertFalse(prefs.unsubscribed_all)
        self.assertFalse(prefs.email_marketing)
        self.assertFalse(prefs.email_newsletter)
        self.assertNotIn(self.user, get_newsletter_recipients(self.newsletter))


class SendTimeConsentRecheckTests(TestCase):
    """Consent/ban revoked after the audience was resolved must stop the send."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='rc@example.com', email='rc@example.com', password='x',
        )
        make_profile(
            user=self.user, date_of_birth='1995-01-01', gender='F',
            location='Luxembourg', is_approved=True,
        )
        self.newsletter = Newsletter.objects.create(
            subject='S', body_html='<p>x</p>', audience='all_users',
        )

    def test_send_newsletter_skips_user_banned_after_resolution(self):
        from crush_lu import newsletter_service

        real = newsletter_service.get_newsletter_recipients

        def resolve_then_revoke(newsletter):
            ids = list(real(newsletter).values_list('id', flat=True))
            UserDataConsent.objects.filter(user=self.user).update(
                crushlu_consent_given=False, crushlu_banned=True,
            )
            return User.objects.filter(id__in=ids)

        with patch.object(
            newsletter_service, 'get_newsletter_recipients',
            side_effect=resolve_then_revoke,
        ), patch.object(newsletter_service, '_send_newsletter_to_user') as send:
            results = send_newsletter(self.newsletter)
        send.assert_not_called()
        self.assertEqual(results['sent'], 0)
        self.assertEqual(results['skipped'], 1)

    def test_can_send_email_denies_banned_user_without_creating_prefs(self):
        from crush_lu.email_helpers import can_send_email

        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=False, crushlu_banned=True,
        )
        EmailPreference.objects.filter(user=self.user).delete()
        self.assertFalse(can_send_email(self.user, 'newsletter'))
        self.assertFalse(EmailPreference.objects.filter(user=self.user).exists())

    def test_has_current_consent(self):
        from crush_lu.newsletter_service import has_current_consent

        self.assertTrue(has_current_consent(self.user))
        UserDataConsent.objects.filter(user=self.user).update(crushlu_banned=True)
        self.assertFalse(has_current_consent(self.user))


class EmailPreferenceNewsletterFieldTests(TestCase):
    """Test the new email_newsletter field on EmailPreference."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='pref@example.com', email='pref@example.com', password='pass',
        )

    def test_default_newsletter_preference_is_true(self):
        pref = EmailPreference.get_or_create_for_user(self.user)
        self.assertTrue(pref.email_newsletter)

    def test_can_send_newsletter_true(self):
        pref = EmailPreference.get_or_create_for_user(self.user)
        self.assertTrue(pref.can_send('newsletter'))

    def test_can_send_newsletter_false_when_opted_out(self):
        pref, _ = EmailPreference.objects.update_or_create(
            user=self.user, defaults={'email_newsletter': False},
        )
        self.assertFalse(pref.can_send('newsletter'))

    def test_can_send_newsletter_false_when_unsubscribed_all(self):
        pref, _ = EmailPreference.objects.update_or_create(
            user=self.user, defaults={'unsubscribed_all': True},
        )
        self.assertFalse(pref.can_send('newsletter'))

    def test_get_enabled_categories_includes_newsletter(self):
        pref = EmailPreference.get_or_create_for_user(self.user)
        self.assertIn('newsletter', pref.get_enabled_categories())

    def test_get_enabled_categories_excludes_newsletter_when_off(self):
        pref, _ = EmailPreference.objects.update_or_create(
            user=self.user, defaults={'email_newsletter': False},
        )
        self.assertNotIn('newsletter', pref.get_enabled_categories())


class NewsletterLanguageFilterTests(TestCase):
    """Test that language filtering restricts recipients correctly."""

    def setUp(self):
        # User with no profile (defaults to English)
        self.user_no_profile = User.objects.create_user(
            username='nolang@example.com',
            email='nolang@example.com',
            password='testpass123',
        )

        # English-speaking user
        self.user_en = User.objects.create_user(
            username='en@example.com',
            email='en@example.com',
            password='testpass123',
        )
        make_profile(
            user=self.user_en,
            date_of_birth='1995-01-01',
            gender='M',
            location='Luxembourg',
            preferred_language='en',
        )

        # German-speaking user
        self.user_de = User.objects.create_user(
            username='de@example.com',
            email='de@example.com',
            password='testpass123',
        )
        make_profile(
            user=self.user_de,
            date_of_birth='1995-01-01',
            gender='F',
            location='Luxembourg',
            preferred_language='de',
        )

        # French-speaking user
        self.user_fr = User.objects.create_user(
            username='fr@example.com',
            email='fr@example.com',
            password='testpass123',
        )
        make_profile(
            user=self.user_fr,
            date_of_birth='1995-01-01',
            gender='M',
            location='Luxembourg',
            preferred_language='fr',
        )

    def test_language_all_includes_everyone(self):
        """all_users with language=all includes all users with CrushProfiles."""
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Hi</p>',
            audience='all_users', language='all',
        )
        recipients = get_newsletter_recipients(newsletter)
        # user_no_profile excluded (no CrushProfile)
        self.assertEqual(recipients.count(), 3)
        self.assertNotIn(self.user_no_profile, recipients)

    def test_language_en_includes_english_users(self):
        """all_users with language=en includes only English-speaking profile users."""
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Hi</p>',
            audience='all_users', language='en',
        )
        recipients = get_newsletter_recipients(newsletter)
        self.assertEqual(recipients.count(), 1)
        self.assertIn(self.user_en, recipients)
        self.assertNotIn(self.user_no_profile, recipients)

    def test_language_de_only_german(self):
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Hi</p>',
            audience='all_users', language='de',
        )
        recipients = get_newsletter_recipients(newsletter)
        self.assertEqual(recipients.count(), 1)
        self.assertIn(self.user_de, recipients)

    def test_language_fr_only_french(self):
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Hi</p>',
            audience='all_users', language='fr',
        )
        recipients = get_newsletter_recipients(newsletter)
        self.assertEqual(recipients.count(), 1)
        self.assertIn(self.user_fr, recipients)

    def test_language_filter_combined_with_audience(self):
        """Language filter should work alongside audience filter."""
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Hi</p>',
            audience='all_profiles', language='de',
        )
        recipients = get_newsletter_recipients(newsletter)
        # Only user_de has profile + German language
        self.assertEqual(recipients.count(), 1)
        self.assertIn(self.user_de, recipients)


class NewsletterAdminFormTests(TestCase):
    """Test the NewsletterAdminForm validation."""

    def _form_data(self, **overrides):
        """Build valid form data dict, including modeltranslation fields."""
        data = {
            'newsletter_type': 'standard',
            'subject': 'Test',
            'subject_en': 'Test',
            'body_html': '<p>Hi</p>',
            'body_html_en': '<p>Hi</p>',
            'audience': 'all_users',
            'segment_key': '',
            'language': 'all',
        }
        data.update(overrides)
        return data

    def test_segment_audience_requires_segment_key(self):
        from crush_lu.admin.newsletter import NewsletterAdminForm

        form = NewsletterAdminForm(data=self._form_data(
            audience='segment',
            segment_key='',
        ))
        self.assertFalse(form.is_valid())
        self.assertIn('segment_key', form.errors)

    def test_non_segment_audience_clears_segment_key(self):
        from crush_lu.admin.newsletter import NewsletterAdminForm

        form = NewsletterAdminForm(data=self._form_data(
            segment_key='some_key',
        ))
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['segment_key'], '')

    def test_valid_form_with_all_users(self):
        from crush_lu.admin.newsletter import NewsletterAdminForm

        form = NewsletterAdminForm(data=self._form_data())
        self.assertTrue(form.is_valid())


class NewsletterTypeTests(TestCase):
    """Test the newsletter_type field and template dispatch."""

    def test_default_newsletter_type_is_standard(self):
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Hi</p>',
        )
        self.assertEqual(newsletter.newsletter_type, 'standard')

    def test_patch_notes_type(self):
        newsletter = Newsletter.objects.create(
            subject='What\'s New',
            body_html='<h3>New Features</h3><ul><li>Feature 1</li></ul>',
            newsletter_type='patch_notes',
        )
        self.assertEqual(newsletter.newsletter_type, 'patch_notes')
        self.assertEqual(newsletter.get_newsletter_type_display(), 'Patch Notes')

    def test_newsletter_str_includes_subject(self):
        newsletter = Newsletter.objects.create(
            subject='Patch Notes v2.0',
            body_html='<p>test</p>',
            newsletter_type='patch_notes',
        )
        self.assertIn('Patch Notes v2.0', str(newsletter))

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_patch_notes_template_used(self, mock_send):
        """Patch notes newsletters should use the patch_notes.html template."""
        user = User.objects.create_user(
            username='pntest@example.com',
            email='pntest@example.com',
            password='testpass123',
            first_name='Tester',
        )
        make_profile(
            user=user, date_of_birth='1995-01-01',
            gender='F', location='Luxembourg',
        )
        newsletter = Newsletter.objects.create(
            subject='What\'s New on Crush.lu',
            body_html='<h3>New Features</h3><ul><li>Patch Notes Newsletter</li></ul>',
            audience='all_users',
            newsletter_type='patch_notes',
        )
        send_newsletter(newsletter)

        call_kwargs = mock_send.call_args[1]
        html = call_kwargs['html_message']
        # The patch_notes.html template contains this translated string
        self.assertIn('Crush.lu', html)
        # The body content should be rendered
        self.assertIn('Patch Notes Newsletter', html)

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_standard_template_used_by_default(self, mock_send):
        """Standard newsletters should use the newsletter.html template."""
        user = User.objects.create_user(
            username='stdtest@example.com',
            email='stdtest@example.com',
            password='testpass123',
            first_name='Standard',
        )
        make_profile(
            user=user, date_of_birth='1995-01-01',
            gender='M', location='Luxembourg',
        )
        newsletter = Newsletter.objects.create(
            subject='Standard Subject',
            body_html='<p>Standard body content</p>',
            audience='all_users',
            newsletter_type='standard',
        )
        send_newsletter(newsletter)

        call_kwargs = mock_send.call_args[1]
        html = call_kwargs['html_message']
        self.assertIn('Standard body content', html)


class NewsletterMultilingualTests(TestCase):
    """Test multilingual newsletter content delivery."""

    def setUp(self):
        # English-speaking user
        self.user_en = User.objects.create_user(
            username='mlen@example.com',
            email='mlen@example.com',
            password='testpass123',
            first_name='English',
        )
        make_profile(
            user=self.user_en,
            date_of_birth='1995-01-01',
            gender='M',
            location='Luxembourg',
            preferred_language='en',
        )

        # German-speaking user
        self.user_de = User.objects.create_user(
            username='mlde@example.com',
            email='mlde@example.com',
            password='testpass123',
            first_name='Deutsch',
        )
        make_profile(
            user=self.user_de,
            date_of_birth='1995-01-01',
            gender='F',
            location='Luxembourg',
            preferred_language='de',
        )

        # French-speaking user
        self.user_fr = User.objects.create_user(
            username='mlfr@example.com',
            email='mlfr@example.com',
            password='testpass123',
            first_name='Francais',
        )
        make_profile(
            user=self.user_fr,
            date_of_birth='1995-01-01',
            gender='M',
            location='Luxembourg',
            preferred_language='fr',
        )

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_multilingual_content_per_user(self, mock_send):
        """Each user should receive content in their preferred language."""
        newsletter = Newsletter.objects.create(
            subject='English Subject',
            body_html='<p>English body</p>',
            audience='all_users',
        )
        # Set translated fields directly (simulating modeltranslation)
        newsletter.subject_en = 'English Subject'
        newsletter.subject_de = 'Deutscher Betreff'
        newsletter.subject_fr = 'Sujet Francais'
        newsletter.body_html_en = '<p>English body</p>'
        newsletter.body_html_de = '<p>Deutscher Inhalt</p>'
        newsletter.body_html_fr = '<p>Contenu Francais</p>'
        newsletter.save()

        send_newsletter(newsletter)

        # Check that send_domain_email was called 3 times (one per user)
        self.assertEqual(mock_send.call_count, 3)

        # Collect all calls and map by recipient email
        calls_by_email = {}
        for call in mock_send.call_args_list:
            email = call[1]['recipient_list'][0]
            calls_by_email[email] = call[1]

        # English user gets English content
        en_call = calls_by_email['mlen@example.com']
        self.assertEqual(en_call['subject'], 'English Subject')
        self.assertIn('English body', en_call['html_message'])

        # German user gets German content
        de_call = calls_by_email['mlde@example.com']
        self.assertEqual(de_call['subject'], 'Deutscher Betreff')
        self.assertIn('Deutscher Inhalt', de_call['html_message'])

        # French user gets French content
        fr_call = calls_by_email['mlfr@example.com']
        self.assertEqual(fr_call['subject'], 'Sujet Francais')
        self.assertIn('Contenu Francais', fr_call['html_message'])

    @patch('crush_lu.newsletter_service.BATCH_PAUSE_SECONDS', 0)
    @patch('crush_lu.newsletter_service.send_domain_email', return_value=1)
    def test_fallback_to_english_when_translation_missing(self, mock_send):
        """Users whose language has no translation should get English content."""
        newsletter = Newsletter.objects.create(
            subject='English Only Subject',
            body_html='<p>English only content</p>',
            audience='all_users',
            language='de',  # target only German users
        )
        # Only set English content, leave German empty
        newsletter.subject_en = 'English Only Subject'
        newsletter.body_html_en = '<p>English only content</p>'
        # subject_de and body_html_de left as None (fallback to English)
        newsletter.save()

        send_newsletter(newsletter)

        # German user should receive something (fallback to English)
        self.assertEqual(mock_send.call_count, 1)
        call_kwargs = mock_send.call_args[1]
        # The subject will be from fallback (English)
        self.assertIn('English Only Subject', call_kwargs['subject'])
