"""Tests for the campaign dispatch admin API endpoint.

Covers Bearer auth, the CAMPAIGN_DISPATCH_ENABLED gate, method guard, and
that an enabled tick actually dispatches a due campaign.

Run with: pytest crush_lu/tests/test_api_admin_campaigns.py -v
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from crush_lu.models import Campaign, CrushProfile
from crush_lu.services.campaigns import create_campaign

User = get_user_model()

API_KEY = "test-admin-api-key"
DISPATCH_URL = "/api/admin/campaigns/dispatch/"
CRUSH_URLS = {"ROOT_URLCONF": "azureproject.urls_crush", "ADMIN_API_KEY": API_KEY}


def make_member(email):
    user = User.objects.create_user(
        username=email, email=email, password='x', first_name='Member',
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth='1995-01-01',
        gender='M',
        location='Luxembourg',
        is_approved=True,
    )
    return user


@override_settings(**CRUSH_URLS)
class DispatchEndpointAuthTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST='crush.lu')

    def test_missing_bearer_unauthorized(self):
        resp = self.client.post(DISPATCH_URL)
        self.assertEqual(resp.status_code, 401)

    def test_wrong_bearer_unauthorized(self):
        resp = self.client.post(
            DISPATCH_URL, HTTP_AUTHORIZATION="Bearer not-the-key",
        )
        self.assertEqual(resp.status_code, 401)

    def test_get_method_not_allowed(self):
        resp = self.client.get(
            DISPATCH_URL, HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
        )
        self.assertEqual(resp.status_code, 405)

    def test_flag_off_returns_skipped(self):
        resp = self.client.post(
            DISPATCH_URL, HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["skipped"])


@override_settings(CAMPAIGN_DISPATCH_ENABLED=True, **CRUSH_URLS)
class DispatchEndpointTickTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST='crush.lu')

    def test_tick_dispatches_due_campaign(self):
        make_member('due@example.com')
        campaign = create_campaign(
            name='Endpoint tick',
            channels=['email'],
            audience='all_users',
            email_content={'subject_en': 'Hi', 'body_html_en': '<p>Yo</p>'},
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        resp = self.client.post(
            DISPATCH_URL, HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
        )
        self.assertEqual(resp.status_code, 202)
        payload = resp.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["promoted"], 1)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, 'sent')
        self.assertEqual(len(mail.outbox), 1)

    def test_tick_with_nothing_due_is_a_noop(self):
        resp = self.client.post(
            DISPATCH_URL, HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
        )
        self.assertEqual(resp.status_code, 202)
        payload = resp.json()
        self.assertEqual(payload["promoted"], 0)
        self.assertEqual(payload["campaigns"], [])
        self.assertEqual(Campaign.objects.count(), 0)
