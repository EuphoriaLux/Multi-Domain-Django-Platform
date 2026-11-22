"""Abandoned Mollie checkouts: one reminder, then the seat goes back to the pool.

``crush_lu/services/abandoned_checkouts.py`` and the late-capture branch in
``views_payments._settle_capture_for_released_seat``. Mollie is mocked at
``requests.request`` as in test_mollie_event_payments.
"""

from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone

from crush_lu.models.credits import CrushCredit
from crush_lu.models.events import EventRegistration
from crush_lu.models.payments import PaymentTransaction
from crush_lu.services import abandoned_checkouts as sweep
from crush_lu.tests.test_crush_credit import FEE, CreditFixture
from crush_lu.tests.test_mollie_event_payments import (
    MOLLIE_ON,
    REQUEST,
    WEBHOOK,
    _payment,
)
from crush_lu.views_events import _resale_claim_from

API_KEY = "test-admin-key"


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    ONLINE_PAYMENTS_ENABLED=False,
    ADMIN_API_KEY=API_KEY,
    **MOLLIE_ON,
)
class AbandonedCheckoutTests(CreditFixture):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.buyer = self._user("buyer@crush.lu")
        self.target = self._event(hours_away=96, max_participants=2)
        self.seat = self._registration(self.target, self.buyer, status="pending")
        self.now = timezone.now()
        # Signed up a while ago; each test backdates its checkouts after that.
        EventRegistration.objects.filter(pk=self.seat.pk).update(
            registered_at=self.now - timedelta(days=1)
        )
        self.seat.refresh_from_db()

    # -- builders -----------------------------------------------------------

    def _checkout(self, status, minutes_ago, payment_id="tr_abandon01", ref=None):
        tx = PaymentTransaction.objects.create(
            transaction_reference=ref or f"CRUSH-EVT-{payment_id}",
            provider=PaymentTransaction.Provider.MOLLIE,
            mollie_payment_id=payment_id,
            amount=FEE,
            currency="EUR",
            status=status,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.buyer,
            event_registration=self.seat,
        )
        PaymentTransaction.objects.filter(pk=tx.pk).update(
            created_at=self.now - timedelta(minutes=minutes_ago)
        )
        return tx

    def _run(self, minutes_later=0):
        with self.captureOnCommitCallbacks(execute=True):
            return sweep.run_sweep(now=self.now + timedelta(minutes=minutes_later))

    def _fill_other_seat(self, email="other@crush.lu"):
        other = self._user(email)
        return self._registration(self.target, other, status="confirmed")

    # -- reminder -----------------------------------------------------------

    def test_no_reminder_while_the_member_may_still_be_retrying(self):
        self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=5)
        with mock.patch(REQUEST) as call:
            counts = self._run()
        call.assert_not_called()
        self.assertEqual(counts, {})
        self.seat.refresh_from_db()
        self.assertIsNone(self.seat.checkout_reminder_at)
        self.assertEqual(len(mail.outbox), 0)

    def test_one_reminder_after_fifteen_minutes_seat_still_held(self):
        self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=16)
        counts = self._run()
        self.assertEqual(counts, {sweep.Outcome.REMINDED: 1})
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "pending")
        self.assertIsNotNone(self.seat.checkout_reminder_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["buyer@crush.lu"])
        self.assertIn(f"/events/{self.target.pk}/", mail.outbox[0].alternatives[0][0])

        # The next run, still inside the hold, sends nothing more.
        counts = self._run(minutes_later=5)
        self.assertEqual(counts, {sweep.Outcome.WAITING: 1})
        self.assertEqual(len(mail.outbox), 1)

    def test_reminder_goes_out_in_the_members_language(self):
        for lang, subject_start, body_text in (
            ("de", "Dein Platz für", "Zahlung abschließen"),
            ("fr", "Votre place pour", "Finaliser mon paiement"),
        ):
            with self.subTest(lang=lang):
                mail.outbox.clear()
                EventRegistration.objects.filter(pk=self.seat.pk).update(
                    checkout_reminder_at=None
                )
                self.buyer.crushprofile.preferred_language = lang
                self.buyer.crushprofile.save(update_fields=["preferred_language"])
                PaymentTransaction.objects.filter(event_registration=self.seat).delete()
                self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=16)
                self._run()
                self.assertEqual(len(mail.outbox), 1)
                self.assertTrue(mail.outbox[0].subject.startswith(subject_start))
                html = mail.outbox[0].alternatives[0][0]
                self.assertIn(body_text, html)
                self.assertIn(f"/{lang}/events/{self.target.pk}/", html)

    def test_open_checkout_at_mollie_is_left_alone(self):
        tx = self._checkout(PaymentTransaction.Status.PENDING, minutes_ago=40)
        with mock.patch(REQUEST, return_value=_payment("open")):
            counts = self._run()
        self.assertEqual(counts, {sweep.Outcome.WAITING: 1})
        tx.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.PENDING)
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "pending")
        self.assertEqual(len(mail.outbox), 0)

    def test_pay_by_bank_still_settling_is_left_alone(self):
        self._checkout(PaymentTransaction.Status.PENDING, minutes_ago=120)
        with mock.patch(REQUEST, return_value=_payment("pending", checkout=False)):
            counts = self._run()
        self.assertEqual(counts, {sweep.Outcome.WAITING: 1})
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "pending")

    def test_expired_checkout_is_recorded_then_reminded(self):
        tx = self._checkout(PaymentTransaction.Status.PENDING, minutes_ago=20)
        with mock.patch(REQUEST, return_value=_payment("expired", checkout=False)):
            counts = self._run()
        self.assertEqual(counts, {sweep.Outcome.REMINDED: 1})
        tx.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.CANCELLED)

    def test_a_late_paid_read_confirms_instead_of_reminding(self):
        self._checkout(PaymentTransaction.Status.PENDING, minutes_ago=20)
        with mock.patch(REQUEST, return_value=_payment("paid", checkout=False)):
            self._run()
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "confirmed")
        self.assertTrue(self.seat.payment_confirmed)
        self.assertIsNone(self.seat.checkout_reminder_at)

    def test_seat_without_an_online_checkout_is_never_touched(self):
        """A staff-added, comp or not-yet-clicked seat holds as before."""
        EventRegistration.objects.filter(pk=self.seat.pk).update(
            registered_at=self.now - timedelta(days=2)
        )
        counts = self._run(minutes_later=600)
        self.assertEqual(counts, {})
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "pending")

    def test_checkout_from_an_earlier_cycle_does_not_count(self):
        self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=300)
        EventRegistration.objects.filter(pk=self.seat.pk).update(
            registered_at=self.now - timedelta(minutes=60)
        )
        self.assertEqual(self._run(), {})

    def test_nothing_runs_while_mollie_event_payments_are_off(self):
        self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=200)
        with self.settings(MOLLIE_EVENT_PAYMENTS_ENABLED=False):
            counts = self._run()
        self.assertEqual(counts, {"skipped_inactive": 1})
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "pending")

    # -- release ------------------------------------------------------------

    def test_release_after_the_hold_and_waitlist_gets_the_seat(self):
        self._fill_other_seat()
        waiting = self._registration(
            self.target, self._user("waiting@crush.lu"), status="waitlist"
        )
        self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=16)
        self._run()  # reminder
        mail.outbox.clear()

        # 31 min after the checkout but only 15 after the reminder: hold.
        counts = self._run(minutes_later=10)
        self.assertEqual(counts, {sweep.Outcome.WAITING: 1})

        counts = self._run(minutes_later=16)
        self.assertEqual(counts, {sweep.Outcome.RELEASED: 1})
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "cancelled")
        self.assertIsNotNone(self.seat.checkout_released_at)
        self.assertEqual(self.seat.cancelled_at, self.seat.checkout_released_at)
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, "pending")
        self.assertIsNotNone(waiting.waitlist_promoted_at)
        # No cancellation email to the released member, and no credit.
        self.assertFalse(any("buyer@crush.lu" in m.to for m in mail.outbox))
        self.assertFalse(CrushCredit.objects.filter(user=self.buyer).exists())

    def test_release_without_reminder_when_sends_keep_failing(self):
        self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=16)
        with mock.patch(
            "crush_lu.email_helpers.send_event_checkout_reminder",
            side_effect=RuntimeError("graph down"),
        ):
            self.assertEqual(self._run(), {sweep.Outcome.REMINDER_FAILED: 1})
            self.assertEqual(self._run(minutes_later=80), {sweep.Outcome.RELEASED: 1})

    def test_checkout_that_ended_late_still_gets_its_reminder(self):
        """A checkout open for two hours, then expired: email first, not release."""
        self._checkout(PaymentTransaction.Status.PENDING, minutes_ago=120)
        with mock.patch(REQUEST, return_value=_payment("expired", checkout=False)):
            counts = self._run()
        self.assertEqual(counts, {sweep.Outcome.REMINDED: 1})
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "pending")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self._run(minutes_later=10), {sweep.Outcome.WAITING: 1})
        self.assertEqual(self._run(minutes_later=16), {sweep.Outcome.RELEASED: 1})

    def test_release_rechecks_the_latest_checkout_under_the_locks(self):
        self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=200)
        self._run()  # reminder
        # The member tries again and backs out just before the release.
        self._checkout(
            PaymentTransaction.Status.CANCELLED,
            minutes_ago=0,
            payment_id="tr_retry0001",
        )
        released = sweep.release_seat(
            self.seat.pk,
            registered_at=self.seat.registered_at,
            now=self.now + timedelta(minutes=20),
        )
        self.assertFalse(released)
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "pending")

    def test_release_refuses_a_cycle_it_did_not_inspect(self):
        self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=200)
        self._run()
        released = sweep.release_seat(
            self.seat.pk,
            registered_at=self.seat.registered_at - timedelta(days=3),
            now=self.now + timedelta(minutes=20),
        )
        self.assertFalse(released)

    def test_seats_past_the_per_run_cap_are_reached_on_the_next_run(self):
        other = self._user("second@crush.lu")
        second = self._registration(self.target, other, status="pending")
        EventRegistration.objects.filter(pk=second.pk).update(
            registered_at=self.now - timedelta(days=1)
        )
        # The first seat's checkout stays open, so it waits on every run.
        self._checkout(PaymentTransaction.Status.PENDING, minutes_ago=40)
        tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-second",
            provider=PaymentTransaction.Provider.MOLLIE,
            mollie_payment_id="tr_second001",
            amount=FEE,
            currency="EUR",
            status=PaymentTransaction.Status.CANCELLED,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=other,
            event_registration=second,
        )
        PaymentTransaction.objects.filter(pk=tx.pk).update(
            created_at=self.now - timedelta(minutes=20)
        )
        with mock.patch.object(sweep, "MAX_REGISTRATIONS_PER_RUN", 1), mock.patch(
            REQUEST, return_value=_payment("open")
        ):
            first = self._run()
            second_run = self._run()
        self.assertEqual(first, {sweep.Outcome.WAITING: 1, "deferred": 1})
        self.assertEqual(second_run, {sweep.Outcome.REMINDED: 1, "deferred": 1})
        second.refresh_from_db()
        self.assertIsNotNone(second.checkout_reminder_at)

    def test_unsubscribed_member_is_released_on_the_normal_clock(self):
        self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=16)
        with mock.patch("crush_lu.email_helpers.can_send_email", return_value=False):
            self.assertEqual(self._run(), {sweep.Outcome.REMINDER_SKIPPED: 1})
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self._run(minutes_later=16), {sweep.Outcome.RELEASED: 1})

    def test_started_event_is_not_released(self):
        self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=16)
        self._run()
        self.target.date_time = self.now + timedelta(minutes=20)
        self.target.save(update_fields=["date_time"])
        self.assertEqual(self._run(minutes_later=30), {})
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "pending")

    def test_curated_event_is_skipped(self):
        self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=200)
        with mock.patch(
            "crush_lu.models.events.MeetupEvent.uses_curated_registration",
            new_callable=mock.PropertyMock,
            return_value=True,
        ):
            self.assertEqual(self._run(), {sweep.Outcome.SKIPPED: 1})

    def test_released_seat_carries_no_resale_claim(self):
        self.target.date_time = self.now + timedelta(hours=10)  # inside 48h
        self.target.save(update_fields=["date_time"])
        self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=200)
        self._run()
        self._run(minutes_later=20)
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "cancelled")
        self.assertIsNone(_resale_claim_from(self.seat, self.target))

    def test_re_registration_clears_the_markers(self):
        self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=200)
        self._run()
        self._run(minutes_later=20)
        self.client.force_login(self.buyer)
        response = self.client.post(f"/en/events/{self.target.pk}/register/")
        self.assertIn(response.status_code, (200, 302))
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "pending")
        self.assertIsNone(self.seat.checkout_released_at)
        self.assertIsNone(self.seat.checkout_reminder_at)

    # -- a payment that lands after the release ------------------------------

    def _release(self):
        self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=200)
        self._run()
        self._run(minutes_later=20)
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "cancelled")
        mail.outbox.clear()

    def _late_capture(self):
        self._checkout(
            PaymentTransaction.Status.PENDING,
            minutes_ago=1,
            payment_id="tr_latepay01",
        )
        with mock.patch(
            REQUEST, return_value=_payment("paid", payment_id="tr_latepay01")
        ):
            with self.captureOnCommitCallbacks(execute=True):
                response = Client(HTTP_HOST="crush.lu").post(
                    WEBHOOK, {"id": "tr_latepay01"}
                )
        self.assertEqual(response.status_code, 200)

    def test_late_payment_reconfirms_when_a_seat_is_free(self):
        self._release()
        self._late_capture()
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "confirmed")
        self.assertTrue(self.seat.payment_confirmed)
        self.assertIsNone(self.seat.checkout_released_at)
        self.assertIsNone(self.seat.cancelled_at)
        self.assertFalse(CrushCredit.objects.filter(user=self.buyer).exists())
        self.assertTrue(any("buyer@crush.lu" in m.to for m in mail.outbox))

    def test_late_payment_becomes_full_credit_when_the_event_filled(self):
        self._release()
        self._fill_other_seat("a@crush.lu")
        self._fill_other_seat("b@crush.lu")
        self._late_capture()
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "cancelled")
        self.assertFalse(self.seat.payment_confirmed)
        credit = CrushCredit.objects.get(user=self.buyer)
        self.assertEqual(credit.reason, CrushCredit.Reason.SEAT_RELEASED_UNPAID)
        self.assertEqual(credit.amount_cents, int(FEE * 100))
        self.assertTrue(credit.cash_refund_eligible)
        late = PaymentTransaction.objects.get(mollie_payment_id="tr_latepay01")
        self.assertEqual(late.status, PaymentTransaction.Status.PAID)
        self.assertEqual(
            [m.to for m in mail.outbox if "buyer@crush.lu" in m.to],
            [["buyer@crush.lu"]],
        )

        # A repeat of the webhook issues nothing more.
        with mock.patch(
            REQUEST, return_value=_payment("paid", payment_id="tr_latepay01")
        ):
            Client(HTTP_HOST="crush.lu").post(WEBHOOK, {"id": "tr_latepay01"})
        self.assertEqual(CrushCredit.objects.filter(user=self.buyer).count(), 1)

    # -- endpoint -----------------------------------------------------------

    def test_admin_endpoint_runs_the_sweep(self):
        client = Client(HTTP_HOST="crush.lu")
        url = "/api/admin/abandoned-checkout-sweep/"
        self.assertIn(
            client.post(url, HTTP_AUTHORIZATION="Bearer wrong").status_code,
            (401, 403),
        )
        self._checkout(PaymentTransaction.Status.CANCELLED, minutes_ago=16)
        response = client.post(url, HTTP_AUTHORIZATION=f"Bearer {API_KEY}")
        self.assertEqual(response.status_code, 202)
        self.seat.refresh_from_db()
        self.assertIsNotNone(self.seat.checkout_reminder_at)
