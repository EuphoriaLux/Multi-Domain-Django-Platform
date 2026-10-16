"""Follow-ups to the payment pause (#1272).

- While paused, a checkout SumUp will not close counts as closed, so cash,
  Crush Credit and curated repair are not blocked by a dead provider.
- The SumUp refund admin action is hidden while paused.
- Coaches record door cash from the check-in page (``Mark paid (cash)``).
"""

import json
from unittest.mock import MagicMock, patch

import requests
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings

from crush_lu.models.credits import CrushCredit
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import CrushCoach
from crush_lu.services.credits import issue_credit
from crush_lu.services.manual_payments import (
    CashPaymentRefused,
    record_cash_payment,
)
from crush_lu.services.sumup import SumUpClient, SumUpConfigurationError
from crush_lu.tests.test_crush_credit import (
    FEE_CENTS,
    CreditFixture,
    _admin_request,
)


def _forbidden(*args, **kwargs):
    response = MagicMock(status_code=403)
    raise requests.HTTPError("403 Client Error: Forbidden", response=response)


class _NoSumUpReads:
    """A refused close is followed by a status read; never let it go online."""

    def setUp(self):
        super().setUp()
        patcher = patch("crush_lu.services.sumup.requests.get", side_effect=_forbidden)
        patcher.start()
        self.addCleanup(patcher.stop)


