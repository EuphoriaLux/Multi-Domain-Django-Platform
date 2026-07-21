"""Tests for the extracted WhatsApp send service (hub/whatsapp_service.py).

Covers the service directly, the refactored hub CRM views that delegate to
it, and the campaign WhatsAppAdapter that consumes it.

Run with: pytest hub/tests/test_whatsapp_service.py -v
"""
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from crush_lu.models import Campaign, CampaignRecipient, CrushProfile, EmailPreference
from crush_lu.services.campaigns import CHANNEL_ADAPTERS
from hub.models import WhatsAppMessage
from hub.whatsapp_service import (
    TEMPLATES_CACHE_KEY,
    TemplatesFetchError,
    fetch_approved_templates,
    send_whatsapp_template,
)

User = get_user_model()

meta_test_settings = override_settings(
    META_WHATSAPP_ACCESS_TOKEN='test-token',
    META_PHONE_NUMBER_ID='12345',
    META_WABA_ID='67890',
)


def graph_response(ok=True, status_code=200, body=None):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.content = b'{}'
    resp.json.return_value = body if body is not None else {}
    return resp


SENT_BODY = {'messages': [{'id': 'wamid.TEST123'}]}
META_ERROR_BODY = {'error': {'code': 132001, 'message': 'Template not found'}}
NOT_ON_WHATSAPP_BODY = {'error': {'code': 131026, 'message': 'Not on WhatsApp'}}


@meta_test_settings
class SendWhatsAppTemplateTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin@example.com', email='admin@example.com',
            password='x', is_staff=True,
        )

    def _send(self, **overrides):
        kwargs = {
            'sender': self.admin,
            'recipient': '+352621111111',
            'template_name': 'event_reminder',
            'language': 'en',
            'parameters': {'1': 'Tom'},
        }
        kwargs.update(overrides)
        return send_whatsapp_template(**kwargs)

    def test_successful_send_records_sent_message(self):
        with patch(
            'hub.whatsapp_service.requests.post',
            return_value=graph_response(body=SENT_BODY),
        ) as mock_post:
            message = self._send()

        self.assertEqual(message.status, WhatsAppMessage.Status.SENT)
        self.assertEqual(message.wa_message_id, 'wamid.TEST123')
        self.assertEqual(message.user, self.admin)
        self.assertEqual(message.recipient, '+352621111111')
        statuses = [entry['status'] for entry in message.status_history]
        self.assertEqual(statuses, ['queued', 'sent'])
        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(payload['to'], '352621111111')
        self.assertEqual(payload['template']['name'], 'event_reminder')

    def test_meta_error_records_failed_message(self):
        with patch(
            'hub.whatsapp_service.requests.post',
            return_value=graph_response(ok=False, status_code=400, body=META_ERROR_BODY),
        ):
            message = self._send()

        self.assertEqual(message.status, WhatsAppMessage.Status.FAILED)
        last = message.status_history[-1]
        self.assertEqual(last['error_code'], 132001)
        self.assertEqual(last['error_message'], 'Template not found')

    def test_transport_error_records_failed_message(self):
        with patch(
            'hub.whatsapp_service.requests.post',
            side_effect=requests.ConnectionError(),
        ):
            message = self._send()

        self.assertEqual(message.status, WhatsAppMessage.Status.FAILED)
        self.assertIn(
            'Transport error', message.status_history[-1]['error_message']
        )

    def test_not_on_whatsapp_flags_matching_profile(self):
        user = User.objects.create_user(
            username='member@example.com', email='member@example.com',
            password='x',
        )
        profile = CrushProfile.objects.create(
            user=user,
            date_of_birth='1995-01-01',
            gender='F',
            location='Luxembourg',
            phone_number='+352621111111',
            phone_verified=True,
        )
        with patch(
            'hub.whatsapp_service.requests.post',
            return_value=graph_response(
                ok=False, status_code=400, body=NOT_ON_WHATSAPP_BODY,
            ),
        ):
            self._send()

        profile.refresh_from_db()
        self.assertTrue(profile.not_on_whatsapp)


@meta_test_settings
class FetchApprovedTemplatesTests(TestCase):
    def setUp(self):
        cache.delete(TEMPLATES_CACHE_KEY)

    def test_returns_normalized_items(self):
        body = {'data': [{
            'name': 'event_reminder', 'language': 'en',
            'category': 'MARKETING', 'status': 'APPROVED',
            'components': [{'type': 'BODY', 'text': 'Hi {{1}}'}],
        }]}
        with patch(
            'hub.whatsapp_service.requests.get',
            return_value=graph_response(body=body),
        ):
            items = fetch_approved_templates(use_cache=False)
        self.assertEqual(items[0]['name'], 'event_reminder')
        self.assertEqual(items[0]['status'], 'APPROVED')

    def test_caches_successful_results(self):
        body = {'data': []}
        with patch(
            'hub.whatsapp_service.requests.get',
            return_value=graph_response(body=body),
        ) as mock_get:
            fetch_approved_templates()
            fetch_approved_templates()
        self.assertEqual(mock_get.call_count, 1)

    def test_transport_error_raises(self):
        with patch(
            'hub.whatsapp_service.requests.get',
            side_effect=requests.ConnectionError(),
        ):
            with self.assertRaises(TemplatesFetchError):
                fetch_approved_templates(use_cache=False)

    @override_settings(META_WABA_ID='')
    def test_unconfigured_returns_empty(self):
        self.assertEqual(fetch_approved_templates(), [])


