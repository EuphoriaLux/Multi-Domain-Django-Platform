"""Bank-transfer donations on the support card.

SumUp refused the platform in 2026-10, so with DONATION_BANK_* configured the
support card shows the account details and an EPC "scan to pay" QR code where
the card checkout used to be. A coach matches the transfer by its reference
and grants the Community Supporter badge from the CrushProfile admin.

Requests use literal paths on the crush.lu host (AGENTS.md: ``reverse()``
resolves against the default urlconf, not the host's).
"""

from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings

from crush_lu.models.profiles import CrushProfile, UserDataConsent
from crush_lu.services import donation_bank_transfer as bank_transfer
from crush_lu.services.donation_bank_transfer import (
    DONATION_REFERENCE_RE,
    epc_payload,
    get_bank_transfer_details,
    iban_is_valid,
    normalize_iban,
)

User = get_user_model()

# The IBAN registry's Luxembourg example: valid checksum, not a real account.
IBAN = "LU28 0019 4006 4475 0000"
BANK = {
    "DONATION_BANK_IBAN": IBAN,
    "DONATION_BANK_BENEFICIARY": "Crush.lu Test",
    "DONATION_BANK_BIC": "BCEELULL",
}
NO_BANK = {"DONATION_BANK_IBAN": "", "DONATION_BANK_BENEFICIARY": ""}
MY_EVENTS = "/en/my-events/"
DONATION_ENDPOINT = "/payments/sumup/create-donation-checkout/"
PAUSED = "Online contributions are paused for now. Thank you for your support!"
QR_LABEL = 'aria-label="QR code for a bank transfer to Crush.lu"'


class _Profile:
    pk = 1234


class EpcPayloadTests(SimpleTestCase):
    def test_iban_checksum(self):
        self.assertTrue(iban_is_valid(normalize_iban(IBAN)))
        self.assertTrue(iban_is_valid("DE89370400440532013000"))
        # One digit off: the transfer form must never get a typo.
        self.assertFalse(iban_is_valid("LU280019400644750001"))
        self.assertFalse(iban_is_valid("not an iban"))
        self.assertFalse(iban_is_valid(""))

    @override_settings(**BANK)
    def test_payload_follows_epc069_12(self):
        details = get_bank_transfer_details(_Profile())

        self.assertEqual(
            details.payload,
            "BCD\n002\n1\nSCT\nBCEELULL\nCrush.lu Test\nLU280019400644750000"
            # No amount, purpose or structured reference: the member picks
            # the amount in their banking app.
            "\n\n\n\nCrush.lu support 1234",
        )
        # EPC forbids a separator after the last populated element.
        self.assertFalse(details.payload.endswith("\n"))
        self.assertEqual(details.iban_display, "LU28 0019 4006 4475 0000")

    def test_long_fields_are_cut_to_epc_limits(self):
        payload = epc_payload(
            iban="LU280019400644750000",
            beneficiary="B" * 100,
            remittance="R" * 200,
        )
        lines = payload.split("\n")

        self.assertEqual(len(lines[5]), 70)
        self.assertEqual(len(lines[10]), 140)
        self.assertLessEqual(
            len(payload.encode("utf-8")), bank_transfer.EPC_MAX_PAYLOAD_BYTES
        )

    @override_settings(**BANK)
    def test_qr_is_only_built_when_read(self):
        bank_transfer.qr_svg.cache_clear()
        with mock.patch.object(
            bank_transfer, "qr_svg", wraps=bank_transfer.qr_svg
        ) as built:
            details = get_bank_transfer_details(_Profile())
            built.assert_not_called()

            qr = details.qr

        built.assert_called_once_with(details.payload)
        # Version 5 (37 modules) plus the 4-module quiet zone on each side.
        self.assertEqual(qr.size, 45)
        self.assertTrue(qr.path.startswith("M"))

    @override_settings(**{**BANK, "DONATION_BANK_IBAN": "LU28 0019 4006 4475 0001"})
    def test_an_iban_failing_its_checksum_hides_the_details(self):
        with self.assertLogs(bank_transfer.logger, level="WARNING"):
            self.assertIsNone(get_bank_transfer_details(_Profile()))

    @override_settings(**{**BANK, "DONATION_BANK_BENEFICIARY": "  "})
    def test_details_need_a_beneficiary(self):
        self.assertIsNone(get_bank_transfer_details(_Profile()))

    @override_settings(**{**BANK, "DONATION_BANK_BIC": "NOT-A-BIC"})
    def test_a_malformed_bic_is_left_out(self):
        with self.assertLogs(bank_transfer.logger, level="WARNING"):
            details = get_bank_transfer_details(_Profile())

        self.assertEqual(details.bic, "")
        self.assertTrue(details.payload.startswith("BCD\n002\n1\nSCT\n\n"))

    def test_reference_is_read_back_off_a_bank_statement(self):
        for line in (
            "Crush.lu support 1234",
            "CRUSH.LU SUPPORT 1234",
            "crushlu support 1234",
            "/RMT/Crush.lu support 1234 J. DOE",
        ):
            with self.subTest(line=line):
                self.assertEqual(DONATION_REFERENCE_RE.search(line).group(1), "1234")
        self.assertIsNone(DONATION_REFERENCE_RE.search("jane@example.com"))


