"""
Tests for Arborist informational site.

Tests use Client(HTTP_HOST='arborist.lu') to ensure domain routing works correctly.
This follows the established pattern in power_up/tests.py.

Note: Arborist uses i18n_patterns, so user-facing pages are under /en/, /de/, /fr/.
"""

from django.test import TestCase, Client
from django.contrib.sites.models import Site


class ArboristRoutingTestCase(TestCase):
    """Test that arborist.lu routes to the correct URL configuration."""

    @classmethod
    def setUpTestData(cls):
        """Set up Site object for arborist.lu domain."""
        cls.site, _ = Site.objects.update_or_create(
            domain="arborist.lu", defaults={"name": "Arborist"}
        )

    def setUp(self):
        """Create test client with arborist.lu host header."""
        self.client = Client(HTTP_HOST="arborist.lu")

    def test_home_page_returns_200(self):
        """Home page returns 200 and uses correct template."""
        response = self.client.get("/en/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "arborist/home.html")

    def test_about_page_returns_200(self):
        """About page returns 200 and uses correct template."""
        response = self.client.get("/en/about/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "arborist/about.html")

    def test_services_page_returns_200(self):
        """Services page returns 200 and uses correct template."""
        response = self.client.get("/en/services/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "arborist/services.html")

    def test_contact_page_returns_200(self):
        """Contact page returns 200 and uses correct template."""
        response = self.client.get("/en/contact/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "arborist/contact.html")

    def test_robots_txt_returns_200(self):
        """robots.txt is accessible and has correct content type."""
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain")
        self.assertIn("arborist.lu", response.content.decode())

    def test_health_check_returns_200(self):
        """Health check endpoint returns 200."""
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)


class ArboristI18nRedirectTestCase(TestCase):
    """Verify that arborist.lu i18n redirects work correctly."""

    def setUp(self):
        """Create test client with arborist.lu host header."""
        self.client = Client(HTTP_HOST="arborist.lu")
        Site.objects.update_or_create(
            domain="arborist.lu", defaults={"name": "Arborist"}
        )

    def test_home_redirects_to_language(self):
        """Root path redirects to language-prefixed URL."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/en/", response["Location"])

    def test_about_redirects_to_language(self):
        """About path without language prefix redirects."""
        response = self.client.get("/about/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/en/about/", response["Location"])

    def test_language_prefixed_pages_no_redirect(self):
        """Language-prefixed pages return 200 (no redirect)."""
        response = self.client.get("/en/")
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/de/")
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/fr/")
        self.assertEqual(response.status_code, 200)


class ArboristSEOTestCase(TestCase):
    """Test SEO-related elements are present."""

    def setUp(self):
        """Create test client with arborist.lu host header."""
        self.client = Client(HTTP_HOST="arborist.lu")
        Site.objects.update_or_create(
            domain="arborist.lu", defaults={"name": "Arborist"}
        )

    def test_home_has_meta_description(self):
        """Home page has meta description."""
        response = self.client.get("/en/")
        content = response.content.decode()
        self.assertIn('name="description"', content)

    def test_home_has_canonical_url(self):
        """Home page has canonical URL."""
        response = self.client.get("/en/")
        content = response.content.decode()
        self.assertIn('rel="canonical"', content)
        self.assertIn("arborist.lu", content)

    def test_robots_txt_allows_crawling(self):
        """robots.txt allows crawling of public pages."""
        response = self.client.get("/robots.txt")
        content = response.content.decode()
        self.assertIn("Allow: /", content)

    def test_robots_txt_blocks_healthz(self):
        """robots.txt blocks health check endpoint."""
        response = self.client.get("/robots.txt")
        content = response.content.decode()
        self.assertIn("Disallow: /healthz/", content)
