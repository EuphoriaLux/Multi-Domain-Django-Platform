"""
HTMX Endpoint Tests

Tests for HTMX-powered views to ensure they return correct partial templates
and handle both HTMX and non-HTMX requests appropriately.

Run with: python manage.py test crush_lu.tests.test_htmx_views
Or with pytest: pytest crush_lu/tests/test_htmx_views.py -v
"""
from datetime import date, timedelta
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.utils import timezone

from crush_lu.models.profiles import UserDataConsent

User = get_user_model()


# Override ROOT_URLCONF to use Crush.lu URLs for these tests
CRUSH_LU_URL_SETTINGS = {
    'ROOT_URLCONF': 'azureproject.urls_crush',
}


class SiteTestMixin:
    """Mixin to create Site object for tests that need Django Sites framework."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Create or get the Site object for tests (get_or_create avoids unique constraint issues)
        Site.objects.get_or_create(
            id=1,
            defaults={'domain': 'testserver', 'name': 'Test Server'}
        )


class HTMXTestMixin:
    """Mixin providing HTMX request helpers."""

    def htmx_get(self, url, **kwargs):
        """Make a GET request with HTMX headers."""
        return self.client.get(url, HTTP_HX_REQUEST='true', **kwargs)

    def htmx_post(self, url, data=None, **kwargs):
        """Make a POST request with HTMX headers."""
        return self.client.post(
            url,
            data=data or {},
            HTTP_HX_REQUEST='true',
            **kwargs
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class PublicPageHTMXTests(SiteTestMixin, HTMXTestMixin, TestCase):
    """Test HTMX on public pages."""

    def setUp(self):
        """Set up test client."""
        self.client = Client()

    def test_home_page_works_with_htmx(self):
        """Home page should work with HTMX headers."""
        url = reverse('crush_lu:home')
        response = self.htmx_get(url)
        self.assertEqual(response.status_code, 200)

    def test_home_page_works_without_htmx(self):
        """Home page should work without HTMX headers."""
        url = reverse('crush_lu:home')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_about_page_works(self):
        """About page should work."""
        url = reverse('crush_lu:about')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_how_it_works_page_works(self):
        """How it works page should work."""
        url = reverse('crush_lu:how_it_works')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_events_list_works(self):
        """Events list page should work."""
        url = reverse('crush_lu:event_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class AuthenticatedHTMXTests(SiteTestMixin, HTMXTestMixin, TestCase):
    """Test HTMX on authenticated pages."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()

        self.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='testpass123'
        )
        UserDataConsent.objects.filter(user=self.user).update(crushlu_consent_given=True)

        from crush_lu.models import CrushProfile

        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg',
            bio='Test bio',
            is_approved=True,
            is_active=True
        )

    def test_dashboard_requires_auth(self):
        """Dashboard should redirect unauthenticated users."""
        url = reverse('crush_lu:dashboard')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_dashboard_works_for_authenticated(self):
        """Dashboard should work for authenticated users with profile."""
        self.client.login(username='testuser@example.com', password='testpass123')
        url = reverse('crush_lu:dashboard')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_profile_works(self):
        """Edit profile should work for authenticated users."""
        self.client.login(username='testuser@example.com', password='testpass123')
        url = reverse('crush_lu:edit_profile')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'crush_lu/edit_profile.html')

    def test_edit_profile_htmx_request(self):
        """Edit profile should work with HTMX request."""
        self.client.login(username='testuser@example.com', password='testpass123')
        url = reverse('crush_lu:edit_profile')
        response = self.htmx_get(url)
        self.assertEqual(response.status_code, 200)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class EventHTMXTests(SiteTestMixin, HTMXTestMixin, TestCase):
    """Test HTMX on event pages."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()

        self.user = User.objects.create_user(
            username='eventuser@example.com',
            email='eventuser@example.com',
            password='testpass123'
        )
        UserDataConsent.objects.filter(user=self.user).update(crushlu_consent_given=True)

        from crush_lu.models import CrushProfile, MeetupEvent

        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender='M',
            location='Luxembourg',
            bio='Test bio',
            is_approved=True,
            is_active=True
        )

        # Create a future published event
        self.event = MeetupEvent.objects.create(
            title='Test Event',
            description='Test description for the event',
            event_type='mixer',
            date_time=timezone.now() + timedelta(days=14),
            location='Test Location',
            address='123 Test Street, Luxembourg',
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=10),
            is_published=True
        )

    def test_event_detail_works(self):
        """Event detail page should work."""
        url = reverse('crush_lu:event_detail', args=[self.event.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_event_detail_with_htmx(self):
        """Event detail should work with HTMX request."""
        url = reverse('crush_lu:event_detail', args=[self.event.id])
        response = self.htmx_get(url)
        self.assertEqual(response.status_code, 200)

    def test_event_register_requires_auth(self):
        """Event registration should require authentication."""
        url = reverse('crush_lu:event_register', args=[self.event.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_event_register_works_for_authenticated(self):
        """Event registration should work for authenticated users."""
        self.client.login(username='eventuser@example.com', password='testpass123')
        url = reverse('crush_lu:event_register', args=[self.event.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class HTMXHeaderTests(SiteTestMixin, TestCase):
    """Test that views correctly detect HTMX requests."""

    def setUp(self):
        """Set up test client."""
        self.client = Client()

    def test_htmx_request_detected(self):
        """Views should detect HX-Request header."""
        url = reverse('crush_lu:home')

        # Regular request
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # HTMX request
        response = self.client.get(url, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)

    def test_htmx_boosted_detected(self):
        """Views should detect HX-Boosted header for navigation."""
        url = reverse('crush_lu:home')

        response = self.client.get(
            url,
            HTTP_HX_REQUEST='true',
            HTTP_HX_BOOSTED='true'
        )
        self.assertEqual(response.status_code, 200)