@meta_test_settings
class WhatsAppAdapterSendTests(TestCase):
    """Campaign WhatsApp leg end-to-end through the extracted service."""

    def setUp(self):
        self.coach = User.objects.create_user(
            username='coach@example.com', email='coach@example.com',
            password='x', is_staff=True,
        )
        self.member = User.objects.create_user(
            username='member@example.com', email='member@example.com',
            password='x', first_name='Anna',
        )
        CrushProfile.objects.create(
            user=self.member,
            date_of_birth='1995-01-01',
            gender='F',
            location='Luxembourg',
            is_approved=True,
            phone_number='+352621222333',
            phone_verified=True,
        )
        EmailPreference.objects.update_or_create(
            user=self.member, defaults={'whatsapp_opt_in': True},
        )
        self.campaign = Campaign.objects.create(
            name='WA campaign',
            channels=['whatsapp'],
            audience='all_users',
            status='sending',
            whatsapp_template_name='event_reminder',
            whatsapp_parameters={'1': 'Hi {first_name}'},
            created_by=self.coach,
        )
        self.adapter = CHANNEL_ADAPTERS['whatsapp']

    def test_send_batch_links_message_and_substitutes_tokens(self):
        with patch(
            'hub.whatsapp_service.requests.post',
            return_value=graph_response(body=SENT_BODY),
        ) as mock_post:
            result = self.adapter.send_batch(self.campaign, limit=10)

        self.assertEqual(result.sent, 1)
        self.assertEqual(result.remaining, 0)
        recipient = CampaignRecipient.objects.get(
            campaign=self.campaign, channel='whatsapp', user=self.member,
        )
        self.assertEqual(recipient.status, 'sent')
        self.assertEqual(
            recipient.whatsapp_message.wa_message_id, 'wamid.TEST123'
        )
        self.assertEqual(recipient.whatsapp_message.user, self.coach)
        payload = mock_post.call_args.kwargs['json']
        params = payload['template']['components'][0]['parameters']
        self.assertEqual(params[0]['text'], 'Hi Anna')

    def test_failed_send_is_terminal(self):
        with patch(
            'hub.whatsapp_service.requests.post',
            return_value=graph_response(ok=False, status_code=400, body=META_ERROR_BODY),
        ):
            result = self.adapter.send_batch(self.campaign, limit=10)

        self.assertEqual(result.failed, 1)
        self.assertEqual(result.remaining, 0)
        recipient = CampaignRecipient.objects.get(user=self.member)
        self.assertEqual(recipient.status, 'failed')
        self.assertIn('Template not found', recipient.error_message)

        # A later batch must not retry the failed (paid) template send.
        with patch('hub.whatsapp_service.requests.post') as mock_post:
            result = self.adapter.send_batch(self.campaign, limit=10)
        mock_post.assert_not_called()
        self.assertEqual(result.processed, 0)

    def test_campaign_stats_include_whatsapp_delivery(self):
        with patch(
            'hub.whatsapp_service.requests.post',
            return_value=graph_response(body=SENT_BODY),
        ):
            self.adapter.send_batch(self.campaign, limit=10)

        # Simulate the Meta status webhook advancing the message to 'read'.
        WhatsAppMessage.objects.filter(wa_message_id='wamid.TEST123').update(
            status=WhatsAppMessage.Status.READ,
        )
        stats = self.campaign.stats
        self.assertEqual(stats['whatsapp']['sent'], 1)
        self.assertEqual(stats['whatsapp']['delivered'], 1)
        self.assertEqual(stats['whatsapp']['read'], 1)

    def test_webhook_delivery_failure_reclassifies_stats(self):
        """An accepted send that later fails via webhook counts as failed."""
        with patch(
            'hub.whatsapp_service.requests.post',
            return_value=graph_response(body=SENT_BODY),
        ):
            self.adapter.send_batch(self.campaign, limit=10)

        WhatsAppMessage.objects.filter(wa_message_id='wamid.TEST123').update(
            status=WhatsAppMessage.Status.FAILED,
        )
        stats = self.campaign.stats
        self.assertEqual(stats['whatsapp']['sent'], 0)
        self.assertEqual(stats['whatsapp']['failed'], 1)
        self.assertEqual(stats['totals']['failed'], 1)

    def test_batch_stops_after_cancellation(self):
        """A cancel landing mid-batch stops further (paid) sends."""
        second = User.objects.create_user(
            username='second@example.com', email='second@example.com',
            password='x', first_name='Ben',
        )
        CrushProfile.objects.create(
            user=second,
            date_of_birth='1994-01-01',
            gender='M',
            location='Luxembourg',
            is_approved=True,
            phone_number='+352621444555',
            phone_verified=True,
        )
        EmailPreference.objects.update_or_create(
            user=second, defaults={'whatsapp_opt_in': True},
        )
        self.campaign.status = 'sending'
        self.campaign.save(update_fields=['status'])

        campaign_pk = self.campaign.pk

        def send_then_cancel(*args, **kwargs):
            Campaign.objects.get(pk=campaign_pk).cancel()
            return graph_response(body=SENT_BODY)

        with patch(
            'hub.whatsapp_service.requests.post',
            side_effect=send_then_cancel,
        ) as mock_post:
            result = self.adapter.send_batch(self.campaign, limit=10)

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(result.sent, 1)
        self.assertTrue(result.interrupted)
        self.assertEqual(WhatsAppMessage.objects.count(), 1)
