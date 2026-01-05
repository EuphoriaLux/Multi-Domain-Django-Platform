"""
Tests for Power-Up corporate/investor site.

Tests use Client(HTTP_HOST='power-up.lu') to ensure domain routing works correctly.
This follows the established pattern in entreprinder/tests.py.

Note: Power-Up uses i18n_patterns, so user-facing pages are under /en/, /de/, /fr/.
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
        response = self.client.get("/en/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "power_up/home.html")

    def test_home_page_has_platforms(self):
        """Home page includes platforms in context."""
        response = self.client.get("/en/")
        self.assertIn("platforms", response.context)
        self.assertEqual(len(response.context["platforms"]), len(PLATFORMS))

    def test_about_page_returns_200(self):
        """About page returns 200 and uses correct template."""
        response = self.client.get("/en/about/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "power_up/about.html")

    def test_platforms_page_returns_200(self):
        """Platforms page returns 200 and includes platform data."""
        response = self.client.get("/en/platforms/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "power_up/platforms.html")
        self.assertIn("platforms", response.context)
        self.assertGreater(len(response.context["platforms"]), 0)

    def test_investors_page_returns_200(self):
        """Investors page returns 200 and uses correct template."""
        response = self.client.get("/en/investors/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "power_up/investors.html")

    def test_contact_page_returns_200(self):
        """Contact page returns 200 and uses correct template."""
        response = self.client.get("/en/contact/")
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


class PowerUpI18nRedirectTestCase(TestCase):
    """Verify that power-up.lu i18n redirects work correctly."""

    def setUp(self):
        """Create test client with power-up.lu host header."""
        self.client = Client(HTTP_HOST="power-up.lu")
        Site.objects.update_or_create(
            domain="power-up.lu", defaults={"name": "Power-Up"}
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


class PowerUpNoBleedTestCase(TestCase):
    """Ensure Power-Up domain doesn't serve other app content."""

    def setUp(self):
        """Create test client with power-up.lu host header."""
        self.client = Client(HTTP_HOST="power-up.lu")
        Site.objects.update_or_create(
            domain="power-up.lu", defaults={"name": "Power-Up"}
        )

    def test_no_api_endpoints(self):
        """API token endpoints should not be available (404)."""
        response = self.client.get("/api/token/")
        self.assertEqual(response.status_code, 404)

    def test_no_crush_pages(self):
        """Crush.lu specific pages should not be available (404)."""
        response = self.client.get("/en/dashboard/")
        self.assertEqual(response.status_code, 404)

    def test_no_entreprinder_pages(self):
        """Entreprinder specific pages should not be available (404)."""
        response = self.client.get("/en/matching/swipe/")
        self.assertEqual(response.status_code, 404)

    def test_auth_endpoints_available(self):
        """Authentication endpoints are available via base_patterns."""
        # Test signup page which is simpler and doesn't have namespace dependencies
        response = self.client.get("/accounts/signup/")
        # Should not return 404 - auth endpoints are available
        self.assertNotEqual(response.status_code, 404)

    def test_admin_requires_login(self):
        """Admin is available but requires authentication."""
        response = self.client.get("/admin/")
        # Should redirect to login, not 404
        self.assertIn(response.status_code, [200, 302])


class PowerUpNavigationTestCase(TestCase):
    """Test that navigation links are present in pages."""

    def setUp(self):
        """Create test client with power-up.lu host header."""
        self.client = Client(HTTP_HOST="power-up.lu")
        Site.objects.update_or_create(
            domain="power-up.lu", defaults={"name": "Power-Up"}
        )

    def test_home_has_nav_links(self):
        """Home page contains navigation links (with language prefix)."""
        response = self.client.get("/en/")
        content = response.content.decode()
        # URLs now have language prefix
        self.assertIn('href="/en/about/"', content)
        self.assertIn('href="/en/platforms/"', content)
        self.assertIn('href="/en/investors/"', content)
        self.assertIn('href="/en/contact/"', content)

    def test_home_has_platform_links(self):
        """Home page contains links to external platforms."""
        response = self.client.get("/en/")
        content = response.content.decode()
        self.assertIn("https://crush.lu", content)
        self.assertIn("https://vinsdelux.com", content)
        self.assertIn("https://entreprinder.lu", content)


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
        response = self.client.get("/en/")
        content = response.content.decode()
        self.assertIn('name="description"', content)

    def test_home_has_canonical_url(self):
        """Home page has canonical URL."""
        response = self.client.get("/en/")
        content = response.content.decode()
        self.assertIn('rel="canonical"', content)
        self.assertIn("power-up.lu", content)

    def test_home_has_og_tags(self):
        """Home page has Open Graph tags."""
        response = self.client.get("/en/")
        content = response.content.decode()
        self.assertIn('property="og:title"', content)
        self.assertIn('property="og:description"', content)

    def test_home_has_twitter_card(self):
        """Home page has Twitter Card meta tags."""
        response = self.client.get("/en/")
        content = response.content.decode()
        self.assertIn('name="twitter:card"', content)
        self.assertIn('name="twitter:image"', content)

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
        """Platform URLs are valid HTTPS URLs or internal paths."""
        for platform in PLATFORMS:
            url = platform["url"]
            # External URLs must be HTTPS, internal URLs start with /
            self.assertTrue(
                url.startswith("https://") or url.startswith("/"),
                f"Platform {platform['name']} URL should be HTTPS or internal path",
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