class PausedCheckoutClosingTests(_NoSumUpReads, TestCase):
    @override_settings(ONLINE_PAYMENTS_ENABLED=False, SUMUP_API_KEY="key")
    @patch("crush_lu.services.sumup.requests.delete", side_effect=_forbidden)
    def test_a_refused_close_counts_as_closed_while_paused(self, _delete):
        with self.assertLogs("crush_lu.services.sumup", "WARNING") as logs:
            self.assertTrue(SumUpClient().deactivate_checkout("CHK-OLD"))
            self.assertTrue(SumUpClient().ensure_checkout_not_payable("CHK-OLD"))
        self.assertIn("[PAYMENTS-PAUSED]", "\n".join(logs.output))

    @override_settings(ONLINE_PAYMENTS_ENABLED=False, SUMUP_API_KEY="key")
    @patch("crush_lu.services.sumup.requests.delete", side_effect=_forbidden)
    @patch.object(SumUpClient, "get_checkout", return_value={"status": "PAID"})
    def test_a_checkout_sumup_reports_paid_is_never_closed_locally(self, _get, _delete):
        self.assertFalse(SumUpClient().deactivate_checkout("CHK-PAID"))
        self.assertFalse(SumUpClient().ensure_checkout_not_payable("CHK-PAID"))

    @override_settings(ONLINE_PAYMENTS_ENABLED=True, SUMUP_API_KEY="key")
    @patch("crush_lu.services.sumup.requests.delete", side_effect=_forbidden)
    def test_a_refused_close_still_blocks_when_payments_are_live(self, _delete):
        self.assertFalse(SumUpClient().deactivate_checkout("CHK-OLD"))

    @override_settings(ONLINE_PAYMENTS_ENABLED=False, SUMUP_API_KEY="")
    def test_a_missing_key_counts_as_closed_while_paused(self):
        self.assertTrue(SumUpClient().deactivate_checkout("CHK-OLD"))

    @override_settings(ONLINE_PAYMENTS_ENABLED=True, SUMUP_API_KEY="")
    def test_a_missing_key_stays_a_configuration_error_when_live(self):
        with self.assertRaises(SumUpConfigurationError):
            SumUpClient().deactivate_checkout("CHK-OLD")


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    ONLINE_PAYMENTS_ENABLED=False,
    SUMUP_API_KEY="key",
)
class PauseFollowupTests(_NoSumUpReads, CreditFixture):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.buyer = self._user("buyer@crush.lu")
        self.target = self._event(hours_away=48, max_participants=10)
        self.seat = self._registration(self.target, self.buyer, status="pending")
        coach_user = self._user("coach@crush.lu")
        self.coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        self.coach_user = coach_user

    def _open_card_checkout(self, checkout_id="CHK-OLD"):
        return PaymentTransaction.objects.create(
            transaction_reference=f"CRUSH-EVT-{self.seat.pk}-{checkout_id}",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id=checkout_id,
            amount=self.target.registration_fee,
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.buyer,
            event_registration=self.seat,
        )

    def _cash_url(self, registration=None):
        registration = registration or self.seat
        return (
            f"/en/coach/events/{registration.event_id}/registrations/"
            f"{registration.pk}/mark-paid-cash/"
        )

    # -- Crush Credit no longer blocked by an old card checkout -------------

    @patch("crush_lu.services.sumup.requests.delete", side_effect=_forbidden)
    def test_credit_pays_over_an_old_checkout_sumup_will_not_close(self, _delete):
        old = self._open_card_checkout()
        issue_credit(self.buyer, FEE_CENTS, CrushCredit.Reason.GOODWILL)
        self.client.force_login(self.buyer)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/payments/sumup/create-event-checkout/{self.seat.pk}/",
                data=json.dumps({"payment_method": "credit"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200, response.content)
        old.refresh_from_db()
        self.assertEqual(old.status, PaymentTransaction.Status.CANCELLED)
        self.seat.refresh_from_db()
        self.assertTrue(self.seat.payment_confirmed)

    # -- SumUp refund action -------------------------------------------------

    def test_sumup_refund_action_is_hidden_only_while_paused(self):
        from crush_lu.admin import CrushCreditAdmin
        from crush_lu.admin.site import crush_admin_site

        staff = self._user("staff@crush.lu")
        staff.is_staff = True
        staff.is_superuser = True
        staff.save(update_fields=["is_staff", "is_superuser"])
        admin_obj = CrushCreditAdmin(CrushCredit, crush_admin_site)
        request = _admin_request(staff)

        self.assertNotIn("refund_via_sumup", admin_obj.get_actions(request))
        self.assertIn("void_credits", admin_obj.get_actions(request))
        with self.settings(ONLINE_PAYMENTS_ENABLED=True):
            self.assertIn("refund_via_sumup", admin_obj.get_actions(request))

    def test_refund_guidance_names_the_manual_path_while_paused(self):
        from crush_lu.admin import CrushCreditAdmin
        from crush_lu.admin.site import crush_admin_site

        admin_obj = CrushCreditAdmin(CrushCredit, crush_admin_site)
        request = _admin_request(self.coach_user)

        def note():
            return dict(admin_obj.get_fieldsets(request))["Cash refund"]["description"]

        self.assertNotIn("SumUp dashboard", note())
        self.assertIn("bank transfer", note())
        with self.settings(ONLINE_PAYMENTS_ENABLED=True):
            self.assertIn("Refund via SumUp", note())

    def test_cash_reference_keeps_the_registration_id(self):
        record_cash_payment(self.seat.pk, actor=self.coach_user)
        cash = PaymentTransaction.objects.get(
            event_registration=self.seat, provider=PaymentTransaction.Provider.MANUAL
        )
        self.assertTrue(
            cash.transaction_reference.startswith(f"CRUSH-MANUAL-{self.seat.pk}-")
        )

    def test_a_checkout_opened_meanwhile_blocks_the_cash(self):
        from crush_lu.models.payments import EventCheckoutCreationClaim

        EventCheckoutCreationClaim.objects.create(
            registration=self.seat,
            registration_id_snapshot=self.seat.pk,
            event_id_snapshot=self.target.pk,
            transaction_reference=f"CRUSH-EVT-{self.seat.pk}-claim",
            payment_method="card",
        )
        with self.assertRaises(CashPaymentRefused):
            record_cash_payment(self.seat.pk, actor=self.coach_user)
        self.seat.refresh_from_db()
        self.assertFalse(self.seat.payment_confirmed)

    @patch("crush_lu.services.sumup.requests.delete")
    def test_a_close_survives_a_later_refusal(self, _delete):
        old = self._open_card_checkout()
        self.target.is_cancelled = True
        self.target.save(update_fields=["is_cancelled"])
        # Preview passes; the locked re-check refuses after the close.
        with patch(
            "crush_lu.services.manual_payments.cash_payment_refusal",
            side_effect=[None, "This event is cancelled."],
        ):
            with self.assertRaises(CashPaymentRefused):
                record_cash_payment(self.seat.pk, actor=self.coach_user)
        old.refresh_from_db()
        self.assertEqual(old.status, PaymentTransaction.Status.CANCELLED)

    # -- Coach "Mark paid (cash)" -------------------------------------------

    def test_coach_records_door_cash(self):
        self.client.force_login(self.coach_user)
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self._cash_url())

        self.assertEqual(response.status_code, 302)
        self.seat.refresh_from_db()
        self.assertTrue(self.seat.payment_confirmed)
        self.assertEqual(self.seat.status, "confirmed")
        self.assertIsNotNone(self.seat.payment_date)
        cash = PaymentTransaction.objects.get(
            event_registration=self.seat, status=PaymentTransaction.Status.PAID
        )
        self.assertEqual(cash.provider, PaymentTransaction.Provider.MANUAL)
        self.assertEqual(cash.amount, self.target.registration_fee)
        self.assertEqual(cash.raw_response["method"], "cash_at_door")
        self.assertEqual(
            cash.raw_response["recorded_by_coach_user_id"], self.coach_user.pk
        )
        self.assertEqual(len(mail.outbox), 1)

    def test_an_attendee_scanned_in_first_stays_attended(self):
        self.seat.status = "attended"
        self.seat.save(update_fields=["status"])

        record_cash_payment(self.seat.pk, actor=self.coach_user)

        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "attended")
        self.assertTrue(self.seat.payment_confirmed)

    def test_cash_is_not_recorded_twice(self):
        record_cash_payment(self.seat.pk, actor=self.coach_user)
        with self.assertRaises(CashPaymentRefused):
            record_cash_payment(self.seat.pk, actor=self.coach_user)
        self.assertEqual(
            PaymentTransaction.objects.filter(
                event_registration=self.seat,
                provider=PaymentTransaction.Provider.MANUAL,
            ).count(),
            1,
        )

    def test_cash_is_refused_for_a_waitlisted_or_free_seat(self):
        waiting = self._registration(
            self.target, self._user("wait@crush.lu"), status="waitlist"
        )
        with self.assertRaises(CashPaymentRefused):
            record_cash_payment(waiting.pk, actor=self.coach_user)

        free_event = self._event(hours_away=48, fee=0, max_participants=10)
        free_seat = self._registration(
            free_event, self._user("free@crush.lu"), status="confirmed"
        )
        with self.assertRaises(CashPaymentRefused):
            record_cash_payment(free_seat.pk, actor=self.coach_user)

    @patch("crush_lu.services.sumup.requests.delete", side_effect=_forbidden)
    def test_old_card_checkout_is_closed_while_paused(self, _delete):
        old = self._open_card_checkout()

        record_cash_payment(self.seat.pk, actor=self.coach_user)

        old.refresh_from_db()
        self.assertEqual(old.status, PaymentTransaction.Status.CANCELLED)

    @override_settings(ONLINE_PAYMENTS_ENABLED=True)
    @patch("crush_lu.services.sumup.requests.delete", side_effect=_forbidden)
    def test_old_card_checkout_blocks_cash_when_payments_are_live(self, _delete):
        old = self._open_card_checkout()

        with self.assertRaises(CashPaymentRefused):
            record_cash_payment(self.seat.pk, actor=self.coach_user)

        old.refresh_from_db()
        self.seat.refresh_from_db()
        self.assertEqual(old.status, PaymentTransaction.Status.PENDING)
        self.assertFalse(self.seat.payment_confirmed)

    def test_members_cannot_mark_themselves_paid(self):
        self.client.force_login(self.buyer)
        self.client.post(self._cash_url())
        self.seat.refresh_from_db()
        self.assertFalse(self.seat.payment_confirmed)

    def test_registration_must_belong_to_the_event(self):
        other_event = self._event(hours_away=72, max_participants=10)
        self.client.force_login(self.coach_user)
        response = self.client.post(
            f"/en/coach/events/{other_event.pk}/registrations/"
            f"{self.seat.pk}/mark-paid-cash/"
        )
        self.assertEqual(response.status_code, 404)

    def test_next_only_accepts_a_same_site_path(self):
        self.client.force_login(self.coach_user)
        response = self.client.post(
            self._cash_url(), {"next": "https://evil.example/steal"}
        )
        self.assertNotIn("evil.example", response["Location"])
        self.assertIn(f"/coach/events/{self.target.pk}/checkin/", response["Location"])

    def test_checkin_page_offers_the_button_for_unpaid_seats_only(self):
        paid = self._paid_registration(self.target, self._user("paid@crush.lu"))
        self.client.force_login(self.coach_user)

        page = self.client.get(
            f"/en/coach/events/{self.target.pk}/checkin/"
        ).content.decode()

        self.assertIn(self._cash_url(), page)
        self.assertNotIn(self._cash_url(paid), page)
