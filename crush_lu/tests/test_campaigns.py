"""
Multi-channel campaign engine tests.

Covers:
- Per-channel consent/eligibility exclusions (email vs WhatsApp vs push)
- Resumability (processed CampaignRecipient rows excluded from later batches)
- send_newsletter bounded-run finalization (regression for the --limit fix)
- Dispatcher lifecycle (scheduling, heartbeat claim, finalization, cancel)
- create_campaign / estimate_campaign

Run with: pytest crush_lu/tests/test_campaigns.py -v
"""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models import (
    Campaign,
    CampaignRecipient,
    CrushProfile,
    EmailPreference,
    PushSubscription,
    UserDataConsent,
)
from crush_lu.models.newsletter import Newsletter, NewsletterRecipient
from crush_lu.newsletter_service import send_newsletter
from crush_lu.services.campaigns import (
    CHANNEL_ADAPTERS,
    create_campaign,
    dispatch_campaigns,
    estimate_campaign,
)

User = get_user_model()

# VAPID keys only exist in production settings; push sends need them present
# (pywebpush itself is mocked module-wide in the root conftest).
vapid_test_settings = override_settings(
    VAPID_PRIVATE_KEY='test-vapid-private-key',
    VAPID_PUBLIC_KEY='test-vapid-public-key',
    VAPID_ADMIN_EMAIL='test@example.com',
)


def make_user(email, **profile_kwargs):
    user = User.objects.create_user(
        username=email,
        email=email,
        password='testpass123',
        first_name=email.split('@')[0].title(),
    )
    if profile_kwargs.pop('profile', True):
        defaults = {
            'date_of_birth': '1995-01-01',
            'gender': 'M',
            'location': 'Luxembourg',
            'is_approved': True,
        }
        defaults.update(profile_kwargs)
        CrushProfile.objects.create(user=user, **defaults)
    return user


def opt_in_whatsapp(user):
    EmailPreference.objects.update_or_create(
        user=user, defaults={'whatsapp_opt_in': True},
    )


def add_push_subscription(user, enabled=True, endpoint_suffix='1'):
    return PushSubscription.objects.create(
        user=user,
        endpoint=f'https://push.example.com/{user.pk}/{endpoint_suffix}',
        p256dh_key='p256dh-test-key',
        auth_key='auth-test-key',
        enabled=enabled,
    )


class ChannelEligibilityTests(TestCase):
    """Each channel applies its own consent gate on the shared audience."""

    def setUp(self):
        self.plain = make_user('plain@example.com')
        self.whatsapp_ready = make_user(
            'wa@example.com',
            phone_number='+352621111111',
            phone_verified=True,
        )
        opt_in_whatsapp(self.whatsapp_ready)
        self.push_ready = make_user('push@example.com')
        add_push_subscription(self.push_ready)

        self.campaign = Campaign.objects.create(
            name='Eligibility',
            channels=['email', 'whatsapp', 'push'],
            audience='all_users',
        )

    def test_whatsapp_requires_explicit_opt_in(self):
        eligible = CHANNEL_ADAPTERS['whatsapp'].eligible_users(self.campaign)
        self.assertIn(self.whatsapp_ready, eligible)
        # No EmailPreference row at all => not opted in.
        self.assertNotIn(self.plain, eligible)
        self.assertNotIn(self.push_ready, eligible)

    def test_whatsapp_requires_verified_number(self):
        unverified = make_user(
            'unverified@example.com',
            phone_number='+352621222222',
            phone_verified=False,
        )
        opt_in_whatsapp(unverified)
        flagged = make_user(
            'flagged@example.com',
            phone_number='+352621333333',
            phone_verified=True,
            not_on_whatsapp=True,
        )
        opt_in_whatsapp(flagged)

        eligible = CHANNEL_ADAPTERS['whatsapp'].eligible_users(self.campaign)
        self.assertNotIn(unverified, eligible)
        self.assertNotIn(flagged, eligible)

    def test_whatsapp_respects_master_unsubscribe(self):
        EmailPreference.objects.filter(user=self.whatsapp_ready).update(
            unsubscribed_all=True,
        )
        eligible = CHANNEL_ADAPTERS['whatsapp'].eligible_users(self.campaign)
        self.assertNotIn(self.whatsapp_ready, eligible)

    def test_push_requires_enabled_subscription(self):
        disabled = make_user('disabled@example.com')
        add_push_subscription(disabled, enabled=False)

        eligible = CHANNEL_ADAPTERS['push'].eligible_users(self.campaign)
        self.assertIn(self.push_ready, eligible)
        self.assertNotIn(self.plain, eligible)
        self.assertNotIn(disabled, eligible)

    def test_push_counts_multi_device_user_once(self):
        add_push_subscription(self.push_ready, endpoint_suffix='2')
        eligible = CHANNEL_ADAPTERS['push'].eligible_users(self.campaign)
        self.assertEqual(
            eligible.filter(pk=self.push_ready.pk).count(), 1
        )

    def test_banned_user_excluded_everywhere(self):
        UserDataConsent.objects.update_or_create(
            user=self.whatsapp_ready, defaults={'crushlu_banned': True},
        )
        add_push_subscription(self.whatsapp_ready)
        for channel in ('whatsapp', 'push'):
            eligible = CHANNEL_ADAPTERS[channel].eligible_users(self.campaign)
            self.assertNotIn(self.whatsapp_ready, eligible, channel)

    def test_opted_out_of_email_still_reachable_on_push(self):
        """Channel gates are independent: email opt-out ≠ push opt-out."""
        EmailPreference.objects.update_or_create(
            user=self.push_ready, defaults={'email_newsletter': False},
        )
        campaign = create_campaign(
            name='Independent gates',
            channels=['email', 'push'],
            audience='all_users',
        )
        email_eligible = CHANNEL_ADAPTERS['email'].eligible_users(campaign)
        push_eligible = CHANNEL_ADAPTERS['push'].eligible_users(campaign)
        self.assertNotIn(self.push_ready, email_eligible)
        self.assertIn(self.push_ready, push_eligible)


