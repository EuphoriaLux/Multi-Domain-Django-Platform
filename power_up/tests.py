"""
Tests for Power-Up corporate/investor site.

Tests use Client(HTTP_HOST='power-up.lu') to ensure domain routing works correctly.
This follows the established pattern in entreprinder/tests.py.
"""

from django.test import TestCase, Client
from django.contrib.sites.models import Site

from .platforms import PLATFORMS


class PowerUpRoutingTestCase(TestCase):
    """Test that power-up.lu routes to the correct URL configuration."""

    @classmethod
    def setUpTestData(cls):
        """Set up Site object for power-up.lu domain."""
        cls.site, _ = Site.objects.update_or_create(
            domain="power-up.lu", defaults={"name": "Power-Up"}
        )

    def setUp(self):
        """Create test client with power-up.lu host header."""
        self.client = Client(HTTP_HOST="power-up.lu")

    def test_home_page_returns_200(self):
        """Home page returns 200 and uses correct template."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "power_up/home.html")

    def test_home_page_has_platforms(self):
        """Home page includes platforms in context."""
        response = self.client.get("/")
        self.assertIn("platforms", response.context)
        self.assertEqual(len(response.context["platforms"]), len(PLATFORMS))

    def test_about_page_returns_200(self):
        """About page returns 200 and uses correct template."""
        response = self.client.get("/about/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "power_up/about.html")

    def test_platforms_page_returns_200(self):
        """Platforms page returns 200 and includes platform data."""
        response = self.client.get("/platforms/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "power_up/platforms.html")
        self.assertIn("platforms", response.context)
        self.assertGreater(len(response.context["platforms"]), 0)

    def test_investors_page_returns_200(self):
        """Investors page returns 200 and uses correct template."""
        response = self.client.get("/investors/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "power_up/investors.html")

    def test_contact_page_returns_200(self):
        """Contact page returns 200 and uses correct template."""
        response = self.client.get("/contact/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "power_up/contact.html")

    def test_robots_txt_returns_200(self):
        """robots.txt is accessible and has correct content type."""
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain")
        self.assertIn("power-up.lu", response.content.decode())

    def test_health_check_returns_200(self):
        """Health check endpoint returns 200."""
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)


class PowerUpNoRedirectTestCase(TestCase):
    """Verify that power-up.lu pages don't redirect."""

    def setUp(self):
        """Create test client with power-up.lu host header."""
        self.client = Client(HTTP_HOST="power-up.lu")
        Site.objects.update_or_create(
            domain="power-up.lu", defaults={"name": "Power-Up"}
        )

    def test_home_no_redirect(self):
        """Home page doesn't redirect (status is 200, not 301/302)."""
        response = self.client.get("/")
        self.assertNotIn(response.status_code, [301, 302])
        self.assertEqual(response.status_code, 200)

    def test_about_no_redirect(self):
        """About page doesn't redirect."""
        response = self.client.get("/about/")
        self.assertNotIn(response.status_code, [301, 302])
        self.assertEqual(response.status_code, 200)

    def test_platforms_no_redirect(self):
        """Platforms page doesn't redirect."""
        response = self.client.get("/platforms/")
        self.assertNotIn(response.status_code, [301, 302])
        self.assertEqual(response.status_code, 200)

    def test_investors_no_redirect(self):
        """Investors page doesn't redirect."""
        response = self.client.get("/investors/")
        self.assertNotIn(response.status_code, [301, 302])
        self.assertEqual(response.status_code, 200)

    def test_contact_no_redirect(self):
        """Contact page doesn't redirect."""
        response = self.client.get("/contact/")
        self.assertNotIn(response.status_code, [301, 302])
        self.assertEqual(response.status_code, 200)


class PowerUpNoBleedTestCase(TestCase):
    """Ensure Power-Up domain doesn't serve other app content."""

    def setUp(self):
        """Create test client with power-up.lu host header."""
        self.client = Client(HTTP_HOST="power-up.lu")
        Site.objects.update_or_create(
            domain="power-up.lu", defaults={"name": "Power-Up"}
        )

    def test_no_auth_endpoints(self):
        """Authentication endpoints should not be available (404)."""
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 404)

    def test_no_api_endpoints(self):
        """API endpoints should not be available (404)."""
        response = self.client.get("/api/token/")
        self.assertEqual(response.status_code, 404)

    def test_no_admin(self):
        """Admin should not be accessible (404)."""
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 404)

    def test_no_crush_pages(self):
        """Crush.lu specific pages should not be available (404)."""
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 404)

    def test_no_entreprinder_pages(self):
        """Entreprinder specific pages should not be available (404)."""
        response = self.client.get("/matching/swipe/")
        self.assertEqual(response.status_code, 404)


class PowerUpNavigationTestCase(TestCase):
    """Test that navigation links are present in pages."""

    def setUp(self):
        """Create test client with power-up.lu host header."""
        self.client = Client(HTTP_HOST="power-up.lu")
        Site.objects.update_or_create(
            domain="power-up.lu", defaults={"name": "Power-Up"}
        )

    def test_home_has_nav_links(self):
        """Home page contains navigation links."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('href="/about/"', content)
        self.assertIn('href="/platforms/"', content)
        self.assertIn('href="/investors/"', content)
        self.assertIn('href="/contact/"', content)

    def test_home_has_platform_links(self):
        """Home page contains links to external platforms."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn("https://crush.lu", content)
        self.assertIn("https://vinsdelux.com", content)
        self.assertIn("https://powerup.lu", content)


class PowerUpSEOTestCase(TestCase):
    """Test SEO-related elements are present."""

    def setUp(self):
        """Create test client with power-up.lu host header."""
        self.client = Client(HTTP_HOST="power-up.lu")
        Site.objects.update_or_create(
            domain="power-up.lu", defaults={"name": "Power-Up"}
        )

    def test_home_has_meta_description(self):
        """Home page has meta description."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('name="description"', content)

    def test_home_has_canonical_url(self):
        """Home page has canonical URL."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('rel="canonical"', content)
        self.assertIn("power-up.lu", content)

    def test_home_has_og_tags(self):
        """Home page has Open Graph tags."""
        response = self.client.get("/")
        content = response.content.decode()
        self.assertIn('property="og:title"', content)
        self.assertIn('property="og:description"', content)

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


class PowerUpPlatformDataTestCase(TestCase):
    """Test platform data structure."""

    def test_platforms_have_required_fields(self):
        """Each platform has all required fields."""
        required_fields = [
            "slug",
            "name",
            "tagline",
            "description",
            "url",
            "icon",
            "status",
        ]
        for platform in PLATFORMS:
            for field in required_fields:
                self.assertIn(
                    field, platform, f"Platform {platform.get('name', '?')} missing {field}"
                )

    def test_platforms_have_valid_urls(self):
        """Platform URLs are valid HTTPS URLs."""
        for platform in PLATFORMS:
            url = platform["url"]
            self.assertTrue(
                url.startswith("https://"),
                f"Platform {platform['name']} URL should be HTTPS",
            )

    def test_platforms_have_highlights(self):
        """Each platform has at least one highlight."""
        for platform in PLATFORMS:
            self.assertIn("highlights", platform)
            self.assertGreater(
                len(platform["highlights"]),
                0,
                f"Platform {platform['name']} should have highlights",
            )
