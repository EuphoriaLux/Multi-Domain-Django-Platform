"""Campaign dashboard view tests (Coach Panel).

Covers access control (anonymous / regular user / active coach / superuser)
across the page, HTMX, and chart JSON endpoints, plus the composer create
flow, cancellation, estimates and chart payload shapes.

Run with: pytest crush_lu/tests/test_campaign_dashboard.py -v
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import Campaign, CrushCoach, CrushProfile
from crush_lu.services.campaigns import create_campaign

User = get_user_model()

crush_urlconf = override_settings(ROOT_URLCONF='azureproject.urls_crush')


def urls_under_test(campaign_id):
    return {
        'dashboard': reverse('campaign_dashboard'),
        'composer': reverse('campaign_new'),
        'detail': reverse('campaign_detail', kwargs={'campaign_id': campaign_id}),
        'status': reverse('campaign_status_partial', kwargs={'campaign_id': campaign_id}),
        'overview_api': reverse('campaign_overview_api'),
        'clicks_api': reverse('campaign_clicks_api', kwargs={'campaign_id': campaign_id}),
        'reminders_api': reverse('reminders_funnel_api'),
    }


@crush_urlconf
class CampaignDashboardAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.regular = User.objects.create_user(
            username='user@example.com', email='user@example.com', password='x',
        )
        cls.coach_user = User.objects.create_user(
            username='coach@example.com', email='coach@example.com', password='x',
        )
        CrushCoach.objects.create(user=cls.coach_user, is_active=True)
        cls.inactive_coach_user = User.objects.create_user(
            username='inactive@example.com', email='inactive@example.com', password='x',
        )
        CrushCoach.objects.create(user=cls.inactive_coach_user, is_active=False)
        cls.superuser = User.objects.create_superuser(
            username='super@example.com', email='super@example.com', password='x',
        )
        cls.campaign = Campaign.objects.create(
            name='Access test', channels=['email'], audience='all_users',
        )

    def setUp(self):
        self.client = Client(HTTP_HOST='crush.lu')

    def test_anonymous_is_denied(self):
        for name, url in urls_under_test(self.campaign.pk).items():
            response = self.client.get(url)
            # @login_required redirects to login before the JSON 401 check.
            self.assertIn(response.status_code, (301, 302), name)

    def test_regular_user_is_denied(self):
        self.client.force_login(self.regular)
        for name, url in urls_under_test(self.campaign.pk).items():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, name)

    def test_inactive_coach_is_denied(self):
        self.client.force_login(self.inactive_coach_user)
        response = self.client.get(reverse('campaign_dashboard'))
        self.assertEqual(response.status_code, 403)

    def test_active_coach_has_access(self):
        self.client.force_login(self.coach_user)
        for name, url in urls_under_test(self.campaign.pk).items():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, name)

    def test_superuser_has_access(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('campaign_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Campaign Dashboard')

    def test_post_endpoints_denied_for_regular_user(self):
        self.client.force_login(self.regular)
        for url_name in ('campaign_create', 'campaign_estimate', 'campaign_preview'):
            response = self.client.post(reverse(url_name), {})
            self.assertEqual(response.status_code, 403, url_name)
        response = self.client.post(
            reverse('campaign_cancel', kwargs={'campaign_id': self.campaign.pk}),
        )
        self.assertEqual(response.status_code, 403)


@crush_urlconf
class CampaignCreateFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            username='super@example.com', email='super@example.com', password='x',
        )
        member = User.objects.create_user(
            username='member@example.com', email='member@example.com', password='x',
        )
        CrushProfile.objects.create(
            user=member,
            date_of_birth='1995-01-01',
            gender='F',
            location='Luxembourg',
            is_approved=True,
        )

    def setUp(self):
        self.client = Client(HTTP_HOST='crush.lu')
        self.client.force_login(self.superuser)

    def _base_form(self, **overrides):
        form = {
            'name': 'Composer campaign',
            'audience': 'all_users',
            'segment_key': '',
            'language': 'all',
            'channels': ['email'],
            'email_subject': 'Hello!',
            'email_body_html': '<p>Come to the event</p>',
            'send_mode': 'draft',
        }
        form.update(overrides)
        return form

    def test_create_draft_campaign(self):
        response = self.client.post(
            reverse('campaign_create'), self._base_form(),
        )
        campaign = Campaign.objects.get(name='Composer campaign')
        self.assertRedirects(
            response,
            reverse('campaign_detail', kwargs={'campaign_id': campaign.pk}),
        )
        self.assertEqual(campaign.status, 'draft')
        self.assertEqual(campaign.created_by, self.superuser)
        newsletter = campaign.email_newsletter
        self.assertEqual(newsletter.subject_en, 'Hello!')
        self.assertEqual(newsletter.audience, 'all_users')

    def test_create_send_now_schedules_immediately(self):
        response = self.client.post(
            reverse('campaign_create'), self._base_form(send_mode='now'),
        )
        campaign = Campaign.objects.get(name='Composer campaign')
        self.assertEqual(campaign.status, 'scheduled')
        self.assertLessEqual(campaign.scheduled_at, timezone.now())

    def test_create_scheduled_campaign(self):
        future = (timezone.now() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        self.client.post(
            reverse('campaign_create'),
            self._base_form(send_mode='schedule', scheduled_at=future),
        )
        campaign = Campaign.objects.get(name='Composer campaign')
        self.assertEqual(campaign.status, 'scheduled')
        self.assertGreater(campaign.scheduled_at, timezone.now())

    def test_past_schedule_rejected(self):
        past = (timezone.now() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        response = self.client.post(
            reverse('campaign_create'),
            self._base_form(send_mode='schedule', scheduled_at=past),
        )
        self.assertRedirects(response, reverse('campaign_new'))
        self.assertFalse(Campaign.objects.exists())

    def test_missing_email_content_rejected(self):
        response = self.client.post(
            reverse('campaign_create'),
            self._base_form(email_subject=''),
        )
        self.assertRedirects(response, reverse('campaign_new'))
        self.assertFalse(Campaign.objects.exists())

    def test_segment_audience_requires_segment_key(self):
        response = self.client.post(
            reverse('campaign_create'),
            self._base_form(audience='segment', segment_key=''),
        )
        self.assertRedirects(response, reverse('campaign_new'))
        self.assertFalse(Campaign.objects.exists())

    def test_estimate_endpoint_returns_counts(self):
        response = self.client.post(reverse('campaign_estimate'), {
            'audience': 'all_users',
            'segment_key': '',
            'language': 'all',
            'channels': ['email'],
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Email recipients')
        # The single profiled member is eligible.
        self.assertContains(response, '<h3>1</h3>', html=True)

    def test_composer_only_offers_approved_whatsapp_templates(self):
        from unittest.mock import patch

        templates = [
            {'name': 'event_reminder', 'language': 'en',
             'category': 'MARKETING', 'status': 'APPROVED', 'components': []},
            {'name': 'unfinished_promo', 'language': 'en',
             'category': 'MARKETING', 'status': 'PENDING', 'components': []},
        ]
        with patch(
            'crush_lu.admin.campaign_dashboard.fetch_approved_templates',
            return_value=templates,
        ):
            response = self.client.get(reverse('campaign_new'))
        self.assertContains(response, 'event_reminder')
        self.assertNotContains(response, 'unfinished_promo')

    def test_preview_endpoint_renders_email(self):
        response = self.client.post(reverse('campaign_preview'), {
            'channels': ['email', 'push'],
            'email_subject': 'Preview subject',
            'email_body_html': '<p>Preview body</p>',
            'push_title': 'Push title',
            'push_body': 'Push body',
            'push_url': '/events/',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Preview subject')
        self.assertContains(response, 'Push title')

    def test_cancel_transitions_campaign(self):
        campaign = create_campaign(
            name='Cancel me', channels=['email'], audience='all_users',
            email_content={'subject_en': 'S', 'body_html_en': 'B'},
            scheduled_at=timezone.now() + timedelta(hours=1),
        )
        response = self.client.post(
            reverse('campaign_cancel', kwargs={'campaign_id': campaign.pk}),
        )
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, 'cancelled')
        self.assertRedirects(
            response,
            reverse('campaign_detail', kwargs={'campaign_id': campaign.pk}),
        )


@crush_urlconf
class CampaignChartApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            username='super@example.com', email='super@example.com', password='x',
        )
        cls.campaign = Campaign.objects.create(
            name='Charts', channels=['email'], audience='all_users',
        )

    def setUp(self):
        self.client = Client(HTTP_HOST='crush.lu')
        self.client.force_login(self.superuser)

    def test_overview_api_shape(self):
        response = self.client.get(reverse('campaign_overview_api'))
        payload = response.json()
        self.assertIn('labels', payload)
        self.assertEqual(len(payload['datasets']), 3)
        self.assertEqual(
            [d['label'] for d in payload['datasets']],
            ['Email', 'Whatsapp', 'Push'],
        )
        self.assertIn('summary', payload)

    def test_clicks_api_shape(self):
        response = self.client.get(
            reverse('campaign_clicks_api', kwargs={'campaign_id': self.campaign.pk}),
        )
        payload = response.json()
        self.assertEqual(payload['labels'], [])
        self.assertEqual(payload['summary']['total'], 0)

    def test_reminders_api_shape(self):
        response = self.client.get(reverse('reminders_funnel_api'))
        payload = response.json()
        self.assertEqual(payload['labels'], ['24h', '72h', '7d'])
        self.assertEqual(len(payload['datasets']), 2)