@vapid_test_settings
class ResumabilityTests(TestCase):
    def setUp(self):
        self.users = [
            make_user(f'user{i}@example.com') for i in range(3)
        ]
        for user in self.users:
            add_push_subscription(user)
        self.campaign = Campaign.objects.create(
            name='Resume', channels=['push'], audience='all_users',
            status='sending', started_at=timezone.now(),
        )

    def test_processed_recipients_excluded(self):
        # 'pending' is the durable pre-send claim: a worker that died after
        # Meta accepted must not cause a second paid send.
        for status in ('pending', 'sent', 'failed', 'skipped'):
            CampaignRecipient.objects.all().delete()
            CampaignRecipient.objects.create(
                campaign=self.campaign,
                channel='push',
                user=self.users[0],
                status=status,
            )
            eligible = CHANNEL_ADAPTERS['push'].eligible_users(self.campaign)
            self.assertNotIn(self.users[0], eligible, status)
            self.assertEqual(eligible.count(), 2, status)

    def test_push_batch_respects_limit_and_resumes(self):
        adapter = CHANNEL_ADAPTERS['push']
        result = adapter.send_batch(self.campaign, limit=2)
        self.assertEqual(result.sent, 2)
        self.assertEqual(result.remaining, 1)
        self.assertFalse(result.complete)

        result = adapter.send_batch(self.campaign, limit=2)
        self.assertEqual(result.sent, 1)
        self.assertEqual(result.remaining, 0)
        self.assertTrue(result.complete)
        self.assertEqual(
            CampaignRecipient.objects.filter(
                campaign=self.campaign, channel='push', status='sent'
            ).count(),
            3,
        )


