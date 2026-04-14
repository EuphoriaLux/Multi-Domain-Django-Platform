import json
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
import requests
from allauth.socialaccount.models import SocialAccount, SocialApp
from django.conf import settings
from .models import EntrepreneurProfile, Industry
from .linkedin_adapter import LinkedInOAuth2Adapter

class EntrepreneurProfileTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='12345')
        self.industry = Industry.objects.create(name='Tech')
        self.profile = EntrepreneurProfile.objects.create(
            user=self.user,
            bio='Test bio',
            company='Test Company',
            industry=self.industry,
            location='Test City'
        )

    def test_profile_creation(self):
        self.assertEqual(self.profile.user.username, 'testuser')
        self.assertEqual(self.profile.bio, 'Test bio')

class ViewsTestCase(TestCase):
    """
    Test views for Entreprinder app.

    Note: We use HTTP_HOST='entreprinder.lu' to ensure the DomainURLRoutingMiddleware
    routes requests to the correct URL configuration (urls_entreprinder.py which
    includes entreprinder URLs). Without this, 'testserver' falls back to
    default URLs with different routing behavior.

    Important: We use direct URLs (/, /profile/) instead of reverse() because:
    - reverse() uses ROOT_URLCONF (azureproject.urls) which has i18n_patterns
    - urls_entreprinder.py does NOT use i18n_patterns, so URLs are at / not /en/
    - Using reverse() would give us /en/ which returns 404 on entreprinder.lu
    """
    @classmethod
    def setUpTestData(cls):
        """Set up data needed by all tests - runs once per TestCase."""
        # Create/update Site for the tests - ensure domain matches HTTP_HOST
        # Django creates Site id=1 with 'example.com' by default in tests
        cls.site, created = Site.objects.update_or_create(
            id=1,
            defaults={'domain': 'entreprinder.lu', 'name': 'Entreprinder'}
        )

        # Create LinkedIn OpenID Connect SocialApp (required by landing page template)
        # The template uses {% provider_login_url 'openid_connect' %}
        cls.linkedin_app = SocialApp.objects.create(
            provider='openid_connect',
            provider_id='linkedin',
            name='LinkedIn',
            client_id='test-linkedin-id',
            secret='test-linkedin-secret',
            settings={'server_url': 'https://www.linkedin.com/oauth'}
        )
        cls.linkedin_app.sites.add(cls.site)

    def setUp(self):
        self.client = Client(HTTP_HOST='entreprinder.lu')
        self.user = User.objects.create_user(username='testuser', password='12345')
        self.industry = Industry.objects.create(name='Tech')
        self.profile = EntrepreneurProfile.objects.create(
            user=self.user,
            bio='Test bio',
            company='Test Company',
            industry=self.industry,
            location='Test City'
        )

    def test_home_view(self):
        """
        Test that the home view returns a successful response.

        Note: The base template includes {% provider_login_url 'openid_connect' %}
        which requires a SocialApp. We create a dummy app for the test site.
        """
        # Use direct URL - urls_powerup.py has home at '/' (no i18n prefix)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'entreprinder/landing_page.html')

    def test_profile_view(self):
        self.client.login(username='testuser', password='12345')
        # Use direct URL - urls_powerup.py has profile at '/profile/' (no i18n prefix)
        response = self.client.get('/profile/')
        self.assertEqual(response.status_code, 200)
        # Template is in entreprinder/ subdirectory
        self.assertTemplateUsed(response, 'entreprinder/profile.html')

class LinkedInOAuthFlowTests(TestCase):
    """Exercise LinkedIn OAuth across all hosted domains."""

    @classmethod
    def setUpTestData(cls):
        cls.hosts = ['entreprinder.lu', 'crush.lu', 'vinsdelux.com']
        cls.provider_slug = 'linkedin_oauth2'
        cls.login_path = f'/accounts/{cls.provider_slug}/login/'
        cls.callback_path = f'/accounts/{cls.provider_slug}/login/callback/'
        cls.social_apps = {}

        # First, update the default site (id=1) to use the first host
        # This ensures compatibility with other tests that rely on Site id=1
        first_host = cls.hosts[0]
        Site.objects.update_or_create(
            id=1,
            defaults={'domain': first_host, 'name': first_host}
        )

        for host in cls.hosts:
            if host == first_host:
                site = Site.objects.get(id=1)
            else:
                site, _ = Site.objects.get_or_create(domain=host, defaults={'name': host})
            app = SocialApp.objects.create(
                provider=cls.provider_slug,
                name=f'LinkedIn ({host})',
                client_id=f'{host}-client-id',
                secret='dummy-secret',
            )
            app.sites.add(site)
            cls.social_apps[host] = app

    def _json_response(self, url, payload, status=200):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(payload).encode()
        response.headers['Content-Type'] = 'application/json'
        response.url = url
        return response

    def test_login_redirects_use_domain_specific_social_app(self):
        for host in self.hosts:
            with self.subTest(host=host):
                client = Client(HTTP_HOST=host)
                response = client.get(self.login_path)

                self.assertEqual(response.status_code, 302)
                parsed = urlparse(response['Location'])
                self.assertEqual(
                    f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
                    LinkedInOAuth2Adapter.authorize_url,
                )

                query = parse_qs(parsed.query)
                self.assertEqual(query['client_id'][0], self.social_apps[host].client_id)
                self.assertTrue(query.get('state', [''])[0])

                redirect_uri = query['redirect_uri'][0]
                self.assertTrue(
                    redirect_uri.startswith(
                        f"{settings.ACCOUNT_DEFAULT_HTTP_PROTOCOL}://{host}"
                    )
                )
                self.assertIn(self.callback_path, redirect_uri)

    def test_callback_exchanges_code_and_logs_user_in(self):
        host = 'entreprinder.lu'
        client = Client(HTTP_HOST=host)

        start_response = client.get(self.login_path)
        state = parse_qs(urlparse(start_response['Location']).query)['state'][0]

        token_payload = {'access_token': 'test-token', 'expires_in': 3600}
        profile_payload = {
            'id': 'linkedin-user-123',
            'localizedFirstName': 'OAuth',
            'localizedLastName': 'Tester',
            'emailAddress': 'oauth.tester@example.com',
        }
        email_payload = {
            'elements': [
                {'handle~': {'emailAddress': profile_payload['emailAddress']}},
            ]
        }

        def mock_request(method, url, **kwargs):
            if 'accessToken' in url:
                return self._json_response(LinkedInOAuth2Adapter.access_token_url, token_payload)
            if 'userinfo' in url or url.endswith('/me') or 'linkedin.com/v2/me' in url:
                return self._json_response(url, profile_payload)
            if 'emailAddress' in url:
                return self._json_response(url, email_payload)
            raise AssertionError(f"Unexpected request to {url}")

        with mock.patch('requests.sessions.Session.request', side_effect=mock_request):
            callback = client.get(
                f'{self.callback_path}?code=test-code&state={state}',
                follow=False,
            )

        self.assertEqual(callback.status_code, 302)
        self.assertTrue(callback['Location'].endswith('/profile/'))

        user = User.objects.get(email=profile_payload['emailAddress'])
        self.assertTrue(
            SocialAccount.objects.filter(user=user, provider=self.provider_slug).exists()
        )

        self.assertEqual(client.session.get('_auth_user_id'), str(user.id))

        client.force_login(user)
        profile_response = client.get('/profile/')
        self.assertEqual(profile_response.status_code, 200)
