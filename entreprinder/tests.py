import json

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp
from .models import EntrepreneurProfile, Industry, Like, Match

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

    Note: We use HTTP_HOST='powerup.lu' to ensure the DomainURLRoutingMiddleware
    routes requests to the correct URL configuration (urls_powerup.py which
    includes entreprinder URLs). Without this, 'testserver' falls back to
    PowerUP URLs but with different routing behavior.

    Important: We use direct URLs (/, /profile/) instead of reverse() because:
    - reverse() uses ROOT_URLCONF (azureproject.urls) which has i18n_patterns
    - urls_powerup.py does NOT use i18n_patterns, so URLs are at / not /en/
    - Using reverse() would give us /en/ which returns 404 on powerup.lu
    """
    @classmethod
    def setUpTestData(cls):
        """Set up data needed by all tests - runs once per TestCase."""
        # Create/update Site for the tests - ensure domain matches HTTP_HOST
        # Django creates Site id=1 with 'example.com' by default in tests
        cls.site, created = Site.objects.update_or_create(
            id=1,
            defaults={'domain': 'powerup.lu', 'name': 'PowerUP'}
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
        self.client = Client(HTTP_HOST='powerup.lu')
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

class MatchingTestCase(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username='user1', password='12345')
        self.user2 = User.objects.create_user(username='user2', password='12345')
        self.industry = Industry.objects.create(name='Tech')
        self.profile1 = EntrepreneurProfile.objects.create(user=self.user1, industry=self.industry)
        self.profile2 = EntrepreneurProfile.objects.create(user=self.user2, industry=self.industry)

    def test_like_and_match(self):
        # Create likes
        Like.objects.create(liker=self.profile1, liked=self.profile2)
        Like.objects.create(liker=self.profile2, liked=self.profile1)
        
        # Check if a match was created
        match = Match.objects.filter(
            entrepreneur1__in=[self.profile1, self.profile2],
            entrepreneur2__in=[self.profile1, self.profile2]
        ).first()
        
        # Debug print statements
        print(f"Likes from profile1 to profile2: {Like.objects.filter(liker=self.profile1, liked=self.profile2).exists()}")
        print(f"Likes from profile2 to profile1: {Like.objects.filter(liker=self.profile2, liked=self.profile1).exists()}")
        print(f"All matches: {list(Match.objects.all())}")
        
        self.assertIsNotNone(match, "No match was created after mutual likes")

    def test_match_creation_logic(self):
        # This test checks if the match creation logic is working properly
        Like.objects.create(liker=self.profile1, liked=self.profile2)
        self.assertEqual(Match.objects.count(), 0, "Match shouldn't be created after only one like")
        
        Like.objects.create(liker=self.profile2, liked=self.profile1)
        self.assertEqual(Match.objects.count(), 1, "Match should be created after mutual likes")


class SwipeActionValidationTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Align the test site with the currently active settings module (local or production).
        from django.conf import settings
        from azureproject import domains

        preferred_host = None
        for host, config in domains.DOMAINS.items():
            if config.get('app') == 'entreprinder':
                preferred_host = host
                break

        preferred_host = preferred_host or domains.PRODUCTION_DEFAULT

        cls.site, _ = Site.objects.update_or_create(
            id=getattr(settings, 'SITE_ID', 1),
            defaults={'domain': preferred_host, 'name': preferred_host}
        )
        cls.test_host = preferred_host

    def setUp(self):
        self.client = Client(HTTP_HOST=self.test_host)
        self.user = User.objects.create_user(username='swipeuser', password='12345')
        self.target_user = User.objects.create_user(username='targetuser', password='12345')
        self.industry = Industry.objects.create(name='Tech')
        self.user_profile = EntrepreneurProfile.objects.create(user=self.user, industry=self.industry)
        self.target_profile = EntrepreneurProfile.objects.create(user=self.target_user, industry=self.industry)
        self.client.login(username='swipeuser', password='12345')

    def test_invalid_json_returns_error(self):
        response = self.client.post(
            '/matching/swipe/action/',
            data='not-json',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['message'], 'Invalid JSON payload')

    def test_invalid_action_returns_error(self):
        payload = {'profile_id': self.target_profile.id, 'action': 'maybe'}
        response = self.client.post(
            '/matching/swipe/action/',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['message'], 'Invalid action')

    def test_missing_profile_returns_not_found(self):
        payload = {'profile_id': 9999, 'action': 'like'}
        response = self.client.post(
            '/matching/swipe/action/',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['message'], 'Profile not found')
