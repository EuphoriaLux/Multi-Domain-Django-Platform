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
from django.utils import timezone

from crush_lu.models import CrushProfile, EmailPreference
from crush_lu.models.newsletter import Newsletter, NewsletterRecipient
from crush_lu.newsletter_service import get_newsletter_recipients, send_newsletter

User = get_user_model()


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
        CrushProfile.objects.create(
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
        CrushProfile.objects.create(
            user=self.user_approved,
            date_of_birth='1995-01-01',
            gender='F',
            location='Luxembourg',
            is_approved=True,
        )

    def test_all_users_audience(self):
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Hi</p>', audience='all_users',
        )
        recipients = get_newsletter_recipients(newsletter)
        self.assertEqual(recipients.count(), 3)

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
        self.assertEqual(recipients.count(), 2)

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
        self.assertEqual(recipients.count(), 3)


class NewsletterSendTests(TestCase):
    """Test the send_newsletter function."""

    def setUp(self):
        self.user1 = User.objects.create_user(
            username='user1@example.com',
            email='user1@example.com',
            password='testpass123',
            first_name='Alice',
        )
        self.user2 = User.objects.create_user(
            username='user2@example.com',
            email='user2@example.com',
            password='testpass123',
            first_name='Bob',
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
        # Create 3 users
        User.objects.create_user(
            username='cmd2@example.com', email='cmd2@example.com', password='pass',
        )
        User.objects.create_user(
            username='cmd3@example.com', email='cmd3@example.com', password='pass',
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
        self.assertIn('Draft', str(newsletter))

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
        CrushProfile.objects.create(
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
        CrushProfile.objects.create(
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
        CrushProfile.objects.create(
            user=self.user_fr,
            date_of_birth='1995-01-01',
            gender='M',
            location='Luxembourg',
            preferred_language='fr',
        )

    def test_language_all_includes_everyone(self):
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Hi</p>',
            audience='all_users', language='all',
        )
        recipients = get_newsletter_recipients(newsletter)
        self.assertEqual(recipients.count(), 4)

    def test_language_en_includes_english_and_no_profile(self):
        newsletter = Newsletter.objects.create(
            subject='Test', body_html='<p>Hi</p>',
            audience='all_users', language='en',
        )
        recipients = get_newsletter_recipients(newsletter)
        self.assertEqual(recipients.count(), 2)
        self.assertIn(self.user_en, recipients)
        self.assertIn(self.user_no_profile, recipients)

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

    def test_segment_audience_requires_segment_key(self):
        from crush_lu.admin.newsletter import NewsletterAdminForm

        form = NewsletterAdminForm(data={
            'subject': 'Test',
            'body_html': '<p>Hi</p>',
            'audience': 'segment',
            'segment_key': '',
            'language': 'all',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('segment_key', form.errors)

    def test_non_segment_audience_clears_segment_key(self):
        from crush_lu.admin.newsletter import NewsletterAdminForm

        form = NewsletterAdminForm(data={
            'subject': 'Test',
            'body_html': '<p>Hi</p>',
            'audience': 'all_users',
            'segment_key': 'some_key',
            'language': 'all',
        })
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['segment_key'], '')

    def test_valid_form_with_all_users(self):
        from crush_lu.admin.newsletter import NewsletterAdminForm

        form = NewsletterAdminForm(data={
            'subject': 'Test',
            'body_html': '<p>Hi</p>',
            'audience': 'all_users',
            'segment_key': '',
            'language': 'all',
        })
        self.assertTrue(form.is_valid())
