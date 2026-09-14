"""
Tests for the arborist.lu booking flow: zone pricing, the booking form and
views, the receipt page, bank details, WhatsApp links and admin re-pricing.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core import mail
from django.test import Client, RequestFactory, TestCase, override_settings
from django.utils import timezone

from arborist.admin import ArboristBookingAdmin, arborist_admin_site
from arborist.forms import BookingForm
from arborist.models import ArboristBooking
from arborist.services.payment import get_prepayment_bank_details, is_valid_iban
from arborist.services.phone import to_whatsapp_number
from arborist.services.zones import (
    ZONE_1_FEE,
    ZONE_2_FEE,
    calculate_zone,
    clean_postal_code,
    is_valid_postal_code,
)

# The ISO 13616 registry's example Luxembourg IBAN: checksum-valid, no real account.
VALID_TEST_IBAN = "LU28 0019 4006 4475 0000"
# The account this PR first shipped: fails the mod-97 checksum.
PLACEHOLDER_IBAN = "LU86 0030 8123 4567 8901"

BOOKING_POST = {
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


class ArboristClientTestCase(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="arborist.lu")
        Site.objects.update_or_create(
            domain="arborist.lu", defaults={"name": "Arborist"}
        )

    def book(self, **overrides):
        """Submit the public booking form; return the stored booking."""
        response = self.client.post("/en/termin/", data={**BOOKING_POST, **overrides})
        self.assertEqual(response.status_code, 302)
        return ArboristBooking.objects.latest("created_at")

    def admin_mail(self):
        return next(m for m in mail.outbox if "tom@arborist.lu" in m.to)

    def client_mail(self, booking):
        return next(m for m in mail.outbox if booking.email in m.to)


class ZoneCalculationTests(TestCase):
    def test_zone_1_altrier(self):
        """Altrier postcode 6211 resolves to Zone 1 with 50 EUR base fee."""
        quote = calculate_zone("6211")
        self.assertEqual(quote.zone, 1)
        self.assertEqual(quote.base_prepayment_eur, ZONE_1_FEE)
        self.assertEqual(quote.total_prepayment_eur, Decimal("50.00"))
        self.assertFalse(quote.is_rush)

    def test_zone_1_junglinster(self):
        """Junglinster postcode 6110 resolves to Zone 1 with 50 EUR base fee."""
        quote = calculate_zone("6110")
        self.assertEqual(quote.zone, 1)
        self.assertEqual(quote.base_prepayment_eur, Decimal("50.00"))

    def test_zone_2_esch_sur_alzette(self):
        """Esch-sur-Alzette postcode 4010 resolves to Zone 2 with 100 EUR base fee."""
        quote = calculate_zone("4010")
        self.assertEqual(quote.zone, 2)
        self.assertEqual(quote.base_prepayment_eur, ZONE_2_FEE)
        self.assertEqual(quote.total_prepayment_eur, Decimal("100.00"))

    def test_zone_2_luxembourg_city(self):
        """Luxembourg City postcode 1110 resolves to Zone 2 with 100 EUR base fee."""
        quote = calculate_zone("1110")
        self.assertEqual(quote.zone, 2)
        self.assertEqual(quote.base_prepayment_eur, Decimal("100.00"))

    def test_rush_order_surcharge(self):
        """Rush order adds 50 EUR emergency surcharge."""
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

    def test_published_18_km_boundary_is_what_is_charged(self):
        """Grevenmacher (17.8 km) is Zone 1; Niederanven (18.2) and Steinsel
        (20.5) are Zone 2, as the booking page promises for anything > 18 km."""
        self.assertEqual(calculate_zone("6711").zone, 1)
        for code in ("1209", "7300"):
            quote = calculate_zone(code)
            self.assertEqual(quote.zone, 2, code)
            self.assertEqual(quote.base_prepayment_eur, ZONE_2_FEE, code)

    def test_typed_town_cannot_price_an_unknown_postcode(self):
        """A cheap town typed next to a postcode CACLR does not know is shown
        back to the customer but never priced."""
        quote = calculate_zone("0000", "Bech")
        self.assertEqual(quote.zone, 2)
        self.assertEqual(quote.commune, "Bech")

    def test_malformed_postcode_is_rejected_not_truncated(self):
        for code in ("62111", "abc6211", "621"):
            with self.assertRaises(ValueError, msg=code):
                calculate_zone(code)


class PostalCodeTests(TestCase):
    def test_clean_postal_code_strips_prefix_and_whitespace(self):
        self.assertEqual(clean_postal_code("L-6211"), "6211")
        self.assertEqual(clean_postal_code("L 6211"), "6211")
        self.assertEqual(clean_postal_code(" 6211 "), "6211")
        self.assertEqual(clean_postal_code("L6211"), "6211")

    def test_clean_postal_code_drops_nothing_else(self):
        self.assertEqual(clean_postal_code("62111"), "62111")
        for code in ("62111", "abc6211", "6 211", "621", ""):
            self.assertFalse(is_valid_postal_code(clean_postal_code(code)), code)

    def test_booking_form_rejects_malformed_postcodes(self):
        for code in ("62111", "abc6211", "6 211", "621"):
            form = BookingForm(data={**BOOKING_POST, "postal_code": code})
            self.assertFalse(form.is_valid(), code)
            self.assertIn("postal_code", form.errors, code)

    def test_booking_form_accepts_l_prefix(self):
        form = BookingForm(data={**BOOKING_POST, "postal_code": "L-6211"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["postal_code"], "6211")


class RushWindowTests(TestCase):
    """The rush surcharge buys a visit within 24-48 hours, so a rush booking
    may not ask for a later date."""

    def form(self, **overrides):
        return BookingForm(data={**BOOKING_POST, **overrides})

    def in_days(self, days):
        return (timezone.localdate() + timedelta(days=days)).isoformat()

    def test_rush_date_must_be_within_two_days(self):
        form = self.form(is_rush="on", preferred_date=self.in_days(5))
        self.assertFalse(form.is_valid())
        self.assertIn("preferred_date", form.errors)
        for days in (0, 1, 2):
            form = self.form(is_rush="on", preferred_date=self.in_days(days))
            self.assertTrue(form.is_valid(), (days, form.errors))

    def test_rush_without_a_date_means_as_soon_as_possible(self):
        self.assertTrue(self.form(is_rush="on").is_valid())

    def test_standard_booking_may_be_weeks_away(self):
        self.assertTrue(self.form(preferred_date=self.in_days(30)).is_valid())


class BookingFlowTests(ArboristClientTestCase):
    def test_booking_page_get(self):
        response = self.client.get("/en/termin/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "arborist/booking.html")

    def test_booking_page_preselected_service(self):
        response = self.client.get("/en/termin/?service=obstbaumpflege")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="obstbaumpflege" selected')

    def test_unknown_service_param_is_ignored(self):
        response = self.client.get("/en/termin/?service=bogus")
        self.assertNotIn("service_type", response.context["form"].initial)

    def test_rush_cta_preselects_the_rush_box(self):
        response = self.client.get("/en/termin/?service=notdienst&rush=1")
        self.assertContains(response, 'value="notdienst" selected')
        self.assertTrue(response.context["form"]["is_rush"].value())

    def test_emergency_service_link_preselects_the_rush_box(self):
        response = self.client.get("/en/termin/?service=notdienst")
        self.assertTrue(response.context["form"]["is_rush"].value())

    def test_home_rush_cta_asks_for_rush(self):
        response = self.client.get("/en/")
        self.assertContains(response, "?service=notdienst&amp;rush=1")

    def test_api_zone_lookup_endpoint(self):
        response = self.client.get("/en/api/zone-lookup/?postal_code=6211")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["zone"], 1)
        self.assertEqual(data["total_prepayment_eur"], 50.0)

    def test_api_zone_lookup_rush(self):
        response = self.client.get("/en/api/zone-lookup/?postal_code=4010&is_rush=true")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["zone"], 2)
        self.assertEqual(data["total_prepayment_eur"], 150.0)
        self.assertTrue(data["is_rush"])

    def test_api_rejects_malformed_postcodes(self):
        for code in ("62", "62111", "abc6211"):
            response = self.client.get(f"/en/api/zone-lookup/?postal_code={code}")
            self.assertEqual(response.status_code, 400, code)

    def test_booking_submission_creates_model_and_redirects(self):
        response = self.client.post("/en/termin/", data=BOOKING_POST)
        self.assertEqual(response.status_code, 302)

        booking = ArboristBooking.objects.get(email="tom.test@example.lu")
        self.assertEqual(booking.name, "Tom Test")
        self.assertEqual(booking.zone, 1)
        self.assertEqual(booking.prepayment_amount, Decimal("50.00"))
        self.assertFalse(booking.is_rush)
        self.assertTrue(booking.booking_reference.startswith("ARB-"))
        self.assertIn(
            f"/en/booking/success/{booking.booking_reference}/", response["Location"]
        )

    def test_rush_submission_is_charged_and_flagged(self):
        booking = self.book(postal_code="7300", is_rush="on")
        self.assertEqual(booking.zone, 2)
        self.assertEqual(booking.prepayment_amount, Decimal("150.00"))
        self.assertIn("RUSH ORDER", self.admin_mail().subject)

    def test_emergency_without_rush_is_still_flagged_urgent(self):
        booking = self.book(service_type="notdienst")
        self.assertEqual(booking.prepayment_amount, Decimal("50.00"))
        self.assertIn("EMERGENCY", self.admin_mail().subject)

    def test_whatsapp_link_uses_international_number(self):
        self.book(phone="621 123 456")
        self.assertIn("https://wa.me/352621123456?text=", self.admin_mail().body)

    def test_no_flash_message_leaks_to_the_next_page(self):
        self.book()
        response = self.client.get("/en/termin/")
        self.assertEqual(list(response.context["messages"]), [])

    def test_booking_page_is_served_in_every_language(self):
        for path, text in [
            ("/en/termin/", "Postal code (4 digits)"),
            ("/fr/termin/", "Code postal (4 chiffres)"),
            ("/de/termin/", "Postleitzahl (4-stellig)"),
        ]:
            self.assertContains(self.client.get(path), text, msg_prefix=path)

    def test_alias_slugs_redirect_with_the_query_string(self):
        for alias, target in [
            (
                "/en/booking/?service=notdienst&rush=1",
                "/en/termin/?service=notdienst&rush=1",
            ),
            ("/fr/rendez-vous/", "/fr/termin/"),
        ]:
            response = self.client.get(alias)
            self.assertEqual(response.status_code, 301, alias)
            self.assertEqual(response["Location"], target, alias)

    def test_home_zone_teaser_is_translated(self):
        self.assertContains(self.client.get("/en/"), "rest of Luxembourg")
        self.assertContains(self.client.get("/fr/"), "reste du Luxembourg")


class ReceiptPageTests(ArboristClientTestCase):
    def receipt_url(self, booking):
        return f"/en/booking/success/{booking.booking_reference}/"

    def test_submitting_session_sees_its_receipt(self):
        booking = self.book()
        response = self.client.get(self.receipt_url(booking))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "arborist/booking_success.html")
        self.assertContains(response, booking.booking_reference)
        self.assertContains(response, "50.00 €")
        self.assertIn("no-store", response["Cache-Control"])

    def test_reference_alone_does_not_open_the_receipt(self):
        booking = self.book()
        stranger = Client(HTTP_HOST="arborist.lu")
        response = stranger.get(self.receipt_url(booking))
        self.assertEqual(response.status_code, 404)

    @override_settings(ARBORIST_PREPAYMENT_IBAN="")
    def test_no_bank_details_until_an_account_is_configured(self):
        booking = self.book()
        response = self.client.get(self.receipt_url(booking))
        self.assertNotContains(response, "IBAN")
        body = self.client_mail(booking).body
        self.assertNotIn("IBAN", body)
        self.assertIn("You will receive the bank details for the prepayment", body)

    @override_settings(
        ARBORIST_PREPAYMENT_IBAN=VALID_TEST_IBAN, ARBORIST_PREPAYMENT_BIC="bceelull"
    )
    def test_configured_account_is_shown_and_emailed(self):
        booking = self.book()
        response = self.client.get(self.receipt_url(booking))
        self.assertContains(response, VALID_TEST_IBAN)
        self.assertContains(response, "BCEELULL")
        body = self.client_mail(booking).body
        self.assertIn(f"IBAN: {VALID_TEST_IBAN}", body)
        self.assertIn(f"Payment reference: {booking.booking_reference}", body)

    def test_confirmation_email_follows_the_form_language(self):
        booking = self.book()  # the /en/ form
        self.assertIn("Hello Tom Test,", self.client_mail(booking).body)

        response = self.client.post(
            "/fr/termin/", data={**BOOKING_POST, "email": "fr@example.lu"}
        )
        self.assertEqual(response.status_code, 302)
        french = next(m for m in mail.outbox if "fr@example.lu" in m.to)
        self.assertIn("Confirmation de réservation", french.subject)
        self.assertIn("Bonjour Tom Test,", french.body)
        self.assertIn("Référence de réservation", french.body)
        self.assertNotIn("Moien", french.body)

    @override_settings(ARBORIST_PREPAYMENT_IBAN=PLACEHOLDER_IBAN)
    def test_invalid_iban_is_never_published(self):
        with self.assertLogs("arborist.services.payment", level="WARNING"):
            booking = self.book()
        response = self.client.get(self.receipt_url(booking))
        self.assertNotContains(response, "LU86")
        self.assertNotIn("LU86", self.client_mail(booking).body)


class BankDetailsTests(TestCase):
    def test_iban_checksum(self):
        self.assertTrue(is_valid_iban(VALID_TEST_IBAN))
        self.assertTrue(is_valid_iban(VALID_TEST_IBAN.lower().replace(" ", "")))
        self.assertFalse(is_valid_iban(PLACEHOLDER_IBAN))
        self.assertFalse(is_valid_iban("LU28 0019"))
        self.assertFalse(is_valid_iban(""))

    @override_settings(
        ARBORIST_PREPAYMENT_IBAN="lu280019400644750000", ARBORIST_PREPAYMENT_BIC=""
    )
    def test_iban_is_normalized_and_grouped(self):
        bank = get_prepayment_bank_details()
        self.assertEqual(bank.iban, "LU280019400644750000")
        self.assertEqual(bank.iban_display, VALID_TEST_IBAN)
        self.assertEqual(bank.bic, "")


class WhatsAppNumberTests(TestCase):
    def test_numbers(self):
        cases = {
            "+352 621 123 456": "352621123456",
            "00352 621 123 456": "352621123456",
            "352 621 123 456": "352621123456",
            "621 123 456": "352621123456",
            "43 12 34": "352431234",
            "+49 171 1234567": "491711234567",
            "0171 1234567": None,
            "": None,
        }
        for phone, expected in cases.items():
            self.assertEqual(to_whatsapp_number(phone), expected, phone)


class AdminRepricingTests(TestCase):
    def setUp(self):
        self.model_admin = ArboristBookingAdmin(ArboristBooking, arborist_admin_site)
        self.request = RequestFactory().post("/arborist-admin/")
        self.request.user = get_user_model().objects.create_superuser(
            username="staff", email="staff@example.lu", password="pw"
        )

    def form_data(self, **overrides):
        data = {
            "name": "Phone Booking",
            "email": "phone@example.lu",
            "phone": "+352 621 000 111",
            "street_address": "1, Grand-Rue",
            "postal_code": "4010",
            "city_or_commune": "Esch-sur-Alzette",
            "service_type": "baumpflege",
            "number_of_trees": "",
            "is_rush": "on",
            "preferred_date": "",
            "preferred_time_slot": "flexible",
            "notes": "",
            "payment_status": "pending",
            "status": "pending",
            "admin_notes": "",
        }
        data.update(overrides)
        return {k: v for k, v in data.items() if v is not None}

    def save(self, data, instance=None):
        change = instance is not None
        form_class = self.model_admin.get_form(
            self.request, obj=instance, change=change
        )
        form = form_class(data=data, instance=instance)
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        self.model_admin.save_model(self.request, obj, form, change)
        obj.refresh_from_db()
        return obj

    def test_add_prices_the_booking(self):
        booking = self.save(self.form_data())
        self.assertEqual(booking.zone, 2)
        self.assertEqual(booking.distance_km, Decimal("42.0"))
        self.assertEqual(booking.prepayment_amount, Decimal("150.00"))

    def test_editing_a_pricing_input_reprices(self):
        booking = self.save(self.form_data())
        booking = self.save(self.form_data(is_rush=None), instance=booking)
        self.assertEqual(booking.prepayment_amount, Decimal("100.00"))

    def test_other_edits_keep_the_quote(self):
        booking = self.save(self.form_data())
        ArboristBooking.objects.filter(pk=booking.pk).update(
            prepayment_amount=Decimal("120.00")
        )
        booking.refresh_from_db()
        booking = self.save(self.form_data(admin_notes="called back"), instance=booking)
        self.assertEqual(booking.prepayment_amount, Decimal("120.00"))

    def test_admin_form_rejects_malformed_postcode(self):
        form_class = self.model_admin.get_form(self.request)
        form = form_class(data=self.form_data(postal_code="62111"))
        self.assertFalse(form.is_valid())
        self.assertIn("postal_code", form.errors)
