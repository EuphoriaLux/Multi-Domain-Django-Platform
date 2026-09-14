"""
Tests for Arborist informational site.

Tests use Client(HTTP_HOST='arborist.lu') to ensure domain routing works correctly.
This follows the established pattern in power_up/tests.py.

Note: Arborist uses i18n_patterns, so user-facing pages are under /en/, /de/, /fr/.
"""

from decimal import Decimal
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


class ArboristZoneCalculationTestCase(TestCase):
    """Test the distance and zone calculation service."""

    def test_zone_1_altrier(self):
        """Altrier postcode 6211 resolves to Zone 1 with 50 EUR base fee."""
        from arborist.services.zones import calculate_zone, ZONE_1_FEE
        quote = calculate_zone("6211")
        self.assertEqual(quote.zone, 1)
        self.assertEqual(quote.base_prepayment_eur, ZONE_1_FEE)
        self.assertEqual(quote.total_prepayment_eur, Decimal("50.00"))
        self.assertFalse(quote.is_rush)

    def test_zone_1_junglinster(self):
        """Junglinster postcode 6110 resolves to Zone 1 with 50 EUR base fee."""
        from arborist.services.zones import calculate_zone
        quote = calculate_zone("6110")
        self.assertEqual(quote.zone, 1)
        self.assertEqual(quote.base_prepayment_eur, Decimal("50.00"))

    def test_zone_2_esch_sur_alzette(self):
        """Esch-sur-Alzette postcode 4010 resolves to Zone 2 with 100 EUR base fee."""
        from arborist.services.zones import calculate_zone, ZONE_2_FEE
        quote = calculate_zone("4010")
        self.assertEqual(quote.zone, 2)
        self.assertEqual(quote.base_prepayment_eur, ZONE_2_FEE)
        self.assertEqual(quote.total_prepayment_eur, Decimal("100.00"))

    def test_zone_2_luxembourg_city(self):
        """Luxembourg City postcode 1110 resolves to Zone 2 with 100 EUR base fee."""
        from arborist.services.zones import calculate_zone
        quote = calculate_zone("1110")
        self.assertEqual(quote.zone, 2)
        self.assertEqual(quote.base_prepayment_eur, Decimal("100.00"))

    def test_rush_order_surcharge(self):
        """Rush order adds 50 EUR emergency surcharge."""
        from arborist.services.zones import calculate_zone
        quote_z1_rush = calculate_zone("6211", is_rush=True)
        self.assertEqual(quote_z1_rush.zone, 1)
        self.assertTrue(quote_z1_rush.is_rush)
        self.assertEqual(quote_z1_rush.rush_fee_eur, Decimal("50.00"))
        self.assertEqual(quote_z1_rush.total_prepayment_eur, Decimal("100.00"))

        quote_z2_rush = calculate_zone("4010", is_rush=True)
        self.assertEqual(quote_z2_rush.zone, 2)
        self.assertTrue(quote_z2_rush.is_rush)
        self.assertEqual(quote_z2_rush.rush_fee_eur, Decimal("50.00"))
        self.assertEqual(quote_z2_rush.total_prepayment_eur, Decimal("150.00"))

    def test_clean_postal_code(self):
        """Postal code cleaner handles prefixes and whitespace."""
        from arborist.services.zones import clean_postal_code
        self.assertEqual(clean_postal_code("L-6211"), "6211")
        self.assertEqual(clean_postal_code("L 6211"), "6211")
        self.assertEqual(clean_postal_code(" 6211 "), "6211")
        self.assertEqual(clean_postal_code("L6211"), "6211")


class ArboristBookingFlowTestCase(TestCase):
    """Test booking views, API, and submission workflow."""

    def setUp(self):
        self.client = Client(HTTP_HOST="arborist.lu")
        Site.objects.update_or_create(
            domain="arborist.lu", defaults={"name": "Arborist"}
        )

    def test_booking_page_get(self):
        """Booking page renders form correctly."""
        response = self.client.get("/en/booking/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "arborist/booking.html")
        self.assertContains(response, "Vor-Ort-Termin")

    def test_booking_page_preselected_service(self):
        """Booking page preselects service when requested via query parameter."""
        response = self.client.get("/en/booking/?service=obstbaumpflege")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="obstbaumpflege" selected')

    def test_api_zone_lookup_endpoint(self):
        """API zone lookup returns JSON calculation for postal code."""
        response = self.client.get("/en/api/zone-lookup/?postal_code=6211")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["zone"], 1)
        self.assertEqual(data["total_prepayment_eur"], 50.0)

    def test_api_zone_lookup_rush(self):
        """API zone lookup includes rush order surcharge when requested."""
        response = self.client.get("/en/api/zone-lookup/?postal_code=4010&is_rush=true")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["zone"], 2)
        self.assertEqual(data["total_prepayment_eur"], 150.0)
        self.assertTrue(data["is_rush"])

    def test_booking_submission_creates_model_and_redirects(self):
        """Valid form submission creates ArboristBooking record and redirects to success."""
        from arborist.models import ArboristBooking
        post_data = {
            "name": "Tom Test",
            "email": "tom.test@example.lu",
            "phone": "+352 621 999 888",
            "street_address": "5, Rue d'Altrier",
            "postal_code": "6211",
            "city_or_commune": "Altrier",
            "service_type": "baumpflege",
            "number_of_trees": "2 Eichen",
            "preferred_time_slot": "morning",
            "notes": "Totholzentfernung in Baumkrone",
        }
        response = self.client.post("/en/booking/", data=post_data)
        self.assertEqual(response.status_code, 302)

        booking = ArboristBooking.objects.filter(email="tom.test@example.lu").first()
        self.assertIsNotNone(booking)
        self.assertEqual(booking.name, "Tom Test")
        self.assertEqual(booking.zone, 1)
        self.assertEqual(booking.prepayment_amount, Decimal("50.00"))
        self.assertFalse(booking.is_rush)
        self.assertTrue(booking.booking_reference.startswith("ARB-"))

        # Check redirect target
        self.assertIn(f"/en/booking/success/{booking.booking_reference}/", response["Location"])

    def test_booking_success_screen(self):
        """Success screen displays reference, zone and bank details."""
        from arborist.models import ArboristBooking
        booking = ArboristBooking.objects.create(
            name="Alice Lux",
            email="alice@example.lu",
            phone="+352 691 111 222",
            street_address="10, Grand-Rue",
            postal_code="4010",
            city_or_commune="Esch-sur-Alzette",
            service_type="baumkontrolle",
            zone=2,
            distance_km=Decimal("42.0"),
            prepayment_amount=Decimal("100.00"),
        )
        response = self.client.get(f"/en/booking/success/{booking.booking_reference}/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "arborist/booking_success.html")
        self.assertContains(response, booking.booking_reference)
        self.assertContains(response, "100.00 €")
        self.assertContains(response, "LU86 0030 8123 4567 8901")

    def test_multilingual_booking_routes(self):
        """All localized booking paths return HTTP 200."""
        for path in ["/en/booking/", "/de/termin/", "/fr/rendez-vous/"]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)