class SupportCardBankTransferTests(TestCase):
    def setUp(self):
        # Recycled SQLite primary keys share one rate-limit counter.
        cache.clear()
        self.user = User.objects.create_user(
            username="donor@crush.lu", email="donor@crush.lu", password="pw12345678"
        )
        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.profile = CrushProfile.objects.create(
            user=self.user, verification_status="verified", completion_status="step4"
        )
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(self.user)

    def _card(self, path=MY_EVENTS):
        response = self.client.get(path, follow=True)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    @override_settings(ONLINE_PAYMENTS_ENABLED=False, **BANK)
    def test_paused_card_offers_the_bank_transfer(self):
        body = self._card()

        self.assertIn("Support us by bank transfer", body)
        self.assertIn("LU28 0019 4006 4475 0000", body)
        self.assertIn("Crush.lu Test", body)
        self.assertIn(f"Crush.lu support {self.profile.pk}", body)
        self.assertIn(QR_LABEL, body)
        self.assertIn('x-data="copyValue"', body)
        # The copy button puts the IBAN on the clipboard without spaces.
        self.assertIn('data-value="LU280019400644750000"', body)
        self.assertNotIn(PAUSED, body)
        self.assertNotIn(DONATION_ENDPOINT, body)

    @override_settings(ONLINE_PAYMENTS_ENABLED=False, **NO_BANK)
    def test_paused_card_without_bank_details_is_unchanged(self):
        body = self._card()

        self.assertIn(PAUSED, body)
        self.assertNotIn("Support us by bank transfer", body)
        self.assertNotIn(QR_LABEL, body)

    @override_settings(
        ONLINE_PAYMENTS_ENABLED=False, ANDROID_NATIVE_COMMERCE_ENABLED=False, **BANK
    )
    def test_the_native_shell_gets_no_bank_details(self):
        """Store compliance: account details are a purchase surface too."""
        body = self._card(f"{MY_EVENTS}?source=android_app")

        self.assertIn("Support Crush.lu", body)
        self.assertIn("Available outside the mobile app", body)
        self.assertNotIn("LU28 0019 4006 4475 0000", body)
        self.assertNotIn(QR_LABEL, body)

    @override_settings(ONLINE_PAYMENTS_ENABLED=True, **BANK)
    def test_card_checkout_keeps_the_bank_transfer_as_an_alternative(self):
        body = self._card()

        self.assertIn(DONATION_ENDPOINT, body)
        self.assertIn("Prefer a bank transfer?", body)
        self.assertIn("LU28 0019 4006 4475 0000", body)

    @override_settings(ONLINE_PAYMENTS_ENABLED=False, **BANK)
    def test_german_card(self):
        body = self._card("/de/my-events/")

        self.assertIn("Unterstütze uns per Banküberweisung", body)
        self.assertIn("Verwendungszweck", body)


class GrantCommunitySupporterAdminTests(TestCase):
    PROFILES = "/crush-admin/crush_lu/crushprofile/"

    def setUp(self):
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        self.superuser = User.objects.create_superuser(
            username="bank_admin", email="bank-admin@example.com", password="pw12345678"
        )
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(self.superuser)
        CrushProfile.objects.filter(user=self.superuser).delete()
        self.donor = self._profile("donor")
        self.other = self._profile("other")

    def _profile(self, username):
        user = User.objects.create_user(
            username=username, email=f"{username}@example.com", password="pw12345678"
        )
        return CrushProfile.objects.create(
            user=user, date_of_birth=date(1992, 3, 4), gender="F"
        )

    def test_the_reference_from_the_statement_finds_the_profile(self):
        response = self.client.get(
            self.PROFILES, {"q": f"CRUSH.LU SUPPORT {self.donor.pk}"}
        )

        self.assertEqual(response.status_code, 200)
        listed = set(response.context["cl"].queryset.values_list("pk", flat=True))
        self.assertEqual(listed, {self.donor.pk})

    def test_action_grants_the_badge(self):
        response = self.client.post(
            self.PROFILES,
            {
                "action": "grant_community_supporter",
                "_selected_action": [self.donor.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.donor.refresh_from_db()
        self.other.refresh_from_db()
        self.assertTrue(self.donor.is_community_supporter)
        self.assertFalse(self.other.is_community_supporter)