class SendNewsletterBoundedRunTests(TestCase):
    """Regression tests for the --limit finalization fix."""

    def setUp(self):
        self.users = [make_user(f'nl{i}@example.com') for i in range(3)]
        self.newsletter = Newsletter.objects.create(
            subject='Hello',
            body_html='<p>Hi</p>',
            audience='all_users',
        )

    def test_limited_run_stays_sending_when_recipients_remain(self):
        result = send_newsletter(self.newsletter, limit=2)
        self.newsletter.refresh_from_db()
        self.assertEqual(result['sent'], 2)
        self.assertFalse(result['complete'])
        self.assertEqual(result['remaining'], 1)
        self.assertEqual(self.newsletter.status, 'sending')
        self.assertIsNone(self.newsletter.sent_at)

    def test_limited_runs_converge_to_sent(self):
        send_newsletter(self.newsletter, limit=2)
        result = send_newsletter(self.newsletter, limit=2)
        self.newsletter.refresh_from_db()
        self.assertEqual(result['sent'], 1)
        self.assertTrue(result['complete'])
        self.assertEqual(self.newsletter.status, 'sent')
        self.assertEqual(self.newsletter.total_sent, 3)
        self.assertEqual(len(mail.outbox), 3)

    def test_unlimited_run_finalizes_like_before(self):
        result = send_newsletter(self.newsletter)
        self.newsletter.refresh_from_db()
        self.assertEqual(result['sent'], 3)
        self.assertTrue(result['complete'])
        self.assertEqual(self.newsletter.status, 'sent')

    def test_limited_run_does_not_retry_failed_recipients(self):
        NewsletterRecipient.objects.create(
            newsletter=self.newsletter,
            user=self.users[0],
            email=self.users[0].email,
            status='failed',
            error_message='bounced',
        )
        result = send_newsletter(self.newsletter, limit=10)
        self.newsletter.refresh_from_db()
        # The two non-failed users are sent; the failed one is not retried
        # and does not block completion — but it must still be reflected in
        # the final status (total_failed=1 and 'sent' would contradict).
        self.assertEqual(result['sent'], 2)
        self.assertTrue(result['complete'])
        self.assertEqual(self.newsletter.status, 'failed')
        self.assertEqual(self.newsletter.total_failed, 1)
        self.assertEqual(
            NewsletterRecipient.objects.get(user=self.users[0]).status,
            'failed',
        )

    def test_bounded_runs_finalize_from_persisted_failures(self):
        """A failure in an earlier tick must surface in the final status."""
        real_send = 'crush_lu.newsletter_service._send_newsletter_to_user'
        calls = {'n': 0}

        def flaky(newsletter, user, link_rewriter=None):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('bounce')

        with patch(real_send, side_effect=flaky):
            send_newsletter(self.newsletter, limit=2)
        result = send_newsletter(self.newsletter, limit=2)
        self.newsletter.refresh_from_db()
        self.assertTrue(result['complete'])
        self.assertEqual(self.newsletter.total_failed, 1)
        # 'sent' would contradict total_failed=1 on the same record.
        self.assertEqual(self.newsletter.status, 'failed')

    def test_unlimited_run_still_retries_failed_recipients(self):
        NewsletterRecipient.objects.create(
            newsletter=self.newsletter,
            user=self.users[0],
            email=self.users[0].email,
            status='failed',
            error_message='bounced',
        )
        result = send_newsletter(self.newsletter)
        self.assertEqual(result['sent'], 3)
        self.assertEqual(
            NewsletterRecipient.objects.get(user=self.users[0]).status,
            'sent',
        )


@vapid_test_settings
class DispatcherTests(TestCase):
    def setUp(self):
        self.users = [make_user(f'd{i}@example.com') for i in range(2)]

    def _email_campaign(self, **overrides):
        campaign = create_campaign(
            name=overrides.pop('name', 'Dispatch test'),
            channels=['email'],
            audience='all_users',
            email_content={'subject': 'Hi', 'body_html': '<p>Yo</p>'},
            **overrides,
        )
        return campaign

    def test_future_scheduled_campaign_not_dispatched(self):
        campaign = self._email_campaign(
            scheduled_at=timezone.now() + timedelta(hours=1),
        )
        summary = dispatch_campaigns()
        campaign.refresh_from_db()
        self.assertEqual(summary['promoted'], 0)
        self.assertEqual(campaign.status, 'scheduled')
        self.assertEqual(len(mail.outbox), 0)

    def test_due_campaign_promoted_sent_and_finalized(self):
        campaign = self._email_campaign(
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        summary = dispatch_campaigns()
        campaign.refresh_from_db()
        self.assertEqual(summary['promoted'], 1)
        self.assertEqual(campaign.status, 'sent')
        self.assertIsNotNone(campaign.completed_at)
        self.assertIsNone(campaign.dispatch_heartbeat_at)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(campaign.stats['email']['sent'], 2)

    def test_multi_channel_campaign_dispatches_all_channels(self):
        for user in self.users:
            add_push_subscription(user)
        campaign = create_campaign(
            name='Email and push',
            channels=['email', 'push'],
            audience='all_users',
            email_content={'subject': 'Hi', 'body_html': '<p>Yo</p>'},
            push={'title': 'Hello', 'body': 'World'},
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        dispatch_campaigns()
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, 'sent')
        self.assertEqual(campaign.stats['email']['sent'], 2)
        self.assertEqual(campaign.stats['push']['sent'], 2)
        self.assertEqual(campaign.stats['totals']['sent'], 4)

    def test_fresh_heartbeat_skips_campaign(self):
        campaign = self._email_campaign()
        Campaign.objects.filter(pk=campaign.pk).update(
            status='sending',
            started_at=timezone.now(),
            dispatch_heartbeat_at=timezone.now(),
        )
        summary = dispatch_campaigns()
        campaign.refresh_from_db()
        self.assertEqual(summary['campaigns'], [])
        self.assertEqual(campaign.status, 'sending')
        self.assertEqual(len(mail.outbox), 0)

    def test_stale_heartbeat_is_reclaimed(self):
        campaign = self._email_campaign()
        Campaign.objects.filter(pk=campaign.pk).update(
            status='sending',
            started_at=timezone.now(),
            dispatch_heartbeat_at=timezone.now() - timedelta(minutes=30),
        )
        dispatch_campaigns()
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, 'sent')
        self.assertEqual(len(mail.outbox), 2)

    def test_partial_status_when_some_sends_fail(self):
        campaign = self._email_campaign(
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        real_send = 'crush_lu.newsletter_service._send_newsletter_to_user'
        calls = {'n': 0}

        def flaky(newsletter, user, link_rewriter=None):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('SMTP exploded')

        with patch(real_send, side_effect=flaky):
            dispatch_campaigns()
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, 'partial')
        stats = campaign.stats
        self.assertEqual(stats['email']['sent'], 1)
        self.assertEqual(stats['email']['failed'], 1)

    def test_campaign_id_scopes_the_whole_tick(self):
        """--campaign-id must never promote or send other due campaigns."""
        target = self._email_campaign(
            name='Target', scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        bystander = self._email_campaign(
            name='Bystander', scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        summary = dispatch_campaigns(campaign_id=target.pk)
        target.refresh_from_db()
        bystander.refresh_from_db()
        self.assertEqual(summary['promoted'], 1)
        self.assertEqual(target.status, 'sent')
        self.assertEqual(bystander.status, 'scheduled')
        self.assertEqual([c['id'] for c in summary['campaigns']], [target.pk])

    def test_cancellation_during_send_is_not_overwritten(self):
        """A cancel landing mid-batch must survive finalization."""
        campaign = self._email_campaign(
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        from crush_lu.services.campaigns import CHANNEL_ADAPTERS, BatchResult

        def cancel_then_complete(c, limit, deadline=None, stdout=None):
            Campaign.objects.get(pk=c.pk).cancel()
            return BatchResult(sent=0, remaining=0)

        with patch.object(
            CHANNEL_ADAPTERS['email'], 'send_batch',
            side_effect=cancel_then_complete,
        ):
            dispatch_campaigns()
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, 'cancelled')

    def test_cancellation_stops_email_batch_mid_flight(self):
        """A cancel during the email loop stops remaining sends."""
        campaign = self._email_campaign(
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        real_send = 'crush_lu.newsletter_service._send_newsletter_to_user'

        def cancel_after_first(newsletter, user, link_rewriter=None):
            Campaign.objects.get(pk=campaign.pk).cancel()

        with patch(real_send, side_effect=cancel_after_first):
            dispatch_campaigns()
        campaign.refresh_from_db()
        newsletter = campaign.email_newsletter
        newsletter.refresh_from_db()
        self.assertEqual(campaign.status, 'cancelled')
        # Only the first of the two recipients was processed; the newsletter
        # is left resumable, not finalized.
        self.assertEqual(
            NewsletterRecipient.objects.filter(
                newsletter=newsletter, status='sent',
            ).count(),
            1,
        )
        self.assertEqual(newsletter.status, 'sending')

    def test_cancelled_campaign_not_dispatched(self):
        campaign = self._email_campaign(
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertTrue(campaign.cancel())
        summary = dispatch_campaigns()
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, 'cancelled')
        self.assertEqual(summary['promoted'], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_cancel_only_from_active_states(self):
        campaign = self._email_campaign()
        Campaign.objects.filter(pk=campaign.pk).update(status='sent')
        campaign.refresh_from_db()
        self.assertFalse(campaign.cancel())
        self.assertEqual(campaign.status, 'sent')


class PushConfigGuardTests(TestCase):
    """No VAPID override here on purpose — dev settings lack the keys."""

    def test_missing_vapid_defers_batch_instead_of_skipping(self):
        user = make_user('vapidless@example.com')
        add_push_subscription(user)
        campaign = Campaign.objects.create(
            name='No VAPID', channels=['push'], audience='all_users',
            status='sending', started_at=timezone.now(),
        )
        result = CHANNEL_ADAPTERS['push'].send_batch(campaign, limit=10)
        self.assertTrue(result.interrupted)
        self.assertEqual(result.remaining, 1)
        # Nobody recorded as skipped/sent — the batch waits for a config fix.
        self.assertEqual(CampaignRecipient.objects.count(), 0)


class CreateAndEstimateTests(TestCase):
    def setUp(self):
        self.user = make_user('create@example.com')
        add_push_subscription(self.user)

    def test_create_campaign_mirrors_targeting_to_newsletter(self):
        campaign = create_campaign(
            name='Mirrored',
            channels=['email', 'push'],
            audience='approved_profiles',
            language='en',
            email_content={'subject': 'S', 'body_html': '<p>B</p>'},
            push={'title': 'T', 'body': 'B'},
        )
        newsletter = campaign.email_newsletter
        self.assertEqual(newsletter.audience, 'approved_profiles')
        self.assertEqual(newsletter.language, 'en')
        self.assertEqual(newsletter.campaign, campaign)
        self.assertEqual(campaign.status, 'draft')
        self.assertTrue(campaign.slug)
        self.assertIn('captured_at', campaign.audience_snapshot)
        self.assertEqual(campaign.audience_snapshot['email'], 1)
        self.assertEqual(campaign.audience_snapshot['push'], 1)

    def test_create_campaign_without_email_skips_newsletter(self):
        campaign = create_campaign(
            name='Push only',
            channels=['push'],
            audience='all_users',
            push={'title': 'T', 'body': 'B'},
        )
        self.assertIsNone(getattr(campaign, 'email_newsletter', None))

    def test_create_campaign_rejects_unknown_channel(self):
        with self.assertRaises(ValueError):
            create_campaign(
                name='Nope', channels=['carrier-pigeon'], audience='all_users',
            )

    def test_slugs_are_unique(self):
        first = create_campaign(
            name='Same Name', channels=['push'], audience='all_users',
        )
        second = create_campaign(
            name='Same Name', channels=['push'], audience='all_users',
        )
        self.assertNotEqual(first.slug, second.slug)

    def test_estimate_counts_and_reach(self):
        other = make_user('estimate@example.com')
        estimate = estimate_campaign(
            audience='all_users', channels=['email', 'push'],
        )
        self.assertEqual(estimate['email'], 2)
        self.assertEqual(estimate['push'], 1)
        # Reach is the union of channel audiences, not their sum.
        self.assertEqual(estimate['reach'], 2)


class DispatchCommandTests(TestCase):
    def test_dry_run_lists_eligible_counts(self):
        make_user('cmd@example.com')
        create_campaign(
            name='Command test',
            channels=['email'],
            audience='all_users',
            email_content={'subject': 'S', 'body_html': '<p>B</p>'},
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        out = StringIO()
        call_command('dispatch_campaigns', '--dry-run', stdout=out)
        output = out.getvalue()
        self.assertIn('Command test', output)
        self.assertIn('email: 1 eligible recipients', output)
        self.assertEqual(len(mail.outbox), 0)

    def test_tick_sends_due_campaign(self):
        make_user('cmd2@example.com')
        campaign = create_campaign(
            name='Command send',
            channels=['email'],
            audience='all_users',
            email_content={'subject': 'S', 'body_html': '<p>B</p>'},
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        out = StringIO()
        call_command('dispatch_campaigns', stdout=out)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, 'sent')
        self.assertEqual(len(mail.outbox), 1)
